"""Clasa Agent + bucla principală. Un singur fișier, fără registry global
(diferit de a0: fiecare instanță Agent are propriul dict de tool-uri).
"""

import json
import os
import re
import signal
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

import agent_config
import agent_tools
import agent_wm
import attachments
import history
import llm
import safety
import working_memory
from tools.browser_tool import BrowserBox
from tools.tool_base import Tool

_PROJECT_ROOT = Path(__file__).resolve().parent
# modelele locale (32k) pe care e activă memoria de lucru — vezi agent_wm._WM_MODELS
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _SETTINGS = yaml.safe_load(f)

_AGENT_CFG = _SETTINGS["agent"]
_TOOL_CHOICE = _AGENT_CFG.get("tool_choice", "auto")
_SHELL_CFG = _SETTINGS["shell"]

# (tool_name, detail_pentru_UI) -> True dacă utilizatorul aprobă
ConfirmCallback = Callable[[str, str], Awaitable[bool]]

# Regulă obligatorie pentru toți agenții (nu doar oss, care are tool-calling
# nativ nesigur pe build-ul curent de llama.cpp): dacă răspunsul final nu are
# tool_calls și pare un răspuns nesigur/incomplet, bucla face automat un
# memory_recall pe mesajul utilizatorului înainte să dea răspunsul final —
# nu lăsăm decizia doar pe modelul ales, care poate "uita" să caute în dimag.
_STRONG_MARKERS = (
    "nu am suficiente informații",
    "nu am suficient context",
    "nu am acces la",
    "nu știu sigur",
    "nu sunt sigur",
    "nu sunt sigură",
    "nu am informații despre",
    "nu pot răspunde cu certitudine",
    "nu am date despre",
    "ar trebui să verific",
    "i don't have enough information",
    "i'm not sure",
    "i don't know",
    "i do not have access to",
    "if you provide",
    "please provide the",
    "let me know if you'd like",
    "once the key is available",
)

# Markeri "slabi" (familia find/exist) — apar firesc și în rapoarte de stare
# corecte după ce tool-urile au fost deja folosite (ex. "vm1 nu există"),
# deci declanșează auto-recall doar dacă NU s-a executat niciun tool în
# turul curent (vezi tools_used în _run și _looks_insufficient).
_WEAK_MARKERS = (
    "is missing",
    "does not exist",
    "cannot find",
    "could not find",
    "nu găsesc",
    "nu există",
    "nu am găsit",
)

class AgentError(Exception):
    """Eroare vizibilă care încheie rularea curentă (limită de iterații sau repetiție)."""


class AgentCancelled(AgentError):
    """Rularea a fost anulată explicit de utilizator (etapa 2: POST /cancel)."""


# Importul stă DUPĂ definiția AgentError: agent_loopguard importă la rândul lui
# AgentError din acest modul (AgentLoopAborted îl extinde), deci clasa de bază
# trebuie deja să existe în acest punct — altfel import circular.
from agent_loopguard import AgentLoopAborted, LoopGuard  # noqa: E402


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
        project_id: str | None = None,
        working_memory: bool = False,
    ) -> None:
        self.workdir = workdir or _AGENT_CFG["workdir"]
        self.shell_timeout_seconds = _SHELL_CFG["timeout_seconds"]
        self.confirm = confirm
        # blocul de proiect (prompt + fișă + rezumate) — setat de chat_session.py
        # (project_context.build_initial) și citit de _system_prompt()
        self.project_prompt: str | None = None
        self.project_id = project_id
        # apelat după compactarea istoricului — chat_session.py regenerează acolo
        # contextul de proiect din history[0] (fișa curentă, nu cea de la început)
        self.on_compacted: Callable[[], None] | None = None
        self.depth = depth  # adâncime delegare sub-agenți (folosit de call_subordinate, etapa 3)
        self.on_event = on_event
        # sesiune de browser partajată cu sub-agenții aceleiași rulări (etapa 5) —
        # vezi tools/browser_tool.py:BrowserBox; creat nou doar la rădăcină (depth 0),
        # altfel call_subordinate.py trece referința părintelui.
        self.browser_box = browser_box or BrowserBox()
        self.history: list[dict[str, Any]] = []
        self._last_user_message: str = ""
        # memoria de lucru distilată (working_memory.py): cerută de chat_session.py,
        # moștenită de sub-agenți; efectivă doar pe modelele locale — vezi _wm_active()
        self.working_memory = working_memory
        self._obs_key = uuid.uuid4().hex[:8]
        self._obs_n = 0
        self.tools: dict[str, Tool] = self._build_tools()
        # guard-ul de buclă (agent_loopguard.LoopGuard): fereastra de apeluri
        # + detectoarele consecutive/cycle/failure/noprogress, cu pragurile
        # din conf/settings.yaml (secțiunea agent), citite tot din _AGENT_CFG
        self._loop_guard = LoopGuard(_AGENT_CFG)
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
        return agent_tools.build_tools(self)

    def _is_voice_only(self) -> bool:
        return agent_config.is_voice_only(self)

    def _chat_model(self) -> str | None:
        return agent_config.chat_model(self)

    def _system_prompt(self) -> str:
        return agent_config.system_prompt(self)

    async def run(self, user_message: str, images: list[str] | None = None) -> str:
        """Rulează bucla agentului pentru un mesaj de utilizator și întoarce
        textul răspunsului final (cel care devine mesajul verde în UI).
        `images` = căi locale /files/uploads/... ale capturilor atașate."""
        try:
            return await self._run(user_message, images or [])
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

    async def _run(self, user_message: str, images: list[str]) -> str:
        if not self.history:
            self.history.append({"role": "system", "content": self._system_prompt()})
        self.history.append({"role": "user", "content": attachments.user_content(user_message, images)})
        self._last_user_message = user_message

        tool_schemas = [t.schema() for t in self.tools.values()]
        # ce trimite cererea în afara mesajelor: schemele tool-urilor + plafonul
        # de ieșire (rezervat ca prompt + ieșire să nu depășească fereastra pe
        # modelele locale cu fereastră mică — vezi llm.OUTPUT_TOKENS, varianta A
        # 2026-11-08). Pe cloud (fereastră 1M) rezervarea e neglijabilă.
        schema_tokens = len(json.dumps(tool_schemas)) // history.CHARS_PER_TOKEN
        reserved_tokens = schema_tokens + llm.OUTPUT_TOKENS
        auto_recall_done = False
        # True dacă s-a executat deja un tool real (nu terminal response) în
        # turul curent — gating pentru markerii "slabi" din _looks_insufficient
        tools_used = False

        for _ in range(_AGENT_CFG["max_iterations"]):
            if self.cancelled:
                raise AgentCancelled("Rulare anulată de utilizator.")

            compacted = await history.compact_if_needed(
                self.history, model=self._chat_model(), reserved_tokens=reserved_tokens
            )
            if compacted is not self.history:
                self.history = compacted
                if self.on_compacted is not None:
                    self.on_compacted()
                self._emit("history_compacted", kept_messages=len(self.history))

            if self._wm_active():
                working_memory.collapse_old(self.history)
            response = await llm.chat(
                self.history, tools=tool_schemas, model=self._chat_model(),
                tool_choice=_TOOL_CHOICE if tool_schemas else None,
            )

            if not response.tool_calls:
                final_text = response.content or ""
                if not auto_recall_done and self._looks_insufficient(final_text, tools_used):
                    auto_recall_done = True
                    self.history.append({"role": "assistant", "content": final_text})
                    self._emit("auto_recall", query=user_message)
                    recall_tool = self.tools.get("memory_recall")
                    recall_message = "[memorie dimag: indisponibilă]"
                    if recall_tool is not None:
                        result = await recall_tool.execute(query=user_message)
                        recall_message = result.message
                    self.history.append(
                        {
                            # NU "system": template-ul SenseNova (SSN, :8093)
                            # ridică excepție jinja „System message must be at
                            # the beginning" la orice system care nu e primul,
                            # deci run-ul cădea cu 500. Ca "user" merge pe toate
                            # modelele locale (și gpt-oss îl citea oricum).
                            "role": "user",
                            "content": (
                                "[auto-recall dimag — răspunsul anterior a părut nesigur/incomplet, "
                                "s-a căutat automat context relevant]\n" + recall_message
                            ),
                        }
                    )
                    continue
                self.history.append({"role": "assistant", "content": final_text})
                self._emit("final", text=final_text)
                return final_text

            # argumentele stricate nu intră brute în istoric: modelul și le copia
            # la retry, învelite în {"arguments": ...}, crescând de la 4 KB la 66 KB
            broken = {tc.id: self._broken_args_note(tc, response) for tc in response.tool_calls}
            self.history.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": "{}" if broken[tc.id] else tc.arguments,
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
            )

            break_text: str | None = None
            answered_ids: set[str] = set()
            try:
                for tc in response.tool_calls:
                    if self.cancelled:
                        raise AgentCancelled("Rulare anulată de utilizator.")

                    self._emit("tool_call", name=tc.name, arguments=tc.arguments)
                    if broken[tc.id]:
                        result_text, break_loop = broken[tc.id], False
                    else:
                        result_text, break_loop = await self._execute_tool_call(tc.name, tc.arguments)
                    self._emit("tool_result", name=tc.name, message=result_text)
                    self.history.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": await self._wm_content(tc, result_text)}
                    )
                    answered_ids.add(tc.id)
                    if not broken[tc.id] and not break_loop:
                        tools_used = True
                    if break_loop:
                        break_text = result_text
            except BaseException:
                # cancel sau excepție neprevăzută la mijlocul buclei: mesajul
                # assistant cu tool_calls e deja în istoric (mai sus), așa că
                # fiecare tool_call_id neajuns la execuție trebuie completat
                # cu un răspuns tool, altfel istoricul rămâne otrăvit definitiv
                # (providerul respinge orice request viitor pe acest chat cu
                # 400 "insufficient tool messages following tool_calls").
                for tc in response.tool_calls:
                    if tc.id not in answered_ids:
                        self.history.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": "Eroare: execuția a fost întreruptă înainte ca acest tool să răspundă.",
                            }
                        )
                raise

            if break_text is not None:
                if not auto_recall_done and self._looks_insufficient(break_text, tools_used):
                    # cu tool_choice=required modelul termină mereu prin tool-ul
                    # "response", deci ramura de mai sus (fără tool_calls) nu se
                    # mai declanșează niciodată — auto-recall-ul trebuie verificat
                    # și aici, pe textul trimis prin response, altfel moare (2026-11-07)
                    auto_recall_done = True
                    self._emit("auto_recall", query=user_message)
                    recall_tool = self.tools.get("memory_recall")
                    recall_message = "[memorie dimag: indisponibilă]"
                    if recall_tool is not None:
                        result = await recall_tool.execute(query=user_message)
                        recall_message = result.message
                    self.history.append(
                        {
                            "role": "user",
                            "content": (
                                "[auto-recall dimag — răspunsul anterior a părut nesigur/incomplet, "
                                "s-a căutat automat context relevant]\n" + recall_message
                            ),
                        }
                    )
                    continue
                self._emit("final", text=break_text)
                return break_text

        raise AgentError(
            f"Agentul a atins limita de {_AGENT_CFG['max_iterations']} iterații fără răspuns final."
        )

    @staticmethod
    def _broken_args_note(tc: Any, response: Any) -> str | None:
        """Mesajul pentru model dacă argumentele nu sunt un obiect JSON valid; None dacă sunt OK."""
        try:
            if isinstance(json.loads(tc.arguments) if tc.arguments else {}, dict):
                return None
            err = "argumentele nu formează un obiect JSON"
        except json.JSONDecodeError as e:
            err = str(e)
        if response.truncated:
            return (
                f"Eroare: apelul '{tc.name}' a fost TĂIAT la limita de ieșire a modelului "
                f"({response.max_tokens} tokeni; argumentele au ajuns la {len(tc.arguments)} caractere) "
                f"— nu e o greșeală de escapare. Nu repeta același apel și nu-l înveli în alt JSON. "
                "Trimite argumente mai scurte: la code_task un task concis (fișiere + comportament + "
                "verify), nu cod; în loc de fs_write cu un fișier mare, fă modificări punctuale "
                "(code_task sau shell_execute) sau împarte conținutul."
            )
        return (
            f"Eroare: argumente JSON invalide pentru tool '{tc.name}': {err}. "
            "Trimite din nou argumentele ca obiect JSON simplu, fără să-l înveli în alt JSON."
        )

    @staticmethod
    def _mentions_existing_dimag_path(raw_arguments: str) -> bool:
        """Repo-ul /srv/robo-dimag e o cale reală: redirect-ul spre memorie se
        aplică doar când „dimag” nu e parte dintr-o cale care există pe disc."""
        for token in re.findall(r"/[^\s\"'`;|&<>]*dimag[^\s\"'`;|&<>]*", raw_arguments, re.IGNORECASE):
            # urcă până la segmentul care conține „dimag”: fișierele noi dintr-un
            # repo existent (ex. /srv/robo-dimag/nou.py) contează și ele
            path = token.rstrip("/")
            while "dimag" in path.lower():
                if os.path.exists(path):
                    return True
                path = os.path.dirname(path)
        return False

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

        if (name in ("fs_read", "fs_list", "shell_execute") and "dimag" in raw_arguments.lower()
                and not self._mentions_existing_dimag_path(raw_arguments)):
            # "dimag" confundat cu o cale/folder (vezi prompts/agent.system.md) —
            # harness-ul redirecționează forțat către memory_recall, în loc să
            # lase doar mesajul de acces refuzat (pe care modelul îl ignoră).
            recall_tool = self.tools.get("memory_recall")
            query = self._last_user_message or raw_arguments
            if recall_tool is not None:
                result = await recall_tool.execute(query=query)
                self._emit("auto_recall", query=query, reason="dimag_path_redirect")
                note = (
                    "[redirect automat: \"dimag\" nu e un folder/cale de fișier — e memoria "
                    "robo-dimag, accesată prin tool-ul memory_recall, apelat automat aici cu "
                    "întrebarea curentă]\n"
                )
                return (note + result.message, False)

        tool = self.tools.get(name)
        if tool is None:
            return (f"Eroare: tool necunoscut '{name}'.", False)

        # detectoarele care nu au nevoie de rezultat (consecutive + cycle) —
        # înainte de execuție; LoopGuard ridică AgentLoopAborted la pragul de
        # oprire, iar evenimentul `loop_aborted` se emite aici (locul vechii
        # emisii din guard), apoi excepția se re-randuie
        try:
            repetition_note = self._loop_guard.check_repetition(name, raw_arguments)
        except AgentLoopAborted as exc:
            self._emit("loop_aborted", **exc.event_data)
            raise

        pre_check = getattr(tool, "pre_check", None)
        if callable(pre_check) and pre_check(args) and not self._is_voice_only():
            approved = await self.confirm(name, raw_arguments)
            if not approved:
                return (f"Acțiunea '{name}' a fost refuzată de utilizator.", False)

        try:
            result = await tool.execute(**args)
        except Exception as e:  # defensiv: un tool nu trebuie să crape bucla
            return (f"Eroare neprevăzută în tool '{name}': {e}", False)

        message = safety.mask_secrets(result.message)
        # detectoarele care au nevoie de rezultat abia acum, după tool.execute
        try:
            result_note = self._loop_guard.check_result_repetition(name, raw_arguments, message)
        except AgentLoopAborted as exc:
            self._emit("loop_aborted", **exc.event_data)
            raise
        notes = [n for n in (repetition_note, result_note) if n]
        if notes:
            message = "\n".join(notes) + "\n" + message
        return (message, result.break_loop)

    def _wm_active(self) -> bool:
        return agent_wm.wm_active(self)

    async def _wm_content(self, tc: Any, result_text: str) -> str:
        return await agent_wm.wm_content(self, tc, result_text)

    @staticmethod
    def _looks_insufficient(text: str, tools_used: bool) -> bool:
        """Detectează euristic un răspuns final nesigur/incomplet (fără
        tool_calls), ca să declanșeze automat un memory_recall în dimag —
        regulă obligatorie pentru toți agenții, nu doar pentru modelele cu
        tool-calling nativ nesigur (ex. gpt-oss pe build-ul curent).

        Markerii "strong" (epistemici) declanșează mereu. Markerii "weak"
        (familia find/exist, ex. "nu există") apar firesc în rapoarte de
        stare corecte după ce tool-urile au fost deja folosite — declanșează
        doar dacă încă nu s-a executat niciun tool în turul curent (fals
        pozitiv real: raport SSH valid "vm1 inactive / nu există" după 2
        apeluri ssh, 2026-11)."""
        lowered = text.lower()
        if any(marker in lowered for marker in _STRONG_MARKERS):
            return True
        return not tools_used and any(marker in lowered for marker in _WEAK_MARKERS)
