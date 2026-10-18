"""Căutare full-text în arhiva brută a proiectului chat-ului curent (toate
chat-urile proiectului) — pentru detalii exacte pe care fișa și rezumatele
injectate în context nu le mai conțin. Înregistrat doar în chat-urile legate
de un proiect (vezi Agent._build_tools)."""

from datetime import datetime
from typing import Any

import project_archive
from tools.tool_base import Tool, ToolResult


class ProjectSearch(Tool):
    name: str = "project_search"
    description: str = (
        "Caută în textul integral al tuturor discuțiilor din proiectul curent "
        "(mesaje utilizator, răspunsuri, rezultate de tool-uri). Folosește-l "
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
        if not hits:
            return ToolResult(message=f"Nimic găsit în discuțiile proiectului pentru „{query}”.")
        lines = [
            f"- [{h['chat_title']} · {datetime.fromtimestamp(h['ts']).strftime('%Y-%m-%d')} · "
            f"{h['role']}] {h['snippet']}"
            for h in hits
        ]
        return ToolResult(message="Fragmente găsite:\n" + "\n".join(lines))
