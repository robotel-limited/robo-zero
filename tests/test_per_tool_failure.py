"""Extras #4 — plafon failure per-tool (cumulat pe sesiune) + max_iterations per task_type.

Fără rețea și fără LLM: tool-urile sunt stub-uri, iar bucla cu task_type
rulează cu `llm.chat` înlocuit local (monkeypatch). Data/projects.db nu e atins.

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_per_tool_failure.py -q
"""

import asyncio
import json

import pytest

import agent
import llm
from tools.tool_base import Tool, ToolResult


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


def _make_agent(tools_messages: dict[str, list[str]], task_type: str | None = None) -> agent.Agent:
    """Agent minim, cu tool-urile reale înlocuite de stub-uri (fără execuție reală)."""

    async def confirm(tool_name: str, detail: str) -> bool:
        return True

    ag = agent.Agent(confirm=confirm, task_type=task_type)
    ag.tools = {name: _StubTool(ag, msgs) for name, msgs in tools_messages.items()}
    return ag


def _call(ag: agent.Agent, name: str, args: dict) -> str:
    """Un apel de tool, prin `_execute_tool_call`, întorcând mesajul final."""
    message, _break_loop = asyncio.run(ag._execute_tool_call(name, json.dumps(args)))
    return message


def test_sase_esecuri_cumulate_pe_un_tool_opresc_bucla():
    """(a) 6 eșecuri pe același tool, NU consecutive (cu apeluri alt tool printre
    ele) -> AgentLoopAborted cu detector='per_tool_failure'. Chiar dacă în
    ferestra de 12 sunt sub 4 eșecuri (detectorul global failure nu trage),
    contorul cumulat pe sesiune ajunge la pragul de 6."""
    assert agent._AGENT_CFG["per_tool_failure_abort"] == 6

    ag = _make_agent(
        {
            "web_read": ["Eroare: HTTP 401 Unauthorized"] * 6,
            # 4 apeluri reușite între eșecuri: fereastra globală de 12 vede
            # cel mult 3 eșecuri web_read, sub pragul de 4 al detectorului failure
            "filler": [f"OK {i}" for i in range(40)],
        }
    )

    with pytest.raises(agent.AgentLoopAborted) as excinfo:
        for i in range(6):
            _call(ag, "web_read", {"u": f"https://exemplu/{i}"})
            for j in range(4):
                _call(ag, "filler", {"i": f"{i}-{j}"})

    err = excinfo.value
    assert err.detector == "per_tool_failure"
    assert err.tool == "web_read"
    assert err.count == 6
    assert "web_read" in str(err)
    # contorul e per tool: filler (40 apeluri reușite) nu are niciun eșec
    assert ag._loop_guard._tool_failure_counts == {"web_read": 6}


def test_esecuri_impartite_pe_doua_tooluri_nu_opresc_bucla():
    """(b) același număr total de eșecuri (6), distribuite pe două tool-uri
    (3 + 3) -> niciun abort: sub pragul per-tool de 6 pentru fiecare."""
    ag = _make_agent(
        {
            "alpha": ["Eroare: alpha unu", "Eroare: alpha doi", "Eroare: alpha trei"],
            "beta": ["Eroare: beta unu", "Eroare: beta doi", "Eroare: beta trei"],
        }
    )

    for i in range(3):
        _call(ag, "alpha", {"i": str(i)})
        _call(ag, "beta", {"i": str(i)})

    assert ag._loop_guard._tool_failure_counts == {"alpha": 3, "beta": 3}


def test_task_type_research_opreste_bucla_la_30_iteratii(monkeypatch):
    """(c) task_type='research' -> plafonul din agent.task_types (30), nu cel
    global (50); bucla chiar se oprește după exact 30 de iterații."""
    ag = _make_agent({"stub": [f"OK {i}" for i in range(60)]}, task_type="research")
    assert ag.task_type == "research"
    assert agent._AGENT_CFG["max_iterations"] == 50

    calls = {"n": 0}

    async def fake_chat(history, tools=None, model=None, tool_choice=None, **kwargs):
        calls["n"] += 1
        i = calls["n"]
        return llm.LlmResponse(
            content=None,
            tool_calls=[llm.ToolCall(id=f"c{i}", name="stub", arguments=json.dumps({"i": i}))],
        )

    monkeypatch.setattr(llm, "chat", fake_chat)

    with pytest.raises(agent.AgentError) as excinfo:
        asyncio.run(ag._run("fă treaba", []))

    assert calls["n"] == 30
    assert "limita de 30 iterații" in str(excinfo.value)
    assert "50" not in str(excinfo.value)


def test_fara_task_type_ramane_plafonul_global():
    """task_type None = comportament identic cu înainte: fallback la
    agent.max_iterations (50) din conf/settings.yaml."""
    ag = _make_agent({"stub": ["OK"]})
    assert ag.task_type is None

    task_max = (
        agent._AGENT_CFG.get("task_types", {})
        .get(ag.task_type or "", {})
        .get("max_iterations", agent._AGENT_CFG["max_iterations"])
    )
    assert task_max == 50
