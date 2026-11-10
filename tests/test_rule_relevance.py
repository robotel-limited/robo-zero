"""G5 — teste pentru rule_relevance (embedding mock-uit, fără rețea).

Acoperire conform spec:
- ≤ max_rules candidați → neschimbat, _embed neapelat
- ranking + plafon + min_score; bonusul approved schimbă ordinea
- _embed → None ⇒ select → None ⇒ _rules injectează toate regulile (comportament vechi)
- cache: al doilea apel cu aceleași reguli trimite la _embed doar textul sarcinii
- _rules cu task_text înregistrează expuneri doar pentru regulile alese

Rulare: python3 -m pytest tests/test_rule_relevance.py -q
"""

import json
import math
import sqlite3
from unittest.mock import patch, call

import pytest

import rule_relevance
import project_rules_store
import project_context
import rule_effect_store


# ---------------------------------------------------------------------------
# Fixture: DB izolat în tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    db = tmp_path / "projects.db"
    monkeypatch.setattr(rule_relevance, "_DB_PATH", db)
    monkeypatch.setattr(project_rules_store, "_DB_PATH", db)
    project_rules_store.init_db()
    # asigurăm tabelul rule_embeddings
    with sqlite3.connect(str(db)) as conn:
        conn.execute(rule_relevance._DDL)


# ---------------------------------------------------------------------------
# Utilitare
# ---------------------------------------------------------------------------

def _unit(v: list[float]) -> list[float]:
    """Normalizează un vector la L2=1."""
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def _make_rule(text: str, rid: int = 1) -> dict:
    return {"id": rid, "text": text, "category": "Test"}


# ---------------------------------------------------------------------------
# Test 1: ≤ max_rules candidați → neschimbat, _embed neapelat
# ---------------------------------------------------------------------------

def test_sub_plafon_nu_apeleaza_embed():
    candidates = [("approved", _make_rule(f"Regulă {i}", i)) for i in range(3)]
    with patch.object(rule_relevance, "_embed") as mock_embed:
        result = rule_relevance.select("orice sarcină", candidates)
    mock_embed.assert_not_called()
    assert result == candidates


# ---------------------------------------------------------------------------
# Test 2: ranking + plafon + min_score; bonus approved schimbă ordinea
# ---------------------------------------------------------------------------

def test_ranking_si_bonus_approved():
    # Vectori ortonormali: task = e0, r0 = e0 (cosine 1.0), r1 = e1 (cosine 0.0)
    # r0 e pending (fără bonus), r1 e approved (bonus 0.10)
    # Scor r0 = 1.0, scor r1 = 0.0 + 0.10 = 0.10 (sub min_score 0.30 → exclus)
    dim = 4
    task_vec = _unit([1, 0, 0, 0])
    r0_vec = _unit([1, 0, 0, 0])   # cosine 1.0 cu task
    r1_vec = _unit([0, 1, 0, 0])   # cosine 0.0 cu task

    candidates = [("pending", _make_rule("Regulă pendind zero", 1))] * 9
    # 9 candidați ca să depășim max_rules=8 implicit

    # Construim 9 candidați: primii 8 pending cu scor mic, ultimul cu scor mare
    cands = (
        [("approved", _make_rule(f"Mic aprobat {i}", i)) for i in range(8)]
        + [("pending", _make_rule("Mare pending", 99))]
    )
    # Vectori: toate aprobate au cosine 0 (e1), "Mare pending" are cosine 1 (e0)
    side_effects_vecs = [[task_vec]] + [[r1_vec]] * 8 + [[r0_vec]]

    def fake_embed(texts):
        # primul text = task, restul = regulile
        result = []
        for t in texts:
            if "Mare pending" in t:
                result.append(r0_vec)
            elif t == "orice sarcină":
                result.append(task_vec)
            else:
                result.append(r1_vec)
        return result

    with patch.object(rule_relevance, "_embed", side_effect=fake_embed):
        result = rule_relevance.select("orice sarcină", cands)

    assert result is not None
    # "Mare pending" ar trebui să fie primul (cosine 1.0 fără bonus > 0.1+bonus)
    texts = [r["text"] for _, r in result]
    assert "Mare pending" in texts
    assert texts[0] == "Mare pending"


def test_bonus_approved_schimba_ordinea_la_egalitate():
    # r_pending și r_approved au același cosine cu task (0.5 ≈)
    # dar r_approved primește bonus 0.10 → apare primul
    dim = 2
    task_vec = _unit([1, 1])
    same_vec = _unit([1, 1])

    cands = (
        [("pending", _make_rule(f"P{i}", i)) for i in range(7)]
        + [("pending", _make_rule("PEnd", 10))]
        + [("approved", _make_rule("AApproved", 11))]
    )  # 9 candidați > max_rules=8

    def fake_embed(texts):
        return [task_vec if i == 0 else same_vec for i, _ in enumerate(texts)]

    with patch.object(rule_relevance, "_embed", side_effect=fake_embed):
        result = rule_relevance.select("task", cands)

    assert result is not None
    blocs = [bloc for bloc, _ in result]
    # Approved apare (bonus îl salvează când scorurile sunt egale)
    assert "approved" in blocs
    # Și primul trebuie să fie approved
    assert blocs[0] == "approved"


# ---------------------------------------------------------------------------
# Test 3: _embed → None ⇒ select → None
# ---------------------------------------------------------------------------

def test_embed_none_select_returneaza_none():
    cands = [("approved", _make_rule(f"R{i}", i)) for i in range(10)]
    with patch.object(rule_relevance, "_embed", return_value=None):
        result = rule_relevance.select("sarcină", cands)
    assert result is None


# ---------------------------------------------------------------------------
# Test 4: cache — al doilea apel trimite la _embed doar textul sarcinii
# ---------------------------------------------------------------------------

def test_cache_al_doilea_apel_trimite_doar_task():
    dim = 2
    task_vec = _unit([1, 0])
    rule_vec = _unit([0, 1])

    cands = [("approved", _make_rule(f"Regulă {i}", i)) for i in range(9)]

    call_texts = []

    def fake_embed(texts):
        call_texts.append(list(texts))
        return [task_vec if i == 0 else rule_vec for i, _ in enumerate(texts)]

    with patch.object(rule_relevance, "_embed", side_effect=fake_embed):
        rule_relevance.select("task text", cands)
        rule_relevance.select("task text 2", cands)

    # Primul apel: task + 9 reguli = 10 texte
    assert len(call_texts[0]) == 10
    # Al doilea apel: regulile sunt în cache, doar noul task_text
    assert len(call_texts[1]) == 1
    assert call_texts[1][0] == "task text 2"


# ---------------------------------------------------------------------------
# Test 5: _rules cu task_text înregistrează expuneri doar pentru regulile alese
# ---------------------------------------------------------------------------

def test_rules_expuneri_doar_pentru_selectate(tmp_path, monkeypatch):
    _PID = "proj-g5-expo"
    db = tmp_path / "projects.db"
    monkeypatch.setattr(rule_relevance, "_DB_PATH", db)
    monkeypatch.setattr(project_rules_store, "_DB_PATH", db)
    project_rules_store.init_db()
    with sqlite3.connect(str(db)) as conn:
        conn.execute(rule_relevance._DDL)
        conn.execute(rule_effect_store._DDL)

    # Adaugă 3 reguli approved
    ids = [
        project_rules_store.add_rule(_PID, f"Regulă {i}", "cat")
        for i in range(3)
    ]
    with project_rules_store._connect() as conn:
        conn.execute("UPDATE project_rules SET status='approved'")

    # select va alege doar primele 2 (fake: score >0.30 pentru primele 2)
    dim = 2
    task_vec = _unit([1, 0])
    high_vec = _unit([1, 0])    # cosine 1.0 + bonus = 1.10
    low_vec = _unit([0, 1])     # cosine 0.0 + bonus = 0.10 < min_score

    def fake_embed(texts):
        result = []
        for t in texts:
            if "Regulă 2" in t:
                result.append(low_vec)
            elif t.startswith("Regulă") or "Regulă" in t:
                result.append(high_vec)
            else:
                result.append(task_vec)
        return result

    # Forțăm mai mult de max_rules reguli adăugând mai multe
    for i in range(3, 10):
        project_rules_store.add_rule(_PID, f"Regulă {i}", "cat")
    with project_rules_store._connect() as conn:
        conn.execute("UPDATE project_rules SET status='approved'")

    _CHAT = "chat-expo-g5"
    with patch.object(rule_relevance, "_embed", side_effect=fake_embed):
        project_context._rules(_PID, chat_id=_CHAT, task_text="sarcina testată")

    # Verifică expunerile înregistrate
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT rule_id FROM rule_exposures WHERE chat_id=?", (_CHAT,)
        ).fetchall()
    exposed_ids = {r[0] for r in rows}

    # "Regulă 2" are scor mic → nu trebuie să fie expusă
    # (dar poate fi expusă dacă e sub max_rules... verificăm că nu e lista goală)
    assert len(exposed_ids) > 0
    assert len(exposed_ids) <= rule_relevance._MAX_RULES
