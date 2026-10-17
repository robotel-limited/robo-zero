"""Compactarea istoricului (etapa 6): peste un prag de ocupare a contextului
modelului, mesajele vechi (excluzând primul mesaj — system prompt — și o
coadă recentă) sunt rezumate într-un singur mesaj printr-un apel LLM, ca
bucla să poată continua fără să depășească fereastra de context.
"""

from pathlib import Path
from typing import Any

import yaml

import llm

_PROJECT_ROOT = Path(__file__).resolve().parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_HISTORY_CFG = _SETTINGS["history"]
CONTEXT_TOKENS = _HISTORY_CFG["context_tokens"]
TRIGGER_FRACTION = _HISTORY_CFG["trigger_fraction"]
KEEP_LAST_MESSAGES = _HISTORY_CFG["keep_last_messages"]

# Nu avem un tokenizer dedicat (nici DeepSeek nu publică unul simplu de
# instalat) — 4 caractere/token e o estimare brută dar suficientă pentru un
# prag de compactare (nu trebuie să fie exactă, doar să declanșeze din timp).
CHARS_PER_TOKEN = 4


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m)) for m in messages) // CHARS_PER_TOKEN


def _safe_tail_start(history: list[dict[str, Any]], desired_start: int) -> int:
    """Un mesaj `tool` sau un `assistant` cu `tool_calls` nu poate deveni
    primul mesaj păstrat — DeepSeek respinge un istoric care începe în
    mijlocul unei perechi apel-tool/rezultat-tool. Se recuează înapoi până
    la cel mai apropiat început de tură (rol `user`)."""
    idx = desired_start
    while idx > 1 and history[idx]["role"] != "user":
        idx -= 1
    return idx


async def compact_if_needed(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Întoarce istoricul, compactat dacă s-a trecut pragul configurat.
    Întoarce aceeași listă (același obiect) când nu e nevoie de compactare,
    ca apelantul să poată detecta ieftin dacă s-a schimbat ceva."""
    if _estimate_tokens(history) < CONTEXT_TOKENS * TRIGGER_FRACTION:
        return history
    if len(history) <= 1:
        return history

    tail_start = _safe_tail_start(history, max(1, len(history) - KEEP_LAST_MESSAGES))
    middle = history[1:tail_start]
    if not middle:
        return history

    summary_text = await _summarize(middle)
    summary_message = {
        "role": "assistant",
        "content": f"[Rezumat automat al conversației anterioare]\n{summary_text}",
    }
    return [history[0], summary_message, *history[tail_start:]]


async def _summarize(messages: list[dict[str, Any]]) -> str:
    transcript = "\n".join(_format_message(m) for m in messages)
    prompt = [
        {
            "role": "system",
            "content": (
                "Rezumă concis, în română, conversația de mai jos dintre un "
                "utilizator și un agent AI cu tool-uri. Păstrează deciziile, "
                "faptele și rezultatele importante ale tool-urilor; elimină "
                "detaliile redundante. Răspunde doar cu rezumatul, fără alt text."
            ),
        },
        {"role": "user", "content": transcript},
    ]
    response = await llm.chat(prompt)
    return response.content or "(rezumat indisponibil)"


def _format_message(message: dict[str, Any]) -> str:
    role = message.get("role", "?")
    if message.get("tool_calls"):
        calls = ", ".join(
            f"{tc['function']['name']}({tc['function']['arguments']})"
            for tc in message["tool_calls"]
        )
        return f"[{role}] apel tool: {calls}"
    return f"[{role}] {message.get('content') or ''}"
