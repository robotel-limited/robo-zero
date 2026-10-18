"""Preț/cost per apel LLM, pe baza prețurilor publice DeepSeek/Alibaba/NVIDIA
(cache hit/miss, output, peak/off-peak — api-docs.deepseek.com/quick_start/pricing,
verificat 2026-10-18). Peak = 01:00-04:00 și 06:00-10:00 UTC, luni-vineri; ignorăm
excepția sărbătorilor publice chinezești din peak, prea granulară pentru un
cost estimativ — de aici eticheta "estimat" din UI.

Agregarea (totals/stats) e în usage_store.py, care deține baza de date; acest
modul se ocupă doar de cât costă un apel individual, calculat la scriere
(usage_log.py) și la migrarea datelor istorice din usage.jsonl."""

from datetime import datetime, timezone

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


def entry_cost(
    model: str,
    ts: float,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    completion_tokens: int,
) -> float:
    rates = _PRICING.get(model)
    if rates is None:
        return 0.0
    cache_hit, cache_miss, output = rates["peak" if _is_peak(ts) else "offpeak"]
    usd = cache_hit_tokens * cache_hit + cache_miss_tokens * cache_miss + completion_tokens * output
    return usd / 1_000_000
