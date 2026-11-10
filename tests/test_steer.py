"""Steer — corectură trimisă de utilizator în timpul unei rulări active:
nu anulează și nu repornește bucla, mesajul e injectat la următoarea iterație
sigură (agent.add_steer + _drain_steers, chat_session.steer, POST /messages).

Fără rețea și fără LLM: `llm.chat` e înlocuit local (monkeypatch), iar
tool-urile sunt stub-uri. Scrierile ChatSession ajung în tmp_path —
data/ de producție nu e atins.

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_steer.py -q
"""

import asyncio

import agent
import chat_session
import llm
import path_access
from tools.tool_base import Tool, ToolResult

STEER_PREFIX = "[Corectură de la utilizator în timpul rulării — ține cont de ea și continuă sarcina]"


class _StubTool(Tool):
    """Tool de test: întoarce mereu un rezultat scurt."""

    name = "stub"
    description = "tool fals pentru teste"
    parameters = {"type": "object", "properties": {}}
    required = []

    def __init__(self, agent_obj) -> None:
        super().__init__(agent_obj)
        self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        return ToolResult(message=f"rezultat {self.calls}")


def _make_agent() -> agent.Agent:
    """Agent minim, cu tool-urile reale înlocuite de stub-uri (fără execuție reală)."""

    async def confirm(tool_name: str, detail: str) -> bool:
        return True

    ag = agent.Agent(confirm=confirm)
    ag.tools = {"stub": _StubTool(ag)}
    return ag


def _snapshot(history: list[dict]) -> list[dict]:
    """Copie a istoricului la momentul apelului llm.chat (liste separate)."""
    return [dict(m) for m in history]


def _assert_fără_user_între_tool_calls_și_răspunsuri(history: list[dict]) -> None:
    """Niciun mesaj în afara rolului tool nu poate apărea între un mesaj
    assistant cu tool_calls și răspunsurile lui tool (providerul le-ar
    respinge pe restul tururilor cu 400)."""
    deschise: set[str] = set()
    for i, msg in enumerate(history):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            deschise |= {tc["id"] for tc in msg["tool_calls"]}
        elif msg.get("role") == "tool":
            deschise.discard(msg.get("tool_call_id"))
        else:
            assert not deschise, (
                f"mesaj {msg.get('role')} la poziția {i} înainte ca "
                f"tool_calls {sorted(deschise)} să primească răspuns: {msg}"
            )


def test_steer_intra_după_tool_results_și_inainte_de_următorul_llm_chat(monkeypatch):
    """(a) steer trimis în timpul rulării (după primul tool) devine mesaj
    user în istoricul văzut de al doilea llm.chat, DUPĂ răspunsul tool."""
    ag = _make_agent()
    seen: list[list[dict]] = []
    events: list[tuple[str, dict]] = []
    steered = {"done": False}

    async def fake_chat(history, tools=None, model=None, tool_choice=None, **kwargs):
        seen.append(_snapshot(history))
        if len(seen) == 1:
            return llm.LlmResponse(
                content=None,
                tool_calls=[llm.ToolCall(id="c1", name="stub", arguments="{}")],
            )
        return llm.LlmResponse(content="gata")

    def on_event(event_type: str, data: dict) -> None:
        events.append((event_type, data))
        # utilizatorul trimite steer-ul chiar în timpul rulării, după tool
        if event_type == "tool_result" and not steered["done"]:
            steered["done"] = True
            ag.add_steer("folosește altă abordare")

    ag.on_event = on_event
    monkeypatch.setattr(llm, "chat", fake_chat)

    result = asyncio.run(ag.run("fă treaba"))

    assert result == "gata"
    assert len(seen) == 2
    # istoricul văzut de llm.chat #2: steer-ul e ultimul mesaj, imediat după
    # răspunsul tool și înainte de acest apel
    assert [m["role"] for m in seen[1]] == ["system", "user", "assistant", "tool", "user"]
    assert seen[1][-1]["content"] == f"{STEER_PREFIX}\nfolosește altă abordare"
    assert any(
        t == "steer_applied" and d.get("text") == "folosește altă abordare"
        for t, d in events
    )
    _assert_fără_user_între_tool_calls_și_răspunsuri(ag.history)


def test_steer_pending_la_final_prelungește_bucla(monkeypatch):
    """(b) steer ajuns pending chiar cât modelul returnează finalul → bucla
    continuă cu încă un apel llm.chat, finalul e emis o singură dată la capăt."""
    ag = _make_agent()
    seen: list[list[dict]] = []
    finals: list[str] = []

    async def fake_chat(history, tools=None, model=None, tool_choice=None, **kwargs):
        seen.append(_snapshot(history))
        if len(seen) == 1:
            # steer-ul devine pending înainte ca răspunsul final să fie procesat
            ag.add_steer("adaugă și teste")
            return llm.LlmResponse(content="răspuns provizoriu")
        return llm.LlmResponse(content="Răspuns final")

    def on_event(event_type: str, data: dict) -> None:
        if event_type == "final":
            finals.append(data["text"])

    ag.on_event = on_event
    monkeypatch.setattr(llm, "chat", fake_chat)

    result = asyncio.run(ag.run("fă treaba"))

    assert result == "Răspuns final"
    assert len(seen) == 2  # bucla nu s-a oprit la finalul cu steer pending
    assert finals == ["Răspuns final"]  # "răspuns provizoriu" n-a fost emis
    assert seen[1][-1]["role"] == "user"
    assert "adaugă și teste" in seen[1][-1]["content"]
    assert ag.pending_steers == []


def test_niciun_user_între_tool_calls_și_răspunsurile_lor(monkeypatch):
    """(c) pe tot parcursul unei rulări cu mai mulți steer-uri (drenați între
    iterații + unul la final) istoricul nu conține niciodată un mesaj user
    între un assistant cu tool_calls și răspunsurile lui tool."""
    ag = _make_agent()
    calls = {"n": 0}

    async def fake_chat(history, tools=None, model=None, tool_choice=None, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return llm.LlmResponse(
                content=None,
                tool_calls=[llm.ToolCall(id=f"c{calls['n']}", name="stub", arguments="{}")],
            )
        # ultimul steer ajunge pending chiar înainte de finalul din iterația 3
        if calls["n"] == 3:
            ag.add_steer("ultimul steer")
        return llm.LlmResponse(content="final")

    def on_event(event_type: str, data: dict) -> None:
        if event_type == "tool_result":
            ag.add_steer(f"corectură după {data['name']}")

    ag.on_event = on_event
    monkeypatch.setattr(llm, "chat", fake_chat)

    result = asyncio.run(ag.run("fă treaba"))

    assert result == "final"
    # 4 apeluri: 2 cu tool_calls + finalul cu steer pending + finalul real
    assert calls["n"] == 4
    assert ag.pending_steers == []
    _assert_fără_user_între_tool_calls_și_răspunsuri(ag.history)
    steer_texts = [
        m["content"]
        for m in ag.history
        if m.get("role") == "user" and str(m.get("content", "")).startswith(STEER_PREFIX)
    ]
    assert len(steer_texts) == 3  # două drenate între iterații + ultimul la final


def test_pending_steers_cleared_la_startul_unei_ture_noi(monkeypatch):
    """(d) steer-ele rămase nedrenate de la o tură anterioară nu leak-uiesc
    în istoricul unei ture noi (se golesc la startul run())."""
    ag = _make_agent()
    ag.add_steer("de la o tură anterioară")
    assert ag.pending_steers == ["de la o tură anterioară"]

    async def fake_chat(history, tools=None, model=None, tool_choice=None, **kwargs):
        return llm.LlmResponse(content="ok")

    monkeypatch.setattr(llm, "chat", fake_chat)
    result = asyncio.run(ag.run("salut"))

    assert result == "ok"
    assert ag.pending_steers == []
    assert not any(
        str(m.get("content", "")).startswith(STEER_PREFIX) for m in ag.history
    )


def test_chat_session_steer(tmp_path, monkeypatch):
    """(e) API-ul de steer de la nivel de sesiune: False fără rulare activă,
    True cu rulare activă → coadă în agent + intrare „steer” în jurnal."""
    monkeypatch.setattr(chat_session, "CHATS_DIR", tmp_path)
    monkeypatch.setattr(path_access, "BASE_WORKDIR", str(tmp_path / "workdir"))
    session = chat_session.ChatSession()

    assert session.steer("mesaj fără rulare") is False
    assert session.agent.pending_steers == []

    async def _cu_rulare_activă() -> None:
        task = asyncio.create_task(asyncio.sleep(60))
        session._running_task = task
        try:
            assert session.steer("schimbă planul") is True
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            session._running_task = None

    asyncio.run(_cu_rulare_activă())

    assert session.agent.pending_steers == ["schimbă planul"]
    assert [e["text"] for e in session.log if e["type"] == "steer"] == ["schimbă planul"]
