"""Indexarea în fundal a folderelor de proiect marcate „indexabil”:
parcurge folderul (fără .git/.venv/node_modules/ascunse/secrete), extrage
textul fișierelor noi sau modificate (text/cod, PDF cu text, DOCX — fără
apeluri LLM) și îl pune în folder_index_store. Incremental pe mtime+mărime."""

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import folder_index_store as index
import folder_map
import kb_extract
import path_access
import project_folders_store as folders
import projects_store

_MAX_FILES = 5000
_MAX_TEXT_BYTES = 1024 * 1024        # text/cod
_MAX_DOC_BYTES = 30 * 1024 * 1024    # PDF/DOCX (textul extras e mult mai mic)
_REFRESH_MIN_SECONDS = 60            # reverificare la deschiderea unui chat, nu mai des

_running: set[str] = set()
_tasks: set[asyncio.Task] = set()


def _candidates(root: str) -> tuple[list[tuple[str, float, int]], bool]:
    """(fișiere indexabile: cale relativă, mtime, mărime), plafon atins."""
    found: list[tuple[str, float, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in folder_map.SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            ext = Path(name).suffix.lstrip(".").lower()
            if name.startswith(".") or ext not in kb_extract.LOCAL_EXTENSIONS or path_access.is_secret(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            if st.st_size > (_MAX_DOC_BYTES if ext in ("pdf", "docx") else _MAX_TEXT_BYTES):
                continue
            found.append((os.path.relpath(full, root), st.st_mtime, st.st_size))
            if len(found) >= _MAX_FILES:
                return found, True
    return found, False


async def _index(folder: dict[str, Any]) -> None:
    folders.set_index_state(folder["id"], "indexing")
    root = folder["path"]
    try:
        files, capped = await asyncio.to_thread(_candidates, root)
        known = index.known(folder["id"])
        for i, (relpath, mtime, size) in enumerate(files):
            if known.get(relpath) == (mtime, size):
                continue
            try:
                text = await kb_extract.extract_local(Path(root) / relpath)
            except Exception:  # fișier corupt / necitibil: memorat ca văzut, fără text
                text = ""
            if folders.get(folder["id"]) is None:  # folder dezlegat în timpul indexării
                return
            index.upsert(folder["project_id"], folder["id"], relpath, mtime, size, text)
            if i % 50 == 0:
                await asyncio.sleep(0)
        seen = {f[0] for f in files}
        index.remove(folder["id"], [p for p in known if p not in seen])
        note = f"plafon de {_MAX_FILES} fișiere atins — restul nu e indexat" if capped else ""
        folders.set_index_state(folder["id"], "ready", note, files=index.count_indexed(folder["id"]))
    except Exception as e:
        folders.set_index_state(folder["id"], "error", str(e))


async def _run(folder: dict[str, Any]) -> None:
    try:
        await _index(folder)
    finally:
        _running.discard(folder["id"])


def schedule(folder: dict[str, Any]) -> None:
    """Pornește indexarea în fundal (o singură rulare per folder odată)."""
    if not folder["indexable"] or folder["id"] in _running:
        return
    _running.add(folder["id"])
    task = asyncio.create_task(_run(folder))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def refresh_project(project_id: str) -> None:
    """La un mesaj nou într-un chat de proiect: reverifică folderele
    indexabile, dar nu mai des de o dată pe minut per folder."""
    now = time.time()
    for folder in folders.list_for(project_id):
        if folder["indexable"] and now - folder["indexed_at"] >= _REFRESH_MIN_SECONDS:
            schedule(folder)


def unindex(folder_id: str) -> None:
    index.delete_folder(folder_id)
    folders.set_index_state(folder_id, "", files=0)


def resume_pending() -> None:
    """La pornirea serviciului: indexările întrerupte se reiau."""
    for project in projects_store.list_all():
        for folder in folders.list_for(project["id"]):
            if folder["indexable"] and folder["index_status"] == "indexing":
                schedule(folder)
