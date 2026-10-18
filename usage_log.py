"""Consumul de tokeni al fiecărui apel LLM, în data/usage.jsonl (append-only),
ca baza pentru costul per chat/proiect. Chat-ul și proiectul vin dintr-un
ContextVar setat la pornirea rulării (chat_session.py) sau a extragerii KB
(kb_ingest.py) — task-urile asyncio pornite de acolo (ex. extragerea
faptelor) îl moștenesc automat, fără parametri prin toate straturile."""

import json
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent
_LOG_PATH = _PROJECT_ROOT / "data" / "usage.jsonl"

scope: ContextVar[dict[str, str | None]] = ContextVar("usage_scope", default={})


def set_scope(chat_id: str | None = None, project_id: str | None = None) -> None:
    scope.set({"chat_id": chat_id, "project_id": project_id})


def record(model: str, purpose: str, usage: dict[str, Any] | None) -> None:
    """`usage` = câmpul omonim din răspunsul DeepSeek; prompt_cache_hit_tokens
    e partea servită din cache (taxată mult mai ieftin). Nu ridică excepții —
    o scriere eșuată nu trebuie să strice apelul LLM."""
    if not usage:
        return
    entry = {
        "ts": time.time(),
        "model": model,
        "purpose": purpose,
        **scope.get(),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
        "cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
