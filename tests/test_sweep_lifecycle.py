"""Etapa 2 — sweep_lifecycle: auto-promovare (distinct_chats >= K) și expirare
(last_seen_at vechi & times_seen==1). Izolat pe tmp_path."""
import time
import pytest
import project_rules_store

@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    project_rules_store.init_db()

_PID = "proj-sweep"

def _add(text="Regula test", chat="chat-A"):
    rid, _ = project_rules_store.add_or_merge(_PID, text, "buclă", chat, "Chat")
    return rid

def _sight(rid, chat):
    project_rules_store.record_sighting(rid, chat)


def test_promote_la_K_sightings():
    """Pending cu K sightings distincte -> approved, decided_by='auto'."""
    K = int(project_rules_store._RULES_CFG.get("promote_distinct_chats", 3))
    rid = _add("Regula A", "chat-1")
    for i in range(2, K + 1):
        _sight(rid, f"chat-{i}")
    result = project_rules_store.sweep_lifecycle(_PID)
    assert rid in result["approved"]
    r = project_rules_store.get_rule(rid)
    assert r["status"] == "approved"
    assert r["decided_by"] == "auto"


def test_sub_K_nu_se_promoveaza():
    """La K-1 sightings distincte, rămâne pending."""
    K = int(project_rules_store._RULES_CFG.get("promote_distinct_chats", 3))
    rid = _add("Regula B", "chat-1")
    for i in range(2, K):  # K-1 total
        _sight(rid, f"chat-{i}")
    result = project_rules_store.sweep_lifecycle(_PID)
    assert rid not in result["approved"]
    assert project_rules_store.get_rule(rid)["status"] == "pending"


def test_expirare_la_M_zile_times_seen_1():
    """Pending cu last_seen_at vechi (>M zile) și times_seen==1 -> rejected."""
    M = int(project_rules_store._RULES_CFG.get("expire_pending_days", 30))
    rid = _add("Regula veche", "chat-X")
    old_ts = time.time() - (M + 1) * 86400
    with project_rules_store._connect() as conn:
        conn.execute("UPDATE project_rules SET last_seen_at = ? WHERE id = ?", (old_ts, rid))
    result = project_rules_store.sweep_lifecycle(_PID)
    assert rid in result["expired"]
    r = project_rules_store.get_rule(rid)
    assert r["status"] == "rejected"
    assert r["decided_by"] == "auto"


def test_nu_expira_times_seen_mai_mare():
    """times_seen >= 2 nu se expiră chiar dacă e veche."""
    M = int(project_rules_store._RULES_CFG.get("expire_pending_days", 30))
    rid = _add("Regula recurentă", "chat-1")
    _sight(rid, "chat-2")  # times_seen = 2
    old_ts = time.time() - (M + 1) * 86400
    with project_rules_store._connect() as conn:
        conn.execute("UPDATE project_rules SET last_seen_at = ? WHERE id = ?", (old_ts, rid))
    result = project_rules_store.sweep_lifecycle(_PID)
    assert rid not in result["expired"]
    assert project_rules_store.get_rule(rid)["status"] == "pending"


def test_approved_nu_se_expira():
    """O regulă approved nu e atinsă de sweep."""
    rid = _add("Regula aprobată", "chat-1")
    project_rules_store.approve(rid)
    old_ts = time.time() - 999 * 86400
    with project_rules_store._connect() as conn:
        conn.execute("UPDATE project_rules SET last_seen_at = ? WHERE id = ?", (old_ts, rid))
    result = project_rules_store.sweep_lifecycle(_PID)
    assert rid not in result["expired"]
    assert project_rules_store.get_rule(rid)["status"] == "approved"


def test_sweep_dict_raporteaza_corect():
    """Dicționarul conține id-urile corecte pentru aprobate și expirate."""
    K = int(project_rules_store._RULES_CFG.get("promote_distinct_chats", 3))
    M = int(project_rules_store._RULES_CFG.get("expire_pending_days", 30))
    r_prom = _add("Regula prom", "chat-1")
    for i in range(2, K + 1):
        _sight(r_prom, f"chat-{i}")
    r_exp = _add("Regula exp unica", "chat-Z")
    old_ts = time.time() - (M + 1) * 86400
    with project_rules_store._connect() as conn:
        conn.execute("UPDATE project_rules SET last_seen_at = ? WHERE id = ?", (old_ts, r_exp))
    r_ok = _add("Regula normala", "chat-N")
    result = project_rules_store.sweep_lifecycle(_PID)
    assert result == {"approved": [r_prom], "expired": [r_exp]}
    assert project_rules_store.get_rule(r_ok)["status"] == "pending"
