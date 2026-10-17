"""Tool pentru execuția de comenzi shell în workdir, cu confirmare pentru
comenzile din denylist (safety.requires_confirmation) și limite de timeout/output.
"""

import asyncio
import os
import signal
from typing import Any

import safety
from tools.tool_base import Tool, ToolResult


def _kill_process_group(pid: int, sig: signal.Signals) -> None:
    """Trimite semnalul întregului grup de procese (vezi start_new_session
    în execute()), nu doar procesului shell. Folosit la timeout și la
    anulare (agent.py: request_cancel -> os.killpg prin acest helper)."""
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


class ShellExecute(Tool):
    """Execută o comandă shell în workdir. Comenzile distructive (denylist)
    cer confirmare explicită — vezi agent.py, care interceptează via
    `self.agent.confirm(...)` înainte de a apela execute()."""

    name: str = "shell_execute"
    description: str = (
        "Execută o comandă shell în workdir. Comenzile distructive "
        "(rm -rf, sudo, systemctl, kill, dd, mkfs, chmod -R, git push/reset --hard, "
        "redirectare în afara workdir) cer aprobare înainte de execuție."
    )
    parameters: dict[str, Any] = {
        "command": {
            "type": "string",
            "description": "Comanda shell de executat.",
        }
    }
    required: list[str] = ["command"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Rulează comanda cu subprocess, cwd=workdir, timeout și output trunchiat.

        Verificarea denylist-ului (safety.requires_confirmation) și cererea
        de aprobare se fac ÎNAINTE de a apela execute() — în agent.py, pe
        baza câmpului `requires_confirmation` expus de `pre_check`. Dacă
        aprobarea a fost refuzată, agent.py nu mai apelează execute().
        """
        command = kwargs.get("command", "")
        if not command:
            return ToolResult(message="Eroare: parametrul 'command' e obligatoriu.")

        workdir = getattr(self.agent, "workdir", "/srv/agent-zero-light/workdir")
        timeout = getattr(self.agent, "shell_timeout_seconds", safety.SHELL_TIMEOUT_SECONDS)

        try:
            # start_new_session=True pune comanda într-un grup de procese nou,
            # al cărui pgid == pid-ul shell-ului. Esențial pentru terminare:
            # o comandă ca "sleep 30 && echo done" pornește un copil (sleep)
            # care ține pipe-ul de stdout deschis chiar dacă shell-ul moare —
            # fără grup de procese propriu, kill()/terminate() pe shell NU
            # întrerupe copilul, iar communicate() rămâne blocat până la finalul lui.
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            # Expus pe agent cât timp procesul rulează, ca request_cancel() să
            # poată întrerupe întregul grup de procese (anulare din UI, etapa 2).
            self.agent.current_shell_proc = proc
            try:
                stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                _kill_process_group(proc.pid, signal.SIGKILL)
                await proc.wait()
                return ToolResult(
                    message=f"Eroare: comanda a expirat după {timeout}s și a fost întreruptă (SIGKILL)."
                )
            finally:
                self.agent.current_shell_proc = None

            output = stdout_bytes.decode("utf-8", errors="replace")
            output = safety.mask_secrets(output)
            output = safety.truncate(output, safety.SHELL_OUTPUT_LIMIT)

            exit_code = proc.returncode
            if exit_code is not None and exit_code < 0:
                return ToolResult(message=f"[întrerupt, exit code {exit_code}]\n{output}")
            return ToolResult(message=f"[exit code {exit_code}]\n{output}")

        except Exception as e:
            return ToolResult(message=f"Eroare la execuția comenzii: {e}")

    def pre_check(self, args: dict[str, Any]) -> bool:
        """True dacă comanda e în denylist și trebuie confirmată în UI
        înainte ca agent.py să apeleze execute(). Convenție comună pentru
        toate tool-urile: pre_check(args) -> bool, apelat cu dict-ul de
        argumente deja parsat din JSON."""
        return safety.requires_confirmation(args.get("command", ""))
