"""Rute de upload: imagini atașate în chat (capturi de ecran) și fișierele
bazei de cunoștințe a unui proiect. Fișierul vine ca body brut (Content-Type
= tipul fișierului, numele în query) — fără multipart, deci fără dependența
python-multipart."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import attachments
import kb_ingest
import project_kb_store as kb
import projects_store

router = APIRouter()

MAX_KB_FILE_BYTES = 50 * 1024 * 1024


async def _read_body(request: Request, max_bytes: int) -> bytes:
    declared = int(request.headers.get("content-length") or 0)
    if declared > max_bytes:
        raise HTTPException(status_code=413, detail=f"Fișier prea mare (max {max_bytes // (1024 * 1024)} MB).")
    data = await request.body()
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Fișier prea mare (max {max_bytes // (1024 * 1024)} MB).")
    if not data:
        raise HTTPException(status_code=400, detail="Fișier gol.")
    return data


@router.post("/api/attachments")
async def upload_attachment(request: Request):
    """Imagine atașată unui mesaj de chat; întoarce URL-ul local pe care
    clientul îl trimite apoi în `images` la POST /api/chats/{id}/messages."""
    data = await _read_body(request, attachments.MAX_IMAGE_BYTES)
    try:
        url = attachments.save_image(data, request.headers.get("content-type", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"url": url}


def _project_or_404(project_id: str) -> dict:
    project = projects_store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Proiect inexistent.")
    return project


def _kb_file_or_404(project_id: str, file_id: str) -> dict:
    file = kb.get_file(file_id)
    if file is None or file["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Fișier inexistent în acest proiect.")
    return file


@router.get("/api/projects/{project_id}/kb")
async def list_kb_files(project_id: str):
    _project_or_404(project_id)
    return kb.list_files(project_id)


@router.post("/api/projects/{project_id}/kb")
async def upload_kb_file(project_id: str, request: Request, filename: str = ""):
    _project_or_404(project_id)
    if not filename.strip():
        raise HTTPException(status_code=400, detail="Lipsește numele fișierului (?filename=).")
    data = await _read_body(request, MAX_KB_FILE_BYTES)
    mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
    file = kb.add_file(project_id, filename, mime, data)
    kb_ingest.schedule(file)
    return file


@router.get("/api/projects/{project_id}/kb/{file_id}/raw")
async def get_kb_file_raw(project_id: str, file_id: str):
    file = _kb_file_or_404(project_id, file_id)
    return FileResponse(
        kb.file_path(file),
        media_type=file["mime"] or None,
        filename=file["filename"],
        content_disposition_type="inline",
    )


@router.delete("/api/projects/{project_id}/kb/{file_id}")
async def delete_kb_file(project_id: str, file_id: str):
    _kb_file_or_404(project_id, file_id)
    kb.delete_file(file_id)
    return {"status": "ok"}
