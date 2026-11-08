"""Căutare pe internet prin Exa (API făcut pentru agenți: JSON, fără pagină de
rezultate, deci fără captcha). Cu EXA_API_KEY în .env folosește API-ul oficial;
fără cheie, endpoint-ul MCP public (limite nedocumentate)."""

import json
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from tools.tool_base import Tool, ToolResult

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_TIMEOUT = 30
_MAX_RESULTS = 10
_HIGHLIGHT_CHARS = 1500


class WebSearch(Tool):
    name: str = "web_search"
    description: str = (
        "Caută pe internet și întoarce rezultate (titlu, URL, dată, fragment relevant). "
        "Folosește-l ÎNTOTDEAUNA pentru căutări, în locul deschiderii Google/Bing/DuckDuckGo "
        "în browser (acelea dau captcha). Apoi citește paginile utile cu web_read."
    )
    parameters: dict[str, Any] = {
        "query": {"type": "string", "description": "Interogarea de căutare, în limbaj natural."},
        "num_results": {"type": "integer", "description": "Câte rezultate (1-10, implicit 5)."},
    }
    required: list[str] = ["query"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(message="Eroare: parametrul 'query' e obligatoriu.")
        n = max(1, min(int(kwargs.get("num_results") or 5), _MAX_RESULTS))
        key = os.environ.get("EXA_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                if key:
                    return await self._api(client, key, query, n)
                return await self._public_mcp(client, query, n)
        except httpx.HTTPError as e:
            return ToolResult(message=f"Eroare căutare Exa: {e}")

    async def _api(self, client: httpx.AsyncClient, key: str, query: str, n: int) -> ToolResult:
        resp = await client.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": key},
            json={"query": query, "numResults": n, "contents": {"highlights": True}},
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return ToolResult(message="Niciun rezultat.")
        blocks = []
        for r in results:
            hl = " … ".join(r.get("highlights") or [])[:_HIGHLIGHT_CHARS]
            blocks.append(
                f"Title: {r.get('title') or ''}\nURL: {r.get('url')}\n"
                f"Published: {r.get('publishedDate') or 'N/A'}\n{hl}"
            )
        return ToolResult(message="\n\n---\n\n".join(blocks))

    async def _public_mcp(self, client: httpx.AsyncClient, query: str, n: int) -> ToolResult:
        resp = await client.post(
            "https://mcp.exa.ai/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "web_search_exa", "arguments": {"query": query, "numResults": n}},
            },
        )
        resp.raise_for_status()
        payload = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:])
        if payload is None:
            payload = resp.json()
        if "error" in payload:
            return ToolResult(message=f"Eroare Exa: {payload['error'].get('message')}")
        texts = [c.get("text", "") for c in payload.get("result", {}).get("content", [])]
        return ToolResult(message="\n".join(texts) or "Niciun rezultat.")
