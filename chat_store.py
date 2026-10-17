"""Registru în memorie al conversațiilor: creare, listare, încărcare de pe
disc la pornirea serviciului (vezi plan, secțiunea „Funcții preluate din
cele 22 de hook-uri": salvare chat-uri, reîncărcate la pornire).
"""

from chat_session import CHATS_DIR, ChatSession

_sessions: dict[str, ChatSession] = {}


def load_all() -> None:
    """Încarcă toate chat-urile salvate pe disc în registru. Apelat o dată,
    la pornirea aplicației (app.py, startup)."""
    for path in sorted(CHATS_DIR.glob("*.json")):
        try:
            session = ChatSession.load(path)
        except Exception:
            continue
        _sessions[session.id] = session


def create() -> ChatSession:
    session = ChatSession()
    _sessions[session.id] = session
    return session


def get(chat_id: str) -> ChatSession | None:
    return _sessions.get(chat_id)


def delete(chat_id: str) -> bool:
    session = _sessions.pop(chat_id, None)
    if session is None:
        return False
    session._path().unlink(missing_ok=True)
    return True


def list_summaries() -> list[dict]:
    items = [
        {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in _sessions.values()
    ]
    return sorted(items, key=lambda x: x["updated_at"], reverse=True)
