"""G4 — reguli candidate din chat-uri reușite (project_digest).

Izolează ambele DB-uri în tmp_path; monkeypatch pe llm.chat (async, fără rețea).
Rulează din rădăcina repo-ului: python3 -m pytest tests/test_success_rules.py -q
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import llm
import project_digest
import project_memory_store
import project_rules_store


# --- helper: sesiune minimală în memorie ---

class _FakeSession:
    """Sesiune minimală care imită interfața folosită de project_digest."""

    def __init__(self, project_id: str, tmp_path: Path) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.title = "Test sesiune G4"
        self.project_id = project_id
        self._file = tmp_path / f"chat_{self.id}.json"
        self._file.write_text("{}")
        self.log: list[dict[str, Any]] = []

    def _path(self) -> Path:
        return self._file

    def _append_log(self, event_type: str, **data: Any) -> None:
        self.log.append({"type": event_type, "ts": time.time(), **data})


def _add_terminal(session: _FakeSession, kind: str) -> None:
    """Adaugă un entry terminal în log (final / error / cancelled)."""
    session.log.append({"type": kind, "ts": time.time(), "text": f"test {kind}"})


def _add_user(session: _FakeSession, text: str = "mesaj utilizator") -> None:
    session.log.append({"type": "user", "ts": time.time(), "text": text})


# --- fixture de izolare ---

@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    monkeypatch.setattr(project_memory_store, "_DB_PATH", tmp_path / "pm.db")
    project_rules_store.init_db()
    project_memory_store.init_db()


# --- helper: fake llm.chat ---

def _make_fake_llm(payload: dict[str, Any]):
    """Întoarce un fake async pentru llm.chat care răspunde cu payload-ul dat."""
    @dataclass
    class _Resp:
        content: str | None

    async def _fake_chat(messages, **kwargs):
        return _Resp(content=json.dumps(payload))

    return _fake_chat


# --- cazurile din spec ---

def test_chat_reusit_creeaza_regula_reusita(tmp_path, monkeypatch):
    """Caz 1: log terminal 'final' → regulă 'reușită' pending creată."""
    session = _FakeSession("p-g4", tmp_path)
    _add_user(session)
    _add_terminal(session, "final")

    payload = {
        "summary": "test",
        "ops": [],
        "rules": [],
        "success_rules": [{"op": "add", "category": "ignorat", "text": "Rulează testele înainte de commit."}],
    }
    monkeypatch.setattr(llm, "chat", _make_fake_llm(payload))

    asyncio.run(project_digest._digest(session, "p-g4"))

    reguli = project_rules_store.list_for("p-g4", status="pending")
    assert len(reguli) == 1
    assert reguli[0]["category"] == "reușită"
    assert reguli[0]["source_chat_id"] == session.id


def test_chat_nereusit_ignora_success_rules(tmp_path, monkeypatch):
    """Caz 2: log terminal 'error' → hard gate în cod, nicio regulă 'reușită'."""
    session = _FakeSession("p-g4", tmp_path)
    _add_user(session)
    _add_terminal(session, "error")

    payload = {
        "summary": "test",
        "ops": [],
        "rules": [],
        "success_rules": [{"op": "add", "text": "Regulă care nu trebuie aplicată."}],
    }
    monkeypatch.setattr(llm, "chat", _make_fake_llm(payload))

    asyncio.run(project_digest._digest(session, "p-g4"))

    reușite = [r for r in project_rules_store.list_for("p-g4") if r["category"] == "reușită"]
    assert reușite == []


def test_dedup_prin_add_or_merge(tmp_path, monkeypatch):
    """Caz 3: două rulări de digest cu același text → o singură regulă."""
    session = _FakeSession("p-g4", tmp_path)
    _add_user(session)
    _add_terminal(session, "final")

    text_fix = "Verifică întotdeauna output-ul înainte de a raporta succesul."
    payload = {
        "summary": "test",
        "ops": [],
        "rules": [],
        "success_rules": [{"op": "add", "text": text_fix}],
    }
    monkeypatch.setattr(llm, "chat", _make_fake_llm(payload))

    asyncio.run(project_digest._digest(session, "p-g4"))
    asyncio.run(project_digest._digest(session, "p-g4"))

    reguli = project_rules_store.list_for("p-g4")
    assert len(reguli) == 1


def test_max_per_chat_respectat(tmp_path, monkeypatch):
    """Caz 4: 3 success_rules distincte, max_per_chat=2 → exact 2 create."""
    session = _FakeSession("p-g4", tmp_path)
    _add_user(session)
    _add_terminal(session, "final")

    payload = {
        "summary": "test",
        "ops": [],
        "rules": [],
        "success_rules": [
            {"op": "add", "text": "Rulează testele automate înainte de orice commit."},
            {"op": "add", "text": "Verifică întotdeauna că serviciul pornește fără erori după deploy."},
            {"op": "add", "text": "Documentează decizia de arhitectură imediat după finalizare."},
        ],
    }
    monkeypatch.setattr(llm, "chat", _make_fake_llm(payload))
    # forțăm max_per_chat=2 (default din config, dar explicit pentru claritate)
    monkeypatch.setitem(project_digest._RULES_CFG, "max_per_chat", 2)
    monkeypatch.setitem(project_digest._RULES_CFG, "enabled", True)

    asyncio.run(project_digest._digest(session, "p-g4"))

    reguli = project_rules_store.list_for("p-g4", status="pending")
    assert len(reguli) == 2


def test_calea_rules_existenta_neatinsa(tmp_path, monkeypatch):
    """Caz 5: rules cu categorie 'stil' + success_rules pe chat reușit →
    regula din rules păstrează 'stil', cea din success_rules are 'reușită'."""
    session = _FakeSession("p-g4", tmp_path)
    _add_user(session)
    _add_terminal(session, "final")

    payload = {
        "summary": "test",
        "ops": [],
        "rules": [{"op": "add", "category": "stil", "text": "Folosește întotdeauna tipuri explicite."}],
        "success_rules": [{"op": "add", "text": "Rulează benchmark după orice refactor major."}],
    }
    monkeypatch.setattr(llm, "chat", _make_fake_llm(payload))

    asyncio.run(project_digest._digest(session, "p-g4"))

    reguli = project_rules_store.list_for("p-g4", status="pending")
    assert len(reguli) == 2
    categorii = {r["category"] for r in reguli}
    assert "stil" in categorii
    assert "reușită" in categorii
