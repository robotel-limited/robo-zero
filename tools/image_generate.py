"""Generare de imagini prin NVIDIA FLUX (black-forest-labs/flux.2-klein-4b),
același provider ca Hermes (etapa 4). Apel HTTP mic, fără import din Hermes.

Format confirmat prin test direct: POST {endpoint}/{model} întoarce
{"artifacts": [{"base64": "<jpeg>", "finishReason": "SUCCESS", "seed": ...}]}.
"""

import base64
import os
import uuid
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

_IMAGE_CFG = _SETTINGS["image"]
_ENDPOINT = _IMAGE_CFG["endpoint"]
_MODEL = _IMAGE_CFG["model"]
_TIMEOUT = _IMAGE_CFG["timeout_seconds"]


class ImageGenerateError(Exception):
    """Eroare definitivă la generarea imaginii (fără retry — nu e tranzitorie ca la LLM)."""


def _api_key() -> str:
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise ImageGenerateError("NVIDIA_API_KEY nu e setat (verifică .env).")
    return key


class ImageGenerate(Tool):
    """Tool pentru generarea unei imagini dintr-un prompt text."""

    name: str = "image_generate"
    description: str = (
        "Generează o imagine dintr-o descriere text și o salvează în workdir/images/. "
        "Rezultatul include calea servită prin webUI, afișată direct în chat."
    )
    parameters: dict[str, Any] = {
        "prompt": {
            "type": "string",
            "description": "Descrierea imaginii de generat.",
        }
    }
    required: list[str] = ["prompt"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return ToolResult(message="Eroare: parametrul 'prompt' e obligatoriu.")

        # comun tuturor chat-urilor (nu workdir-ul proiectului): UI-ul o afișează din /files/images/
        workdir = _SETTINGS["agent"]["workdir"]
        images_dir = Path(workdir) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_ENDPOINT}/{_MODEL}",
                    headers={
                        "Authorization": f"Bearer {_api_key()}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json={"prompt": prompt},
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ImageGenerateError) as e:
            return ToolResult(message=f"Eroare la generarea imaginii: {e}")

        artifacts = data.get("artifacts") or []
        if not artifacts or not artifacts[0].get("base64"):
            return ToolResult(message="Eroare: răspunsul NVIDIA nu conține o imagine.")

        artifact = artifacts[0]
        if artifact.get("finishReason") not in (None, "SUCCESS"):
            return ToolResult(message=f"Generare eșuată: {artifact.get('finishReason')}")

        image_bytes = base64.b64decode(artifact["base64"])
        filename = f"{uuid.uuid4().hex}.jpg"
        (images_dir / filename).write_bytes(image_bytes)

        return ToolResult(message=f"Imagine generată: /files/images/{filename}")
