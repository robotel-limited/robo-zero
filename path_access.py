"""Unde poate lucra un agent pe disc: workdir-ul propriu (separat per
proiect) plus folderele legate de proiect (ro = doar citire, rw = și
scriere). Fișierele cu secrete nu se citesc nicăieri prin fs_read.

Shell-ul nu e restricționat aici: modul ro e impus doar la fs_write,
iar pentru shell rămâne regulă în prompt (vezi folder_map.py)."""

import fnmatch
import os
from pathlib import Path
from typing import Any

import yaml

import project_folders_store as folders

_PROJECT_ROOT = Path(__file__).resolve().parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    BASE_WORKDIR = yaml.safe_load(f)["agent"]["workdir"]

# nume de fișiere care conțin de obicei chei/parole — nu ajung la LLM
_SECRET_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
    "id_ecdsa*", "credentials*", "*.keystore", ".netrc", ".pgpass", "secrets.*",
)


def project_workdir(project_id: str | None) -> str:
    """Workdir-ul unui chat: workdir/projects/<id>/ pentru proiecte,
    workdir/ pentru chat-urile neafiliate. Creat la nevoie."""
    path = os.path.join(BASE_WORKDIR, "projects", project_id) if project_id else BASE_WORKDIR
    os.makedirs(path, exist_ok=True)
    return path


def resolve(agent: Any, path: str) -> str:
    workdir = getattr(agent, "workdir", BASE_WORKDIR)
    return os.path.realpath(path if os.path.isabs(path) else os.path.join(workdir, path))


def is_secret(full_path: str) -> bool:
    name = os.path.basename(full_path).lower()
    return any(fnmatch.fnmatch(name, p) for p in _SECRET_PATTERNS)


def _inside(root: str, full_path: str) -> bool:
    root = os.path.realpath(root)
    return os.path.commonpath([root, full_path]) == root


def folder_for(agent: Any, full_path: str) -> dict[str, Any] | None:
    """Cel mai specific folder legat de proiect care conține calea."""
    matches = [f for f in folders.list_for(getattr(agent, "project_id", None)) if _inside(f["path"], full_path)]
    return max(matches, key=lambda f: len(f["path"])) if matches else None


def read_error(agent: Any, full_path: str, shown_path: str) -> str | None:
    """Mesajul de refuz pentru citire/listare, sau None dacă e permisă."""
    if is_secret(full_path):
        return f"Eroare: '{shown_path}' pare să conțină secrete (chei, parole). Acces refuzat."
    if _inside(getattr(agent, "workdir", BASE_WORKDIR), full_path) or folder_for(agent, full_path):
        return None
    return f"Eroare: calea '{shown_path}' iese în afara workdir și a folderelor proiectului. Acces refuzat."


def write_policy(agent: Any, full_path: str) -> str:
    """'ok' (workdir sau folder rw), 'ro' (folder doar-citire: refuz) sau
    'confirm' (în afara oricărei zone permise: cere aprobare în UI)."""
    if _inside(getattr(agent, "workdir", BASE_WORKDIR), full_path):
        return "ok"
    folder = folder_for(agent, full_path)
    if folder is None:
        return "confirm"
    return "ok" if folder["mode"] == "rw" else "ro"
