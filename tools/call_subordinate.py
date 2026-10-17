"""Tool pentru delegarea unei sarcini unui sub-agent (etapa 3). Adâncimea
de delegare e limitată (conf/settings.yaml: agent.max_subordinate_depth)
pentru a preveni delegare recursivă infinită.
"""

from pathlib import Path
from typing import Any

import yaml

from tools.tool_base import Tool, ToolResult

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _MAX_DEPTH = yaml.safe_load(f)["agent"]["max_subordinate_depth"]


class CallSubordinate(Tool):
    name: str = "call_subordinate"
    description: str = (
        "Delegă o sarcină (cu tot contextul necesar, inclus explicit în mesaj) "
        "unui sub-agent independent, care o rezolvă singur și întoarce rezultatul. "
        "Folosește-l pentru a împărți o sarcină mare în bucăți independente. "
        "Adâncimea de delegare e limitată."
    )
    parameters: dict[str, Any] = {
        "message": {
            "type": "string",
            "description": "Sarcina completă de delegat sub-agentului (context + cerință explicită).",
        }
    }
    required: list[str] = ["message"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        # Import tardiv: agent.py importă CallSubordinate la nivel de modul,
        # deci un import la nivel de modul aici ar crea un ciclu agent<->tools.
        from agent import Agent, AgentCancelled, AgentError

        message = kwargs.get("message", "")
        if not message:
            return ToolResult(message="Eroare: parametrul 'message' e obligatoriu.")

        parent = self.agent
        if parent.depth >= _MAX_DEPTH:
            return ToolResult(
                message=(
                    f"Eroare: adâncimea maximă de delegare ({_MAX_DEPTH}) a fost atinsă — "
                    "rezolvă sarcina direct, fără alt sub-agent."
                )
            )

        subordinate = Agent(
            confirm=parent.confirm,
            workdir=parent.workdir,
            depth=parent.depth + 1,
            on_event=parent.on_event,
        )
        parent.current_subordinate = subordinate
        try:
            result = await subordinate.run(message)
        except AgentCancelled:
            return ToolResult(message="Sub-agentul a fost anulat.")
        except AgentError as e:
            return ToolResult(message=f"Sub-agentul s-a oprit cu eroare: {e}")
        finally:
            parent.current_subordinate = None

        return ToolResult(message=result)
