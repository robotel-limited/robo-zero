"""Etapa 1 — scripts/cleanup_near_dupes.py pe un DB TEMPORAR din tmp_path
(niciodată data/projects.db de producție): backup table-copy înainte de orice
ștergere, clusterizare pending near-dupe, mutat sightings + max(times_seen) pe
cea mai nouă/lungă regulă, raport.

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_cleanup_near_dupes.py -q
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest

import project_rules_store

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "cleanup_near_dupes.py"

@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    project_rules_store.init_db()


_PID = "proj-cleanup"

_T1 = ("Tool-ul 'flaky' a întors erori repetat. Nu relua același apel cu "
       "aceleași argumente — schimbă sursa sau oprește-te și raportează blocajul")
_T2 = ("Tool-ul 'flaky' a întors erori repetate. Nu relua acelasi apel cu "
       "aceleasi argumente, schimba sursa sau opreste-te si raporteaza blocajul.")
_T3 = "Trimite facturile către client în maximum 5 zile lucrătoare de la emitere."


def _run(db: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--db", str(db)],
        capture_output=True,
        text=True,
        cwd=_REPO,
    )


def _toate_riduri() -> set[int]:
    with project_rules_store._connect() as conn:
        return {row[0] for row in conn.execute("SELECT id FROM project_rules")}


def test_cleanup_comaseaza_pending_si_muta_sightings(tmp_path):
    # două near-dupe pending create direct (add_rule, nu add_or_merge, ca să
    # existe chiar dublura pe care scriptul o are de comasat)
    r1 = project_rules_store.add_rule(_PID, _T1, "buclă")
    r2 = project_rules_store.add_rule(_PID, _T2, "buclă")
    project_rules_store.record_sighting(r1, "chat-A")
    project_rules_store.record_sighting(r2, "chat-B")
    # o regulă clar diferită (pending) + aprobată și respinsă apropiate de ele:
    # niciunea nu trebuie atinsă (clusterizăm doar pending)
    other = project_rules_store.add_rule(_PID, _T3, "Cod")
    aprobat = project_rules_store.add_rule(_PID, _T1 + ".", "Cod")
    project_rules_store.approve(aprobat)
    respins = project_rules_store.add_rule(_PID, _T2 + ".", "Cod")
    project_rules_store.reject(respins)

    cu_linii_riduri = _toate_riduri()
    assert len(cu_linii_riduri) == 5

    # cea mai nouă/lungă din cluster, calculată înainte de ștergere
    pastrata = max(
        (project_rules_store.get_rule(r1), project_rules_store.get_rule(r2)),
        key=lambda r: (r["created_at"] or 0.0, len(r["text"]), r["id"]),
    )

    db = tmp_path / "projects.db"
    proc = _run(db)
    assert proc.returncode == 0, proc.stderr

    # backup table-copy ÎNAINTE de ștergeri, cu tot ce era acolo
    backup = f"project_rules_bak_{time.strftime('%Y%m%d')}"
    with project_rules_store._connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (backup,)
        ).fetchone()
        assert conn.execute(f"SELECT COUNT(*) FROM {backup}").fetchone()[0] == 5
    assert f"backup: {backup} (5 reguli)" in proc.stdout

    # clusterul de 2 -> o singură regulă păstrată (cea mai nouă/lungă)
    ramase = project_rules_store.list_for(_PID)
    assert {r["id"] for r in ramase} == {pastrata["id"], other, aprobat, respins}
    assert project_rules_store.get_rule(aprobat)["status"] == "approved"
    assert project_rules_store.get_rule(respins)["status"] == "rejected"

    # sightings mutate pe păstrată + max(times_seen)
    assert project_rules_store.distinct_chats(pastrata["id"]) == 2
    assert project_rules_store.get_rule(pastrata["id"])["times_seen"] == 2
    with project_rules_store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM rule_sightings").fetchone()[0] == 2

    # raport: o comasare
    assert "cluster de 2" in proc.stdout
    assert "comasate 1" in proc.stdout
    assert "TOTAL: 1 near-dupe comasate în 1 cluster(e)" in proc.stdout

    # a doua rulare: nimic nou, backup-ul inițial nu e suprascris
    proc2 = _run(db)
    assert proc2.returncode == 0, proc2.stderr
    assert "TOTAL: 0 near-dupe comasate în 0 cluster(e)" in proc2.stdout
    with project_rules_store._connect() as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {backup}").fetchone()[0] == 5


def test_cleanup_fara_tabel_nu_scoate_eroare(tmp_path):
    alt = tmp_path / "alt.db"
    proc = _run(alt)
    assert proc.returncode == 0, proc.stderr
    assert "fără tabel project_rules" in proc.stdout
