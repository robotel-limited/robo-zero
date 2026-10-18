"""Harta compactă a unui folder legat de proiect, pentru system prompt:
arbore pe 2 niveluri + începutul README-ului. Se regenerează doar când se
schimbă semnătura (nume + mtime pe cele 2 niveluri, plus README), ca textul
din prompt să rămână identic între chat-uri (servit din cache-ul DeepSeek)."""

import hashlib
import os
from typing import Any

import path_access
import project_folders_store as folders

SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
             ".pytest_cache", ".cache", "dist", "build", ".idea", ".vscode"}
_TOP_MAX = 40          # intrări afișate pe nivelul 1
_CHILDREN_MAX = 12     # nume afișate per subdirector
_README_CHARS = 600
_MAP_CHARS = 1800      # plafon per folder
_README_NAMES = ("README.md", "README.MD", "readme.md", "README.txt", "README", "README.rst")


def _visible(entry: os.DirEntry) -> bool:
    if entry.name.startswith(".") or path_access.is_secret(entry.path):
        return False
    return not (entry.is_dir(follow_symlinks=False) and entry.name in SKIP_DIRS)


def _scan(path: str) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return sorted((e for e in it if _visible(e)), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return []


def _mtime(entry: os.DirEntry) -> float:
    try:
        return entry.stat(follow_symlinks=False).st_mtime
    except OSError:
        return 0.0


def _readme(path: str) -> tuple[str, str]:
    """(nume, început) README-ului de la rădăcina folderului, dacă există."""
    for name in _README_NAMES:
        full = os.path.join(path, name)
        if os.path.isfile(full):
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    return name, f.read(_README_CHARS).strip()
            except OSError:
                return name, ""
    return "", ""


def _build(folder: dict[str, Any]) -> tuple[str, str]:
    """(semnătura, textul hărții) dintr-o singură scanare."""
    root = folder["path"]
    sig = hashlib.sha1()
    top = _scan(root)
    lines: list[str] = []
    for entry in top:
        sig.update(f"{entry.name}|{_mtime(entry)}\n".encode())
        if not entry.is_dir():
            if len(lines) < _TOP_MAX:
                lines.append(f"- {entry.name}")
            continue
        children = _scan(entry.path)
        for child in children:
            sig.update(f"{entry.name}/{child.name}|{_mtime(child)}\n".encode())
        if len(lines) < _TOP_MAX:
            names = [c.name + ("/" if c.is_dir() else "") for c in children[:_CHILDREN_MAX]]
            more = f", … (+{len(children) - _CHILDREN_MAX})" if len(children) > _CHILDREN_MAX else ""
            lines.append(f"- {entry.name}/: {', '.join(names)}{more}" if names else f"- {entry.name}/ (gol)")
    if len(top) > _TOP_MAX:
        lines.append(f"- … (+{len(top) - _TOP_MAX} intrări)")
    readme_name, readme = _readme(root)
    sig.update(readme.encode())
    text = "\n".join(lines) or "(folder gol)"
    if readme:
        text += f"\n{readme_name} (început):\n" + "\n".join(f"> {l}" for l in readme.splitlines())
    if len(text) > _MAP_CHARS:
        text = text[:_MAP_CHARS] + "\n… [hartă trunchiată — explorează cu fs_list]"
    return sig.hexdigest(), text


def get_map(folder: dict[str, Any]) -> str:
    """Harta memorată, regenerată doar dacă folderul s-a schimbat."""
    if not os.path.isdir(folder["path"]):
        return "(folderul nu mai există pe disc)"
    sig, text = _build(folder)
    if sig != folder["map_sig"]:
        folders.set_map(folder["id"], sig, text)
    return text
