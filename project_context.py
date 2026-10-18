"""Ce primește LLM-ul din memoria proiectului: blocul complet la primul mesaj
al unui chat (prompt proiect + fișă + rezumatele celorlalte chat-uri, în
limita bugetului) și diferențele fișei pentru un chat deja deschis."""

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

import history
import project_memory_store as store
import projects_store

_PROJECT_ROOT = Path(__file__).resolve().parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _CFG = yaml.safe_load(f)["project_memory"]

_FISA_HEADER = (
    "## Fișa proiectului\n"
    "Fapte extrase automat din toate discuțiile proiectului. Pot conține erori: "
    "dacă utilizatorul spune altceva, are prioritate el. Când te bazezi pe un fapt, "
    "menționează sursa (discuția și data)."
)
_SEARCH_NOTE = (
    "Pentru detalii exacte din discuțiile proiectului (doze, date, cifre, formulări) "
    "care nu apar mai sus, folosește tool-ul project_search."
)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _fact_line(fact: dict[str, Any]) -> str:
    details = []
    if fact.get("event_date"):
        details.append(f"data evenimentului: {fact['event_date']}")
    title = fact.get("source_chat_title") or "discuție ștearsă"
    details.append(f"sursa: „{title}”, {_day(fact['created_at'])}")
    return f"- {fact['text']} ({'; '.join(details)})"


def _fisa(project_id: str) -> str:
    facts = store.active_facts(project_id)
    if not facts:
        return f"{_FISA_HEADER}\n(încă goală)"
    sections: dict[str, list[str]] = {}
    for fact in facts:
        sections.setdefault(fact["category"] or "Altele", []).append(_fact_line(fact))
    body = "\n".join(f"### {cat}\n" + "\n".join(lines) for cat, lines in sections.items())
    return f"{_FISA_HEADER}\n{body}"


def _other_chats(project_id: str, exclude_chat_id: str | None) -> str:
    budget_chars = _CFG["context_budget_tokens"] * history.CHARS_PER_TOKEN
    parts: list[str] = []
    used = 0
    digests = store.list_digests(project_id, exclude_chat_id)
    for d in digests:
        block = f"### „{d['chat_title']}” (actualizat {_day(d['updated_at'])})\n{d['summary']}"
        if used + len(block) > budget_chars:
            break
        parts.append(block)
        used += len(block)
    if not parts:
        return ""
    omitted = len(digests) - len(parts)
    note = f"\n({omitted} discuții mai vechi omise — caută-le cu project_search.)" if omitted else ""
    return "## Alte discuții din acest proiect (rezumate, cele mai noi primele)\n" + "\n\n".join(parts) + note


def build_initial(project_id: str, exclude_chat_id: str | None) -> str | None:
    project = projects_store.get(project_id)
    if project is None:
        return None
    blocks = [f"# Proiect: {project['title']}"]
    if project["prompt"].strip():
        blocks.append(project["prompt"].strip())
    blocks.append(_fisa(project_id))
    others = _other_chats(project_id, exclude_chat_id)
    if others:
        blocks.append(others)
    blocks.append(_SEARCH_NOTE)
    return "\n\n".join(blocks)


def diff_since(project_id: str, ts: float, chat_id: str) -> list[str]:
    """Liniile de actualizare a fișei de după `ts`, fără faptele produse chiar
    de acest chat (le cunoaște deja din propria conversație)."""
    lines: list[str] = []
    for fact in store.active_facts_since(project_id, ts):
        if fact["source_chat_id"] == chat_id:
            continue
        source = f"„{fact.get('source_chat_title') or 'discuție ștearsă'}”"
        if fact.get("replaces_text"):
            lines.append(f"~ înlocuit: {fact['replaces_text']} → {fact['text']} (sursa: {source})")
        else:
            lines.append(f"+ {fact['text']} (sursa: {source})")
    return lines


def diff_prefix(lines: list[str]) -> str:
    return (
        "[Actualizare fișă proiect — informații noi din alte discuții, de la ultimul mesaj:\n"
        + "\n".join(lines)
        + "]\n\n"
    )
