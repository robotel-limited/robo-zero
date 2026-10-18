"""Baza de cunoștințe a unui proiect: fișiere încărcate de utilizator (text,
PDF, DOCX, imagini) și textul extras din ele, împărțit în bucăți indexate
FTS5 (fără diacritice). Fișierele originale stau în data/project_kb/<proiect>/.
Strict izolat pe project_id, ca restul memoriei de proiect."""

import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent
_DB_PATH = _PROJECT_ROOT / "data" / "projects.db"
KB_DIR = _PROJECT_ROOT / "data" / "project_kb"
KB_DIR.mkdir(parents=True, exist_ok=True)

_CHUNK_CHARS = 2000
_SUMMARY_CHARS = 200


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_files (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'processing',  -- processing / ready / error
                error TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                ready_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks USING fts5(
                content,
                project_id UNINDEXED,
                file_id UNINDEXED,
                chunk_no UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )


def _safe_name(filename: str) -> str:
    name = Path(filename).name.strip() or "fisier"
    return re.sub(r"[^\w.\- ]", "_", name)[:120]


def file_path(file: dict[str, Any]) -> Path:
    return KB_DIR / file["project_id"] / f"{file['id']}__{_safe_name(file['filename'])}"


def add_file(project_id: str, filename: str, mime: str, data: bytes) -> dict[str, Any]:
    file = {
        "id": uuid.uuid4().hex[:12],
        "project_id": project_id,
        "filename": Path(filename).name.strip() or "fisier",
        "mime": mime,
        "size": len(data),
        "created_at": time.time(),
    }
    path = file_path(file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kb_files (id, project_id, filename, mime, size, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (file["id"], project_id, file["filename"], mime, file["size"], file["created_at"]),
        )
    return get_file(file["id"])


_LIST_COLUMNS = "id, project_id, filename, mime, size, status, error, summary, created_at, ready_at, length(text) AS chars"


def get_file(file_id: str, with_text: bool = False) -> dict[str, Any] | None:
    columns = f"{_LIST_COLUMNS}, text" if with_text else _LIST_COLUMNS
    with _connect() as conn:
        row = conn.execute(f"SELECT {columns} FROM kb_files WHERE id = ?", (file_id,)).fetchone()
    return dict(row) if row else None


def list_files(project_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM kb_files WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def files_since(project_id: str, ts: float) -> list[dict[str, Any]]:
    """Fișierele devenite disponibile (status ready) după `ts` — pentru
    actualizarea unui chat deja deschis."""
    return [f for f in list_files(project_id) if f["status"] == "ready" and f["ready_at"] > ts]


def _chunks(text: str) -> list[str]:
    chunks: list[str] = [""]
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > _CHUNK_CHARS:  # paragraf uriaș (ex. PDF fără rânduri goale)
            chunks += [para[:_CHUNK_CHARS], ""]
            para = para[_CHUNK_CHARS:]
        if chunks[-1] and len(chunks[-1]) + len(para) > _CHUNK_CHARS:
            chunks.append("")
        chunks[-1] = f"{chunks[-1]}\n\n{para}" if chunks[-1] else para
    return [c for c in chunks if c]


def set_ready(file_id: str, project_id: str, text: str) -> None:
    summary = re.sub(r"\s+", " ", text).strip()[:_SUMMARY_CHARS]
    with _connect() as conn:
        conn.execute(
            "UPDATE kb_files SET status = 'ready', error = '', text = ?, summary = ?, ready_at = ? WHERE id = ?",
            (text, summary, time.time(), file_id),
        )
        conn.execute("DELETE FROM kb_chunks WHERE file_id = ?", (file_id,))
        conn.executemany(
            "INSERT INTO kb_chunks (content, project_id, file_id, chunk_no) VALUES (?, ?, ?, ?)",
            [(c, project_id, file_id, i) for i, c in enumerate(_chunks(text))],
        )


def set_error(file_id: str, error: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE kb_files SET status = 'error', error = ? WHERE id = ?", (error[:500], file_id))


def delete_file(file_id: str) -> None:
    file = get_file(file_id)
    if file is None:
        return
    file_path(file).unlink(missing_ok=True)
    with _connect() as conn:
        conn.execute("DELETE FROM kb_chunks WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM kb_files WHERE id = ?", (file_id,))


def delete_project(project_id: str) -> None:
    shutil.rmtree(KB_DIR / project_id, ignore_errors=True)
    with _connect() as conn:
        conn.execute("DELETE FROM kb_chunks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM kb_files WHERE project_id = ?", (project_id,))


def search(project_id: str, fts_query: str, limit: int = 8) -> list[dict[str, Any]]:
    if not fts_query:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT kb_chunks.file_id, f.filename, f.created_at AS ts, "
            "snippet(kb_chunks, 0, '«', '»', '…', 40) AS snippet "
            "FROM kb_chunks JOIN kb_files f ON f.id = kb_chunks.file_id "
            "WHERE kb_chunks MATCH ? AND kb_chunks.project_id = ? "
            "ORDER BY bm25(kb_chunks) LIMIT ?",
            (fts_query, project_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


init_db()
