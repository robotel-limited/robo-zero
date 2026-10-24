"""Rute pentru rapoartele (fișierele cu link de rețea) generate de agent în
workdir-ul unui proiect (pagina /projects): listare centralizată, scoatere
din listă (fișierul de pe disc nu e șters)."""

import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request

import chat_store
import path_access
import project_reports_store as reports
import projects_store

router = APIRouter()

# md-reader (separate app, /srv/md-reader) renders .md reports formatted
# instead of as plain text; it exposes robo-zero's workdir under the
# 'robo-zero' source (see its SOURCES dict).
# Rulează pe SVL (192.168.0.160) și e accesibil de pe ambele r0 (SVL/SVAI) pe LAN.
MD_READER_HOST = "192.168.0.160"  # SVL IP on LAN
MD_READER_PORT = 7194


def _project_or_404(project_id: str) -> None:
    if projects_store.get(project_id) is None:
        raise HTTPException(status_code=404, detail="Proiect inexistent.")


def _as_api(report: dict, host: str) -> dict:
    workdir = os.path.realpath(path_access.BASE_WORKDIR)
    rel = os.path.realpath(report["path"])[len(workdir):].lstrip(os.sep).replace(os.sep, "/")
    raw_url = f"/files/{rel}"
    if report["path"].lower().endswith(".md"):
        url = f"http://{MD_READER_HOST}:{MD_READER_PORT}/?source=robo-zero&file={quote(rel)}"
    else:
        url = raw_url
    chat = chat_store.get(report["chat_id"]) if report["chat_id"] else None
    return {
        "id": report["id"],
        "filename": os.path.basename(report["path"]),
        "url": url,
        "raw_url": raw_url,
        "chat_id": report["chat_id"],
        "chat_title": chat.title if chat else None,
        "updated_at": report["updated_at"],
    }


@router.get("/api/projects/{project_id}/reports")
async def list_reports(project_id: str, request: Request):
    _project_or_404(project_id)
    host = request.url.hostname
    return [_as_api(r, host) for r in reports.list_for(project_id) if os.path.exists(r["path"])]


@router.delete("/api/projects/{project_id}/reports/{report_id}")
async def unlist_report(project_id: str, report_id: str):
    _project_or_404(project_id)
    reports.unlist(report_id)
    return {"ok": True}
