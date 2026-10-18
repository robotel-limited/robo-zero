"""Arhiva brută a unui proiect: textul integral al chat-urilor lui, indexat
FTS5 (fără diacritice), căutat la cerere de LLM prin tools/project_search.py.
Nu se injectează în context — e stratul pentru detalii exacte pe care fișa
și rezumatele le-au pierdut."""

import re
import sqlite3
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent
_DB_PATH = _PROJECT_ROOT / "data" / "projects.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# un rezultat de tool (ex. o pagină web citită) poate fi uriaș — arhiva
# păstrează mult, dar nu nelimitat
_ENTRY_MAX_CHARS = 20000
_INDEXED_TYPES = {"user": "utilizator", "final": "agent", "tool_result": "tool"}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS project_archive USING fts5(
                content,
                project_id UNINDEXED,
                chat_id UNINDEXED,
                chat_title UNINDEXED,
                ts UNINDEXED,
                role UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )


def _entry_text(entry: dict[str, Any]) -> str:
    if entry["type"] == "tool_result":
        return f"{entry.get('name', '')}: {entry.get('message', '')}"
    return str(entry.get("text", ""))


def reindex_chat(chat_id: str, project_id: str, chat_title: str, log: list[dict[str, Any]]) -> None:
    """Rescrie complet rândurile unui chat (simplu și corect și la redenumire;
    chat-urile sunt mici). Apelat la finalul fiecărei rulări."""
    rows = [
        (
            _entry_text(e)[:_ENTRY_MAX_CHARS],
            project_id,
            chat_id,
            chat_title,
            float(e.get("ts", 0.0)),
            _INDEXED_TYPES[e["type"]],
        )
        for e in log
        if e.get("type") in _INDEXED_TYPES and _entry_text(e).strip()
    ]
    with _connect() as conn:
        conn.execute("DELETE FROM project_archive WHERE chat_id = ?", (chat_id,))
        conn.executemany(
            "INSERT INTO project_archive (content, project_id, chat_id, chat_title, ts, role) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def remove_chat(chat_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_archive WHERE chat_id = ?", (chat_id,))


def remove_project(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_archive WHERE project_id = ?", (project_id,))


def _fts_query(query: str) -> str:
    # fiecare termen între ghilimele (fără sintaxă FTS acceptată de la LLM),
    # legați cu OR — clasarea bm25 aduce sus fragmentele cu cei mai mulți termeni
    terms = re.findall(r"\w+", query)
    return " OR ".join(f'"{t}"' for t in terms)


def search(project_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    fts = _fts_query(query)
    if not fts:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT chat_id, chat_title, ts, role, "
            "snippet(project_archive, 0, '«', '»', '…', 40) AS snippet "
            "FROM project_archive WHERE project_archive MATCH ? AND project_id = ? "
            "ORDER BY bm25(project_archive) LIMIT ?",
            (fts, project_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


init_db()
