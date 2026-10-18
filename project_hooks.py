"""Legătura dintre ciclul de viață al unui chat (rulare, mutare între
proiecte, ștergere) și memoria proiectului (arhivă, rezumate, fapte).
chat_session.py, chat_store.py și app.py apelează doar funcțiile de aici."""

from typing import Any

import project_archive
import project_digest
import project_memory_store


def reindex(session: Any) -> None:
    if session.project_id:
        project_archive.reindex_chat(session.id, session.project_id, session.title, session.log)


def after_run(session: Any) -> None:
    """Apelat la finalul fiecărei rulări (inclusiv anulate — arhiva trebuie
    să conțină tot ce s-a spus, iar faptele afirmate de utilizator rămân
    fapte și dacă răspunsul agentului a fost întrerupt)."""
    reindex(session)
    project_digest.schedule(session)


def on_project_changed(session: Any, old_project_id: str | None) -> None:
    """Chat-ul iese din proiectul vechi (rezumatul și arhiva lui pleacă de
    acolo; faptele extrase din el rămân) și intră în cel nou de la zero."""
    if old_project_id and old_project_id != session.project_id:
        project_archive.remove_chat(session.id)
        project_memory_store.delete_digest(session.id)
    reindex(session)


def on_chat_deleted(chat_id: str) -> None:
    project_archive.remove_chat(chat_id)
    project_memory_store.delete_digest(chat_id)


def on_project_deleted(project_id: str) -> None:
    project_archive.remove_project(project_id)
    project_memory_store.delete_project(project_id)
