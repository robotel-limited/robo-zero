"""Clasa Agent + bucla principală. Un singur fișier, fără registry global
(diferit de a0: fiecare instanță Agent are propriul dict de tool-uri).
"""

import json
import os
import signal
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

import history
import llm
import safety
from tools.browser_tool import BrowserBox, BrowserTool
from tools.call_subordinate import CallSubordinate
from tools.fs_tool import FsList, FsRead, FsWrite
from tools.image_generate import ImageGenerate
from tools.memory_tool import MemoryRecall, MemoryRemember
from tools.response import FinalResponse
from tools.robo_rag_tool import RoboRagQuery
from tools.shell_tool import ShellExecute
from tools.tool_base import Tool
from tools.web_read_tool import WebRead

_PROJECT_ROOT = Path(__file__).resolve().parent
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_AGENT_CFG = _SETTINGS["agent"]
_SHELL_CFG = _SETTINGS["shell"]

# (tool_name, detail_pentru_UI) -> True dacă utilizatorul aprobă
ConfirmCallback = Callable[[str, str], Awaitable[bool]]


class AgentError(Exception):
    """Eroare vizibilă care încheie rularea curentă (limită de iterații sau repetiție)."""


class AgentCancelled(AgentError):
    """Rularea a fost anulată explicit de utilizator (etapa 2: POST /cancel)."""


# (tip_eveniment, date) -> None — hook opțional folosit de app.py/chat_session.py
# pentru a retransmite evenimente prin SSE, fără ca agent.py să cunoască FastAPI/SSE.
EventCallback = Callable[[str, dict[str, Any]], None]


class Agent:
    def __init__(
        self,
        confirm: ConfirmCallback,
        workdir: str | None = None,
        depth: int = 0,
        on_event: EventCallback | None = None,
        browser_box: BrowserBox | None = None,
        project_prompt: str | None = None,
    ) -> None:
        self.workdir = workdir or _AGENT_CFG["workdir"]
        self.shell_timeout_seconds = _SHELL_CFG["timeout_seconds"]
        self.confirm = confirm
        # prompt-ul proiectului căruia îi e legat chat-ul (dacă e legat) — citit
        # live de _system_prompt() la prima rulare; chat_session.py îl actualizează
        # și după construcție, dacă proiectul se schimbă înainte de primul mesaj.
        self.project_prompt = project_prompt
        self.depth = depth  # adâncime delegare sub-agenți (folosit de call_subordinate, etapa 3)
        self.on_event = on_event
        # sesiune de browser partajată cu sub-agenții aceleiași rulări (etapa 5) —
        # vezi tools/browser_tool.py:BrowserBox; creat nou doar la rădăcină (depth 0),
        # altfel call_subordinate.py trece referința părintelui.
        self.browser_box = browser_box or BrowserBox()
        self.history: list[dict[str, Any]] = []
        self.tools: dict[str, Tool] = self._build_tools()
        # ultimele apeluri (tool, argumente_json) — pentru detecția repetiției
        self._recent_calls: list[tuple[str, str]] = []
        # anulare (etapa 2): flag verificat la începutul fiecărei iterații și
        # înainte de fiecare tool; subprocesul shell curent (dacă există)
        # primește SIGTERM — vezi tools/shell_tool.py.
        self.cancelled = False
        self.current_shell_proc: Any = None
        # sub-agentul pornit curent de call_subordinate.py (dacă există),
        # pentru ca request_cancel() să se propage în cascadă — vezi acolo.
        self.current_subordinate: "Agent | None" = None

    def _emit(self, event_type: str, **data: Any) -> None:
        """Trimite un eveniment către callback-ul extern, dacă există. Nu
        trebuie să poată bloca sau crăpa bucla agentului. `depth` e inclus
        mereu, ca UI-ul să poată distinge evenimentele unui sub-agent."""
        if self.on_event is None:
            return
        try:
            self.on_event(event_type, {"depth": self.depth, **data})
        except Exception:
            pass

    def request_cancel(self) -> None:
        """Marchează rularea curentă pentru anulare și întrerupe comanda
        shell curentă (dacă există una activă) cu SIGTERM, trimis întregului
        grup de procese (tools/shell_tool.py pornește comanda cu
        start_new_session=True) — altfel un copil ca `sleep` dintr-un
        `sleep 30 && echo done` rămâne orfan și ține bucla blocată.
        Se propagă și la un sub-agent activ (call_subordinate, etapa 3),
        altfel anularea nu ajunge la rularea lui, blocată separat în
        await-ul ei proprie."""
        self.cancelled = True
        if self.current_shell_proc is not None:
            try:
                os.killpg(self.current_shell_proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.current_subordinate is not None:
            self.current_subordinate.request_cancel()

    def _build_tools(self) -> dict[str, Tool]:
        instances: list[Tool] = [
            FsRead(self),
            FsWrite(self),
            FsList(self),
            ShellExecute(self),
            CallSubordinate(self),
            MemoryRecall(self),
            MemoryRemember(self),
            ImageGenerate(self),
            WebRead(self),
            BrowserTool(self),
            RoboRagQuery(self),
            FinalResponse(self),
        ]
        return {t.name: t for t in instances}

    def _system_prompt(self) -> str:
        template = (_PROJECT_ROOT / "prompts" / "agent.system.md").read_text(encoding="utf-8")
        base = template.format(datetime=datetime.now().isoformat(timespec="seconds"))
        if self.project_prompt:
            return f"{base}\n\n---\n\n{self.project_prompt}"
        return base

    async def run(self, user_message: str) -> str:
        """Rulează bucla agentului pentru un mesaj de utilizator și întoarce
        textul răspunsului final (cel care devine mesajul verde în UI)."""
        try:
            return await self._run(user_message)
        finally:
            # Browserul (dacă a fost pornit de browser_tool) se închide o
            # singură dată, la finalul rulării rădăcină — sub-agenții (depth > 0)
            # îl lasă deschis, e partajat cu părintele (BrowserBox, etapa 5).
            if self.depth == 0 and self.browser_box.session is not None:
                try:
                    await self.browser_box.session.close()
                except Exception:
                    pass
                self.browser_box.session = None

    async def _run(self, user_message: str) -> str:
        if not self.history:
            self.history.append({"role": "system", "content": self._system_prompt()})
        self.history.append({"role": "user", "content": user_message})

        tool_schemas = [t.schema() for t in self.tools.values()]

        for _ in range(_AGENT_CFG["max_iterations"]):
            if self.cancelled:
                raise AgentCancelled("Rulare anulată de utilizator.")

            compacted = await history.compact_if_needed(self.history)
            if compacted is not self.history:
                self.history = compacted
                self._emit("history_compacted", kept_messages=len(self.history))

            response = await llm.chat(self.history, tools=tool_schemas)

            if not response.tool_calls:
                final_text = response.content or ""
                self.history.append({"role": "assistant", "content": final_text})
                self._emit("final", text=final_text)
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
                if self.cancelled:
                    raise AgentCancelled("Rulare anulată de utilizator.")

                self._emit("tool_call", name=tc.name, arguments=tc.arguments)
                result_text, break_loop = await self._execute_tool_call(tc.name, tc.arguments)
                self._emit("tool_result", name=tc.name, message=result_text)
                self.history.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result_text}
                )
                if break_loop:
                    break_text = result_text

            if break_text is not None:
                self._emit("final", text=break_text)
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
