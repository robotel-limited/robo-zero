"""Gestionarea memoriei de lucru: stocare și scurtare a rezultatelor tool-urilor."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple, Optional

# Constante de configurare
STUB_CHARS = 1500
SHORT_STUB_CHARS = 200
MARKER_PREFIX = "[ieșire completă: "
DISTILLED_PREFIX = "[notă distilată]"  # = distiller.NOTE_PREFIX (fără import circular)

def store(workdir: str, key: str, n: int, text: str) -> str:
    """Salvează textul în fișierul <workdir>/.obs/<key>/<n>.txt și
    întoarce calea relativă în format cu slashuri."""
    rel_dir = Path(".obs") / key
    rel_path = rel_dir / f"{n}.txt"
    abs_dir = Path(workdir) / rel_dir
    abs_path = Path(workdir) / rel_path
    try:
        abs_dir.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
    except OSError:
        raise
    return str(rel_path.as_posix())

def marker(relpath: str, text: str) -> str:
    """Construiește linia marker cu detalii despre fișier și lungime."""
    lines = text.count("\n") + 1
    return f"{MARKER_PREFIX}{relpath} — {len(text)} caractere, {lines} linii; recitește cu fs_read]"

def make_stub(text: str, relpath: str) -> str:
    """Returnează un stub de lungime STUB_CHARS urmat de marker."""
    return text[:STUB_CHARS] + "\n…\n" + marker(relpath, text)

def on_tool_result(
    workdir: str, key: str, n: int, text: str
) -> Tuple[str, Optional[str]]:
    """Procesare rezultat tool: dacă este lung, se salvează și se returnează stub."""
    if len(text) <= STUB_CHARS:
        return text, None
    try:
        relpath = store(workdir, key, n, text)
        return make_stub(text, relpath), relpath
    except OSError:
        return text, None

def is_short_stub(content: str) -> bool:
    """Verifică dacă conținutul este deja scurtat."""
    return (
        MARKER_PREFIX in content
        and len(content) <= SHORT_STUB_CHARS + 400
    )

def shorten(content: str) -> str:
    """Scurtează un mesaj care conține markerul, dacă nu este deja scurt."""
    # notele distilate (distiller.py) sunt deja compacte și nu se mai taie
    if MARKER_PREFIX not in content or is_short_stub(content) or content.startswith(DISTILLED_PREFIX):
        return content
    lines = content.splitlines()
    marker_line = None
    for line in reversed(lines):
        if line.startswith(MARKER_PREFIX):
            marker_line = line
            break
    if not marker_line:
        return content
    return content[:SHORT_STUB_CHARS] + "\n…\n" + marker_line

def collapse_old(history: list[dict], keep_steps: int = 2) -> int:
    """Scurtează mesajele `tool` din istoric, cu excepția celor din ultimele
    `keep_steps` pași (assistant cu tool_calls)."""
    step_indices = [
        i
        for i, m in enumerate(history)
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    if len(step_indices) <= keep_steps:
        return 0
    start_index = step_indices[-keep_steps]
    changed = 0
    for i, msg in enumerate(history):
        if msg.get("role") == "tool" and i < start_index:
            content = msg.get("content")
            if isinstance(content, str):
                new = shorten(content)
                if new != content:
                    msg["content"] = new
                    changed += 1
    return changed