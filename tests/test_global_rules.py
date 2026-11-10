"""Etapa 3 — reguli globale: list_global, propose_global, suggest_globals,
injecție bloc global în project_context._rules. Izolat pe tmp_path."""
import pytest
import project_rules_store

@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    project_rules_store.init_db()


_T1 = "Tool-ul 'flaky' a întors erori repetat — schimbă sursa sau oprește-te."
_T2 = "Tool-ul 'flaky' a intors erori repetate — schimba sursa sau opreste-te."  # near-dup T1
_T3 = "Trimite facturile în maximum 5 zile lucrătoare de la emitere."


def _approved_in(project_id: str, text: str) -> int:
    rid = project_rules_store.add_rule(project_id, text, "buclă")
    project_rules_store.approve(rid)
    return rid


def test_propose_global_creeaza_cu_scope_global():
    rid = project_rules_store.propose_global(_T1, "buclă", "proj-A")
    r = project_rules_store.get_rule(rid)
    assert r["scope"] == "global"
    assert r["status"] == "pending"


def test_propose_global_dedup_near_match():
    """A doua propunere near-dup nu creează rând nou."""
    rid1 = project_rules_store.propose_global(_T1, "buclă", "proj-A")
    rid2 = project_rules_store.propose_global(_T2, "buclă", "proj-B")
    assert rid1 == rid2
    assert len(project_rules_store.list_global(status="pending")) == 1


def test_list_global_filtreaza_pe_status():
    rid = project_rules_store.propose_global(_T1, "buclă", "proj-A")
    assert len(project_rules_store.list_global(status="pending")) == 1
    assert len(project_rules_store.list_global(status="approved")) == 0
    project_rules_store.approve(rid)
    assert len(project_rules_store.list_global(status="approved")) == 1
    assert len(project_rules_store.list_global(status="pending")) == 0


def test_suggest_globals_N_proiecte_distincte(monkeypatch):
    """≥N proiecte cu aceeași regulă aprobată → 1 pending global propus."""
    N = int(project_rules_store._RULES_CFG.get("global_min_projects", 3))
    for i in range(N):
        _approved_in(f"proj-{i}", _T1)
    proposed = project_rules_store.suggest_globals()
    assert len(proposed) == 1
    r = project_rules_store.get_rule(proposed[0])
    assert r["scope"] == "global"
    assert r["status"] == "pending"


def test_suggest_globals_sub_N_proiecte_nu_propune():
    """< N proiecte → niciun global propus."""
    N = int(project_rules_store._RULES_CFG.get("global_min_projects", 3))
    for i in range(N - 1):
        _approved_in(f"proj-{i}", _T1)
    assert project_rules_store.suggest_globals() == []


def test_suggest_globals_nu_dubleaza_global_existent():
    """Dacă există deja un global near-match, suggest_globals nu mai propune altul."""
    N = int(project_rules_store._RULES_CFG.get("global_min_projects", 3))
    for i in range(N):
        _approved_in(f"proj-{i}", _T1)
    first = project_rules_store.suggest_globals()
    assert len(first) == 1
    second = project_rules_store.suggest_globals()
    # a doua rulare: același id returnat (existent), nu rând nou
    assert second == first
    assert len(project_rules_store.list_global(status="pending")) == 1


def test_aprobare_global_devine_approved():
    rid = project_rules_store.propose_global(_T1, "buclă", "proj-A")
    project_rules_store.approve(rid)
    r = project_rules_store.get_rule(rid)
    assert r["scope"] == "global"
    assert r["status"] == "approved"
    assert len(project_rules_store.list_global(status="approved")) == 1


def test_rules_context_include_bloc_global(monkeypatch, tmp_path):
    """_rules() emite blocul global înainte de cele per-proiect."""
    import project_context
    # adaugă o regulă globală aprobată
    gid = project_rules_store.propose_global("Regula globala test", "global", "proj-X")
    project_rules_store.approve(gid)
    # adaugă o regulă per-proiect aprobată
    lid = project_rules_store.add_rule("proj-local", "Regula locala test", "local")
    project_rules_store.approve(lid)

    result = project_context._rules("proj-local")
    assert "Reguli globale" in result
    assert "Regula globala test" in result
    assert "Regula locala test" in result
    # globalele apar înaintea celor locale
    assert result.index("Regula globala test") < result.index("Regula locala test")
