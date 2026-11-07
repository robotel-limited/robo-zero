"""Dict-ul de tool-uri al Agentului, extras din agent.py (refactor):
construcția lui build_tools(agent) ca funcție module-level; Agent._build_tools
rămâne un wrapper subțire (apelat și din chat_session.py).
"""

from tools.browser_tool import BrowserTool
from tools.code_task import CodeTask
from tools.call_subordinate import CallSubordinate
from tools.fs_tool import FsList, FsRead, FsWrite
from tools.image_generate import ImageGenerate
from tools.kb_read import KbRead
from tools.memory_tool import MemoryRecall, MemoryRemember
from tools.project_search import ProjectSearch
from tools.response import FinalResponse
from tools.robo_med_tool import RoboMedIntake, RoboMedReport, RoboMedSearch
from tools.shell_tool import ShellExecute
from tools.tool_base import Tool
from tools.web_read_tool import WebRead
from tools.web_search_tool import WebSearch


def build_tools(agent) -> dict[str, Tool]:
    instances: list[Tool] = [
        FsRead(agent),
        FsWrite(agent),
        FsList(agent),
        ShellExecute(agent),
        CodeTask(agent),
        CallSubordinate(agent),
        MemoryRecall(agent),
        ImageGenerate(agent),
        WebSearch(agent),
        WebRead(agent),
        BrowserTool(agent),
        RoboMedIntake(agent),
        RoboMedSearch(agent),
        RoboMedReport(agent),
        FinalResponse(agent),
    ]
    if agent.project_id:
        # memoria proiectului e locală și izolată; memory_remember ar scrie
        # în vault-ul dimag federat (partajat între mașini și agenți)
        instances += [ProjectSearch(agent), KbRead(agent)]
    else:
        instances.append(MemoryRemember(agent))
    return {t.name: t for t in instances}
