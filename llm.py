"""Client multi-provider (OpenAI-compatible): function-calling nativ,
retry/backoff. Fiecare model e înregistrat în MODEL_CATALOG cu endpoint +
variabila de env care îi ține cheia — DeepSeek, Alibaba/Qwen (DashScope) și
modelele gratuite NVIDIA folosesc același protocol de chat/completions.

Nu face streaming SSE propriu-zis aici (asta e în app.py la etapa 2) — expune
chat() async care întoarce răspunsul complet. app.py va retransmite bucăți
din `content` către client pe măsură ce agent.py le produce.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

import attachments
import usage_log

_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_LLM_CFG = _SETTINGS["llm"]
MODEL = _LLM_CFG["model"]  # implicit de infrastructură — settings_store.default_model are precedență live
TIMEOUT_SECONDS = _LLM_CFG["timeout_seconds"]
MAX_RETRIES = _LLM_CFG["max_retries"]
RETRY_BACKOFF = _LLM_CFG["retry_backoff_seconds"]

_DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"

# Etapa E: catalog multi-provider, sursă unică pentru proiecte/settings/UI
# (vezi projects_store._MODELS, settings_store, app.py /api/models).
# label = ce apare în dropdown-urile din UI.
MODEL_CATALOG: dict[str, dict[str, str]] = {
    "deepseek-flash": {
        "endpoint": _DEEPSEEK_ENDPOINT,
        "api_key_env": "DEEPSEEK_API_KEY",
        "label": "DeepSeek Flash (implicit, ieftin)",
    },
    "deepseek-v4-pro": {
        "endpoint": _DEEPSEEK_ENDPOINT,
        "api_key_env": "DEEPSEEK_API_KEY",
        "label": "DeepSeek V4 Pro (mai scump)",
    },
    "qwen-flash": {
        "endpoint": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        "api_key_env": "QWEN_APIKEY",
        "label": "Qwen Flash (Alibaba)",
    },
    # gratuit pe cheia noastră NVIDIA (verificat live 2026-10-18); model de
    # tip reasoning — la max_tokens mic poate întoarce content=null cu
    # răspunsul doar în reasoning_content, dar cu buget normal de tokeni
    # completează corect `content`.
    "nvidia/nemotron-3-super-120b-a12b": {
        "endpoint": "https://integrate.api.nvidia.com/v1/chat/completions",
        "api_key_env": "NVIDIA_API_KEY",
        "label": "Nemotron 3 Super (NVIDIA, gratuit)",
    },
}


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
    """Eroare definitivă (după epuizarea reîncercărilor) la apelul providerului."""


def _provider_cfg(model: str) -> dict[str, str]:
    cfg = MODEL_CATALOG.get(model)
    if cfg is None:
        raise LlmError(f"Model necunoscut: {model}")
    return cfg


def _api_key(model: str) -> str:
    cfg = _provider_cfg(model)
    env_name = cfg["api_key_env"]
    key = os.environ.get(env_name)
    if not key:
        raise LlmError(f"{env_name} nu e setat (verifică .env).")
    return key


async def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    purpose: str = "chat",
    model: str | None = None,
) -> LlmResponse:
    """Trimite istoricul + schema tool-urilor la DeepSeek, cu retry/backoff
    pe 429/5xx. Ridică LlmError după epuizarea reîncercărilor. `purpose`
    etichetează consumul de tokeni în data/usage.jsonl (vezi usage_log.py).
    `model` suprascrie modelul implicit (etapa D: ales per proiect) — lăsat
    nespecificat de extracția de fundal (project_digest.py, kb_extract.py,
    history.py), care rulează mereu pe modelul implicit (flash)."""
    model = model or MODEL
    endpoint = _provider_cfg(model)["endpoint"]
    # imaginile atașate stau în istoric ca /files/uploads/...; modelul le primește base64
    payload: dict[str, Any] = {"model": model, "messages": attachments.inline_images(messages)}
    if tools:
        payload["tools"] = tools
    if response_format:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {_api_key(model)}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    attempts = MAX_RETRIES + 1

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        for attempt in range(attempts):
            attempt_start = time.perf_counter()
            try:
                resp = await client.post(endpoint, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = LlmError(
                        f"{model} a răspuns {resp.status_code}: {resp.text[:500]}"
                    )
                elif resp.status_code >= 400:
                    # Eroare de client (4xx, altele decât 429) — nu are sens
                    # să reîncercăm, e o problemă de request, nu tranzitorie.
                    raise LlmError(
                        f"{model} a răspuns {resp.status_code}: {resp.text[:500]}"
                    )
                else:
                    data = resp.json()
                    latency_ms = (time.perf_counter() - attempt_start) * 1000
                    usage_log.record(model, purpose, data.get("usage"), latency_ms)
                    return _parse_response(data)

            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                await asyncio.sleep(backoff)

    raise LlmError(f"{model} inaccesibil după {attempts} încercări: {last_error}")


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
