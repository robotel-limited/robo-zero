"""Procesarea în fundal a unui fișier încărcat în baza de cunoștințe a unui
proiect: extragere text (kb_extract) → bucăți indexate (project_kb_store).
Extragerea prin modelul vision durează secunde, deci nu blochează upload-ul;
UI-ul vede statusul processing → ready / error."""

import asyncio
from typing import Any

import kb_extract
import project_kb_store as store
import projects_store

# referințe puternice: altfel un task fără referință poate fi colectat de GC
_tasks: set[asyncio.Task] = set()


def schedule(file: dict[str, Any]) -> None:
    task = asyncio.create_task(_ingest(file))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _ingest(file: dict[str, Any]) -> None:
    try:
        text = await kb_extract.extract(store.file_path(file), file["filename"], file["mime"])
    except Exception as e:  # orice eșec devine status vizibil, nu excepție pierdută
        store.set_error(file["id"], str(e))
        return
    if store.get_file(file["id"]) is not None:  # șters cât a durat extragerea
        store.set_ready(file["id"], file["project_id"], text)


def resume_pending() -> None:
    """La pornirea serviciului: fișierele rămase în `processing` (serviciu
    oprit în timpul extragerii) se reiau."""
    for project in projects_store.list_all():
        for file in store.list_files(project["id"]):
            if file["status"] == "processing":
                schedule(file)
