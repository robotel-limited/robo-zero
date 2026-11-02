"""Listare read-only de directoare pentru picker-ul "Browse…" (pagina /projects).

Doar citire: rădăcini permise din conf/settings.yaml, listare directoare,
urcare controlată. Fără scrieri, fără previzualizări, fără ștergeri.
"""

import os
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

router = APIRouter()

_DEFAULT_ROOTS = ["/srv", "/var/www", "/home/sorin", "/mnt", "/media"]


def _load_roots() -> list:
    """Rădăcini permise din conf/settings.yaml (opțional); la orice eroare, cele implicite."""
    try:
        cfg_path = Path(__file__).resolve().parent / "conf" / "settings.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        roots = list((cfg.get("fs_browse") or {}).get("roots") or [])
        if not roots:
            roots = list(_DEFAULT_ROOTS)
    except Exception:
        roots = list(_DEFAULT_ROOTS)
    # păstrăm doar rădăcinile care există pe disc, realpath-uite
    kept = [os.path.realpath(r) for r in roots if os.path.isdir(r)]
    if not kept:  # nicio rădăcină validă -> cele implicite care există
        kept = [os.path.realpath(r) for r in _DEFAULT_ROOTS if os.path.isdir(r)]
    return kept


ROOTS = _load_roots()


def _allowed(real: str) -> bool:
    """True dacă `real` e chiar o rădăcină sau înăuntrul uneia."""
    for root in ROOTS:
        try:
            if os.path.commonpath([root, real]) == root:
                return True
        except ValueError:
            continue
    return False


@router.get("/api/fs/browse")
async def browse(path: str = ""):
    """Listează directoarele din `path` (goală = prima rădăcină), doar citire."""
    if not path:
        if not ROOTS:
            raise HTTPException(status_code=403, detail="Calea nu e într-o zonă permisă.")
        real = ROOTS[0]
    else:
        real = os.path.realpath(path)
    if not _allowed(real):
        raise HTTPException(status_code=403, detail="Calea nu e într-o zonă permisă.")
    if not os.path.isdir(real):
        raise HTTPException(status_code=404, detail="Director inexistent.")

    try:
        entries = []
        with os.scandir(real) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                if entry.name.startswith("."):
                    continue
                full = os.path.realpath(entry.path)
                entries.append(
                    {"name": entry.name, "path": full, "readable": os.access(full, os.R_OK)}
                )
    except OSError:
        raise HTTPException(status_code=403, detail="Directorul nu poate fi citit.")
    entries.sort(key=lambda e: e["name"].casefold())

    # urcăm doar până într-o rădăcină permisă, altfel UI-ul nu poate ieși din zonă
    parent = os.path.realpath(os.path.dirname(real))
    if not _allowed(parent):
        parent = None

    return {"path": real, "parent": parent, "roots": ROOTS, "entries": entries}
