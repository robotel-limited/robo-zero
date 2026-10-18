"""Rută read-only: cost estimat per chat/proiect, din data/usage.jsonl
(vezi cost.py)."""

from fastapi import APIRouter

import cost

router = APIRouter()


@router.get("/api/usage/cost")
async def get_cost():
    return cost.totals()
