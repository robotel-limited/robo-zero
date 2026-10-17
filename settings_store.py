"""Setări editabile din UI, persistate separat de conf/settings.yaml (care
e configurație de infrastructură, citită o singură dată la pornire).
Acestea sunt mici, modificabile live din tab-ul Setări, fără restart."""

import json
from pathlib import Path
from typing import Any

_PATH = Path(__file__).resolve().parent / "conf" / "user_settings.json"

_DEFAULTS: dict[str, Any] = {
    "verbose": True,  # afișează tool_call/tool_result în conversație, nu doar răspunsul final
    "notify_telegram": False,  # trimite "Task ready - [nume]" pe Telegram la finalul rulării
    "notify_sms": False,  # trimite "Task ready - [nume]" prin SMS la finalul rulării
}


def load() -> dict[str, Any]:
    if not _PATH.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)
    return {**_DEFAULTS, **data}


def save(partial: dict[str, Any]) -> dict[str, Any]:
    current = load()
    current.update({k: v for k, v in partial.items() if k in _DEFAULTS})
    _PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current
