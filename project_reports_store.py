"""Fișierele generate de agent în workdir-ul unui proiect (rapoarte, orice
fișier scris prin fs_write sau shell care a primit un link de rețea),
centralizate pe cardul proiectului în loc să rămână împrăștiate prin
chat-urile lui. Persistat în projects.db, izolat pe project_id. Scrierea
repetată a aceluiași fișier (editare/append) actualizează intrarea, nu
duplică."""

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent
_DB_PATH = _PROJECT_ROOT / "data" / "projects.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_reports (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chat_id TEXT,
                path TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (project_id, path)
            )
            """
        )


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def record(project_id: str, chat_id: str | None, path: str) -> None:
    """Upsert: dacă fișierul a mai fost raportat pentru acest proiect,
    doar actualizează chat_id și updated_at (nu duplică)."""
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO project_reports (id, project_id, chat_id, path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (project_id, path) DO UPDATE SET chat_id = excluded.chat_id, updated_at = excluded.updated_at
            """,
            (uuid.uuid4().hex[:12], project_id, chat_id, path, now, now),
        )


def list_for(project_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_reports WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)
        ).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def unlist(report_id: str) -> None:
    """Scoate intrarea din listă — fișierul de pe disc rămâne neschimbat."""
    with _connect() as conn:
        conn.execute("DELETE FROM project_reports WHERE id = ?", (report_id,))


def delete_project(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_reports WHERE project_id = ?", (project_id,))


init_db()
