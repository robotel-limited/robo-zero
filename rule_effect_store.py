"""Efectul regulilor: în ce chat-uri s-au injectat (expuneri) și de câte ori
a reapărut problema în acele chat-uri după injectare (recurențe) -> rata de
prevenire. Codul stă aici, nu în project_rules_store.py, fiindcă acel fișier
trece de 300 linii (fallback din specificație) — folosește _connect din el."""

import time
from typing import Any

import project_rules_store as _prs

_DDL = (
    "CREATE TABLE IF NOT EXISTS rule_exposures ("
    "rule_id INTEGER NOT NULL, "
    "chat_id TEXT NOT NULL, "
    "exposed_at REAL NOT NULL, "
    "PRIMARY KEY(rule_id, chat_id))"
)


def _ensure(conn: Any) -> None:
    """CREATE TABLE IF NOT EXISTS rule_exposures — idempotent, ca init_db."""
    conn.execute(_DDL)


def init_db() -> None:
    """Tabelul rule_exposures pe DB-ul curent (echivalentul init_db pentru acest modul)."""
    with _prs._connect() as conn:
        _ensure(conn)


def record_exposures(rule_ids: list[int], chat_id: str | None) -> None:
    """O expunere per (regulă, chat) = regula a fost injectată în acel chat.
    INSERT OR IGNORE pe PK (rule_id, chat_id): re-injectarea nu dublează.
    No-op dacă lipsește chat_id sau lista e goală."""
    if not chat_id or not rule_ids:
        return
    now = time.time()
    with _prs._connect() as conn:
        _ensure(conn)
        conn.executemany(
            "INSERT OR IGNORE INTO rule_exposures (rule_id, chat_id, exposed_at) VALUES (?, ?, ?)",
            [(int(rid), chat_id, now) for rid in rule_ids],
        )


def rule_effect(rule_id: int) -> dict[str, Any]:
    """exposures = chat-urile în care regula a fost injectată; recurrences =
    sightings din ACELE chat-uri cu seen_at > exposed_at din acel chat;
    prevention_rate = 1 - recurrences/exposures (None când exposures == 0)."""
    with _prs._connect() as conn:
        _ensure(conn)
        exposures = int(
            conn.execute(
                "SELECT COUNT(*) FROM rule_exposures WHERE rule_id = ?", (rule_id,)
            ).fetchone()[0]
        )
        recurrences = int(
            conn.execute(
                "SELECT COUNT(*) FROM rule_sightings s "
                "JOIN rule_exposures e ON e.rule_id = s.rule_id AND e.chat_id = s.source_chat_id "
                "WHERE s.rule_id = ? AND s.seen_at > e.exposed_at",
                (rule_id,),
            ).fetchone()[0]
        )
    rate = None if exposures == 0 else 1 - recurrences / exposures
    return {"exposures": exposures, "recurrences": recurrences, "prevention_rate": rate}


def effect_summary(min_exposures: int = 3) -> list[dict[str, Any]]:
    """Regulile cu exposures >= min_exposures: id, project_id, scope, status,
    text, exposures, recurrences, prevention_rate — crescător după
    prevention_rate (cele mai slabe întâi)."""
    with _prs._connect() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT r.id, r.project_id, r.scope, r.status, r.text, "
            "COUNT(*) AS exposures, "
            "SUM(CASE WHEN s.rule_id IS NOT NULL AND s.seen_at > e.exposed_at "
            "THEN 1 ELSE 0 END) AS recurrences "
            "FROM rule_exposures e "
            "JOIN project_rules r ON r.id = e.rule_id "
            "LEFT JOIN rule_sightings s ON s.rule_id = e.rule_id "
            "AND s.source_chat_id = e.chat_id "
            "GROUP BY r.id",
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        exposures = int(row["exposures"])
        if exposures < min_exposures:
            continue
        recurrences = int(row["recurrences"] or 0)
        out.append(
            {
                "id": int(row["id"]),
                "project_id": row["project_id"],
                "scope": row["scope"],
                "status": row["status"],
                "text": row["text"],
                "exposures": exposures,
                "recurrences": recurrences,
                "prevention_rate": 1 - recurrences / exposures,
            }
        )
    out.sort(key=lambda d: d["prevention_rate"])
    return out


# A.1 din specificație numește aceste funcții pe project_rules_store, dar
# fișierul e peste 300 linii -> codul stă aici, expus și pe magazinul de
# reguli pentru apelanții care caută pe acolo (init_db nu se supra-scrie)
for _name in ("record_exposures", "rule_effect", "effect_summary"):
    setattr(_prs, _name, globals()[_name])
