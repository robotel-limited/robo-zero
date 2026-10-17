"""Interogare RAG prin robo-rag (etapa: integrare a0 <-> robo-rag).

Vorbește cu robo-rag pe tenant-ul low-privilege "internal-agents" (creat
2026-09-24 special pentru agenți, nu tenant-ul personal al lui Sorin), prin
endpoint-ul OpenAI-compatible POST /t/{tenant_id}/v1/chat/completions
(RAG augmentat, `model` = collection_id). Autentificare prin login email/
parolă -> token de sesiune (expiră, nu e cheie statică) -- re-logăm la
fiecare apel în loc să cache-uim tokenul, mai simplu și robust decât un
cache cu expirare de urmărit, costul e un singur request suplimentar.
"""

import os
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

from tools.tool_base import Tool, ToolResult

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_CFG = _SETTINGS["robo_rag"]
_BASE_URL = os.environ.get("ROBORAG_BASE_URL", _CFG.get("base_url", ""))
_TENANT_ID = os.environ.get("ROBORAG_TENANT_ID", "")
_COLLECTION_ID = os.environ.get("ROBORAG_COLLECTION_ID", "")
_EMAIL = os.environ.get("ROBORAG_ADMIN_EMAIL", "")
_PASSWORD = os.environ.get("ROBORAG_ADMIN_PASSWORD", "")
_PROVIDER = _CFG["provider"]
_TIMEOUT = _CFG["timeout_seconds"]


class RoboRagQuery(Tool):
    """Tool pentru interogarea bazei de cunoștințe robo-rag (RAG, nu memorie de agent)."""

    name: str = "robo_rag_query"
    description: str = (
        "Pune o întrebare bazei de cunoștințe robo-rag (documente ingerate, căutare "
        "hibridă + răspuns LLM augmentat). Diferit de memory_recall: robo-rag conține "
        "documente/colecții încărcate explicit, nu decizii/context de proiect."
    )
    parameters: dict[str, Any] = {
        "question": {
            "type": "string",
            "description": "Întrebarea de pus bazei de cunoștințe.",
        }
    }
    required: list[str] = ["question"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        question = kwargs.get("question", "")
        if not question:
            return ToolResult(message="Eroare: parametrul 'question' e obligatoriu.")
        if not (_TENANT_ID and _EMAIL and _PASSWORD and _COLLECTION_ID):
            return ToolResult(
                message="Eroare: robo_rag_tool nu are configurate "
                "ROBORAG_TENANT_ID/ROBORAG_ADMIN_EMAIL/ROBORAG_ADMIN_PASSWORD/"
                "ROBORAG_COLLECTION_ID în .env."
            )

        try:
            async with httpx.AsyncClient(base_url=_BASE_URL, timeout=_TIMEOUT) as client:
                login_resp = await client.post(
                    f"/t/{_TENANT_ID}/auth/login",
                    json={"email": _EMAIL, "password": _PASSWORD},
                )
                login_resp.raise_for_status()
                token = login_resp.json()["token"]

                chat_resp = await client.post(
                    f"/t/{_TENANT_ID}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "model": _COLLECTION_ID,
                        "provider": _PROVIDER,
                        "messages": [{"role": "user", "content": question}],
                    },
                )
                chat_resp.raise_for_status()
                data = chat_resp.json()
        except httpx.HTTPStatusError as e:
            return ToolResult(message=f"Eroare robo-rag ({e.response.status_code}): {e.response.text}")
        except httpx.HTTPError as e:
            return ToolResult(message=f"Eroare de rețea la robo-rag: {e}")

        try:
            answer = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return ToolResult(message=f"Răspuns neașteptat de la robo-rag: {data}")

        return ToolResult(message=answer)
