import asyncio

import agent as A
from agent_loopguard import AgentLoopAborted


class _Fake:
    def __init__(self):
        self.history = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

    def _chat_model(self):
        return None


class _Resp:
    def __init__(self, content):
        self.content = content


def _run(monkeypatch, fake_chat):
    monkeypatch.setattr(A.llm, "chat", fake_chat)
    f = _Fake()
    text = asyncio.run(A.Agent.explain_abort(f, AgentLoopAborted("noprogress", "project_search", 4, [])))
    return f, text


def test_explanation_returned_and_kept_in_history(monkeypatch):
    seen = {}

    async def fake_chat(history, **kw):
        seen.update(kw)
        return _Resp(" Îmi lipsește calea fișierelor. ")

    f, text = _run(monkeypatch, fake_chat)
    assert text == "Îmi lipsește calea fișierelor."
    assert "tools" not in seen
    assert f.history[-2]["role"] == "user" and "project_search" in f.history[-2]["content"]
    assert f.history[-1] == {"role": "assistant", "content": text}


def test_llm_failure_returns_none_and_restores_history(monkeypatch):
    async def fake_chat(history, **kw):
        raise A.llm.LlmError("down")

    f, text = _run(monkeypatch, fake_chat)
    assert text is None
    assert len(f.history) == 2


def test_empty_content_returns_none(monkeypatch):
    async def fake_chat(history, **kw):
        return _Resp("")

    f, text = _run(monkeypatch, fake_chat)
    assert text is None
    assert len(f.history) == 2
