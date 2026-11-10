"""Clasa Agent + bucla principală. Un singur fișier, fără registry global
(diferit de a0: fiecare instanță Agent are propriul dict de tool-uri).
"""

import hashlib
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

# Guard-ul de buclă: detectorii numără apelurile dintr-o fereastră de
# _LOOP_WINDOW apeluri (ca un tool apelat demult să nu declanșeze azi un
# avertisment); pragurile de avertizare/oprire sunt în conf/settings.yaml,
# secțiunea agent, și se citesc cu .get() ca un config vechi fără cheile noi.
_LOOP_WINDOW = 12
# cât din argumente/rezultate păstrăm în raportul AgentLoopAborted
_LOOP_SNIPPET = 200
# textul comun de la finalul oricărui avertisment de buclă
_LOOP_SWITCH = (
    "Schimbă abordarea: alt tool, alte argumente, "
    "sau răspunde cu ce ai și spune ce te blochează."
)


def _clip(text: str) -> str:
    """Trunchiază un argument/rezultat pentru raportul de oprire al buclei."""
    if len(text) <= _LOOP_SNIPPET:
        return text
    return text[:_LOOP_SNIPPET] + "…"


def _digest(text: str) -> str:
    """Hash-ul stabil al unui mesaj de rezultat — pentru detectorul
    noprogress (identitate prin hash, nu comparație de stringuri lungi)."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class AgentError(Exception):
    """Eroare vizibilă care încheie rularea curentă (limită de iterații sau repetiție)."""


class AgentCancelled(AgentError):
    """Rularea a fost anulată explicit de utilizator (etapa 2: POST /cancel)."""


class AgentLoopAborted(AgentError):
    """Oprirea buclei de un detector de repetiție (consecutive / cycle /
    failure / noprogress). Subclasă de AgentError, deci e prinsă de orice
    handler existent `except AgentError`.

    `detector`  — numele detectorului care a declanșat oprirea
    `tool`      — tool-ul monitorizat
    `count`     — numărul de apeluri problematice numărate în fereastră
    `entries`   — ultimele apeluri (tool, argumente, rezultat), fiecare
                  trunchiat la _LOOP_SNIPPET caractere"""

    def __init__(
        self,
        detector: str,
        tool: str,
        count: int,
        entries: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...] = (),
    ) -> None:
        self.detector = detector
        self.tool = tool
        self.count = count
        self.entries = [(_t, _clip(str(a)), _clip(str(m))) for _t, a, m in entries][-5:]
        super().__init__(self._report())

    def _report(self) -> str:
        """Raport uman în limba română: ce s-a încercat și de ce s-a oprit."""
        linii = [
            f"Bucla oprită de detectorul {self.detector}: tool-ul '{self.tool}' a atins "
            f"pragul de {self.count} apeluri problematice în fereastra curentă.",
            "Ultimele apeluri:",
        ]
        for i, (tool, args, msg) in enumerate(self.entries, 1):
            linii.append(f"  {i}. {tool}({args}) -> {msg or '(fără rezultat)'}")
        linii.append(_LOOP_SWITCH)
        return "\n".join(linii)

    def __str__(self) -> str:
        return self._report()


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
        # ultimele apeluri (tool, argumente_json) — pentru detectoarele
        # consecutive/cycle; fereastra are _LOOP_WINDOW apeluri (vezi constanta)
        self._recent_calls: list[tuple[str, str]] = []
        # aceeași fereastră, dar cu mesajul rezultat — pentru detectoarele
        # failure (mesaje „Eroare…”) și noprogress (aceleași mesaje repetate),
        # evaluate abia după tool.execute
        self._recent_results: list[tuple[str, str, str]] = []
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
        # înainte de execuție; ridică AgentLoopAborted la pragul de oprire
        repetition_note = self._check_repetition(name, raw_arguments)

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
        result_note = self._check_result_repetition(name, raw_arguments, message)
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

    def _check_repetition(self, name: str, raw_arguments: str) -> str | None:
        """Urmărește ultimele apeluri (tool, argumente) în fereastra de
        _LOOP_WINDOW apeluri; avertizează la 2 repetiții, oprește rularea la 4
        (vezi plan, secțiunea Bucla și maparea tool-calls). Două detectoare,
        ambele evaluate înainte de execuție:
          consecutive — aceleași argumente una alta (streak),
          cycle       — aceeași cheie (tool, argumente) reapărută în fereastră
                        chiar neconsecutive (prinde A,B,A,B)."""
        key = (name, raw_arguments)
        self._recent_calls.append(key)
        self._recent_calls = self._recent_calls[-_LOOP_WINDOW:]

        streak = 0
        for call in reversed(self._recent_calls):
            if call == key:
                streak += 1
            else:
                break
        # de câte ori a mai apărut cheia în fereastră, chiar și neconsecutive
        cycle = sum(1 for call in self._recent_calls if call == key)

        warn_at = _AGENT_CFG.get("repetition_warn_count", 2)
        abort_at = _AGENT_CFG.get("repetition_abort_count", 4)

        # consecutive înainte de cycle: la un streak pur sunt egale, iar numele
        # detectorului trebuie să spună exact ce s-a întâmplat; apelul curent
        # nu are încă rezultat (message="")
        if streak >= abort_at:
            raise self._loop_abort("consecutive", name, streak, raw_arguments, "")
        if cycle >= abort_at:
            raise self._loop_abort("cycle", name, cycle, raw_arguments, "")
        if streak >= warn_at:
            return self._loop_note(
                "consecutive",
                f"tool-ul '{name}' a fost apelat de {streak} ori consecutiv cu aceleași argumente",
            )
        if cycle >= warn_at:
            return self._loop_note(
                "cycle",
                f"tool-ul '{name}' cu aceleași argumente a reapărut de {cycle} ori în "
                f"ultimele {_LOOP_WINDOW} apeluri (chiar neconsecutive)",
            )
        return None

    def _check_result_repetition(self, name: str, raw_arguments: str, message: str) -> str | None:
        """Detectoarele care au nevoie de rezultat, evaluate după tool.execute,
        tot în fereastra de _LOOP_WINDOW apeluri (nu doar consecutiv):
          failure   — tool-ul întoarce mesaje care încep cu „Eroare”, indiferent
                      de argumente,
          noprogress — tool-ul întoarce mereu același mesaj (comparat prin hash)."""
        self._recent_results.append((name, raw_arguments, message))
        self._recent_results = self._recent_results[-_LOOP_WINDOW:]

        same_tool = [m for tool, _, m in self._recent_results if tool == name]
        failures = sum(1 for m in same_tool if m.lower().startswith("eroare"))
        digest = _digest(message)
        identical = sum(1 for m in same_tool if _digest(m) == digest)

        failure_warn = _AGENT_CFG.get("failure_warn_count", 2)
        failure_abort = _AGENT_CFG.get("failure_abort_count", 4)
        noprogress_warn = _AGENT_CFG.get("noprogress_warn_count", 2)
        noprogress_abort = _AGENT_CFG.get("noprogress_abort_count", 4)

        if failures >= failure_abort:
            raise self._loop_abort("failure", name, failures, raw_arguments, message, result_recorded=True)
        if identical >= noprogress_abort:
            raise self._loop_abort("noprogress", name, identical, raw_arguments, message, result_recorded=True)
        if failures >= failure_warn:
            return self._loop_note(
                "failure",
                f"tool-ul '{name}' a întors mesaje care încep cu „Eroare” de {failures} ori "
                f"în ultimele {_LOOP_WINDOW} apeluri",
            )
        if identical >= noprogress_warn:
            return self._loop_note(
                "noprogress",
                f"tool-ul '{name}' a întors același mesaj de {identical} ori "
                f"în ultimele {_LOOP_WINDOW} apeluri",
            )
        return None

    def _loop_note(self, detector: str, detail: str) -> str:
        """Notă de avertizare, prefixată mesajului tool-ului: numește
        detectorul și numărul de apeluri și dă instrucțiunea de schimbare."""
        return f"[Avertisment de buclă — detectorul {detector}: {detail}. {_LOOP_SWITCH}]"

    def _loop_abort(
        self,
        detector: str,
        tool: str,
        count: int,
        raw_arguments: str,
        message: str,
        result_recorded: bool = False,
    ) -> AgentLoopAborted:
        """Construiește oprirea de buclă: întâi evenimentul `loop_aborted`
        pentru UI (SSE), apoi excepția cu ultimele apeluri din fereastră —
        ridicată de apelant, prinsă de handlerele `except AgentError`.
        `result_recorded` = apelul curent e deja în fereastră (opusul: oprire
        înainte de execuție, când rezultatul lui nu există încă)."""
        entries = list(self._recent_results)
        if not result_recorded:
            entries.append((tool, raw_arguments, message))
        self._emit("loop_aborted", detector=detector, tool=tool, count=count)
        return AgentLoopAborted(detector, tool, count, entries[-5:])
