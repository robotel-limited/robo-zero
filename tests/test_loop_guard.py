"""Teste pentru guard-ul de buclă din agent.py — fără rețea, fără LLM.

Rulează din rădăcina repo-ului: python -m pytest tests/test_loop_guard.py -q
"""

import asyncio
import json

import pytest

import agent
from tools.tool_base import Tool, ToolResult

_INSTRUCTIUNE = (
    "Schimbă abordarea: alt tool, alte argumente, "
    "sau răspunde cu ce ai și spune ce te blochează."
)


class _StubTool(Tool):
    """Tool de test: întoarce, pe rând, mesajele programate la construcție."""

    name = "stub"
    description = "tool fals pentru teste"
    parameters = {"type": "object", "properties": {}}
    required = []

    def __init__(self, agent_obj, messages: list[str]) -> None:
        super().__init__(agent_obj)
        self._messages = list(messages)
        self.calls = 0

    async def execute(self, **kwargs):
        message = self._messages[min(self.calls, len(self._messages) - 1)]
        self.calls += 1
        return ToolResult(message=message)


def _make_agent(tools_messages: dict[str, list[str]]) -> agent.Agent:
    """Agent minim, cu tool-urile reale înlocuite de stub-uri (fără execuție reală)."""

    async def confirm(tool_name: str, detail: str) -> bool:
        return True

    ag = agent.Agent(confirm=confirm)
    ag.tools = {name: _StubTool(ag, msgs) for name, msgs in tools_messages.items()}
    return ag


def _call(ag: agent.Agent, name: str, args: dict) -> str:
    """Un apel de tool, prin `_execute_tool_call`, întorcând mesajul final."""
    message, _break_loop = asyncio.run(ag._execute_tool_call(name, json.dumps(args)))
    return message


def test_ciclu_abab_avertizeaza_la_2():
    """(a) A,B,A,B cu prag de avertizare 2 → avertisment de tip cycle."""
    assert agent._AGENT_CFG["repetition_warn_count"] == 2

    # mesaje diferite, ca detectorul noprogress (nu ăsta e subiectul testului)
    # să nu intervină
    ag = _make_agent({"stub": [f"OK {i}" for i in range(6)]})
    messages = [
        _call(ag, "stub", {"q": "a"}),
        _call(ag, "stub", {"q": "b"}),
        _call(ag, "stub", {"q": "a"}),
        _call(ag, "stub", {"q": "b"}),
    ]

    # primele două apeluri sunt curate, abia al treilea (A reapărut) avertizează
    assert "[Avertisment de buclă" not in messages[0]
    assert "[Avertisment de buclă" not in messages[1]
    assert "[Avertisment de buclă" in messages[2]
    assert "detectorul cycle" in messages[2]
    assert "de 2 ori" in messages[2]
    assert _INSTRUCTIUNE in messages[2]
    # al patrulea apel tot avertizează (B a reapărut), dar nu oprește bucla
    assert "detectorul cycle" in messages[3]
    assert _INSTRUCTIUNE in messages[3]


def test_esecuri_repetate_opresc_bucla():
    """(b) 4 mesaje „Eroare…” de la același tool, cu argumente diferite."""
    events: list[tuple[str, dict]] = []
    ag = _make_agent({"flaky": ["Eroare: fișierul lipsește"] * 6})
    ag.on_event = lambda event_type, data: events.append((event_type, data))

    with pytest.raises(agent.AgentError) as excinfo:  # prins de handlerul existent
        for i in range(4):
            _call(ag, "flaky", {"p": str(i)})

    err = excinfo.value
    assert isinstance(err, agent.AgentLoopAborted)
    assert err.detector == "failure"
    assert err.tool == "flaky"
    assert err.count == 4
    # ultimele intrări (3 anterioare + apelul curent), argumente/rezultate trunchiate
    assert len(err.entries) == 4
    assert err.entries[-1][0] == "flaky"
    assert err.entries[-1][1] == json.dumps({"p": "3"})
    assert err.entries[-1][2] == "Eroare: fișierul lipsește"
    assert all(len(entry[1]) <= 200 and len(entry[2]) <= 200 for entry in err.entries)
    # raportul în română pentru str()
    report = str(err)
    assert "failure" in report
    assert "flaky" in report
    assert "Ultimele apeluri" in report
    assert _INSTRUCTIUNE in report
    # evenimentul loop_aborted a fost emis înainte de ridicare
    aborted = [data for event_type, data in events if event_type == "loop_aborted"]
    assert aborted and aborted[-1]["detector"] == "failure"
    assert aborted[-1]["tool"] == "flaky"
    assert aborted[-1]["count"] == 4


def test_rezultat_identic_repetat_opreste_bucla():
    """(c) același mesaj de 4 ori de la același tool (argumente diferite)."""
    ag = _make_agent({"echo": ["aceeași ieșire de fiecare dată"] * 6})

    with pytest.raises(agent.AgentLoopAborted) as excinfo:
        for i in range(4):
            _call(ag, "echo", {"i": str(i)})

    err = excinfo.value
    assert err.detector == "noprogress"
    assert err.tool == "echo"
    assert err.count == 4
    assert _INSTRUCTIUNE in str(err)


def test_doua_apeluri_reusite_diferite_nu_avertizeaza():
    """(d) două apeluri reușite, diferite → niciun avertisment."""
    ag = _make_agent({"stub": ["primul rezultat bun", "al doilea rezultat bun"]})

    first = _call(ag, "stub", {"q": "unu"})
    second = _call(ag, "stub", {"q": "doi"})

    assert first == "primul rezultat bun"
    assert second == "al doilea rezultat bun"
    assert "[Avertisment de buclă" not in first
    assert "[Avertisment de buclă" not in second


def test_abort_consecutive_cel_initial_mai_functioneaza():
    """(e) 4 apeluri identice consecutive → oprire, ca la comportamentul vechi."""
    # mesaje distincte, ca oprirea să vină din detectorul consecutive, nu din
    # noprogress (care ar vedea 4 rezultate identice)
    ag = _make_agent({"stub": [f"OK {i}" for i in range(6)]})

    with pytest.raises(agent.AgentLoopAborted) as excinfo:
        for _ in range(4):
            _call(ag, "stub", {"q": "același"})

    err = excinfo.value
    assert isinstance(err, agent.AgentError)
    assert err.detector == "consecutive"
    assert err.tool == "stub"
    assert err.count == 4
    assert _INSTRUCTIUNE in str(err)


def test_config_vechi_fara_cheile_noi(monkeypatch):
    """Pragurile noi se citesc cu .get(): un settings.yaml vechi (fără ele) merge."""
    stripped = dict(agent._AGENT_CFG)
    for key in (
        "failure_warn_count",
        "failure_abort_count",
        "noprogress_warn_count",
        "noprogress_abort_count",
    ):
        stripped.pop(key, None)
    monkeypatch.setattr(agent, "_AGENT_CFG", stripped)

    ag = _make_agent({"flaky": ["Eroare: din nou"] * 6})
    with pytest.raises(agent.AgentLoopAborted) as excinfo:
        for i in range(4):
            _call(ag, "flaky", {"i": str(i)})

    assert excinfo.value.detector == "failure"
    assert excinfo.value.count == 4
