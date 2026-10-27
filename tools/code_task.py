"""Tool `code_task`: delegă un task de coding către harness-ul headless r0code.

De ce un subproces și nu o clasă Agent în proces: r0code are propria buclă,
propriul router de modele (free → MiMo → paid) și propriul contract JSON.
Rularea lui separat înseamnă că un model care se încurcă nu poate strica
istoricul chatului principal, iar anularea din UI omoară un singur grup de
procese (r0code + copiii lui), nu bucla părinte.

Cheile modelelor stau în ~/.config/r0code/env (chmod 600), nu aici: workdir-ul
e partajat pe SMB și orice fișier de acolo e vizibil tuturor.
"""

import asyncio
import json
import os
import signal
from typing import Any

import code_guard
import path_access
import usage_log
from tools.tool_base import Tool, ToolResult

# r0code poate fi suprascris din env (ex. pe fuji, unde e altă instalare)
R0CODE_BIN = os.environ.get(
    "R0CODE_BIN", "/srv/robo-zero/workdir/projects/ebcd14e0ea45/r0code/r0code.py"
)
DEFAULT_TIMEOUT = 1200  # 20 min: un task real cu 15-25 de pași încape lejer


def _kill_process_group(pid: int, sig: signal.Signals) -> None:
    """r0code pornește shell-uri proprii; SIGTERM pe grup le ia pe toate."""
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


class CodeTask(Tool):
    """Rulează un task de coding într-un workdir, cu modele alese automat."""

    name: str = "code_task"
    description: str = (
        "Delegatează un task de coding unui harness headless (r0code): citește/editează fișiere, "
        "rulează teste, cu router automat de modele (free → abonament MiMo → paid, cu promovare "
        "pe eșec). Folosește-l pentru sarcini multi-pas care necesită mai multe modificări de "
        "fișiere sau iterații pe teste — NU pentru o singură citire de fișier (folosește fs_read). "
        "Întoarce un raport: succes real (verificat cu `verify`, dacă e dat), rezumat, fișiere "
        "atinse, modelele folosite și de ce s-a promovat."
    )
    parameters: dict[str, Any] = {
        "task": {
            "type": "string",
            "description": "Ce trebuie făcut, în limbaj natural și cât mai concret "
                           "(fișiere, comportament așteptat, ce teste să ruleze).",
        },
        "workdir": {
            "type": "string",
            "description": "Directorul proiectului în care lucrează. Implicit: workdir-ul curent.",
        },
        "verify": {
            "type": "string",
            "description": "Comandă care decide succesul (ex. 'python3 -m unittest'). "
                           "Dacă e dată, `ok` nu mai vine de la model, ci de la exit code-ul ei. "
                           "Puternic recomandat.",
        },
        "tier": {
            "type": "string",
            "description": "Lanțul de modele: 'auto' (implicit, free→sub→paid), 'free' (0 cost, cloud), "
                           "'sub' (doar abonamentul MiMo), 'paid' (doar per token), 'local' (Ollama pe "
                           "SVAI, fără rețea externă — lent, dar nimic nu iese din rețeaua locală).",
        },
        "max_steps": {
            "type": "integer",
            "description": "Limita de pași a agentului de coding (implicit 24).",
        },
    }
    required: list[str] = ["task"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        task = (kwargs.get("task") or "").strip()
        if not task:
            return ToolResult(message="Eroare: parametrul 'task' e obligatoriu.")

        # workdir-ul cerut trebuie să fie în zona permisă agentului (workdir sau
        # folder de proiect); altfel refuzăm — r0code scrie pe disc, nu e fs_read
        workdir = kwargs.get("workdir") or getattr(
            self.agent, "workdir", "/srv/robo-zero/workdir"
        )
        full_workdir = path_access.resolve(self.agent, workdir)
        if path_access.write_policy(self.agent, full_workdir) != "ok":
            return ToolResult(
                message=f"Eroare: '{workdir}' nu e un director în care am voie să scriu "
                        f"(workdir sau folder de proiect cu drept de scriere)."
            )
        if not os.path.isdir(full_workdir):
            return ToolResult(message=f"Eroare: directorul '{workdir}' nu există.")

        guarded = code_guard.enabled(self.agent)
        tier = kwargs.get("tier") or ("sub" if guarded else "auto")
        if guarded and tier != code_guard.ALLOWED_TIER:
            return ToolResult(
                message=f"Eroare: proiectul are code_guard pornit — doar tier "
                        f"'{code_guard.ALLOWED_TIER}' (abonamentul MiMo) e permis, nu '{tier}'."
            )
        if tier not in ("auto", "free", "sub", "paid", "local"):
            return ToolResult(message=f"Eroare: tier necunoscut '{tier}' (auto/free/sub/paid/local).")
        max_steps = int(kwargs.get("max_steps") or 24)

        cmd = [
            "python3", R0CODE_BIN,
            "--workdir", full_workdir,
            "--task", task,
            "--tier", tier,
            "--max-steps", str(max_steps),
            "--json",
        ]
        verify = (kwargs.get("verify") or "").strip()
        if guarded and not verify:
            return ToolResult(
                message="Eroare: proiectul are code_guard pornit — code_task cere `verify` "
                        "(comanda de test care decide succesul), nu declarația modelului."
            )
        if verify:
            cmd += ["--verify", verify]

        try:
            # start_new_session: grup propriu de procese, ca anularea din UI
            # (request_cancel → killpg) să ia și shell-urile pornite de r0code
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=full_workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as e:
            return ToolResult(message=f"Eroare la pornirea r0code: {e}")

        self.agent.current_shell_proc = proc  # reutilizăm hook-ul de anulare
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=DEFAULT_TIMEOUT)
        except asyncio.TimeoutError:
            _kill_process_group(proc.pid, signal.SIGKILL)
            await proc.wait()
            return ToolResult(
                message=f"Eroare: task-ul de coding a depășit {DEFAULT_TIMEOUT}s și a fost oprit."
            )
        finally:
            self.agent.current_shell_proc = None

        if proc.returncode == 2:
            detail = err.decode("utf-8", "replace").strip()
            return ToolResult(message=f"r0code nu a putut porni (configurare): {detail[:800]}")

        try:
            report = json.loads(out.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            detail = err.decode("utf-8", "replace").strip()
            return ToolResult(
                message=f"r0code a ieșit fără raport JSON (exit {proc.returncode}): {detail[:800]}"
            )

        self._record_usage(report)
        text = self._format(report)
        if guarded and not report.get("ok"):
            text = text.split("\nNu raporta acest task", 1)[0] + code_guard.CODE_TASK_FAILED
        return ToolResult(message=text)

    def _record_usage(self, report: dict) -> None:
        """Consumul delegat intră în contabilitatea chatului, cu prefix `r0code:`
        ca să se distingă de apelurile LLM ale agentului principal."""
        for m in report.get("models") or []:
            u = m.get("usage") or {}
            if not u:
                continue
            usage_log.record(
                model=f"r0code:{m.get('model')}",
                purpose=f"code_task/{m.get('tier')}",
                usage={"prompt_tokens": u.get("prompt", 0), "completion_tokens": u.get("completion", 0)},
            )

    @staticmethod
    def _format(report: dict) -> str:
        """Raportul, într-un text pe care modelul părinte îl citește ușor.
        Ordinea: verdict → rezumat → fișiere → modul în care s-a ajuns acolo."""
        ok = report.get("ok")
        head = "OK (verificat)" if report.get("verified") else ("OK (nedeclarat de model, neverificat)"
                                                              if ok else "EȘEC")
        lines = [f"[{head}] {report.get('summary') or '(fără rezumat)'}"]

        files = report.get("files") or []
        if files:
            lines.append("\nFișiere modificate:")
            for f in files:
                lines.append(f"  {f.get('action', '?'):8s} {f.get('path')}  +{f.get('added', 0)}/-{f.get('removed', 0)}")

        if report.get("verify"):
            v = report["verify"]
            lines.append(f"\nVerificare: `{v.get('cmd')}` → exit {v.get('exit')}")
            tail = (v.get("output") or "").strip().splitlines()[-6:]
            if tail:
                lines += ["  " + t for t in tail]

        models = report.get("models") or []
        if models:
            used = ", ".join(
                f"{m.get('model')}({m.get('turns')}t/{m.get('tool_calls')}c)" for m in models
            )
            lines.append(f"\nModele: {used}")
        for e in report.get("escalations") or []:
            lines.append(f"  promovare: {e}")

        u = report.get("usage") or {}
        lines.append(
            f"{report.get('steps', 0)} pași, {report.get('wall_s', 0)}s, "
            f"{u.get('prompt', 0)}+{u.get('completion', 0)} tokeni"
        )
        if not ok:
            lines.append(
                "\nNu raporta acest task ca reușit. Dacă ai nevoie, reia cu --task mai specific "
                "sau folosește shell_execute/fs_* direct."
            )
        return "\n".join(lines)
