"""Configurarea Agentului (model, voice-only, prompt de sistem), extrasă din
agent.py (refactor): funcții module-level care primesc instanța Agent ca prim
argument; metodele Agent (_chat_model, _is_voice_only, _system_prompt) rămân
doar wrappers subțiri ca să nu se schimbe call sites-urile existente.
"""

from datetime import datetime
from pathlib import Path

import projects_store
import settings_store

_PROJECT_ROOT = Path(__file__).resolve().parent


def is_voice_only(agent) -> bool:
    """Proiectele voice-only (robo-audio, fără UI de approve/deny) nu pot
    aproba niciodată o cerere de confirmare — vezi _execute_tool_call,
    care sare peste pre_check/confirm complet pentru ele. Citit live din
    projects_store, la fel ca _chat_model(), ca o schimbare din /projects
    să se aplice imediat."""
    if not agent.project_id:
        return False
    project = projects_store.get(agent.project_id)
    return bool(project and project.get("voice_only"))


def chat_model(agent) -> str | None:
    """Modelul proiectului (etapa D) are prioritate, citit live din
    projects_store la fiecare tură — ca schimbarea din /projects să se
    aplice imediat, inclusiv sub-agenților (project_id se propagă prin
    call_subordinate). Pentru chat-urile neafiliate unui proiect,
    folosește modelul implicit global din Settings (etapa E), citit la
    fel de live — nu doar MODEL hardcodat din settings.yaml."""
    if agent.project_id:
        project = projects_store.get(agent.project_id)
        if project:
            return project["model"]
    return settings_store.load()["default_model"]


def system_prompt(agent) -> str:
    template = (_PROJECT_ROOT / "prompts" / "agent.system.md").read_text(encoding="utf-8")
    base = template.format(datetime=datetime.now().isoformat(timespec="seconds"))
    if agent.project_prompt:
        return f"{base}\n\n---\n\n{agent.project_prompt}"
    return base
