"""Notificări "task ready" prin canalele externe ale lui Sorin (Telegram,
SMS), declanșate server-side la finalul unei rulări — nu depind de un tab
de browser deschis, ca notify.js. Reutilizează scripturile Hermes
existente (fire-and-forget, best-effort: o eroare aici nu trebuie să
strice rularea agentului)."""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("agent-zero-light.notifications")

_HERMES_SCRIPTS = Path.home() / ".hermes" / "scripts"
_TELEGRAM_SCRIPT = _HERMES_SCRIPTS / "claude_notify_telegram.py"
_SMS_SCRIPT = _HERMES_SCRIPTS / "hermes-sms-notify.sh"
_SUBPROCESS_TIMEOUT_SECONDS = 30

# asyncio ține doar o referință weak la task-uri — fără o referință forte
# în viață cât timp task-ul rulează, garbage collector-ul îl poate distruge
# la mijlocul execuției, fără nicio eroare vizibilă (exact simptomul observat:
# testul manual al scriptului mergea, dar cel declanșat din aplicație nu).
_background_tasks: set[asyncio.Task] = set()


async def _run(*args: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SUBPROCESS_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("notificare: %s a depășit timeout-ul de %ss", args[0], _SUBPROCESS_TIMEOUT_SECONDS)
            return
        if proc.returncode != 0:
            logger.warning("notificare: %s a ieșit cu codul %s: %s", args[0], proc.returncode, stderr.decode(errors="replace").strip())
    except Exception:
        logger.exception("notificare: eroare la lansarea %s", args[0])


def _spawn(*args: str) -> None:
    task = asyncio.create_task(_run(*args))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def notify_task_ready(session_title: str, settings: dict) -> None:
    """Fire-and-forget: lansează notificările activate în settings pentru
    chat_id-ul utilizat implicit de scripturile Hermes (Sorin)."""
    message = f"Task ready - {session_title}"
    if settings.get("notify_telegram"):
        _spawn("python3", str(_TELEGRAM_SCRIPT), "notify", message)
    if settings.get("notify_sms"):
        _spawn(str(_SMS_SCRIPT), message)
