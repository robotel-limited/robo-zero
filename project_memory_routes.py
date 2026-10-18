"""Rută read-only pentru memoria unui proiect (fișa, istoricul înlocuirilor,
rezumatele chat-urilor) — afișată în pagina /projects."""

from fastapi import APIRouter, HTTPException

import project_memory_store as store
import projects_store

router = APIRouter()


@router.get("/api/projects/{project_id}/memory")
async def get_project_memory(project_id: str):
    if projects_store.get(project_id) is None:
        raise HTTPException(status_code=404, detail="Proiect inexistent.")
    return {
        "facts": store.active_facts(project_id),
        "superseded": store.superseded_facts(project_id),
        "chat_summaries": store.list_digests(project_id),
    }
