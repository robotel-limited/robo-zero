import asyncio

import llm


class _Resp:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}}


def _payload(monkeypatch, model, **kwargs):
    sent = {}

    async def fake_post(self, url, json=None, headers=None):
        sent.update(json)
        return _Resp()

    monkeypatch.setattr(llm.httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(llm, "_api_key", lambda m: "x")
    monkeypatch.setattr(llm.usage_log, "record", lambda *a, **k: None)
    asyncio.run(llm.chat([{"role": "user", "content": "hi"}], model=model, **kwargs))
    return sent


def test_json_mode_disables_thinking_on_deepseek(monkeypatch):
    p = _payload(monkeypatch, "deepseek-flash", response_format={"type": "json_object"})
    assert p["thinking"] == {"type": "disabled"}
    assert "tool_choice" not in p


def test_tool_choice_still_disables_thinking(monkeypatch):
    p = _payload(monkeypatch, "deepseek-flash", tool_choice="required")
    assert p["thinking"] == {"type": "disabled"}


def test_plain_chat_keeps_thinking(monkeypatch):
    assert "thinking" not in _payload(monkeypatch, "deepseek-flash")


def test_non_deepseek_json_mode_untouched(monkeypatch):
    p = _payload(monkeypatch, "qwen-flash", response_format={"type": "json_object"})
    assert "thinking" not in p
