"""Browser interactiv: Playwright pilotează, Holo4 (vision, gratuit, tier
10 RPM) localizează elementele din ultimul screenshot — pattern validat la
cercetarea Holo4 (login local prin page.set_content, coordonate corecte din
prima, rate limiter ~9/min sub limita reală). Credențialele tastate de
agent nu ajung niciodată la Holo4 — doar imaginea paginii.

O singură sesiune de browser per rulare de chat, pornită leneș la primul
apel, partajată cu sub-agenții aceleiași rulări prin BrowserBox (container
mutabil trecut prin referință de call_subordinate.py) și închisă la final
de agent.py (depth 0).
"""

import base64
import json
import os
import re
import time
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

_BROWSER_CFG = _SETTINGS["browser"]
_HOLO4_ENDPOINT = _BROWSER_CFG["holo4_endpoint"]
_HOLO4_MODEL = _BROWSER_CFG["holo4_model"]
_HOLO4_TIMEOUT = _BROWSER_CFG["holo4_timeout_seconds"]
_RATE_LIMIT_PER_MINUTE = _BROWSER_CFG["rate_limit_per_minute"]
_NAV_TIMEOUT_MS = _BROWSER_CFG["navigation_timeout_seconds"] * 1000
_DEFAULT_WORKDIR = _SETTINGS["agent"]["workdir"]


class BrowserToolError(Exception):
    """Eroare definitivă în browser_tool (Holo4 fără coordonate parsabile, etc.)."""


def _holo4_api_key() -> str:
    key = os.environ.get("HOLO4_API_KEY")
    if not key:
        raise BrowserToolError("HOLO4_API_KEY nu e setat (verifică .env).")
    return key


class _RateLimiter:
    """Fereastră glisantă de 60s, sub limita reală a tier-ului gratuit Holo4
    (10 RPM) — vezi cercetarea Holo4: lovit 429 exact la a 10-a cerere."""

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._calls: list[float] = []

    def wait_seconds(self) -> float:
        now = time.monotonic()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) < self._max:
            self._calls.append(now)
            return 0.0
        wait = 60 - (now - self._calls[0])
        self._calls.append(now + wait)
        return max(wait, 0.0)


_holo4_limiter = _RateLimiter(_RATE_LIMIT_PER_MINUTE)


async def _locate_element(
    screenshot_bytes: bytes, target: str, viewport_width: int, viewport_height: int
) -> tuple[float, float]:
    """Trimite screenshot-ul curent + descrierea țintei la Holo4, întoarce
    coordonatele pixel (x, y) ale centrului elementului, scalate la
    dimensiunea reală a paginii.

    Verificat empiric (etapa 5): Holo4 nu întoarce pixeli literali, ci
    coordonate normalizate pe o scală 0-1000 (indiferent de dimensiunea
    imaginii trimise) — pe un viewport 1280x720, un buton la pixelul real
    (86, 203) a fost raportat ca (67, 277); scalat (67/1000*1280,
    277/1000*720) = (85.8, 199.4), care cade exact în interiorul butonului.
    Fără scalare, click-urile aterizau constant în afara elementului țintă."""
    import asyncio

    wait = _holo4_limiter.wait_seconds()
    if wait > 0:
        await asyncio.sleep(wait)

    b64 = base64.b64encode(screenshot_bytes).decode("ascii")
    prompt = (
        f'În imaginea dată (screenshot al unei pagini web), găsește elementul: "{target}". '
        'Răspunde STRICT cu un obiect JSON de forma {"x": <int>, "y": <int>} — coordonatele '
        "centrului elementului, normalizate pe o scală 0-1000 pe fiecare axă "
        "(0=marginea stângă/de sus, 1000=marginea dreaptă/de jos a imaginii) — fără alt text."
    )
    payload = {
        "model": _HOLO4_MODEL,
        "max_tokens": 300,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
    }
    async with httpx.AsyncClient(timeout=_HOLO4_TIMEOUT) as client:
        resp = await client.post(
            _HOLO4_ENDPOINT,
            headers={"Authorization": f"Bearer {_holo4_api_key()}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise BrowserToolError(f"Răspuns Holo4 fără conținut utilizabil: {e}")

    match = re.search(r"\{[^{}]*\}", content)
    if not match:
        raise BrowserToolError(f"Holo4 nu a întors coordonate parsabile: {content!r}")
    coords = json.loads(match.group(0))
    norm_x, norm_y = float(coords["x"]), float(coords["y"])
    return (norm_x / 1000.0) * viewport_width, (norm_y / 1000.0) * viewport_height


class BrowserSession:
    """O instanță Playwright reală (playwright + browser + pagină). Pornită
    leneș, închisă explicit — nu ține resurse dacă browser_tool nu e folosit."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self.browser: Any = None
        self.page: Any = None

    async def ensure_started(self) -> None:
        if self.page is not None:
            return
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        self.page = await self.browser.new_page()

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self.browser = None
        self.page = None
        self._playwright = None


class BrowserBox:
    """Container mutabil, partajat prin referință între un Agent și
    sub-agenții lui (vezi call_subordinate.py), ca toți să vadă aceeași
    sesiune de browser a rulării curente."""

    def __init__(self) -> None:
        self.session: BrowserSession | None = None


class BrowserTool(Tool):
    """Un singur tool cu mai multe acțiuni (convenție a0 pentru browser),
    nu câte un tool per acțiune — modelul alege acțiunea prin parametru."""

    name: str = "browser"
    description: str = (
        "Pilotează un browser real: open (navighează la un URL), click (click pe un "
        "element descris în text, localizat vizual), type (scrie text într-un câmp "
        "descris), screenshot (salvează imaginea curentă a paginii), close (închide "
        "browserul). Pentru acțiuni ireversibile (submit de plată, ștergere) setează "
        "irreversible=true — va cere aprobare înainte de execuție."
    )
    parameters: dict[str, Any] = {
        "action": {
            "type": "string",
            "enum": ["open", "click", "type", "screenshot", "close"],
            "description": "Acțiunea de executat.",
        },
        "url": {"type": "string", "description": "URL-ul de deschis (doar pentru action=open)."},
        "target": {
            "type": "string",
            "description": "Descrierea elementului vizat, ex. 'butonul Login' (pentru click/type).",
        },
        "text": {"type": "string", "description": "Textul de tastat (pentru action=type)."},
        "irreversible": {
            "type": "boolean",
            "description": "True dacă acțiunea e ireversibilă (submit plată, ștergere) — cere aprobare.",
        },
    }
    required: list[str] = ["action"]

    def pre_check(self, args: dict[str, Any]) -> bool:
        """Confirmare în UI pentru acțiuni marcate ireversibile de model —
        aici nu există un denylist de comenzi ca la shell, judecata e a
        modelului (conform planului, pattern Holo4 validat)."""
        return bool(args.get("irreversible"))

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "")
        box: BrowserBox = self.agent.browser_box

        if action == "open":
            return await self._open(box, kwargs.get("url", ""))

        if box.session is None or box.session.page is None:
            return ToolResult(message="Eroare: browserul nu e pornit — folosește mai întâi action=open.")
        page = box.session.page

        try:
            if action == "click":
                return await self._click(page, kwargs.get("target", ""))
            if action == "type":
                return await self._type(page, kwargs.get("target", ""), kwargs.get("text", ""))
            if action == "screenshot":
                return await self._screenshot(page)
            if action == "close":
                await box.session.close()
                box.session = None
                return ToolResult(message="Browser închis.")
        except BrowserToolError as e:
            return ToolResult(message=f"Eroare: {e}")
        except Exception as e:
            return ToolResult(message=f"Eroare neprevăzută în browser_tool ('{action}'): {e}")

        return ToolResult(message=f"Eroare: acțiune necunoscută '{action}'.")

    async def _open(self, box: BrowserBox, url: str) -> ToolResult:
        if not url:
            return ToolResult(message="Eroare: parametrul 'url' e obligatoriu pentru action=open.")
        if box.session is None:
            box.session = BrowserSession()
        try:
            await box.session.ensure_started()
            await box.session.page.goto(url, timeout=_NAV_TIMEOUT_MS)
            title = await box.session.page.title()
        except Exception as e:
            return ToolResult(message=f"Eroare la navigare către '{url}': {e}")
        return ToolResult(message=f"Pagină deschisă: '{title}' ({url})")

    async def _locate(self, page: Any, target: str) -> tuple[float, float]:
        screenshot = await page.screenshot(type="jpeg")
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        return await _locate_element(screenshot, target, viewport["width"], viewport["height"])

    async def _click(self, page: Any, target: str) -> ToolResult:
        if not target:
            return ToolResult(message="Eroare: parametrul 'target' e obligatoriu pentru action=click.")
        x, y = await self._locate(page, target)
        await page.mouse.click(x, y)
        return ToolResult(message=f"Click efectuat pe '{target}' la ({x:.0f}, {y:.0f}).")

    async def _type(self, page: Any, target: str, text: str) -> ToolResult:
        if not target or not text:
            return ToolResult(message="Eroare: 'target' și 'text' sunt obligatorii pentru action=type.")
        x, y = await self._locate(page, target)
        await page.mouse.click(x, y)
        await page.keyboard.type(text)
        return ToolResult(message=f"Text tastat în '{target}'.")

    async def _screenshot(self, page: Any) -> ToolResult:
        workdir = getattr(self.agent, "workdir", _DEFAULT_WORKDIR)
        images_dir = Path(workdir) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.jpg"
        await page.screenshot(path=str(images_dir / filename), type="jpeg")
        return ToolResult(message=f"Screenshot salvat: /files/images/{filename}")
