"""Extras #5 — regulile aprobate se publică în dimag ca lesson (fire-and-forget).

approve() trimite payload-ul la gateway-ul dimag (POST /remember) după commit;
un gateway picat NU blochează aprobarea. Testele NU fac rețea reală: urlopen e
înlocuit cu monkeypatch (spec extras #5).

Sub pytest, `_publish_to_dimag` se auto-scoate (gard pe PYTEST_CURRENT_TEST) ca
celelalte teste care apelează approve() să nu atingă gateway-ul real — testele
de aici bypass-ează gardul ca să verifice efectiv payload-ul și calea de eșec.
Data/projects.db nu e atins (tmp_path).

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_dimag_publish.py -q
"""

import json
import urllib.request

import pytest

import project_rules_store


@pytest.fixture(autouse=True)
def _izolat(tmp_path, monkeypatch):
    monkeypatch.setattr(project_rules_store, "_DB_PATH", tmp_path / "projects.db")
    monkeypatch.setattr(project_rules_store, "_PUBLISHED_RULES", set())
    project_rules_store.init_db()


def _fara_gard_pytest(monkeypatch):
    """Gardul anti-rețea din _publish_to_dimag se oprește sub pytest; testele
    de aici îl scot ca să verifice apelul real (cu urlopen totuși înlocuit)."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


class _RaspunsFals:
    """Context manager fals pentru urlopen (citește corpul răspunsului)."""

    def read(self) -> bytes:
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _adauga(text: str) -> int:
    rid, _ = project_rules_store.add_or_merge("proj-dimag", text, "buclă", "chat-1", "Chat")
    return rid


def test_approve_publica_lectia_in_dimag(monkeypatch):
    """approve() trimite un payload type=lesson / project=robo-zero cu textul regulii."""
    _fara_gard_pytest(monkeypatch)
    text = "Nu relua apelul fara progres nou"
    rid = _adauga(text)
    apeluri: list[tuple] = []

    def fake_urlopen(req, timeout=None):
        apeluri.append((req, timeout))
        return _RaspunsFals()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    regula = project_rules_store.approve(rid)

    assert regula is not None and regula["status"] == "approved"
    assert len(apeluri) == 1
    req, timeout = apeluri[0]
    assert req.full_url.endswith("/remember")
    assert req.get_method() == "POST"
    assert timeout == 5
    payload = json.loads(req.data.decode())
    assert payload["type"] == "lesson"
    assert payload["project"] == "robo-zero"
    assert payload["statement"] == f"[r0 lecție aprobată] {text}"
    assert payload["assumptions"]
    assert all(len(w) >= 4 for w in payload["assumptions"])


def test_gateway_picat_aprobarea_ramane_fara_exceptie(monkeypatch, caplog):
    """Dacă urlopen aruncă OSError, approve() nu ridică și rândul rămâne approved."""
    _fara_gard_pytest(monkeypatch)
    rid = _adauga("Regula cu gateway picat")
    apeluri = []

    def gateway_picat(req, timeout=None):
        apeluri.append(req)
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", gateway_picat)
    with caplog.at_level("WARNING", logger="project_rules_store"):
        regula = project_rules_store.approve(rid)

    assert apeluri, "approve() a încercat totuși publicarea (fire-and-forget)"
    assert regula is not None and regula["status"] == "approved"
    assert project_rules_store.get_rule(rid)["status"] == "approved"
    assert "_publish_to_dimag failed" in caplog.text


def test_sweep_auto_aprobare_publica_o_singura_data(monkeypatch):
    """Drumul auto (sweep_lifecycle) publică exact o dată — fără postare dublă."""
    _fara_gard_pytest(monkeypatch)
    K = int(project_rules_store._RULES_CFG.get("promote_distinct_chats", 3))
    rid = _adauga("Regula auto-aprobata in sweep")
    for i in range(2, K + 1):
        project_rules_store.record_sighting(rid, f"chat-{i}")
    apeluri = []

    def fake_urlopen(req, timeout=None):
        apeluri.append(req)
        return _RaspunsFals()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rezultat = project_rules_store.sweep_lifecycle("proj-dimag")

    assert rid in rezultat["approved"]
    assert project_rules_store.get_rule(rid)["status"] == "approved"
    assert len(apeluri) == 1
    payload = json.loads(apeluri[0].data.decode())
    assert payload["type"] == "lesson"
    assert payload["statement"].startswith("[r0 lecție aprobată]")
