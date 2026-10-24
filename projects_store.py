"""Registrul proiectelor: titlu, descriere, prompt injectat la fiecare chat
legat de proiect. Persistat în SQLite (singurul loc din aplicație care
folosește o bază de date — restul e JSON pe disc), pentru constraint de
unicitate pe titlu fără cod suplimentar."""

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import llm

_PROJECT_ROOT = Path(__file__).resolve().parent
_DB_PATH = _PROJECT_ROOT / "data" / "projects.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_ALLOWED_FIELDS = {"title", "description", "prompt", "model", "voice_only"}
# extracția (project_digest.py, kb_extract.py) rulează mereu pe flash,
# indiferent de modelul ales aici — vezi llm.py
_MODELS = set(llm.MODEL_CATALOG)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT 'deepseek-flash',
                voice_only INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
        if "model" not in columns:  # tabel creat înainte de coloană
            conn.execute(
                "ALTER TABLE projects ADD COLUMN model TEXT NOT NULL DEFAULT 'deepseek-flash'"
            )
        if "voice_only" not in columns:
            # proiecte voice-only (fără UI de approve/deny, ex. robo-audio):
            # niciun tool confirmabil nu poate fi vreodată aprobat prin voce,
            # așa că cererea de confirmare blochează chat-ul până la timeout
            # (incident Liliana/SVAI 2026-10-21) — pentru aceste proiecte
            # agent.py sare peste pre_check/confirm complet (vezi _execute_tool_call).
            conn.execute(
                "ALTER TABLE projects ADD COLUMN voice_only INTEGER NOT NULL DEFAULT 0"
            )


def list_all() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY title").fetchall()
    return [dict(row) for row in rows]


def get(project_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def create(title: str, description: str = "", prompt: str = "") -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValueError("Titlul e vid.")
    project_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO projects (id, title, description, prompt, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, title, description, prompt, now, now),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Există deja un proiect cu titlul „{title}”.")
    return get(project_id)  # type: ignore[return-value]


def update(project_id: str, **fields: Any) -> dict[str, Any] | None:
    updates = {k: v for k, v in fields.items() if k in _ALLOWED_FIELDS and v is not None}
    if "title" in updates:
        updates["title"] = updates["title"].strip()
        if not updates["title"]:
            raise ValueError("Titlul e vid.")
    if "model" in updates and updates["model"] not in _MODELS:
        raise ValueError(f"Model necunoscut: {updates['model']}")
    if not updates:
        return get(project_id)
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with _connect() as conn:
        try:
            cur = conn.execute(
                f"UPDATE projects SET {set_clause} WHERE id = ?",
                (*updates.values(), project_id),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Există deja un proiect cu titlul „{updates.get('title')}”.")
        if cur.rowcount == 0:
            return None
    return get(project_id)


def delete(project_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return cur.rowcount > 0


init_db()
