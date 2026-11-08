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
import re
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

# Determinism + buget de ieșire (lecția DeepInfer, 2026-11-07): fără ele,
# llama-server rula la temperature 1.0 cu seed aleator, iar max_tokens lipsea
# complet, deci răspunsurile cu preambul + apeluri de unelte se trunchiau.
TEMPERATURE = _LLM_CFG.get("temperature")
SEED = _LLM_CFG.get("seed")

# Plafon de ieșire UNIC pentru toate modelele (decizia Sorin 2026-11-08,
# varianta A): o singură valoare, nu per-model. 8192 e suficient pentru apeluri
# de unelte lungi (code_task/fs_write) fără trunchiere (finish_reason=length).
# Pe modelele locale cu fereastră mică (32768) această ieșire e REZERVATĂ din
# buget în history.compact_if_needed (via agent.py reserved_tokens), ca
# prompt + ieșire să nu depășească fereastra. Înlocuiește settings.max_tokens
# (=1200, calibrat pe llama-server local) care, aplicat global de la 2026-11-07,
# tăia apelurile de unelte ale modelelor cloud.
OUTPUT_TOKENS = 8192

_DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"

# Etapa E: catalog multi-provider, sursă unică pentru proiecte/settings/UI
# (vezi projects_store._MODELS, settings_store, app.py /api/models).
# label = ce apare în dropdown-urile din UI. context_tokens = fereastra reală
# a modelului (verificat live/documentat 2026-10-31 — vezi history.py, care
# citește aceste valori per-model pentru pragul de compactare, în loc de o
# singură constantă globală care era corectă doar pentru deepseek).
MODEL_CATALOG: dict[str, dict[str, str | int]] = {
    "deepseek-flash": {
        "endpoint": _DEEPSEEK_ENDPOINT,
        "api_key_env": "DEEPSEEK_API_KEY",
        "label": "DeepSeek Flash (implicit, ieftin)",
        "context_tokens": 1_000_000,
    },
    "deepseek-v4-pro": {
        "endpoint": _DEEPSEEK_ENDPOINT,
        "api_key_env": "DEEPSEEK_API_KEY",
        "label": "DeepSeek V4 Pro (mai scump)",
        "context_tokens": 1_000_000,
    },
    "qwen-flash": {
        "endpoint": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        "api_key_env": "QWEN_APIKEY",
        "label": "Qwen Flash (Alibaba)",
        "context_tokens": 1_000_000,
    },
    # gratuit pe cheia noastră NVIDIA (verificat live 2026-10-18); model de
    # tip reasoning — la max_tokens mic poate întoarce content=null cu
    # răspunsul doar în reasoning_content, dar cu buget normal de tokeni
    # completează corect `content`. Fereastră: 262144 (default practic pe
    # endpoint-ul NVIDIA hosted, nu maximul teoretic de 1M al arhitecturii).
    "nvidia/nemotron-3-super-120b-a12b": {
        "endpoint": "https://integrate.api.nvidia.com/v1/chat/completions",
        "api_key_env": "NVIDIA_API_KEY",
        "label": "Nemotron 3 Super (NVIDIA, gratuit)",
        "context_tokens": 262_144,
    },
    # Local, pe SVAI: llama-server (llama.cpp) peste gpt-oss-20b, unitate
    # systemd "llama-oss" static (nu e enabled la boot) — pornită la cerere
    # din _ensure_local_server_started(), nu se oprește automat (rămâne caldă
    # pentru turul următor; vezi decizie 2026-10-30 în delegate-oss pentru
    # același pattern). Nu are auth reală — orice Bearer e ignorat de server.
    "gpt-oss-20b": {
        "endpoint": "http://192.168.0.152:8091/v1/chat/completions",
        "api_key_env": "LLAMA_OSS_API_KEY",
        "label": "GPT-OSS 20B (local, SVAI)",
        # -c 32768 în llama-oss.service — fereastra reală a serverului, nu
        # a arhitecturii gpt-oss-20b (care suportă mult mai mult).
        "context_tokens": 32_768,
    },
    # Local, pe SVAI: SenseNova-Loop-9B-Preview Q4_K_M pe forkul
    # /srv/llama.cpp-sensenova-loop, port 8093 (-c 32768 --parallel 1
    # --spec-type ngram-mod, ~7,2 GB VRAM). Unitate systemd "llama-ssn",
    # pornită la cerere ca llama-oss (Conflicts= reciproc). Distilator al memoriei
    # de lucru (distiller.py) și candidat de agent (A/B 2026-11-01).
    "sensenova-loop-9b": {
        "endpoint": "http://192.168.0.152:8093/v1/chat/completions",
        "api_key_env": "LLAMA_OSS_API_KEY",
        "label": "SenseNova Loop 9B (local, SVAI)",
        "context_tokens": 49_152,
    },
    # Local, pe SVAI: LFM3-Omni-2B Q4_K_M, /srv/llama.cpp, port 8095
    # (-c 32768 --parallel 1). Pornit manual (nohup), test limite 2026-11-01.
    "lfm3-2b": {
        "endpoint": "http://192.168.0.152:8095/v1/chat/completions",
        "api_key_env": "LLAMA_OSS_API_KEY",
        "label": "LFM3 Omni 2B (local, SVAI)",
        "context_tokens": 32_768,
    },
}

DEFAULT_CONTEXT_TOKENS = 128_000  # fallback dacă un model nou nu are context_tokens în catalog


def context_tokens_for(model: str | None) -> int:
    cfg = MODEL_CATALOG.get(model or MODEL)
    if cfg is None:
        return DEFAULT_CONTEXT_TOKENS
    return int(cfg.get("context_tokens", DEFAULT_CONTEXT_TOKENS))

_OSS_SSH_HOST = "svai"
# modele locale pornite la cerere pe GPU-ul svai: model -> (unitate systemd, /health).
# llama-oss și llama-ssn au Conflicts= reciproc, deci pornirea unuia îl oprește
# pe celălalt — un singur model pe GPU (decizie Sorin 2026-11-01).
_LOCAL_UNITS = {
    "gpt-oss-20b": ("llama-oss", "http://192.168.0.152:8091/health"),
    "sensenova-loop-9b": ("llama-ssn", "http://192.168.0.152:8093/health"),
}
_OSS_FINAL_MARKER = "<|channel|>final<|message|>"
_OSS_STRAY_TOKENS_RE = re.compile(r"<\|[a-z_]+\|>")


def _health_ok(url: str) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


async def _ensure_local_server_started(model: str, timeout: float = 90.0) -> None:
    """Pornește unitatea systemd a modelului local pe SVAI (start-on-demand,
    nu stă pornită degeaba) și așteaptă /health. Vezi același pattern în
    delegate-oss/llama_client.py (decizie Sorin 2026-10-30)."""
    unit, health_url = _LOCAL_UNITS[model]
    if await asyncio.to_thread(_health_ok, health_url):
        return
    proc = await asyncio.create_subprocess_exec(
        "ssh", _OSS_SSH_HOST, "sudo", "systemctl", "start", unit,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise LlmError(f"ssh systemctl start {unit} a eșuat: {stderr.decode()[:300]}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await asyncio.to_thread(_health_ok, health_url):
            return
        await asyncio.sleep(2)
    raise LlmError(f"llama-server ({unit}) n-a pornit în {timeout}s")


def _split_oss_harmony(raw_content: str) -> str:
    """gpt-oss pe acest build llama.cpp nu separă automat canalul harmony —
    conținutul final vine după ultimul marker <|channel|>final<|message|>
    (vezi delegate-oss/llama_client.py:_split_harmony, decizie 2026-10-30)."""
    idx = raw_content.rfind(_OSS_FINAL_MARKER)
    if idx == -1:
        return ""
    final = raw_content[idx + len(_OSS_FINAL_MARKER):]
    return _OSS_STRAY_TOKENS_RE.sub("", final).strip()


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON brut, neparsat — parsarea e responsabilitatea agent.py


@dataclass
class LlmResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False
    max_tokens: int | None = None


class LlmError(Exception):
    """Eroare definitivă (după epuizarea reîncercărilor) la apelul providerului."""


class ModelToolingUnsupportedError(LlmError):
    """Raised when the PROVIDER rejects the request because the MODEL itself
    generated a tool call whose arguments are not valid JSON (not our bug,
    not transient, retry won't help) — the chosen model can't do
    function-calling correctly on this task. The current chat stays
    poisoned (the broken message is already in history), it must be
    resumed in a new chat with a different model."""

    def __init__(self, model: str, raw: str):
        self.model = model
        super().__init__(
            f"Model '{model}' is unable to perform the task (it generated an "
            f"invalid tool call). Choose another model in a new chat."
        )


# Known signatures of "the provider rejected the tool call because the
# model itself generated it malformed" — not a request error on our side,
# so there's no point showing the provider's raw JSON.
_TOOLING_UNSUPPORTED_MARKERS = (
    ("invalid_parameter_error", "function.arguments"),
)


def _is_tooling_unsupported(status_code: int, body: str) -> bool:
    if status_code != 400:
        return False
    lowered = body.lower()
    return any(
        all(marker in lowered for marker in markers) for markers in _TOOLING_UNSUPPORTED_MARKERS
    )


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
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float | None = None,
    seed: int | None = None,
    max_tokens: int | None = None,
) -> LlmResponse:
    """Trimite istoricul + schema tool-urilor la DeepSeek, cu retry/backoff
    pe 429/5xx. Ridică LlmError după epuizarea reîncercărilor. `purpose`
    etichetează consumul de tokeni în data/usage.jsonl (vezi usage_log.py).
    `model` suprascrie modelul implicit (etapa D: ales per proiect) — lăsat
    nespecificat de extracția de fundal (project_digest.py, kb_extract.py,
    history.py), care rulează mereu pe modelul implicit (flash)."""
    model = model or MODEL
    endpoint = _provider_cfg(model)["endpoint"]
    if model in _LOCAL_UNITS:
        await _ensure_local_server_started(model)
    # imaginile atașate stau în istoric ca /files/uploads/...; modelul le primește base64
    payload: dict[str, Any] = {"model": model, "messages": attachments.inline_images(messages)}
    _temp = TEMPERATURE if temperature is None else temperature
    _seed = SEED if seed is None else seed
    # plafon de ieșire unic pentru toate modelele (OUTPUT_TOKENS); un apelant
    # poate încă suprascrie explicit prin max_tokens (ex. extracții de fundal)
    _max = max_tokens if max_tokens is not None else OUTPUT_TOKENS
    if _temp is not None:
        payload["temperature"] = _temp
    if _seed is not None:
        payload["seed"] = _seed
    if _max is not None:
        payload["max_tokens"] = _max
    if tools:
        payload["tools"] = tools
    if response_format:
        payload["response_format"] = response_format
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
        if model.startswith("deepseek-"):
            # thinking mode respinge orice tool_choice diferit de auto (HTTP 400)
            payload["thinking"] = {"type": "disabled"}

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
                    if _is_tooling_unsupported(resp.status_code, resp.text):
                        raise ModelToolingUnsupportedError(model, resp.text[:500])
                    raise LlmError(
                        f"{model} a răspuns {resp.status_code}: {resp.text[:500]}"
                    )
                else:
                    data = resp.json()
                    choices = data.get("choices") if isinstance(data, dict) else None
                    if not choices or not isinstance(choices[0].get("message"), dict):
                        # furnizor degradat: 200 OK cu corp gol/incomplet — tranzitoriu, reîncercăm
                        last_error = LlmError(f"{model} a răspuns 200 fără mesaj: {resp.text[:300]}")
                    else:
                        latency_ms = (time.perf_counter() - attempt_start) * 1000
                        usage_log.record(model, purpose, data.get("usage"), latency_ms)
                        parsed = _parse_response(data, model)
                        parsed.max_tokens = _max
                        return parsed

            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                await asyncio.sleep(backoff)

    raise LlmError(f"{model} inaccesibil după {attempts} încercări: {last_error}")


def _parse_response(data: dict[str, Any], model: str | None = None) -> LlmResponse:
    choice = data["choices"][0]
    message = choice["message"]
    content = message.get("content")
    if model == "gpt-oss-20b" and content and _OSS_FINAL_MARKER in content:
        content = _split_oss_harmony(content)
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls = [
        ToolCall(
            id=tc["id"],
            name=tc["function"]["name"],
            arguments=tc["function"]["arguments"],
        )
        for tc in raw_tool_calls
    ]
    return LlmResponse(
        content=content, tool_calls=tool_calls, truncated=choice.get("finish_reason") == "length"
    )
