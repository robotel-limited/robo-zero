"""Cost estimat per chat/proiect, calculat din data/usage.jsonl (vezi
usage_log.py) pe baza prețurilor publice DeepSeek (cache hit/miss, output,
peak/off-peak — api-docs.deepseek.com/quick_start/pricing, verificat
2026-10-18). Peak = 01:00-04:00 și 06:00-10:00 UTC, luni-vineri; ignorăm
excepția sărbătorilor publice chinezești din peak, prea granulară pentru un
cost estimativ — de aici eticheta "estimat" din UI."""

import json
from datetime import datetime, timezone
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "data" / "usage.jsonl"

# $ per milion tokeni: (cache_hit, cache_miss, output), separat offpeak/peak.
_PRICING = {
    "deepseek-flash": {"offpeak": (0.003, 0.15, 0.60), "peak": (0.006, 0.30, 1.20)},
    "deepseek-v4-pro": {"offpeak": (0.022, 0.66, 1.98), "peak": (0.044, 1.32, 3.96)},
    # DashScope (Alibaba) nu are discount de cache sau peak/offpeak — preț
    # unic, verificat live 2026-10-06: $0.05/M input, $0.4/M output (<=256K).
    "qwen-flash": {"offpeak": (0.05, 0.05, 0.4), "peak": (0.05, 0.05, 0.4)},
    # gratuit pe cheia noastră NVIDIA.
    "nvidia/nemotron-3-super-120b-a12b": {"offpeak": (0, 0, 0), "peak": (0, 0, 0)},
}


def _is_peak(ts: float) -> bool:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if dt.weekday() >= 5:
        return False
    return 1 <= dt.hour < 4 or 6 <= dt.hour < 10


def _entry_cost(entry: dict) -> float:
    rates = _PRICING.get(entry.get("model"))
    if rates is None:
        return 0.0
    cache_hit, cache_miss, output = rates["peak" if _is_peak(entry["ts"]) else "offpeak"]
    usd = (
        entry.get("cache_hit_tokens", 0) * cache_hit
        + entry.get("cache_miss_tokens", 0) * cache_miss
        + entry.get("completion_tokens", 0) * output
    )
    return usd / 1_000_000


def totals() -> dict:
    """{"by_chat": {chat_id: usd}, "by_project": {project_id: usd}, "total": usd}.
    by_project include și consumul fără chat (extracție KB, indexare foldere)."""
    by_chat: dict[str, float] = {}
    by_project: dict[str, float] = {}
    total = 0.0
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        usd = _entry_cost(entry)
        total += usd
        chat_id = entry.get("chat_id")
        project_id = entry.get("project_id")
        if chat_id:
            by_chat[chat_id] = by_chat.get(chat_id, 0.0) + usd
        if project_id:
            by_project[project_id] = by_project.get(project_id, 0.0) + usd
    return {"by_chat": by_chat, "by_project": by_project, "total": total}
