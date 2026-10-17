"""Clasa Agent + bucla principală. Un singur fișier, fără registry global
(diferit de a0: fiecare instanță Agent are propriul dict de tool-uri).
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

import llm
import safety
from tools.fs_tool import FsList, FsRead, FsWrite
from tools.response import FinalResponse
from tools.shell_tool import ShellExecute
from tools.tool_base import Tool

_PROJECT_ROOT = Path(__file__).resolve().parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_AGENT_CFG = _SETTINGS["agent"]
_SHELL_CFG = _SETTINGS["shell"]

# (tool_name, detail_pentru_UI) -> True dacă utilizatorul aprobă
ConfirmCallback = Callable[[str, str], Awaitable[bool]]


class AgentError(Exception):
    """Eroare vizibilă care încheie rularea curentă (limită de iterații sau repetiție)."""


class Agent:
    def __init__(
        self,
        confirm: ConfirmCallback,
        workdir: str | None = None,
        depth: int = 0,
    ) -> None:
        self.workdir = workdir or _AGENT_CFG["workdir"]
        self.shell_timeout_seconds = _SHELL_CFG["timeout_seconds"]
        self.confirm = confirm
        self.depth = depth  # adâncime delegare sub-agenți (folosit de call_subordinate, etapa 3)
        self.history: list[dict[str, Any]] = []
        self.tools: dict[str, Tool] = self._build_tools()
        # ultimele apeluri (tool, argumente_json) — pentru detecția repetiției
        self._recent_calls: list[tuple[str, str]] = []

    def _build_tools(self) -> dict[str, Tool]:
        instances: list[Tool] = [
            FsRead(self),
            FsWrite(self),
            FsList(self),
            ShellExecute(self),
            FinalResponse(self),
        ]
        return {t.name: t for t in instances}

    def _system_prompt(self) -> str:
        template = (_PROJECT_ROOT / "prompts" / "agent.system.md").read_text(encoding="utf-8")
        return template.format(datetime=datetime.now().isoformat(timespec="seconds"))

    async def run(self, user_message: str) -> str:
        """Rulează bucla agentului pentru un mesaj de utilizator și întoarce
        textul răspunsului final (cel care devine mesajul verde în UI)."""
        if not self.history:
            self.history.append({"role": "system", "content": self._system_prompt()})
        self.history.append({"role": "user", "content": user_message})

        tool_schemas = [t.schema() for t in self.tools.values()]

        for _ in range(_AGENT_CFG["max_iterations"]):
            response = await llm.chat(self.history, tools=tool_schemas)

            if not response.tool_calls:
                final_text = response.content or ""
                self.history.append({"role": "assistant", "content": final_text})
                return final_text

            self.history.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in response.tool_calls
                    ],
                }
            )

            break_text: str | None = None
            for tc in response.tool_calls:
                result_text, break_loop = await self._execute_tool_call(tc.name, tc.arguments)
                self.history.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result_text}
                )
                if break_loop:
                    break_text = result_text

            if break_text is not None:
                return break_text

        raise AgentError(
            f"Agentul a atins limita de {_AGENT_CFG['max_iterations']} iterații fără răspuns final."
        )

    async def _execute_tool_call(self, name: str, raw_arguments: str) -> tuple[str, bool]:
        """Execută un apel de tool. Nu ridică excepție pentru erori normale
        (JSON stricat, tool necunoscut, argumente lipsă) — acelea devin
        mesaje `tool` întoarse modelului, ca să se poată corecta."""
        try:
            args = json.loads(raw_arguments) if raw_arguments else {}
            if not isinstance(args, dict):
                raise ValueError("argumentele nu formează un obiect JSON")
        except (json.JSONDecodeError, ValueError) as e:
            return (f"Eroare: argumente JSON invalide pentru tool '{name}': {e}", False)

        tool = self.tools.get(name)
        if tool is None:
            return (f"Eroare: tool necunoscut '{name}'.", False)

        repetition_note = self._check_repetition(name, raw_arguments)

        pre_check = getattr(tool, "pre_check", None)
        if callable(pre_check) and pre_check(args):
            approved = await self.confirm(name, raw_arguments)
            if not approved:
                return (f"Acțiunea '{name}' a fost refuzată de utilizator.", False)

        try:
            result = await tool.execute(**args)
        except Exception as e:  # defensiv: un tool nu trebuie să crape bucla
            return (f"Eroare neprevăzută în tool '{name}': {e}", False)

        message = safety.mask_secrets(result.message)
        if repetition_note:
            message = f"{repetition_note}\n{message}"
        return (message, result.break_loop)

    def _check_repetition(self, name: str, raw_arguments: str) -> str | None:
        """Urmărește ultimele apeluri (tool, argumente); avertizează la 3
        repetiții consecutive identice, oprește rularea la 5 (vezi plan,
        secțiunea Bucla și maparea tool-calls)."""
        key = (name, raw_arguments)
        self._recent_calls.append(key)
        self._recent_calls = self._recent_calls[-10:]

        streak = 0
        for call in reversed(self._recent_calls):
            if call == key:
                streak += 1
            else:
                break

        warn_at = _AGENT_CFG["repetition_warn_count"]
        abort_at = _AGENT_CFG["repetition_abort_count"]

        if streak >= abort_at:
            raise AgentError(
                f"Tool-ul '{name}' a fost apelat cu argumente identice de {streak} ori consecutiv — rulare oprită."
            )
        if streak >= warn_at:
            return (
                f"[Avertisment: acesta e al {streak}-lea apel identic consecutiv al tool-ului '{name}'. "
                "Schimbă abordarea.]"
            )
        return None
