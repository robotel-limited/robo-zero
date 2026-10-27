"""Regula „codul îl scrie code_task” pentru proiectele marcate cu code_guard.

De ce există: în proiectele în care planul cere ca programarea s-o facă
modelul de coding delegat (mimo, prin code_task), agentul principal tindea
să scrie singur fișierele de cod cu fs_write (incident robo-simulate,
etapa 4-Core, 2026-10-27). Cu code_guard pornit pe proiect:
  - fs_write refuză fișierele de cod (după extensie) și trimite la code_task;
  - code_task rulează doar pe abonamentul MiMo (tier „sub”), fără per token;
  - un code_task eșuat cere oprire și raport către om, nu scriere directă.

Calea prin shell_execute (cat >, sed -i) rămâne deschisă intenționat —
decizia Sorin 2026-10-27 (varianta B): o prinde auditul jurnalului.
"""

import os
from typing import Any

import projects_store

# Fișiere care sunt cod sau configurație de build; documentele (.md, .txt,
# .json de raport) rămân scriibile direct de agent.
CODE_EXTENSIONS = {
    ".cs", ".csproj", ".props", ".targets", ".sln",
    ".ps1", ".psm1", ".py", ".sh", ".js", ".ts",
}

ALLOWED_TIER = "sub"

FS_WRITE_REFUSAL = (
    "Eroare: proiectul are code_guard pornit — fișierele de cod ({ext}) nu se scriu cu "
    "fs_write. Folosește code_task (cu `verify` = comanda de test), care deleagă la mimo. "
    "Nu ocoli regula prin shell_execute."
)

CODE_TASK_FAILED = (
    "\nNu raporta acest task ca reușit și NU scrie tu codul (nici prin fs_write, nici prin "
    "shell_execute). Poți relua code_task o dată, cu un task mai specific; dacă eșuează din "
    "nou, oprește-te și raportează lui Sorin ce a încercat mimo și unde s-a blocat."
)


def enabled(agent: Any) -> bool:
    """Citit live din projects_store, ca o schimbare să se aplice imediat."""
    project_id = getattr(agent, "project_id", None)
    if not project_id:
        return False
    project = projects_store.get(project_id)
    return bool(project and project.get("code_guard"))


def blocked_extension(full_path: str) -> str | None:
    """Extensia de cod a fișierului, dacă e una blocată; altfel None."""
    ext = os.path.splitext(full_path)[1].lower()
    return ext if ext in CODE_EXTENSIONS else None
