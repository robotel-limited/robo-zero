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
import path_access
import project_context
import project_hooks
import settings_store
import usage_log
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
        # momentul în care acest chat a primit ultima oară fișa proiectului
        # (0 = niciodată) — baza diferențelor trimise la turele următoare
        self.facts_seen_at = 0.0
        # tura curentă (vezi start_run/_run) — permite ștergerea ei ulterioară
        # din agent.history, nu doar din jurnalul UI (vezi delete_turn)
        self._current_turn_id: str | None = None
        self._turn_history_start = 0
        self._turn_invalidated_by_compaction = False
        self.agent = Agent(
            confirm=self._confirm,
            on_event=self._on_agent_event,
            project_id=project_id,
            workdir=path_access.project_workdir(project_id),
        )
        self.agent.on_compacted = self._on_history_compacted
        self._subscribers: list[asyncio.Queue] = []
        self._running_task: asyncio.Task | None = None
        self._pending_approval: asyncio.Future | None = None

    # --- memoria proiectului în context ------------------------------------------

    def _on_history_compacted(self) -> None:
        """Compactarea (history.py) rescrie agent.history într-un rezumat —
        orice limite de tură memorate pe mesaje deja absorbite în rezumat nu
        mai corespund la nimic ștergibil, așa că le invalidăm (delete_turn
        nu le va mai găsi ca ștergibile). Dacă e în curs o tură activă,
        marcăm și pe ea ca nemaifiind ștergibilă (vezi _run)."""
        for entry in self.log:
            entry.pop("history_start", None)
            entry.pop("history_end", None)
        if self._current_turn_id is not None:
            self._turn_invalidated_by_compaction = True
        self._refresh_project_context()

    def _refresh_project_context(self) -> None:
        """(Re)construiește blocul de proiect din system prompt; dacă system
        prompt-ul există deja (chat mutat între proiecte, istoric compactat),
        îl înlocuiește pe loc."""
        self.facts_seen_at = time.time()
        self.agent.project_prompt = (
            project_context.build_initial(self.project_id, self.id) if self.project_id else None
        )
        if self.agent.history and self.agent.history[0].get("role") == "system":
            self.agent.history[0] = {"role": "system", "content": self.agent._system_prompt()}

    def _prepare_message(self, user_message: str) -> str:
        if not self.project_id:
            return user_message
        project_hooks.before_message(self)
        if not self.agent.history or self.facts_seen_at == 0:
            self._refresh_project_context()
            return user_message
        since = self.facts_seen_at
        self.facts_seen_at = time.time()
        lines = project_context.diff_since(self.project_id, since, self.id)
        if not lines:
            return user_message
        self._append_log("project_context_refreshed", lines=lines)
        return project_context.diff_prefix(lines) + user_message

    # --- stare rulare -----------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running_task is not None and not self._running_task.done()

    def start_run(self, user_message: str, images: list[str] | None = None) -> None:
        """Pornește bucla agentului ca task asyncio (un singur run activ per chat).
        `images` = capturile atașate (căi /files/uploads/..., vezi attachments.py)."""
        if self.is_running:
            raise RuntimeError("Există deja o rulare activă pentru acest chat.")
        images = images or []
        self.agent.cancelled = False
        self._current_turn_id = uuid.uuid4().hex[:8]
        # oglindește verificarea din Agent._run — dacă e primul mesaj al
        # chat-ului, prompt-ul de system trebuie exclus din tura ștergibilă
        # (altfel ștergerea primei ture l-ar lua cu ea)
        if not self.agent.history:
            self.agent.history.append({"role": "system", "content": self.agent._system_prompt()})
        self._turn_history_start = len(self.agent.history)
        self._turn_invalidated_by_compaction = False
        self._append_log("user", text=user_message, **({"images": images} if images else {}))
        self._running_task = asyncio.create_task(self._run(self._prepare_message(user_message), images))

    async def _run(self, user_message: str, images: list[str]) -> None:
        usage_log.set_scope(chat_id=self.id, project_id=self.project_id)
        try:
            await self.agent.run(user_message, images)
        except AgentCancelled:
            self.agent.history = history.sanitize(self.agent.history)
            self._append_log("cancelled", text="Rulare anulată de utilizator.")
        except AgentError as e:
            self._append_log("error", text=str(e))
        except Exception as e:  # defensiv: o eroare neprevăzută nu trebuie să rămână tăcută
            self._append_log("error", text=f"Eroare neprevăzută: {e}")
        finally:
            self._close_turn()
            self._save()
            try:
                project_hooks.after_run(self)
            except Exception as e:  # memoria proiectului nu trebuie să strice chat-ul
                self._append_log("error", text=f"Memoria proiectului: {e}")
            notify_task_ready(self.title, settings_store.load())

    def _close_turn(self) -> None:
        """Fixează limitele turei curente ([history_start, history_end)) pe
        entry-ul terminal (final/error/cancelled) din jurnal, dacă tura n-a
        fost invalidată de o compactare în timpul rulării (vezi
        _on_history_compacted). Permite delete_turn să șteargă exact mesajele
        acelei ture din agent.history, nimic mai mult/puțin."""
        turn_id = self._current_turn_id
        self._current_turn_id = None
        if turn_id is None or self._turn_invalidated_by_compaction:
            return
        history_end = len(self.agent.history)
        for entry in reversed(self.log):
            if entry.get("turn_id") == turn_id and entry.get("type") in ("final", "error", "cancelled"):
                entry["history_start"] = self._turn_history_start
                entry["history_end"] = history_end
                return

    def rename(self, title: str) -> None:
        self.title = title[:TITLE_MAX_CHARS]
        self.updated_at = time.time()
        self._save()

    def set_project(self, project_id: str | None) -> None:
        """Atribuie/dezatribuie chat-ul unui proiect (din pagina de chat sau
        din pagina Proiecte). Contextul de proiect din system prompt se
        înlocuiește imediat, inclusiv într-un chat cu istoric."""
        old_project_id = self.project_id
        self.project_id = project_id
        self.agent.project_id = project_id
        self.agent.workdir = path_access.project_workdir(project_id)
        self.agent.tools = self.agent._build_tools()
        self._refresh_project_context()
        self.updated_at = time.time()
        self._save()
        project_hooks.on_project_changed(self, old_project_id)

    def request_cancel(self) -> bool:
        if not self.is_running:
            return False
        self.agent.request_cancel()
        if self._pending_approval is not None and not self._pending_approval.done():
            self._pending_approval.set_result(False)
        return True

    # --- ștergerea unei ture (mesaj user + tool calls + răspuns final) -----

    def delete_turn(self, turn_id: str) -> bool:
        """Șterge o tură întreagă — din jurnalul UI ȚI din agent.history —
        identificată prin turn_id (vezi _append_log/_close_turn). Nu se poate
        șterge doar bula de răspuns: modelul ar rămâne cu mesajul user care a
        provocat-o (adică exact informația greșită pe care utilizatorul vrea
        eliminată) sau cu un `tool_calls` fără rezultatele lui, pe care
        DeepSeek îl respinge. Întoarce False dacă tura nu mai poate fi
        ștears (deja compactată într-un rezumat, sau încă în curs)."""
        if self.is_running:
            raise RuntimeError("Nu se poate șterge cât timp o rulare este activă.")
        terminal = next(
            (e for e in self.log if e.get("turn_id") == turn_id and "history_start" in e),
            None,
        )
        if terminal is None:
            return False
        start, end = terminal["history_start"], terminal["history_end"]
        removed = end - start
        del self.agent.history[start:end]
        self.log = [e for e in self.log if e.get("turn_id") != turn_id]
        for entry in self.log:
            if entry.get("history_start") is not None and entry["history_start"] >= end:
                entry["history_start"] -= removed
                entry["history_end"] -= removed
        self.updated_at = time.time()
        self._save()
        self._broadcast({"type": "turn_deleted", "ts": self.updated_at, "turn_id": turn_id})
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
        if self._current_turn_id is not None:
            entry["turn_id"] = self._current_turn_id
        self.log.append(entry)
        self.updated_at = entry["ts"]
        if event_type == "user" and self.title == "Conversație nouă" and str(data.get("text", "")).strip():
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
            "facts_seen_at": self.facts_seen_at,
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
        session.facts_seen_at = data.get("facts_seen_at", 0.0)
        session.agent.history = history.sanitize(data.get("agent_history", []))
        return session
