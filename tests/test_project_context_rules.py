"""Etapa B2 — regulile 'pending' se aplică imediat în contextul proiectului,
sub un header separat „neverificate", pe lângă cele 'approved'. Sorin doar le
respinge; până atunci agentul le vede marcate ca provizorii.

Scrierile în project_rules sunt redirectate într-un SQLite efemer din tmp_path;
data/projects.db de producție nu e atinsă.

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_project_context_rules.py -q
"""

import pytest

import project_rules_store
import project_context


@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    project_rules_store.init_db()


_PID = "proj-b2"


def _aproba(rule_id: int) -> None:
    with project_rules_store._connect() as conn:
        conn.execute(
            "UPDATE project_rules SET status='approved' WHERE id=?", (rule_id,)
        )


def test_pending_apare_sub_header_neverificate():
    rid = project_rules_store.add_rule(_PID, "Regulă candidată de probă.", "buclă")
    out = project_context._rules(_PID)
    assert "## Reguli candidate (neverificate)" in out
    assert "Regulă candidată de probă." in out
    # nu pretinde fals că e aprobată
    assert "## Reguli învățate (aprobate de Sorin)" not in out


def test_approved_si_pending_in_sectiuni_distincte():
    aid = project_rules_store.add_rule(_PID, "Regulă aprobată.", "Cod")
    _aproba(aid)
    project_rules_store.add_rule(_PID, "Regulă neverificată.", "Cod")
    out = project_context._rules(_PID)
    assert "## Reguli învățate (aprobate de Sorin)" in out
    assert "## Reguli candidate (neverificate)" in out
    # ordinea: aprobate înaintea candidatelor
    assert out.index("aprobate de Sorin") < out.index("neverificate")
    assert "Regulă aprobată." in out and "Regulă neverificată." in out


def test_rejected_nu_apare():
    rid = project_rules_store.add_rule(_PID, "Regulă respinsă.", "buclă")
    project_rules_store.reject(rid)
    out = project_context._rules(_PID)
    assert "Regulă respinsă." not in out
    assert out == ""


def test_fara_reguli_sir_gol():
    assert project_context._rules(_PID) == ""
