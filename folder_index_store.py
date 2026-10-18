"""Indexul FTS5 al folderelor de proiect marcate „indexabil”: ce fișiere
sunt indexate (cale relativă, mtime, mărime — pentru reindexare
incrementală) și bucățile lor de text (fără diacritice). Separat de
kb_chunks: fișierele trăiesc pe disc, nu sunt copiate în data/."""

import sqlite3
from pathlib import Path
from typing import Any

from project_kb_store import chunk_text

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
            CREATE TABLE IF NOT EXISTS folder_files (
                folder_id TEXT NOT NULL,
                relpath TEXT NOT NULL,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL,
                PRIMARY KEY (folder_id, relpath)
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS folder_chunks USING fts5(
                content,
                project_id UNINDEXED,
                folder_id UNINDEXED,
                relpath UNINDEXED,
                chunk_no UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )


def known(folder_id: str) -> dict[str, tuple[float, int]]:
    with _connect() as conn:
        rows = conn.execute("SELECT relpath, mtime, size FROM folder_files WHERE folder_id = ?", (folder_id,))
        return {r["relpath"]: (r["mtime"], r["size"]) for r in rows}


def upsert(project_id: str, folder_id: str, relpath: str, mtime: float, size: int, text: str) -> None:
    """Înlocuiește bucățile unui fișier. Text gol (PDF scanat, binar) =
    fișier memorat ca văzut, fără bucăți — nu se reîncearcă până nu se schimbă."""
    with _connect() as conn:
        conn.execute("DELETE FROM folder_chunks WHERE folder_id = ? AND relpath = ?", (folder_id, relpath))
        conn.execute(
            "INSERT OR REPLACE INTO folder_files (folder_id, relpath, mtime, size) VALUES (?, ?, ?, ?)",
            (folder_id, relpath, mtime, size),
        )
        conn.executemany(
            "INSERT INTO folder_chunks (content, project_id, folder_id, relpath, chunk_no) VALUES (?, ?, ?, ?, ?)",
            [(c, project_id, folder_id, relpath, i) for i, c in enumerate(chunk_text(text))],
        )


def remove(folder_id: str, relpaths: list[str]) -> None:
    with _connect() as conn:
        for relpath in relpaths:
            conn.execute("DELETE FROM folder_chunks WHERE folder_id = ? AND relpath = ?", (folder_id, relpath))
            conn.execute("DELETE FROM folder_files WHERE folder_id = ? AND relpath = ?", (folder_id, relpath))


def count_indexed(folder_id: str) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(DISTINCT relpath) FROM folder_chunks WHERE folder_id = ?", (folder_id,)
        ).fetchone()[0]


def delete_folder(folder_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM folder_chunks WHERE folder_id = ?", (folder_id,))
        conn.execute("DELETE FROM folder_files WHERE folder_id = ?", (folder_id,))


def search(project_id: str, fts_query: str, limit: int = 8) -> list[dict[str, Any]]:
    if not fts_query:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT pf.path AS folder, folder_chunks.relpath, "
            "snippet(folder_chunks, 0, '«', '»', '…', 40) AS snippet "
            "FROM folder_chunks JOIN project_folders pf ON pf.id = folder_chunks.folder_id "
            "WHERE folder_chunks MATCH ? AND folder_chunks.project_id = ? AND pf.indexable = 1 "
            "ORDER BY bm25(folder_chunks) LIMIT ?",
            (fts_query, project_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


init_db()
