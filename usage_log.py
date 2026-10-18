"""Consumul de tokeni al fiecărui apel LLM, persistat în usage_store.py
(tabel SQLite `usage_log`), ca baza pentru costul per chat/proiect/model.
Chat-ul și proiectul vin dintr-un ContextVar setat la pornirea rulării
(chat_session.py) sau a extragerii KB (kb_ingest.py) — task-urile asyncio
pornite de acolo (ex. extragerea faptelor) îl moștenesc automat, fără
parametri prin toate straturile."""

from contextvars import ContextVar
from typing import Any

import usage_store

scope: ContextVar[dict[str, str | None]] = ContextVar("usage_scope", default={})


def set_scope(chat_id: str | None = None, project_id: str | None = None) -> None:
    scope.set({"chat_id": chat_id, "project_id": project_id})


def record(
    model: str,
    purpose: str,
    usage: dict[str, Any] | None,
    latency_ms: float | None = None,
) -> None:
    """`usage` = câmpul omonim din răspunsul providerului; prompt_cache_hit_tokens
    e partea servită din cache (taxată mult mai ieftin). Nu ridică excepții —
    o scriere eșuată nu trebuie să strice apelul LLM."""
    if not usage:
        return
    s = scope.get()
    try:
        usage_store.insert(
            model=model,
            purpose=purpose,
            chat_id=s.get("chat_id"),
            project_id=s.get("project_id"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
            cache_miss_tokens=usage.get("prompt_cache_miss_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
        )
    except Exception:
        pass
