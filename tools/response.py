"""Tool pentru trimiterea răspunsului final și încheierea buclei agentului."""

from typing import Any

from tools.tool_base import Tool, ToolResult


class FinalResponse(Tool):
    """Tool pentru trimiterea răspunsului final către utilizator și încheiere buclă."""

    name: str = "response"
    description: str = (
        "Folosește acest tool pentru a trimite răspunsul final către utilizator "
        "și a încheia bucla curentă."
    )
    parameters: dict[str, Any] = {
        "text": {
            "type": "string",
            "description": "Mesajul final de trimis utilizatorului.",
        }
    }
    required: list[str] = ["text"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execută trimiterea răspunsului final."""
        text = kwargs.get("text", "")

        if not text:
            return ToolResult(
                message="Eroare: parametrul 'text' e obligatoriu pentru response."
            )

        return ToolResult(message=text, break_loop=True)
