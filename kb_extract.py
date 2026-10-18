"""Extragerea textului din fișierele bazei de cunoștințe a unui proiect:
text/cod direct, PDF prin pdftotext (poppler), DOCX din XML-ul intern,
imagini prin modelul de chat (deepseek-flash acceptă imagini — OCR +
descriere). Un PDF scanat (fără strat de text) e trecut pagină cu pagină
prin același model, până la o limită de pagini."""

import asyncio
import re
import tempfile
import zipfile
from pathlib import Path

import attachments
import llm

_PDF_SCAN_MAX_PAGES = 10
_PDF_MIN_TEXT_CHARS = 50
_TEXT_EXTENSIONS = {
    "txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "xml", "html", "htm",
    "log", "ini", "toml", "conf", "py", "js", "ts", "sh", "sql", "css", "java", "c", "h",
    "cpp", "go", "rs", "rb", "php",
}
_IMAGE_EXTENSIONS = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "webp": "image/webp", "gif": "image/gif"}

_VISION_PROMPT = (
    "Ești modulul de extragere al unei baze de cunoștințe. Pentru imaginea de mai jos:\n"
    "1. Transcrie EXACT tot textul vizibil (OCR), păstrând cifrele, unitățile și structura "
    "(tabele ca rânduri cu valori separate prin ' | ').\n"
    "2. Apoi, după un rând 'Descriere:', descrie pe scurt ce conține imaginea (tip document/"
    "captură, subiect, elemente importante).\n"
    "Răspunde în română, fără alt text."
)


class ExtractError(Exception):
    """Fișier neacceptat sau extragere eșuată — mesajul ajunge în UI."""


def _ext(filename: str) -> str:
    return Path(filename).suffix.lstrip(".").lower()


async def describe_image(data: bytes, mime: str) -> str:
    response = await llm.chat(
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": attachments.data_url(data, mime)}},
            ],
        }],
        purpose="kb_extract",
    )
    return (response.content or "").strip()


async def _run(*cmd: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise ExtractError(f"{cmd[0]} a eșuat: {err.decode(errors='replace')[:300]}")
    return out


async def _pdf(path: Path) -> str:
    text = (await _run("pdftotext", "-layout", str(path), "-")).decode("utf-8", errors="replace")
    if len(text.strip()) >= _PDF_MIN_TEXT_CHARS:
        return text
    # PDF scanat: randează paginile ca imagini și le trece prin modelul vision
    with tempfile.TemporaryDirectory() as tmp:
        await _run("pdftoppm", "-jpeg", "-r", "150", "-l", str(_PDF_SCAN_MAX_PAGES), str(path), f"{tmp}/p")
        pages = sorted(Path(tmp).glob("p*.jpg"))
        if not pages:
            raise ExtractError("PDF fără text și fără pagini randabile.")
        parts = []
        for i, page in enumerate(pages, 1):
            parts.append(f"--- Pagina {i} ---\n{await describe_image(page.read_bytes(), 'image/jpeg')}")
    return "\n\n".join(parts)


def _docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ExtractError(f"DOCX invalid: {e}")
    xml = re.sub(r"</w:p>", "\n\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return text


async def extract(path: Path, filename: str, mime: str) -> str:
    """Textul complet al fișierului. Ridică ExtractError pentru tipuri
    neacceptate sau fișiere fără conținut extras."""
    ext = _ext(filename)
    if ext in _IMAGE_EXTENSIONS or mime in attachments.IMAGE_TYPES:
        text = await describe_image(path.read_bytes(), _IMAGE_EXTENSIONS.get(ext, mime))
    elif ext == "pdf" or mime == "application/pdf":
        text = await _pdf(path)
    elif ext == "docx":
        text = _docx(path)
    elif ext in _TEXT_EXTENSIONS or mime.startswith("text/"):
        text = path.read_bytes().decode("utf-8", errors="replace")
    else:
        raise ExtractError(
            f"Tip de fișier neacceptat (.{ext or '?'}). Acceptate: text/cod, PDF, DOCX, imagini."
        )
    if not text.strip():
        raise ExtractError("Nu s-a putut extrage niciun text.")
    return text
