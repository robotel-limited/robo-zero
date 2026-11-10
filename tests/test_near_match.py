"""Etapa 1 — infrastructura de near-match: find_near_match, add_or_merge,
sightings/times_seen. Scrierile sunt redirectate într-un SQLite efemer din
tmp_path; data/projects.db de producție nu e atins.

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_near_match.py -q
"""

import difflib

import pytest

import project_rules_store

@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    project_rules_store.init_db()


_PID = "proj-near"

# două enunțuri near-dupe reale (diferă prin diacritice, punctuație finală și
# un singular/plural) + unul clar diferit
_T1 = ("Tool-ul 'flaky' a întors erori repetat. Nu relua același apel cu "
       "aceleași argumente — schimbă sursa sau oprește-te și raportează blocajul")
_T2 = ("Tool-ul 'flaky' a întors erori repetate. Nu relua acelasi apel cu "
       "aceleasi argumente, schimba sursa sau opreste-te si raporteaza blocajul.")
_T3 = "Trimite facturile către client în maximum 5 zile lucrătoare de la emitere."


def test_find_near_match_prinde_near_du_reale():
    """Două reguli apropiate (ratio >= 0.85) se găsesc una pe alta."""
    ratio = difflib.SequenceMatcher(
        None,
        project_rules_store._normalize(_T1),
        project_rules_store._normalize(_T2),
    ).ratio()
    assert ratio >= 0.85

    rid = project_rules_store.add_rule(_PID, _T1, "buclă")
    match = project_rules_store.find_near_match(_PID, _T2)
    assert match is not None
    assert match["id"] == rid
    # pragul din config (0.85), citit cu .get
    assert project_rules_store._RULES_CFG.get("near_match_threshold", 0.85) == 0.85


def test_find_near_match_nu_prinde_reguli_clar_diferite():
    project_rules_store.add_rule(_PID, _T1, "buclă")
    assert project_rules_store.find_near_match(_PID, _T3) is None


def test_find_near_match_statusuri_si_threshold_explicit():
    rid = project_rules_store.add_rule(_PID, _T1, "buclă")
    # implicit: orice status
    assert project_rules_store.find_near_match(_PID, _T2)["id"] == rid
    project_rules_store.reject(rid)
    assert project_rules_store.find_near_match(_PID, _T2) is not None
    assert project_rules_store.find_near_match(_PID, _T2, statuses=("pending",)) is None
    # threshold explicit peste raportul real -> niciun match
    assert project_rules_store.find_near_match(_PID, _T2, threshold=0.99) is None


def test_config_vechi_fara_bloc_rules(monkeypatch):
    """Un settings.yaml vechi (fără blocul rules) merge cu pragul implicit."""
    monkeypatch.setattr(project_rules_store, "_RULES_CFG", {})
    rid = project_rules_store.add_rule(_PID, _T1, "buclă")
    match = project_rules_store.find_near_match(_PID, _T2)
    assert match is not None and match["id"] == rid


def test_add_or_merge_pe_near_match_nu_creeaza_rand_nou():
    """(b) near-match: fără rând nou, dar times_seen crescut + sighting nouă."""
    rid1, creat1 = project_rules_store.add_or_merge(_PID, _T1, "buclă", "chat-A", "Chat A")
    assert creat1 is True

    rid2, creat2 = project_rules_store.add_or_merge(_PID, _T2, "buclă", "chat-B", "Chat B")
    assert creat2 is False
    assert rid2 == rid1
    assert len(project_rules_store.list_for(_PID)) == 1

    regula = project_rules_store.get_rule(rid1)
    assert regula["times_seen"] == 2
    assert project_rules_store.distinct_chats(rid1) == 2


def test_a_doua_sighting_din_acelasi_chat_nu_creste_distinct_chats():
    """(c) același source_chat_id a doua oară: PK (rule_id, chat) nu dublează."""
    rid, creat = project_rules_store.add_or_merge(_PID, _T1, "buclă", "chat-A", "Chat A")
    assert creat is True
    project_rules_store.add_or_merge(_PID, _T2, "buclă", "chat-A", "Chat A")

    assert project_rules_store.distinct_chats(rid) == 1
    assert project_rules_store.get_rule(rid)["times_seen"] == 1
    assert len(project_rules_store.list_for(_PID)) == 1

    # un al treilea chat diferit -> o sighting distinctă în plus
    project_rules_store.add_or_merge(_PID, _T2, "buclă", "chat-B", "Chat B")
    assert project_rules_store.distinct_chats(rid) == 2
    assert project_rules_store.get_rule(rid)["times_seen"] == 2
    assert len(project_rules_store.list_for(_PID)) == 1


def test_add_or_merge_cu_supersedes_ocoloseste_comasarea():
    """Un supersede explicit rămâne pe creație (înlocuire, nu dublare)."""
    vechi = project_rules_store.add_rule(_PID, _T1, "Cod", "chat-A")
    project_rules_store.approve(vechi)

    rid, creat = project_rules_store.add_or_merge(
        _PID, _T2, "Cod", "chat-B", "Chat B", supersedes=vechi
    )
    assert creat is True
    assert rid != vechi
    assert project_rules_store.get_rule(rid)["supersedes"] == vechi
    assert len(project_rules_store.list_for(_PID)) == 2


def test_init_db_idempotent_cu_coloanele_noi():
    project_rules_store.init_db()
    project_rules_store.init_db()
    with project_rules_store._connect() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(project_rules)")}
    assert {"scope", "times_seen", "last_seen_at", "decided_by"} <= cols

    rid = project_rules_store.add_rule(_PID, _T1, "buclă")
    regula = project_rules_store.get_rule(rid)
    assert regula["scope"] == "project"
    assert regula["times_seen"] == 1
    assert regula["decided_by"] is None
    assert regula["last_seen_at"] == regula["created_at"]
    # fără sighting-uri direct prin add_rule
    assert project_rules_store.distinct_chats(rid) == 0
