"""O conversație: istoric LLM (agent.history) + jurnal pentru UI (self.log),
coadă de evenimente SSE, aprobare interactivă, anulare, persistare pe disc.
"""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import history
import projects_store
import settings_store
from agent import Agent, AgentCancelled, AgentError
from notifications import notify_task_ready

_PROJECT_ROOT = Path(__file__).resolve().parent
CHATS_DIR = _PROJECT_ROOT / "data" / "chats"
CHATS_DIR.mkdir(parents=True, exist_ok=True)

APPROVAL_TIMEOUT_SECONDS = 600
TITLE_MAX_CHARS = 60


class ChatSession:
    def __init__(self, chat_id: str | None = None, project_id: str | None = None) -> None:
        self.id = chat_id or uuid.uuid4().hex[:12]
        self.title = "Conversație nouă"
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.project_id = project_id
        self.log: list[dict[str, Any]] = []  # jurnal pentru UI, persistat
        self.agent = Agent(
            confirm=self._confirm,
            on_event=self._on_agent_event,
            project_prompt=self._project_prompt(),
        )
        self._subscribers: list[asyncio.Queue] = []
        self._running_task: asyncio.Task | None = None
        self._pending_approval: asyncio.Future | None = None

    def _project_prompt(self) -> str | None:
        return projects_store.get_prompt(self.project_id) if self.project_id else None

    # --- stare rulare -----------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running_task is not None and not self._running_task.done()

    def start_run(self, user_message: str) -> None:
        """Pornește bucla agentului ca task asyncio (un singur run activ per chat)."""
        if self.is_running:
            raise RuntimeError("Există deja o rulare activă pentru acest chat.")
        self.agent.cancelled = False
        self._append_log("user", text=user_message)
        self._running_task = asyncio.create_task(self._run(user_message))

    async def _run(self, user_message: str) -> None:
        try:
            await self.agent.run(user_message)
        except AgentCancelled:
            self.agent.history = history.sanitize(self.agent.history)
            self._append_log("cancelled", text="Rulare anulată de utilizator.")
        except AgentError as e:
            self._append_log("error", text=str(e))
        except Exception as e:  # defensiv: o eroare neprevăzută nu trebuie să rămână tăcută
            self._append_log("error", text=f"Eroare neprevăzută: {e}")
        finally:
            self._save()
            notify_task_ready(self.title, settings_store.load())

    def rename(self, title: str) -> None:
        self.title = title[:TITLE_MAX_CHARS]
        self.updated_at = time.time()
        self._save()

    def set_project(self, project_id: str | None) -> None:
        """Atribuie/dezatribuie chat-ul unui proiect (din pagina de chat sau
        din pagina Proiecte). Promptul proiectului e aplicat doar dacă system
        prompt-ul nu a fost încă generat (prima rulare) — vezi Agent._system_prompt."""
        self.project_id = project_id
        self.agent.project_prompt = self._project_prompt()
        self.updated_at = time.time()
        self._save()

    def request_cancel(self) -> bool:
        if not self.is_running:
            return False
        self.agent.request_cancel()
        if self._pending_approval is not None and not self._pending_approval.done():
            self._pending_approval.set_result(False)
        return True

    # --- aprobare -----------------------------------------------------------

    async def _confirm(self, tool_name: str, raw_arguments: str) -> bool:
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_approval = future
        self._append_log("approval_required", tool_name=tool_name, arguments=raw_arguments)
        try:
            approved = await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            approved = False
            self._append_log("approval_resolved", approved=False, reason="timeout")
        else:
            self._append_log("approval_resolved", approved=approved, reason="user")
        finally:
            self._pending_approval = None
        return approved

    def resolve_approval(self, approved: bool) -> bool:
        if self._pending_approval is None or self._pending_approval.done():
            return False
        self._pending_approval.set_result(approved)
        return True

    # --- evenimente agent → jurnal + SSE ------------------------------------

    def _on_agent_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._append_log(event_type, **data)

    def _append_log(self, event_type: str, **data: Any) -> None:
        entry = {"type": event_type, "ts": time.time(), **data}
        self.log.append(entry)
        self.updated_at = entry["ts"]
        if event_type == "user" and self.title == "Conversație nouă":
            text = str(data.get("text", ""))
            self.title = text[:TITLE_MAX_CHARS] + ("…" if len(text) > TITLE_MAX_CHARS else "")
        self._broadcast(entry)
        self._save()

    # --- SSE -----------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def _broadcast(self, entry: dict[str, Any]) -> None:
        for queue in self._subscribers:
            queue.put_nowait(entry)

    # --- persistare ------------------------------------------------------------

    def _path(self) -> Path:
        return CHATS_DIR / f"{self.id}.json"

    def _save(self) -> None:
        payload = {
            "id": self.id,
            "title": self.title,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "log": self.log,
            "agent_history": self.agent.history,
        }
        self._path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ChatSession":
        data = json.loads(path.read_text(encoding="utf-8"))
        session = cls(chat_id=data["id"], project_id=data.get("project_id"))
        session.title = data.get("title", "Conversație nouă")
        session.created_at = data.get("created_at", time.time())
        session.updated_at = data.get("updated_at", session.created_at)
        session.log = data.get("log", [])
        session.agent.history = history.sanitize(data.get("agent_history", []))
        return session
