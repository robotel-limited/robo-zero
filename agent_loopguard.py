"""Guard-ul de buclă al agentului: detectarea și oprirea repetițiilor.

Extras din agent.py (un modul = o responsabilitate), fără schimbare de
comportament. Conține DOAR logica de detectare/oprire a buclelor:
  consecutive / cycle  (înainte de execuție) — LoopGuard.check_repetition
  failure / noprogress (după tool.execute)   — LoopGuard.check_result_repetition

Evenimentul SSE `loop_aborted` NU e emis de aici: LoopGuard întoarce
AgentLoopAborted cu `event_data`, iar agent.py îl emite în catch-and-re-raise,
la locul de apel (LoopGuard nu cunoaște callback-urile UI-ului).
"""

import hashlib
from typing import Any

# AgentError/AgentCancelled rămân în agent.py (aici doar se importă):
# AgentLoopAborted le extinde, dar nu le definește — direcția importului e
# agent_loopguard -> agent, ca să nu existe două ierarhii de eroare.
from agent import AgentError

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


class AgentLoopAborted(AgentError):
    """Oprirea buclei de un detector de repetiție (consecutive / cycle /
    failure / noprogress). Subclasă de AgentError, deci e prinsă de orice
    handler existent `except AgentError`.

    `detector`  — numele detectorului care a declanșat oprirea
    `tool`      — tool-ul monitorizat
    `count`     — numărul de apeluri problematice numărate în fereastră
    `entries`   — ultimele apeluri (tool, argumente, rezultat), fiecare
                  trunchiat la _LOOP_SNIPPET caractere
    `event_data`— datele pentru evenimentul `loop_aborted` (detector/tool/
                  count), pe care agent.py le emite în catch-and-re-raise"""

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
        self.event_data: dict[str, Any] = {"detector": detector, "tool": tool, "count": count}
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


class LoopGuard:
    """Detectoarele de buclă, cu starea lor proprie (fereastra de apeluri și
    de rezultate) și pragurile din conf/settings.yaml (secțiunea agent),
    primite ca parametru în __init__ (din _AGENT_CFG) — niciodată hardcodate.

    Interfața publică: check_repetition (înainte de execuție),
    check_result_repetition (după tool.execute); ambele întorc un text de
    avertizare sau ridică AgentLoopAborted la pragul de oprire."""

    def __init__(self, agent_cfg: dict[str, Any]) -> None:
        self._agent_cfg = agent_cfg
        # ultimele apeluri (tool, argumente_json) — pentru detectoarele
        # consecutive/cycle; fereastra are _LOOP_WINDOW apeluri (vezi constanta)
        self._recent_calls: list[tuple[str, str]] = []
        # aceeași fereastră, dar cu mesajul rezultat — pentru detectoarele
        # failure (mesaje „Eroare…”) și noprogress (aceleași mesaje repetate),
        # evaluate abia după tool.execute
        self._recent_results: list[tuple[str, str, str]] = []
        # contorul per tool name al eșecurilor CUMULATE pe durata sesiunii
        # (extras #4): fără fereastră, fără reset — dacă un singur tool
        # depășește per_tool_failure_abort, bucla se oprește cu detectorul
        # per_tool_failure (detectorul global failure rămâne pe fereastră)
        self._tool_failure_counts: dict[str, int] = {}

    def check_repetition(self, name: str, raw_arguments: str) -> str | None:
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

        warn_at = self._agent_cfg.get("repetition_warn_count", 2)
        abort_at = self._agent_cfg.get("repetition_abort_count", 4)

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

    def check_result_repetition(self, name: str, raw_arguments: str, message: str) -> str | None:
        """Detectoarele care au nevoie de rezultat, evaluate după tool.execute,
        tot în fereastra de _LOOP_WINDOW apeluri (nu doar consecutiv):
          failure   — tool-ul întoarce mesaje care încep cu „Eroare”, indiferent
                      de argumente,
          noprogress — tool-ul întoarce mereu același mesaj (comparat prin hash)."""
        self._recent_results.append((name, raw_arguments, message))
        self._recent_results = self._recent_results[-_LOOP_WINDOW:]

        same_tool = [m for tool, _, m in self._recent_results if tool == name]
        is_failure = message.lower().startswith("eroare")
        failures = sum(1 for m in same_tool if m.lower().startswith("eroare"))
        digest = _digest(message)
        identical = sum(1 for m in same_tool if _digest(m) == digest)
        if is_failure:
            # contor cumulativ pe sesiune, per tool name (extras #4): nu se
            # resetează și nu are fereastră, spre deosebire de detectorul failure
            self._tool_failure_counts[name] = self._tool_failure_counts.get(name, 0) + 1

        failure_warn = self._agent_cfg.get("failure_warn_count", 2)
        failure_abort = self._agent_cfg.get("failure_abort_count", 4)
        noprogress_warn = self._agent_cfg.get("noprogress_warn_count", 2)
        noprogress_abort = self._agent_cfg.get("noprogress_abort_count", 4)

        if failures >= failure_abort:
            raise self._loop_abort("failure", name, failures, raw_arguments, message, result_recorded=True)
        if identical >= noprogress_abort:
            raise self._loop_abort("noprogress", name, identical, raw_arguments, message, result_recorded=True)
        # plafonul per tool (extras #4): eșecuri CUMULATE pe durata sesiunii,
        # nu în fereastră — un tool care eșuează mereu (ex. web_read cu 401)
        # oprește bucla chiar dacă detectorul global failure nu-l atinge
        if is_failure:
            per_tool_abort = self._agent_cfg.get("per_tool_failure_abort", 6)
            if self._tool_failure_counts[name] >= per_tool_abort:
                raise self._loop_abort(
                    "per_tool_failure",
                    name,
                    self._tool_failure_counts[name],
                    raw_arguments,
                    message,
                    result_recorded=True,
                )
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
        """Construiește oprirea de buclă: excepția cu ultimele apeluri din
        fereastră, plus `event_data` pentru evenimentul `loop_aborted` —
        emis de agent.py în catch-and-re-raise (aici nu se emit evenimente),
        iar excepția e ridicată de apelant și prinsă de handlerele
        `except AgentError`.
        `result_recorded` = apelul curent e deja în fereastră (opusul: oprire
        înainte de execuție, când rezultatul lui nu există încă)."""
        entries = list(self._recent_results)
        if not result_recorded:
            entries.append((tool, raw_arguments, message))
        return AgentLoopAborted(detector, tool, count, entries[-5:])
