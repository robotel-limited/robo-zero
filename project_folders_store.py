"""Folderele de pe disc legate de un proiect: cale absolută, mod (ro/rw),
notă, dacă se indexează în căutare, plus harta compactă memorată (arbore +
README) și starea indexării. Persistat în projects.db, izolat pe project_id."""

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent
_DB_PATH = _PROJECT_ROOT / "data" / "projects.db"
# datele aplicației (chat-uri, memorie, KB) nu se leagă de un proiect
_FORBIDDEN_ROOTS = [str(_PROJECT_ROOT / "data")]
_MODES = {"ro", "rw"}
_EDITABLE = {"mode", "note", "indexable"}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_folders (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                path TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'ro',
                note TEXT NOT NULL DEFAULT '',
                indexable INTEGER NOT NULL DEFAULT 0,
                map_sig TEXT NOT NULL DEFAULT '',
                map_text TEXT NOT NULL DEFAULT '',
                index_status TEXT NOT NULL DEFAULT '',  -- '' / indexing / ready / error
                index_error TEXT NOT NULL DEFAULT '',
                indexed_files INTEGER NOT NULL DEFAULT 0,
                indexed_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL DEFAULT 0,  -- mod/notă/indexare schimbate
                UNIQUE (project_id, path)
            )
            """
        )
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(project_folders)")}
        if "updated_at" not in columns:  # tabel creat înainte de coloană
            conn.execute("ALTER TABLE project_folders ADD COLUMN updated_at REAL NOT NULL DEFAULT 0")


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    folder = dict(row)
    folder["indexable"] = bool(folder["indexable"])
    return folder


def _validate_path(path: str) -> str:
    path = (path or "").strip()
    if not path or not os.path.isabs(path):
        raise ValueError("Calea trebuie să fie absolută (ex. /srv/proiect).")
    resolved = os.path.realpath(path)
    if not os.path.isdir(resolved):
        raise ValueError(f"Directorul „{path}” nu există.")
    if resolved == "/":
        raise ValueError("Rădăcina sistemului nu poate fi legată de un proiect.")
    for root in _FORBIDDEN_ROOTS:
        if os.path.commonpath([root, resolved]) == root:
            raise ValueError("Datele interne ale a0-light nu pot fi legate de un proiect.")
    return resolved


def add(project_id: str, path: str, mode: str = "ro", note: str = "", indexable: bool = False) -> dict[str, Any]:
    resolved = _validate_path(path)
    if mode not in _MODES:
        raise ValueError("Modul trebuie să fie ro sau rw.")
    folder_id = uuid.uuid4().hex[:12]
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO project_folders (id, project_id, path, mode, note, indexable, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (folder_id, project_id, resolved, mode, note.strip(), int(bool(indexable)), time.time()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Folderul „{resolved}” e deja legat de acest proiect.")
    return get(folder_id)  # type: ignore[return-value]


def get(folder_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        return _row(conn.execute("SELECT * FROM project_folders WHERE id = ?", (folder_id,)).fetchone())


def list_for(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_folders WHERE project_id = ? ORDER BY path", (project_id,)
        ).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def added_since(project_id: str, ts: float) -> list[dict[str, Any]]:
    return [f for f in list_for(project_id) if f["created_at"] > ts]


def changed_since(project_id: str, ts: float) -> list[dict[str, Any]]:
    """Folderele existente deja la `ts` cărora li s-a schimbat modul, nota
    sau indexarea după `ts` — un chat deschis le are cu eticheta veche."""
    return [f for f in list_for(project_id) if f["created_at"] <= ts < f["updated_at"]]


def update(folder_id: str, **fields: Any) -> dict[str, Any] | None:
    updates = {k: v for k, v in fields.items() if k in _EDITABLE and v is not None}
    if "mode" in updates and updates["mode"] not in _MODES:
        raise ValueError("Modul trebuie să fie ro sau rw.")
    if "indexable" in updates:
        updates["indexable"] = int(bool(updates["indexable"]))
    if "note" in updates:
        updates["note"] = str(updates["note"]).strip()
    if updates:
        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with _connect() as conn:
            conn.execute(f"UPDATE project_folders SET {set_clause} WHERE id = ?", (*updates.values(), folder_id))
    return get(folder_id)


def set_map(folder_id: str, sig: str, text: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE project_folders SET map_sig = ?, map_text = ? WHERE id = ?", (sig, text, folder_id))


def set_index_state(folder_id: str, status: str, error: str = "", files: int | None = None) -> None:
    with _connect() as conn:
        if files is None:
            conn.execute(
                "UPDATE project_folders SET index_status = ?, index_error = ? WHERE id = ?",
                (status, error[:500], folder_id),
            )
        else:
            conn.execute(
                "UPDATE project_folders SET index_status = ?, index_error = ?, indexed_files = ?, "
                "indexed_at = ? WHERE id = ?",
                (status, error[:500], files, time.time(), folder_id),
            )


def delete(folder_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_folders WHERE id = ?", (folder_id,))


def delete_project(project_id: str) -> list[str]:
    """Șterge folderele proiectului; întoarce id-urile lor (pentru indexul FTS)."""
    ids = [f["id"] for f in list_for(project_id)]
    with _connect() as conn:
        conn.execute("DELETE FROM project_folders WHERE project_id = ?", (project_id,))
    return ids


init_db()
