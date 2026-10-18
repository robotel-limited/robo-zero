"""Imagini atașate în chat (capturi de ecran lipite, fișiere trase în caseta
de mesaj): salvate în workdir/uploads/ și servite prin /files ca orice
fișier al agentului. În istoricul LLM rămâne doar calea locală (/files/...);
abia la trimiterea către model (llm.chat) calea devine data URL base64 —
altfel JSON-ul fiecărui chat ar crește cu megaocteți la fiecare captură."""

import base64
import copy
import uuid
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _WORKDIR = Path(yaml.safe_load(f)["agent"]["workdir"])

UPLOADS_DIR = _WORKDIR / "uploads"
URL_PREFIX = "/files/uploads/"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
_EXT_TO_MIME = {ext: mime for mime, ext in IMAGE_TYPES.items()} | {"jpeg": "image/jpeg"}


def save_image(data: bytes, content_type: str) -> str:
    """Salvează imaginea și întoarce URL-ul ei local (/files/uploads/<id>.<ext>).
    Ridică ValueError pentru tip neacceptat sau fișier prea mare."""
    mime = content_type.split(";")[0].strip().lower()
    if mime not in IMAGE_TYPES:
        raise ValueError(f"Tip de imagine neacceptat: {mime or 'necunoscut'}.")
    if not data:
        raise ValueError("Fișier gol.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Imagine prea mare (max {MAX_IMAGE_BYTES // (1024 * 1024)} MB).")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{IMAGE_TYPES[mime]}"
    (UPLOADS_DIR / name).write_bytes(data)
    return URL_PREFIX + name


def is_local_image(url: str) -> bool:
    return isinstance(url, str) and url.startswith(URL_PREFIX) and _local_path(url) is not None


def _local_path(url: str) -> Path | None:
    name = url[len(URL_PREFIX):]
    # doar nume simple, fără subdirectoare sau ".." — URL-ul vine de la client
    if not name or "/" in name or name.startswith("."):
        return None
    path = UPLOADS_DIR / name
    return path if path.is_file() else None


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _to_data_url(url: str) -> str:
    path = _local_path(url)
    if path is None:
        return url
    mime = _EXT_TO_MIME.get(path.suffix.lstrip(".").lower(), "image/png")
    return data_url(path.read_bytes(), mime)


def user_content(text: str, image_urls: list[str]) -> str | list[dict[str, Any]]:
    """Conținutul unui mesaj `user`: text simplu, sau listă multimodală
    (text + image_url cu cale locală) dacă are imagini."""
    if not image_urls:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text or "(vezi imaginea atașată)"}]
    parts += [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
    return parts


def inline_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copie a mesajelor în care căile locale ale imaginilor devin data URL.
    Istoricul original (persistat pe disc) rămâne neatins."""
    if not any(isinstance(m.get("content"), list) for m in messages):
        return messages
    result = []
    for message in messages:
        if isinstance(message.get("content"), list):
            message = copy.deepcopy(message)
            for part in message["content"]:
                if part.get("type") == "image_url":
                    part["image_url"]["url"] = _to_data_url(part["image_url"].get("url", ""))
        result.append(message)
    return result


def text_of(content: Any) -> str:
    """Textul unui `content` (string sau listă multimodală) — pentru rezumare
    și estimări, unde imaginile apar doar ca marcaj."""
    if isinstance(content, list):
        texts = [p.get("text", "") for p in content if p.get("type") == "text"]
        images = sum(1 for p in content if p.get("type") == "image_url")
        return " ".join(texts) + (f" [{images} imagine/i atașate]" if images else "")
    return str(content or "")
