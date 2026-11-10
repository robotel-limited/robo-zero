"""Etapa C — oprirea de buclă (AgentLoopAborted) salvează automat o regulă
candidată pending în project_rules, doar pentru chat-urile dintr-un proiect.

Fără rețea și fără LLM: metoda de rulare a agentului e înlocuită local, iar
toate scrierile în project_rules sunt redirectate într-un SQLite efemer din
tmp_path — data/projects.db de producție nu e atinsă (la fel workdir-ul, care
prin conf/settings.yaml arată către /srv/robo-zero/workdir).

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_loop_rule_capture.py -q
"""

import asyncio
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Callable

import pytest

import chat_session
import path_access
import project_hooks
import project_rules_store
from agent import AgentLoopAborted

_DETECTOR = "failure"
_TOOL = "flaky"


def _aborted() -> AgentLoopAborted:
    """O oprire de buclă reală (clasa din agent.py), cu câmpurile pe care
    handlerul din chat_session le transformă în textul regulii."""
    return AgentLoopAborted(
        _DETECTOR,
        _TOOL,
        4,
        [(_TOOL, '{"p": "3"}', "Eroare: fișierul lipsește")],
    )


def _toate_regulile() -> list[dict[str, Any]]:
    conn = sqlite3.connect(project_rules_store._DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM project_rules").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    """Izolează testul de orice scriere în afara tmp_path-ului lui."""
    # toate regulile scrise de test ajung într-un SQLite efemer
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    project_rules_store.init_db()
    # conf/settings.yaml arată către workdir-ul de producție
    monkeypatch.setattr(path_access, "BASE_WORKDIR", str(tmp_path / "workdir"))
    # after_run (arhivă + digest de proiect) și notificările nu fac parte din
    # etapa C — le scoatem din ecuație ca testul să atingă doar regulile
    monkeypatch.setattr(project_hooks, "after_run", lambda session: None)
    monkeypatch.setattr(chat_session, "notify_task_ready", lambda *args, **kwargs: None)


@pytest.fixture()
def make_session(_izolat):
    sessions: list[chat_session.ChatSession] = []

    def _make(project_id: str | None = None) -> chat_session.ChatSession:
        session = chat_session.ChatSession(chat_id=uuid.uuid4().hex[:12], project_id=project_id)
        sessions.append(session)
        return session

    yield _make
    for session in sessions:
        Path(session._path()).unlink(missing_ok=True)


@pytest.fixture()
def run_session() -> Callable[..., None]:
    def _run(
        session: chat_session.ChatSession,
        exc: Exception | None = None,
        message: str = "verifică rularea",
    ) -> None:
        async def fake_run(user_message: str, images: list[str]) -> str:
            if exc is not None:
                raise exc
            return "rulare normală"

        session.agent.run = fake_run
        asyncio.run(session._run(message, []))

    return _run


def test_aborted_cu_project_id_creeaza_o_regula_pending(
    make_session, run_session
) -> None:
    session = make_session(project_id="proj-etapa-c")
    session.title = "Ședință de lucru"

    run_session(session, exc=_aborted())

    reguli = project_rules_store.list_for(session.project_id, status="pending")
    assert len(reguli) == 1
    regula = reguli[0]
    assert regula["category"] == "buclă"
    assert regula["status"] == "pending"
    assert regula["source_chat_id"] == session.id
    assert regula["source_chat_title"] == "Ședință de lucru"
    assert regula["text"] == project_rules_store._loop_rule_text(_DETECTOR, _TOOL, 4)
    assert _TOOL in regula["text"]
    assert len(regula["text"]) <= 400
    # eroarea ajunge și în jurnalul chat-ului, ca în ramura AgentError
    assert any(e["type"] == "error" and _DETECTOR in str(e.get("text", "")) for e in session.log)


def test_aborted_fara_project_id_nu_creeaza_nicio_regula(make_session, run_session) -> None:
    session = make_session(project_id=None)

    run_session(session, exc=_aborted())

    assert session.project_id is None
    assert _toate_regulile() == []
    assert any(e["type"] == "error" for e in session.log)


def test_rulare_normala_nu_creeaza_nicio_regula(make_session, run_session) -> None:
    session = make_session(project_id="proj-etapa-c")

    run_session(session)

    assert _toate_regulile() == []
    assert project_rules_store.list_for(session.project_id, status="pending") == []
    assert not any(e["type"] == "error" for e in session.log)


def test_abort_duplicat_nu_creeaza_a_doua_regula(make_session, run_session) -> None:
    """Aceași oprire de buclă de două ori -> aceeași lecție, o singură regulă."""
    session = make_session(project_id="proj-etapa-c")

    run_session(session, exc=_aborted())
    run_session(session, exc=_aborted())

    assert len(project_rules_store.list_for(session.project_id, status="pending")) == 1


def test_regula_respinsa_nu_se_repropune(make_session, run_session) -> None:
    """Dedup-ul privește ORICE status: o lecție respinsă nu e re-propusă."""
    session = make_session(project_id="proj-etapa-c")

    run_session(session, exc=_aborted())
    reguli = project_rules_store.list_for(session.project_id, status="pending")
    assert len(reguli) == 1
    project_rules_store.reject(reguli[0]["id"])

    run_session(session, exc=_aborted())

    toate = project_rules_store.list_for(session.project_id)
    assert len(toate) == 1
    assert toate[0]["status"] == "rejected"
