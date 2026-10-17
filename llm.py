"""Client DeepSeek (OpenAI-compatible): function-calling nativ, retry/backoff.

Nu face streaming SSE propriu-zis aici (asta e în app.py la etapa 2) — expune
chat() async care întoarce răspunsul complet. app.py va retransmite bucăți
din `content` către client pe măsură ce agent.py le produce.
"""

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_LLM_CFG = _SETTINGS["llm"]
MODEL = _LLM_CFG["model"]
ENDPOINT = _LLM_CFG["endpoint"]
TIMEOUT_SECONDS = _LLM_CFG["timeout_seconds"]
MAX_RETRIES = _LLM_CFG["max_retries"]
RETRY_BACKOFF = _LLM_CFG["retry_backoff_seconds"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON brut, neparsat — parsarea e responsabilitatea agent.py


@dataclass
class LlmResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LlmError(Exception):
    """Eroare definitivă (după epuizarea reîncercărilor) la apelul DeepSeek."""


def _api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise LlmError("DEEPSEEK_API_KEY nu e setat (verifică .env).")
    return key


async def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> LlmResponse:
    """Trimite istoricul + schema tool-urilor la DeepSeek, cu retry/backoff
    pe 429/5xx. Ridică LlmError după epuizarea reîncercărilor."""
    payload: dict[str, Any] = {"model": MODEL, "messages": messages}
    if tools:
        payload["tools"] = tools

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    attempts = MAX_RETRIES + 1

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        for attempt in range(attempts):
            try:
                resp = await client.post(ENDPOINT, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = LlmError(
                        f"DeepSeek a răspuns {resp.status_code}: {resp.text[:500]}"
                    )
                elif resp.status_code >= 400:
                    # Eroare de client (4xx, altele decât 429) — nu are sens
                    # să reîncercăm, e o problemă de request, nu tranzitorie.
                    raise LlmError(
                        f"DeepSeek a răspuns {resp.status_code}: {resp.text[:500]}"
                    )
                else:
                    return _parse_response(resp.json())

            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                await asyncio.sleep(backoff)

    raise LlmError(f"DeepSeek inaccesibil după {attempts} încercări: {last_error}")


def _parse_response(data: dict[str, Any]) -> LlmResponse:
    choice = data["choices"][0]
    message = choice["message"]
    content = message.get("content")
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls = [
        ToolCall(
            id=tc["id"],
            name=tc["function"]["name"],
            arguments=tc["function"]["arguments"],
        )
        for tc in raw_tool_calls
    ]
    return LlmResponse(content=content, tool_calls=tool_calls)
