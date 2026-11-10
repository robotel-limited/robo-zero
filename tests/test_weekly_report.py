"""Extras #6 — raport săptămânal: CLI (subprocess) + post_to_discord (mock)."""
import importlib.util
import sqlite3
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "weekly_report.py"

_SCHEMA = """
CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT);
CREATE TABLE project_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    category TEXT DEFAULT '',
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at REAL,
    decided_by TEXT,
    scope TEXT DEFAULT 'per-project'
);
"""


def _load_weekly_report():
    spec = importlib.util.spec_from_file_location("weekly_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wr = _load_weekly_report()


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "projects.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return db


def _run(db, output, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--days", "7",
         "--db", str(db), "--output", str(output), *extra],
        capture_output=True, text=True, cwd=str(REPO), timeout=60,
    )


def test_report_empty(tmp_db, tmp_path):
    out = tmp_path / "report.txt"
    proc = _run(tmp_db, out)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "Sumar: 0 aprobate, 0 respinse" in out.read_text(encoding="utf-8")


def test_report_with_rules(tmp_db, tmp_path):
    now = time.time()
    conn = sqlite3.connect(tmp_db)
    conn.executemany(
        "INSERT INTO projects (id, title) VALUES (?, ?)",
        [("proj-aaaa", "Proiect Alpha"), ("proj-bbbb", "Proiect Beta")],
    )
    conn.executemany(
        "INSERT INTO project_rules (project_id, category, text, status, decided_at, decided_by)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("proj-aaaa", "buclă", "Regula aprobată 1", "approved", now - 86400, "Sorin"),
            ("proj-aaaa", "buclă", "Regula aprobată 2", "approved", now - 86400, "Sorin"),
            ("proj-bbbb", "răspuns", "Regula respinsă", "rejected", now - 86400, "auto"),
        ],
    )
    conn.commit()
    conn.close()

    out = tmp_path / "report.txt"
    proc = _run(tmp_db, out)
    assert proc.returncode == 0, proc.stderr
    content = out.read_text(encoding="utf-8")
    for needle in (
        "Proiect Alpha", "Proiect Beta", "buclă", "răspuns",
        "decided_by: Sorin", "decided_by: auto",
        "Sumar: 2 aprobate, 1 respinse",
    ):
        assert needle in content, needle


def test_discord_post_called():
    with mock.patch("urllib.request.urlopen") as m:
        ok = wr.post_to_discord("http://fake-webhook.test/abc", "salut raport")
    assert ok is True
    assert m.call_count == 1
    req = m.call_args[0][0]
    assert req.full_url == "http://fake-webhook.test/abc"
    assert b'"content"' in req.data
    assert b"salut raport" in req.data


def test_discord_failure_no_exit(tmp_db, tmp_path, capsys):
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
        result = wr.post_to_discord("http://nonexistent.invalid/", "test")
    assert result is False
    assert "WARNING: Discord POST eșuat" in capsys.readouterr().err

    out = tmp_path / "report.txt"
    proc = _run(tmp_db, out, "--discord-webhook", "http://127.0.0.1:9/abc")
    assert proc.returncode == 0, proc.stderr
