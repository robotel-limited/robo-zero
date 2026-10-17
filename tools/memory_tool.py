"""Memorie prin gateway-ul HTTP robo-dimag (etapa 4).

Agentul standalone nu vede MCP-ul robo-dimag (acela e doar în Claude Code) —
vorbește cu gateway-ul HTTP local pe 127.0.0.1:8930 (POST /recall, /remember).
Două tool-uri separate (recall/remember), fiecare un singur apel HTTP.
"""

from pathlib import Path
from typing import Any

import httpx
import yaml

from tools.tool_base import Tool, ToolResult

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_MEMORY_CFG = _SETTINGS["memory"]
_GATEWAY_URL = _MEMORY_CFG["gateway_url"]
_TIMEOUT = _MEMORY_CFG["timeout_seconds"]
_CALLER = "agent-zero-light"


class MemoryRecall(Tool):
    """Tool pentru interogarea memoriei robo-dimag (recall)."""

    name: str = "memory_recall"
    description: str = (
        "Caută în memoria persistentă robo-dimag (decizii, context de proiect, "
        "lecții verificate) relevante pentru o întrebare sau sarcină."
    )
    parameters: dict[str, Any] = {
        "query": {
            "type": "string",
            "description": "Întrebarea sau sarcina pentru care se caută context relevant.",
        }
    }
    required: list[str] = ["query"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(message="Eroare: parametrul 'query' e obligatoriu.")

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_GATEWAY_URL}/recall",
                    json={"task": query, "caller": _CALLER},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            return ToolResult(message=f"Eroare la interogarea memoriei (recall): {e}")

        items = data.get("data", [])
        if not items:
            return ToolResult(message="Nimic relevant găsit în memorie.")

        lines = [f"- {item.get('text', '')}" for item in items]
        return ToolResult(message="\n".join(lines))


class MemoryRemember(Tool):
    """Tool pentru salvarea unui fapt/decizie în memoria robo-dimag (remember)."""

    name: str = "memory_remember"
    description: str = (
        "Salvează un fapt, o decizie sau o lecție verificată în memoria persistentă "
        "robo-dimag, ca să fie disponibilă în sesiuni viitoare."
    )
    parameters: dict[str, Any] = {
        "statement": {
            "type": "string",
            "description": "Afirmația de salvat (un fapt sau o decizie clară, autonomă).",
        },
        "type": {
            "type": "string",
            "description": "Tipul intrării: 'working' (date de lucru), 'lesson' (lecție verificată), "
            "'decision' sau 'note'.",
        },
    }
    required: list[str] = ["statement", "type"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        statement = kwargs.get("statement", "")
        mem_type = kwargs.get("type", "")
        if not statement or not mem_type:
            return ToolResult(message="Eroare: parametrii 'statement' și 'type' sunt obligatorii.")

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_GATEWAY_URL}/remember",
                    json={"statement": statement, "type": mem_type, "caller": _CALLER},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            return ToolResult(message=f"Eroare la salvarea în memorie (remember): {e}")

        return ToolResult(message=f"Salvat în memorie: {data.get('id', '(fără id)')}")
