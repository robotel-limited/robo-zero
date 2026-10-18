"""Rute pentru folderele de pe disc legate de un proiect (pagina /projects):
listare, legare, modificare mod/notă/indexare, dezlegare, reindexare."""

from fastapi import APIRouter, HTTPException

import folder_index
import folder_map
import project_folders_store as folders
import projects_store

router = APIRouter()


def _project_or_404(project_id: str) -> None:
    if projects_store.get(project_id) is None:
        raise HTTPException(status_code=404, detail="Proiect inexistent.")


def _folder_or_404(project_id: str, folder_id: str) -> dict:
    folder = folders.get(folder_id)
    if folder is None or folder["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Folder inexistent.")
    return folder


@router.get("/api/projects/{project_id}/folders")
async def list_folders(project_id: str):
    _project_or_404(project_id)
    return folders.list_for(project_id)


@router.post("/api/projects/{project_id}/folders")
async def add_folder(project_id: str, body: dict):
    _project_or_404(project_id)
    body = body or {}
    try:
        folder = folders.add(
            project_id, str(body.get("path", "")), str(body.get("mode", "ro")),
            str(body.get("note", "")), bool(body.get("indexable", False)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    folder_map.get_map(folder)
    folder_index.schedule(folder)
    return folders.get(folder["id"])


@router.patch("/api/projects/{project_id}/folders/{folder_id}")
async def update_folder(project_id: str, folder_id: str, body: dict):
    before = _folder_or_404(project_id, folder_id)
    try:
        folder = folders.update(folder_id, **(body or {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if folder["indexable"] and not before["indexable"]:
        folder_index.schedule(folder)
    elif before["indexable"] and not folder["indexable"]:
        folder_index.unindex(folder_id)
    return folders.get(folder_id)


@router.delete("/api/projects/{project_id}/folders/{folder_id}")
async def delete_folder(project_id: str, folder_id: str):
    _folder_or_404(project_id, folder_id)
    folder_index.unindex(folder_id)
    folders.delete(folder_id)
    return {"deleted": folder_id}


@router.post("/api/projects/{project_id}/folders/{folder_id}/reindex")
async def reindex_folder(project_id: str, folder_id: str):
    folder = _folder_or_404(project_id, folder_id)
    if not folder["indexable"]:
        raise HTTPException(status_code=400, detail="Folderul nu e marcat indexabil.")
    folder_index.schedule(folder)
    return folders.get(folder_id)
