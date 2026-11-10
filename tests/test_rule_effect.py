"""Etapa G1 — efectul regulilor: expuneri/recurențe (rule_effect_store),
injectarea din project_context._rules și secțiunea «Efectul regulilor» din
weekly_report. DB izolat în tmp_path (tiparul din test_sweep_lifecycle),
data/projects.db real nu e atins.

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_rule_effect.py -q
"""
import importlib.util
from pathlib import Path

import pytest

import project_context
import project_rules_store
import rule_effect_store

REPO = Path(__file__).resolve().parent.parent


def _load_weekly_report():
    spec = importlib.util.spec_from_file_location(
        "weekly_report_g1", REPO / "scripts" / "weekly_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wr = _load_weekly_report()


@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    project_rules_store.init_db()
    rule_effect_store.init_db()


_PID = "proj-effect"


def _aproba(rule_id: int) -> None:
    with project_rules_store._connect() as conn:
        conn.execute("UPDATE project_rules SET status='approved' WHERE id=?", (rule_id,))


def _add(text: str = "Regula de probă.", status: str | None = None) -> int:
    rid = project_rules_store.add_rule(_PID, text, "buclă")
    if status == "approved":
        _aproba(rid)
    elif status == "rejected":
        project_rules_store.reject(rid)
    return rid


def _exposures(rule_id: int) -> list[dict]:
    with project_rules_store._connect() as conn:
        rows = conn.execute(
            "SELECT chat_id, exposed_at FROM rule_exposures WHERE rule_id = ? "
            "ORDER BY chat_id",
            (rule_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _expose_back(rule_id: int, chat_id: str | None = None, seconds: float = 5.0) -> None:
    """Mută expunerile mai în trecut — teste deterministe pentru „după expunere”."""
    sql = "UPDATE rule_exposures SET exposed_at = exposed_at - ? WHERE rule_id = ?"
    params: list = [seconds, rule_id]
    if chat_id is not None:
        sql += " AND chat_id = ?"
        params.append(chat_id)
    with project_rules_store._connect() as conn:
        conn.execute(sql, params)


def _projects_table() -> None:
    with project_rules_store._connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, title TEXT)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO projects (id, title) VALUES (?, ?)",
            (_PID, "Proiect efect"),
        )


def test_record_exposures_idempotent():
    rid = _add()
    rule_effect_store.record_exposures([rid], "chat-A")
    rule_effect_store.record_exposures([rid], "chat-A")
    assert len(_exposures(rid)) == 1
    assert rule_effect_store.rule_effect(rid)["exposures"] == 1


def test_record_exposures_noop_fara_chat_sau_lista():
    rid = _add()
    rule_effect_store.record_exposures([rid], None)
    rule_effect_store.record_exposures([rid], "")
    rule_effect_store.record_exposures([], "chat-A")
    assert _exposures(rid) == []


def test_prevention_rate_none_fara_expuneri():
    rid = _add()
    eff = rule_effect_store.rule_effect(rid)
    assert eff == {"exposures": 0, "recurrences": 0, "prevention_rate": None}


def test_recurrence_numarata_doar_dupa_expunere_si_din_chat_expus():
    rid = _add()
    # sighting în ACEL chat dar ÎNAINTE de expunere -> nu se numără
    project_rules_store.record_sighting(rid, "chat-A")
    rule_effect_store.record_exposures([rid], "chat-A")
    # sighting dintr-un chat neexpus -> nu se numără
    project_rules_store.record_sighting(rid, "chat-B")
    # sighting DUPĂ expunere, în chat expus -> se numără
    rule_effect_store.record_exposures([rid], "chat-A2")
    _expose_back(rid, "chat-A2")
    project_rules_store.record_sighting(rid, "chat-A2")

    eff = rule_effect_store.rule_effect(rid)
    assert eff["exposures"] == 2
    assert eff["recurrences"] == 1
    assert eff["prevention_rate"] == pytest.approx(1 - 1 / 2)


def test_effect_summary_respecta_min_exposures_si_ordinea():
    r_eficient = _add("Regula eficienta.")
    r_slab = _add("Regula ineficienta.")
    r_rara = _add("Regula rara.")
    for chat in ("c1", "c2", "c3"):
        rule_effect_store.record_exposures([r_eficient, r_slab], chat)
    rule_effect_store.record_exposures([r_rara], "c1")
    _expose_back(r_slab)
    project_rules_store.record_sighting(r_slab, "c1")
    project_rules_store.record_sighting(r_slab, "c2")

    rows = rule_effect_store.effect_summary()  # min_exposures=3
    assert [r["id"] for r in rows] == [r_slab, r_eficient]  # cel mai slab primul
    assert set(rows[0]) == {
        "id", "project_id", "scope", "status", "text",
        "exposures", "recurrences", "prevention_rate",
    }
    assert rows[0]["project_id"] == _PID
    assert rows[0]["scope"] == "project" and rows[0]["status"] == "pending"
    assert rows[0]["exposures"] == 3 and rows[0]["recurrences"] == 2
    assert rows[0]["prevention_rate"] == pytest.approx(1 - 2 / 3)
    assert rows[1]["recurrences"] == 0 and rows[1]["prevention_rate"] == 1.0
    assert r_rara not in [r["id"] for r in rows]

    rows1 = rule_effect_store.effect_summary(min_exposures=1)
    assert [r["id"] for r in rows1] == [r_slab, r_eficient, r_rara]


def test_rules_inregistreaza_exact_regulile_injectate():
    g = _add("Regula globala.")
    with project_rules_store._connect() as conn:
        conn.execute(
            "UPDATE project_rules SET scope='global', status='approved' WHERE id=?",
            (g,),
        )
    aproba = _add("Regula aprobata.", "approved")
    pending = _add("Regula pending.")
    respinsa = _add("Regula respinsa.", "rejected")

    out = project_context._rules(_PID, "chat-exp")
    assert "Regula globala." in out and "Regula aprobata." in out
    assert "Regula pending." in out and "Regula respinsa." not in out

    with project_rules_store._connect() as conn:
        rows = conn.execute(
            "SELECT rule_id, chat_id FROM rule_exposures ORDER BY rule_id"
        ).fetchall()
    assert {(int(r["rule_id"]), r["chat_id"]) for r in rows} == {
        (g, "chat-exp"), (aproba, "chat-exp"), (pending, "chat-exp"),
    }


def test_rules_nu_crapa_daca_record_exposures_arunca(monkeypatch):
    rid = _add("Regula din context.")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("record_exposures căzut")

    monkeypatch.setattr(project_context.rule_effect_store, "record_exposures", _boom)
    out = project_context._rules(_PID, "chat-1")  # nu aruncă
    assert "Regula din context." in out
    assert _exposures(rid) == []  # nu s-a înregistrat nimic


def test_weekly_report_efectul_regulilor():
    r_eficient = _add("Regula care a mers bine.")
    r_slab = _add("Regula care nu a prevenit nimic.")
    r_rara = _add("Regula cu o singura expunere.")
    for chat in ("w1", "w2", "w3"):
        rule_effect_store.record_exposures([r_eficient, r_slab], chat)
    rule_effect_store.record_exposures([r_rara], "w1")
    _expose_back(r_slab)
    for chat in ("w1", "w2", "w3"):
        project_rules_store.record_sighting(r_slab, chat)
    _projects_table()

    report = wr.generate_report(project_rules_store._DB_PATH, 7)
    assert "Efectul regulilor:" in report
    assert f"[{r_slab}] rată prevenire 0%" in report
    assert f"[{r_eficient}] rată prevenire 100%" in report
    assert "expusă în 3 chat-uri, reapărută de 3 ori" in report
    assert "⚠ ineficientă" in report
    assert f"[{r_rara}]" not in report  # sub pragul de 3 expuneri
    # cele mai slabe întâi
    assert report.index(f"[{r_slab}]") < report.index(f"[{r_eficient}]")


def test_weekly_report_efectul_regulilor_faradata():
    _projects_table()
    report = wr.generate_report(project_rules_store._DB_PATH, 7)
    assert "Efectul regulilor:" in report
    assert "încă prea puține date (minim 3 expuneri/regulă)" in report
