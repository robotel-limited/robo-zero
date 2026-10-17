"""Interfața de bază pentru tool-urile agentului.

Fiecare tool concret (fs_tool.py, shell_tool.py, response.py, ...) implementează
clasa Tool: declară schema (nume, descriere, parametri JSON-Schema) și metoda
async execute(**kwargs) -> ToolResult.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Rezultatul execuției unui tool.

    message: text întors modelului ca mesaj role="tool".
    break_loop: True => bucla agentului se încheie, message devine răspunsul final.
    """

    message: str
    break_loop: bool = False


class Tool(ABC):
    """Clasă de bază pentru un tool. name/description/parameters/required
    sunt atribute de clasă suprascrise de fiecare tool concret.
    """

    name: str = ""
    description: str = ""
    # JSON-Schema "properties" pentru argumentele tool-ului
    parameters: dict[str, Any] = {}
    required: list[str] = []

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def schema(self) -> dict:
        """Schema în formatul cerut de API-ul OpenAI-compatible (DeepSeek):
        tools=[{"type": "function", "function": {...}}].
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execută tool-ul cu argumentele primite de la model (deja parsate din JSON)."""
        raise NotImplementedError
