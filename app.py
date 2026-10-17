"""FastAPI: rute HTTP + SSE pentru webui/ (etapa 2).

Fără autentificare (decizie Sorin) — singura protecție e verificarea
Origin/Host la cererile cu efecte (POST/PUT/PATCH/DELETE), ca să respingă
cereri cross-site trimise din alt site deschis în același browser din LAN.
Nu protejează împotriva unui client direct (curl) din LAN — risc acceptat.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

import chat_store
import history
import projects_store
import settings_store
from chat_session import ChatSession

_PROJECT_ROOT = Path(__file__).resolve().parent
_WEBUI_DIR = _PROJECT_ROOT / "webui"

with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _WORKDIR = Path(yaml.safe_load(f)["agent"]["workdir"])

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _host_of(value: str) -> str:
    # Origin e un URL complet ("http://host:port"); Host e deja "host:port".
    parsed = urlparse(value)
    return parsed.netloc or value


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """Respinge cu 403 cererile cu efecte a căror origine declarată nu e
    chiar această aplicație. Un Origin absent (client direct, nu browser)
    e permis — e riscul acceptat al lipsei de login."""

    async def dispatch(self, request: Request, call_next):
        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and _host_of(origin) != request.headers.get("host", ""):
                return JSONResponse(
                    status_code=403, content={"detail": "Origine cross-site respinsă."}
                )
        return await call_next(request)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    chat_store.load_all()
    _WORKDIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="agent-zero-light", lifespan=_lifespan)
app.add_middleware(OriginCheckMiddleware)
app.mount("/static", StaticFiles(directory=_WEBUI_DIR), name="static")
# fișiere produse de agent în workdir (ex. imagini generate) — servite direct în chat.
_WORKDIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=_WORKDIR), name="files")


@app.get("/health")
async def health():
    """Health check endpoint. Returnează status ok."""
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(_WEBUI_DIR / "index.html")


@app.get("/settings")
async def settings_page():
    return FileResponse(_WEBUI_DIR / "settings.html")


@app.get("/projects")
async def projects_page():
    return FileResponse(_WEBUI_DIR / "projects.html")


def _get_chat_or_404(chat_id: str) -> ChatSession:
    session = chat_store.get(chat_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat inexistent.")
    return session


@app.get("/api/settings")
async def get_settings():
    return settings_store.load()


@app.put("/api/settings")
async def put_settings(body: dict):
    return settings_store.save(body or {})


def _get_project_or_404(project_id: str) -> dict:
    project = projects_store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Proiect inexistent.")
    return project


@app.get("/api/projects")
async def list_projects():
    return projects_store.list_all()


@app.post("/api/projects")
async def create_project(body: dict):
    body = body or {}
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Titlul e vid.")
    try:
        return projects_store.create(title, body.get("description", ""), body.get("prompt", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    return _get_project_or_404(project_id)


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, body: dict):
    _get_project_or_404(project_id)
    try:
        return projects_store.update(project_id, **(body or {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    _get_project_or_404(project_id)
    linked = chat_store.list_by_project(project_id)
    if any(s.is_running for s in linked):
        raise HTTPException(
            status_code=409,
            detail="Un chat din acest proiect are o rulare activă — anulează mai întâi.",
        )
    for session in linked:
        chat_store.delete(session.id)
    projects_store.delete(project_id)
    return {"status": "ok"}


@app.get("/api/chats")
async def list_chats():
    return chat_store.list_summaries()


@app.post("/api/chats")
async def create_chat(body: dict | None = None):
    project_id = (body or {}).get("project_id")
    if project_id:
        _get_project_or_404(project_id)
    session = chat_store.create(project_id=project_id)
    return {"id": session.id, "title": session.title, "project_id": session.project_id}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str):
    session = _get_chat_or_404(chat_id)
    return {
        "id": session.id,
        "title": session.title,
        "project_id": session.project_id,
        "log": session.log,
        "is_running": session.is_running,
        "token_count": history.estimate_tokens(session.agent.history),
    }


@app.patch("/api/chats/{chat_id}")
async def update_chat(chat_id: str, body: dict):
    session = _get_chat_or_404(chat_id)
    body = body or {}
    if "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Titlul e vid.")
        session.rename(title)
    if "project_id" in body:
        project_id = body.get("project_id")
        if project_id:
            _get_project_or_404(project_id)
        session.set_project(project_id)
    return {"id": session.id, "title": session.title, "project_id": session.project_id}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    session = _get_chat_or_404(chat_id)
    if session.is_running:
        raise HTTPException(status_code=409, detail="Chat-ul are o rulare activă — anulează mai întâi.")
    chat_store.delete(chat_id)
    return {"status": "ok"}


@app.post("/api/chats/{chat_id}/messages")
async def post_message(chat_id: str, body: dict):
    session = _get_chat_or_404(chat_id)
    text = (body or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Mesajul e vid.")
    try:
        session.start_run(text)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "started"}


@app.post("/api/chats/{chat_id}/approve")
async def post_approve(chat_id: str, body: dict):
    session = _get_chat_or_404(chat_id)
    approved = bool((body or {}).get("approved", False))
    if not session.resolve_approval(approved):
        raise HTTPException(status_code=409, detail="Nu există o aprobare în așteptare.")
    return {"status": "ok"}


@app.post("/api/chats/{chat_id}/cancel")
async def post_cancel(chat_id: str):
    session = _get_chat_or_404(chat_id)
    cancelled = session.request_cancel()
    return {"status": "ok" if cancelled else "no_active_run"}


@app.get("/api/chats/{chat_id}/events")
async def chat_events(chat_id: str):
    session = _get_chat_or_404(chat_id)
    queue = session.subscribe()

    async def event_stream():
        try:
            while True:
                entry = await queue.get()
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            session.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
