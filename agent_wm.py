"""Memoria de lucru (working memory) a Agentului, extrasă din agent.py
(refactor): wm_active / wm_content ca funcții module-level; metodele Agent
(_wm_active, _wm_content) rămân doar wrappers subțiri.
"""

import json
from typing import Any

import distiller
import settings_store
import working_memory

# Oglindă a lui _WM_MODELS din agent.py — importul de-acolo ar crea un import
# circular (agent.py importă acest modul). Ambele seturi se derivă din
# distiller.DISTILL_MODEL; dacă se adaugă/elimină modele locale în agent.py,
# se actualizează și aici.
_WM_MODELS = {"gpt-oss-20b", distiller.DISTILL_MODEL}


def wm_active(agent) -> bool:
    """Memoria de lucru e activă doar dacă a fost cerută (chat-uri UI și
    sub-agenții lor) și modelul efectiv al turei e un model local cu 32k context."""
    if not agent.working_memory or agent._chat_model() not in _WM_MODELS:
        return False
    return bool(settings_store.load()["working_memory"])


async def wm_content(agent, tc: Any, result_text: str) -> str:
    """Conținutul care intră în istoric pentru un rezultat de tool: ieșirea
    brută lungă se salvează pe disc, în istoric rămâne un ciot cu calea.
    Recitirea unui fișier .obs/ prin fs_read rămâne întreagă în pasul
    curent (altfel s-ar ciotui iar, la nesfârșit)."""
    if not agent._wm_active():
        return result_text
    if tc.name == "fs_read" and ".obs/" in (tc.arguments or ""):
        # markerul sursei permite restrângerea ei ulterioară (collapse_old)
        try:
            path = json.loads(tc.arguments).get("path", "")
        except (json.JSONDecodeError, AttributeError):
            return result_text
        return result_text + "\n" + working_memory.marker(path, result_text)
    agent._obs_n += 1
    content, relpath = working_memory.on_tool_result(
        agent.workdir, agent._obs_key, agent._obs_n, result_text
    )
    if relpath is None:
        return content
    # pe SenseNova (A/B 2026-11-01) nota distilată ia locul ciotului;
    # la eșecul distilatorului rămâne ciotul determinist
    note = None
    if agent._chat_model() == distiller.DISTILL_MODEL:
        note = await distiller.distill(agent._last_user_message, result_text)
    if note is not None:
        content = note + "\n" + working_memory.marker(relpath, result_text)
    agent._emit("working_memory", stored=relpath, chars=len(result_text), distilled=note is not None)
    return content
