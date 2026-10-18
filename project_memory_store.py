"""Memoria unui proiect: fișa (fapte cu proveniență, înlocuite — niciodată
șterse individual) și rezumatul per chat cu cursorul de procesare în log.
Același SQLite ca projects_store.py (data/projects.db)."""

import sqlite3
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent
_DB_PATH = _PROJECT_ROOT / "data" / "projects.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                event_date TEXT,
                source_chat_id TEXT,
                source_chat_title TEXT,
                created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by INTEGER,
                superseded_at REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_project ON project_facts(project_id, status)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_digests (
                chat_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chat_title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                log_cursor INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
            """
        )


# --- fapte -------------------------------------------------------------------


def add_fact(
    project_id: str,
    text: str,
    category: str = "",
    event_date: str | None = None,
    source_chat_id: str | None = None,
    source_chat_title: str | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO project_facts (project_id, category, text, event_date, "
            "source_chat_id, source_chat_title, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, category, text, event_date, source_chat_id, source_chat_title, time.time()),
        )
        return int(cur.lastrowid)


def supersede_fact(
    project_id: str,
    fact_id: int,
    text: str,
    category: str | None = None,
    event_date: str | None = None,
    source_chat_id: str | None = None,
    source_chat_title: str | None = None,
) -> int | None:
    """Înlocuiește un fapt activ al proiectului cu unul nou. Întoarce id-ul
    noului fapt, sau None dacă fact_id nu e un fapt activ al acestui proiect
    (ex. LLM-ul a inventat un id) — apelantul decide ce face atunci."""
    old = get_fact(fact_id)
    if old is None or old["project_id"] != project_id or old["status"] != "active":
        return None
    new_id = add_fact(
        project_id,
        text,
        category if category is not None else old["category"],
        event_date if event_date is not None else old["event_date"],
        source_chat_id,
        source_chat_title,
    )
    with _connect() as conn:
        conn.execute(
            "UPDATE project_facts SET status = 'superseded', superseded_by = ?, "
            "superseded_at = ? WHERE id = ?",
            (new_id, time.time(), fact_id),
        )
    return new_id


def get_fact(fact_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM project_facts WHERE id = ?", (fact_id,)).fetchone()
    return dict(row) if row else None


def active_facts(project_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_facts WHERE project_id = ? AND status = 'active' "
            "ORDER BY category, created_at",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def superseded_facts(project_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_facts WHERE project_id = ? AND status = 'superseded' "
            "ORDER BY superseded_at DESC",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def active_facts_since(project_id: str, ts: float) -> list[dict[str, Any]]:
    """Faptele active create după `ts`, fiecare cu textul faptului pe care
    l-a înlocuit (dacă e cazul) — baza pentru diferențele trimise unui chat
    deja deschis (decizia 2C)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT f.*, old.text AS replaces_text, old.created_at AS replaces_created_at "
            "FROM project_facts f LEFT JOIN project_facts old ON old.superseded_by = f.id "
            "WHERE f.project_id = ? AND f.status = 'active' AND f.created_at > ? "
            "ORDER BY f.created_at",
            (project_id, ts),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_change_at(project_id: str) -> float:
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) FROM project_facts WHERE project_id = ?", (project_id,)
        ).fetchone()
    return float(row[0] or 0.0)


# --- rezumate per chat ---------------------------------------------------------


def get_digest(chat_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM chat_digests WHERE chat_id = ?", (chat_id,)).fetchone()
    return dict(row) if row else None


def save_digest(chat_id: str, project_id: str, chat_title: str, summary: str, log_cursor: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_digests (chat_id, project_id, chat_title, summary, log_cursor, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
            "project_id = excluded.project_id, chat_title = excluded.chat_title, "
            "summary = excluded.summary, log_cursor = excluded.log_cursor, "
            "updated_at = excluded.updated_at",
            (chat_id, project_id, chat_title, summary, log_cursor, time.time()),
        )


def list_digests(project_id: str, exclude_chat_id: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_digests WHERE project_id = ? AND chat_id != ? "
            "AND summary != '' ORDER BY updated_at DESC",
            (project_id, exclude_chat_id or ""),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_digest(chat_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM chat_digests WHERE chat_id = ?", (chat_id,))


def delete_project(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_facts WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM chat_digests WHERE project_id = ?", (project_id,))


init_db()
