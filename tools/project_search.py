"""Căutare full-text în arhiva brută a proiectului chat-ului curent (toate
chat-urile proiectului) și în fișierele bazei lui de cunoștințe — pentru
detalii exacte pe care fișa și rezumatele injectate în context nu le conțin. Înregistrat doar în chat-urile legate
de un proiect (vezi Agent._build_tools)."""

from datetime import datetime
from typing import Any

import project_archive
import project_kb_store
from tools.tool_base import Tool, ToolResult


class ProjectSearch(Tool):
    name: str = "project_search"
    description: str = (
        "Caută în textul integral al tuturor discuțiilor din proiectul curent "
        "(mesaje utilizator, răspunsuri, rezultate de tool-uri) și al fișierelor "
        "din baza de cunoștințe a proiectului. Folosește-l "
        "când ai nevoie de un detaliu exact (doză, dată, cifră, formulare) "
        "care nu apare în fișa proiectului sau în rezumatele din context."
    )
    parameters: dict[str, Any] = {
        "query": {
            "type": "string",
            "description": "2-4 cuvinte-cheie distinctive (nu o propoziție); diacriticele nu contează.",
        },
        "limit": {"type": "integer", "description": "Număr maxim de fragmente (implicit 8)."},
    }
    required: list[str] = ["query"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return ToolResult(message="Eroare: parametrul 'query' e obligatoriu.")
        project_id = getattr(self.agent, "project_id", None)
        if not project_id:
            return ToolResult(message="Eroare: chat-ul curent nu aparține unui proiect.")
        try:
            limit = max(1, min(int(kwargs.get("limit") or 8), 20))
        except (TypeError, ValueError):
            limit = 8

        hits = project_archive.search(project_id, query, limit)
        kb_hits = project_kb_store.search(project_id, project_archive.fts_query(query), limit)
        if not hits and not kb_hits:
            return ToolResult(message=f"Nimic găsit în discuțiile și fișierele proiectului pentru „{query}”.")
        lines = [
            f"- [fișier KB {h['file_id']} · {h['filename']} · {_day(h['ts'])}] {h['snippet']}"
            for h in kb_hits
        ] + [
            f"- [{h['chat_title']} · {_day(h['ts'])} · {h['role']}] {h['snippet']}"
            for h in hits
        ]
        return ToolResult(message="Fragmente găsite:\n" + "\n".join(lines))


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
