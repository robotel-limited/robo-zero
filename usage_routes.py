"""Rute read-only pentru consum/cost LLM, din usage_store.py (tabel SQLite
`usage_log`): /api/usage/cost (per chat/proiect, pentru cardurile din UI) și
/api/usage/stats (per model, filtrabil pe interval de timp — pentru tabelul
din Settings)."""

from fastapi import APIRouter

import usage_store

router = APIRouter()


@router.get("/api/usage/cost")
async def get_cost():
    return usage_store.totals()


@router.get("/api/usage/stats")
async def get_stats(model: str | None = None, since: float | None = None, until: float | None = None):
    """`since`/`until` = unix timestamp (secunde); webui le calculează din
    inputurile de dată cu Date.parse(...)/1000."""
    return usage_store.stats(model=model, since=since, until=until)
