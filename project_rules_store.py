"""Regulile învățate ale unui proiect: corecții ale lui Sorin asupra
comportamentului agentului, extrase automat dar aplicate în context doar
după aprobarea lui pe cardul proiectului. Același SQLite ca
project_memory_store.py (data/projects.db)."""

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
            CREATE TABLE IF NOT EXISTS project_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                supersedes INTEGER,
                superseded_by INTEGER,
                source_chat_id TEXT,
                source_chat_title TEXT,
                created_at REAL NOT NULL,
                decided_at REAL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_project ON project_rules(project_id, status)")


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def add_rule(
    project_id: str,
    text: str,
    category: str = "",
    source_chat_id: str | None = None,
    source_chat_title: str | None = None,
    supersedes: int | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO project_rules (project_id, category, text, supersedes, "
            "source_chat_id, source_chat_title, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, category, text, supersedes, source_chat_id, source_chat_title, time.time()),
        )
        return int(cur.lastrowid)


def get_rule(rule_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        return _row(conn.execute("SELECT * FROM project_rules WHERE id = ?", (rule_id,)).fetchone())


def list_for(project_id: str, status: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM project_rules WHERE project_id = ? AND status = ? "
                "ORDER BY category, created_at",
                (project_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM project_rules WHERE project_id = ? ORDER BY category, created_at",
                (project_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def approved_rules_since(project_id: str, ts: float) -> list[dict[str, Any]]:
    """Regulile aprobate după `ts`, cu textul regulii pe care au înlocuit-o
    (dacă e cazul) — baza pentru diferențele trimise unui chat deja deschis."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT r.*, old.text AS replaces_text FROM project_rules r "
            "LEFT JOIN project_rules old ON old.superseded_by = r.id "
            "WHERE r.project_id = ? AND r.status = 'approved' AND r.decided_at > ? "
            "ORDER BY r.decided_at",
            (project_id, ts),
        ).fetchall()
    return [dict(r) for r in rows]


def update_text(rule_id: int, text: str | None = None, category: str | None = None) -> dict[str, Any] | None:
    updates: dict[str, Any] = {}
    if text is not None:
        updates["text"] = str(text).strip()
    if category is not None:
        updates["category"] = str(category).strip()
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with _connect() as conn:
            conn.execute(f"UPDATE project_rules SET {set_clause} WHERE id = ?", (*updates.values(), rule_id))
    return get_rule(rule_id)


def approve(rule_id: int) -> dict[str, Any] | None:
    rule = get_rule(rule_id)
    if rule is None or rule["status"] != "pending":
        return None
    now = time.time()
    with _connect() as conn:
        conn.execute("UPDATE project_rules SET status = 'approved', decided_at = ? WHERE id = ?", (now, rule_id))
        if rule["supersedes"] is not None:
            old = get_rule(rule["supersedes"])
            if old is not None and old["status"] == "approved":
                conn.execute(
                    "UPDATE project_rules SET status = 'superseded', superseded_by = ?, decided_at = ? "
                    "WHERE id = ?",
                    (rule_id, now, old["id"]),
                )
    return get_rule(rule_id)


def reject(rule_id: int) -> dict[str, Any] | None:
    rule = get_rule(rule_id)
    if rule is None or rule["status"] != "pending":
        return None
    with _connect() as conn:
        conn.execute("UPDATE project_rules SET status = 'rejected', decided_at = ? WHERE id = ?", (time.time(), rule_id))
    return get_rule(rule_id)


def delete_project(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_rules WHERE project_id = ?", (project_id,))


init_db()
