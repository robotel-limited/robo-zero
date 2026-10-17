"""Citirea conținutului unei pagini web (etapa 5). Rută principală: robo-crawl
(127.0.0.1:8112, markdown deja extras, respectă robots.txt). Dacă robo-crawl
e indisponibil (serviciu căzut, nu o eroare de business ca robots.txt-deny),
fallback direct prin Playwright: goto() + extrage textul vizibil al paginii.
"""

from pathlib import Path
from typing import Any

import httpx
import yaml

from tools.tool_base import Tool, ToolResult

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_WEB_CFG = _SETTINGS["web"]
_ROBO_CRAWL_URL = _WEB_CFG["robo_crawl_url"]
_TIMEOUT = _WEB_CFG["timeout_seconds"]
_FALLBACK_TEXT_LIMIT = _WEB_CFG["fallback_text_limit_chars"]


class WebRead(Tool):
    """Citește o pagină web și întoarce conținutul ca text/markdown."""

    name: str = "web_read"
    description: str = (
        "Citește conținutul unei pagini web la un URL dat și îl întoarce ca text. "
        "Folosește robo-crawl (extragere curată, respectă robots.txt); dacă serviciul "
        "e indisponibil, citește direct prin browser."
    )
    parameters: dict[str, Any] = {
        "url": {
            "type": "string",
            "description": "URL-ul paginii de citit.",
        }
    }
    required: list[str] = ["url"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        url = kwargs.get("url", "")
        if not url:
            return ToolResult(message="Eroare: parametrul 'url' e obligatoriu.")

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_ROBO_CRAWL_URL}/v1/scrape",
                    json={"url": url, "formats": ["markdown"]},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError:
            # robo-crawl căzut (serviciu nepornit/timeout de conexiune) — fallback.
            return await self._read_via_browser(url)

        if not data.get("ok"):
            error = data.get("error", {})
            return ToolResult(
                message=f"Eroare robo-crawl ({error.get('code')}): {error.get('message')}"
            )

        markdown = data.get("markdown") or "(pagină fără conținut text extras)"
        title = data.get("meta", {}).get("title") or ""
        return ToolResult(message=f"# {title}\n\n{markdown}" if title else markdown)

    async def _read_via_browser(self, url: str) -> ToolResult:
        """Fallback: Playwright direct, fără robo-crawl. Doar citire de text —
        nu respectă robots.txt (nu are acces la politica robo-crawl), motiv
        pentru care rămâne fallback, nu rută principală."""
        from playwright.async_api import async_playwright

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                try:
                    page = await browser.new_page()
                    await page.goto(url, timeout=_TIMEOUT * 1000, wait_until="load")
                    text = await page.inner_text("body")
                    title = await page.title()
                finally:
                    await browser.close()
        except Exception as e:
            return ToolResult(
                message=f"Eroare: robo-crawl indisponibil și citirea directă prin browser a eșuat: {e}"
            )

        text = text.strip()
        if len(text) > _FALLBACK_TEXT_LIMIT:
            text = text[:_FALLBACK_TEXT_LIMIT] + "\n... [trunchiat]"
        return ToolResult(message=f"[fallback browser, robo-crawl indisponibil]\n# {title}\n\n{text}")
