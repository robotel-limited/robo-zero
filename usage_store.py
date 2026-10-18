"""Istoricul consumului LLM (token-uri, cost, latență), în tabelul `usage_log`
din data/projects.db — aceeași convenție SQLite ca projects_store.py (connect
per apel, Row factory, init_db() cu PRAGMA table_info pentru migrări). Scris
de usage_log.py la fiecare apel; citit de usage_routes.py pentru /api/usage/*
și de webui/settings.js pentru tabelul filtrabil cost/latență per model."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import cost

_PROJECT_ROOT = Path(__file__).resolve().parent
_DB_PATH = _PROJECT_ROOT / "data" / "projects.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_JSONL_PATH = _PROJECT_ROOT / "data" / "usage.jsonl"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_from_jsonl(conn: sqlite3.Connection) -> None:
    """Import unic al istoricului din vechiul usage.jsonl (pre-SQLite), ca
    filtrarea pe perioadă să funcționeze și pentru consumul anterior."""
    if not _JSONL_PATH.exists():
        return
    rows = []
    with open(_JSONL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = e.get("ts", 0)
            model = e.get("model", "")
            cache_hit = e.get("cache_hit_tokens", 0)
            cache_miss = e.get("cache_miss_tokens", 0)
            completion = e.get("completion_tokens", 0)
            usd = cost.entry_cost(model, ts, cache_hit, cache_miss, completion)
            rows.append(
                (
                    ts,
                    model,
                    e.get("purpose", "chat"),
                    e.get("chat_id"),
                    e.get("project_id"),
                    e.get("prompt_tokens", 0),
                    cache_hit,
                    cache_miss,
                    completion,
                    None,
                    usd,
                )
            )
    if rows:
        conn.executemany(
            "INSERT INTO usage_log (ts, model, purpose, chat_id, project_id, "
            "prompt_tokens, cache_hit_tokens, cache_miss_tokens, completion_tokens, "
            "latency_ms, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    _JSONL_PATH.rename(_JSONL_PATH.with_suffix(".jsonl.migrated"))


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                model TEXT NOT NULL,
                purpose TEXT NOT NULL,
                chat_id TEXT,
                project_id TEXT,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
                cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                latency_ms REAL,
                cost_usd REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_log_model_ts ON usage_log(model, ts)")
        count = conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
        if count == 0:
            _migrate_from_jsonl(conn)


def insert(
    model: str,
    purpose: str,
    chat_id: str | None,
    project_id: str | None,
    prompt_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    completion_tokens: int,
    latency_ms: float | None,
) -> None:
    ts = time.time()
    usd = cost.entry_cost(model, ts, cache_hit_tokens, cache_miss_tokens, completion_tokens)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO usage_log (ts, model, purpose, chat_id, project_id, "
            "prompt_tokens, cache_hit_tokens, cache_miss_tokens, completion_tokens, "
            "latency_ms, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                model,
                purpose,
                chat_id,
                project_id,
                prompt_tokens,
                cache_hit_tokens,
                cache_miss_tokens,
                completion_tokens,
                latency_ms,
                usd,
            ),
        )


def totals() -> dict[str, Any]:
    """{"by_chat": {chat_id: usd}, "by_project": {project_id: usd}, "total": usd}."""
    with _connect() as conn:
        by_chat = {
            r["chat_id"]: r["usd"]
            for r in conn.execute(
                "SELECT chat_id, SUM(cost_usd) AS usd FROM usage_log "
                "WHERE chat_id IS NOT NULL AND chat_id != '' GROUP BY chat_id"
            )
        }
        by_project = {
            r["project_id"]: r["usd"]
            for r in conn.execute(
                "SELECT project_id, SUM(cost_usd) AS usd FROM usage_log "
                "WHERE project_id IS NOT NULL AND project_id != '' GROUP BY project_id"
            )
        }
        total = conn.execute("SELECT SUM(cost_usd) AS usd FROM usage_log").fetchone()["usd"] or 0.0
    return {"by_chat": by_chat, "by_project": by_project, "total": total}


def stats(model: str | None = None, since: float | None = None, until: float | None = None) -> dict[str, Any]:
    """Cost/tokeni/latență agregate per model, filtrabile pe interval de timp
    (`since`/`until` = unix timestamp) și pe un model anume."""
    query = (
        "SELECT model, COUNT(*) AS calls, "
        "SUM(prompt_tokens) AS prompt_tokens, "
        "SUM(cache_hit_tokens) AS cache_hit_tokens, "
        "SUM(cache_miss_tokens) AS cache_miss_tokens, "
        "SUM(completion_tokens) AS completion_tokens, "
        "SUM(cost_usd) AS cost_usd, "
        "AVG(latency_ms) AS avg_latency_ms "
        "FROM usage_log WHERE 1=1"
    )
    params: list[Any] = []
    if model:
        query += " AND model = ?"
        params.append(model)
    if since is not None:
        query += " AND ts >= ?"
        params.append(since)
    if until is not None:
        query += " AND ts <= ?"
        params.append(until)
    query += " GROUP BY model ORDER BY cost_usd DESC"
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(query, params)]
    return {
        "rows": rows,
        "total_cost_usd": sum(r["cost_usd"] for r in rows),
        "total_calls": sum(r["calls"] for r in rows),
    }


init_db()
