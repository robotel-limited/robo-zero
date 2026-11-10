"""Regulile învățate ale unui proiect: corecții ale lui Sorin asupra
comportamentului agentului, extrase automat dar aplicate în context doar
după aprobarea lui pe cardul proiectului. Același SQLite ca
project_memory_store.py (data/projects.db)."""

import difflib
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent
_DB_PATH = _PROJECT_ROOT / "data" / "projects.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# pragurile ciclului de viață (Etapa 1: near_match_threshold) — citite cu
# .get(), un settings.yaml vechi fără blocul rules merge cu valorile implicite
with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as f:
    _RULES_CFG = (yaml.safe_load(f) or {}).get("rules") or {}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                supersedes INTEGER,
                superseded_by INTEGER,
                source_chat_id TEXT,
                source_chat_title TEXT,
                created_at REAL NOT NULL,
                decided_at REAL
            )
            """
        )
        # Etapa 1: coloanele de viață adăugate idempotent pe un DB vechi
        # (PRAGMA table_info -> doar ce lipsește), apoi sightings
        cols = {row[1] for row in conn.execute("PRAGMA table_info(project_rules)")}
        for name, ddl in (
            ("scope", "TEXT NOT NULL DEFAULT 'project'"),
            ("times_seen", "INTEGER NOT NULL DEFAULT 1"),
            ("last_seen_at", "REAL"),
            ("decided_by", "TEXT"),
        ):
            if name not in cols:
                conn.execute(f"ALTER TABLE project_rules ADD COLUMN {name} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_project ON project_rules(project_id, status)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rule_sightings (
                rule_id INTEGER NOT NULL,
                source_chat_id TEXT,
                seen_at REAL NOT NULL,
                PRIMARY KEY (rule_id, source_chat_id)
            )
            """
        )


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


# ce se taie de la finalul unui text înainte de comparare (punctuație + spații)
_PUNCT_FINAL = " \t.,;:!?…\"'”’)]}»"


def _normalize(text: str) -> str:
    """lowercase, spații colapsate, strip, punctuație finală tăiată —
    baza oricărei comparații near-match."""
    s = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    return s.rstrip(_PUNCT_FINAL).strip()


def _ratio(a: str, b: str) -> float:
    """difflib pe textele normalizate — aceeași formulă oriunde se compară
    două reguli (find_near_match, cleanup-ul de near-dupe)."""
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def find_near_match(
    project_id: str,
    text: str,
    threshold: float | None = None,
    statuses: Any = None,
) -> dict[str, Any] | None:
    """Cea mai apropiată regulă existentă din proiect: raportul maxim
    (difflib pe text normalizat) care atinge `threshold` (pragul din config
    cât timp None), altfel None. `statuses` limitează statusurile; None =
    orice status."""
    if threshold is None:
        threshold = float(_RULES_CFG.get("near_match_threshold", 0.85))
    mine = _normalize(text)
    if not mine:
        return None
    if isinstance(statuses, str):
        statuses = (statuses,)
    sql = "SELECT * FROM project_rules WHERE project_id = ?"
    params: list[Any] = [project_id]
    if statuses:
        sql += " AND status IN (%s)" % ", ".join("?" for _ in statuses)
        params.extend(statuses)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    best: dict[str, Any] | None = None
    best_ratio = threshold
    for row in rows:
        ratio = _ratio(mine, row["text"])
        if ratio >= threshold and (best is None or ratio > best_ratio):
            best, best_ratio = dict(row), ratio
    return best


def record_sighting(rule_id: int, source_chat_id: str | None) -> None:
    """INSERT OR IGNORE în rule_sightings (PK rule_id+chat: același chat nu
    dublează), apoi times_seen = COUNT(sightings) și last_seen_at = acum."""
    now = time.time()
    chat = source_chat_id or ""
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO rule_sightings (rule_id, source_chat_id, seen_at) VALUES (?, ?, ?)",
            (rule_id, chat, now),
        )
        conn.execute(
            "UPDATE project_rules SET times_seen = "
            "(SELECT COUNT(*) FROM rule_sightings WHERE rule_id = ?), last_seen_at = ? WHERE id = ?",
            (rule_id, now, rule_id),
        )


def distinct_chats(rule_id: int) -> int:
    """Nr. de chat-uri distincte care au văzut regula (rânduri în sightings)."""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM rule_sightings WHERE rule_id = ?", (rule_id,)).fetchone()
    return int(row[0])


def add_rule(
    project_id: str,
    text: str,
    category: str = "",
    source_chat_id: str | None = None,
    source_chat_title: str | None = None,
    supersedes: int | None = None,
) -> int:
    with _connect() as conn:
        now = time.time()
        cur = conn.execute(
            "INSERT INTO project_rules (project_id, category, text, supersedes, "
            "source_chat_id, source_chat_title, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, category, text, supersedes, source_chat_id, source_chat_title, now, now),
        )
        return int(cur.lastrowid)


def add_or_merge(
    project_id: str,
    text: str,
    category: str = "",
    source_chat_id: str | None = None,
    source_chat_title: str | None = None,
    supersedes: int | None = None,
) -> tuple[int, bool]:
    """(rule_id, created): un near-match pending/aprovat primește o sighting
    și nu se mai creează rând nou; altfel se creează regula nouă cu o
    sighting-semilă. Un `supersedes` explicit ocolește comasarea — o
    înlocuire e intenționată, nu dublare."""
    if supersedes is None:
        match = find_near_match(project_id, text, statuses=("pending", "approved"))
        if match is not None:
            record_sighting(int(match["id"]), source_chat_id)
            return int(match["id"]), False
    rule_id = add_rule(project_id, text, category, source_chat_id, source_chat_title, supersedes=supersedes)
    record_sighting(rule_id, source_chat_id)
    return rule_id, True


def get_rule(rule_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        return _row(conn.execute("SELECT * FROM project_rules WHERE id = ?", (rule_id,)).fetchone())


def list_for(project_id: str, status: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM project_rules WHERE project_id = ? AND status = ? "
                "ORDER BY category, created_at",
                (project_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM project_rules WHERE project_id = ? ORDER BY category, created_at",
                (project_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def approved_rules_since(project_id: str, ts: float) -> list[dict[str, Any]]:
    """Regulile aprobate după `ts`, cu textul regulii pe care au înlocuit-o
    (dacă e cazul) — baza pentru diferențele trimise unui chat deja deschis."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT r.*, old.text AS replaces_text FROM project_rules r "
            "LEFT JOIN project_rules old ON old.superseded_by = r.id "
            "WHERE r.project_id = ? AND r.status = 'approved' AND r.decided_at > ? "
            "ORDER BY r.decided_at",
            (project_id, ts),
        ).fetchall()
    return [dict(r) for r in rows]


def update_text(rule_id: int, text: str | None = None, category: str | None = None) -> dict[str, Any] | None:
    updates: dict[str, Any] = {}
    if text is not None:
        updates["text"] = str(text).strip()
    if category is not None:
        updates["category"] = str(category).strip()
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with _connect() as conn:
            conn.execute(f"UPDATE project_rules SET {set_clause} WHERE id = ?", (*updates.values(), rule_id))
    return get_rule(rule_id)


def approve(rule_id: int) -> dict[str, Any] | None:
    rule = get_rule(rule_id)
    if rule is None or rule["status"] != "pending":
        return None
    now = time.time()
    with _connect() as conn:
        conn.execute("UPDATE project_rules SET status = 'approved', decided_at = ? WHERE id = ?", (now, rule_id))
        if rule["supersedes"] is not None:
            old = get_rule(rule["supersedes"])
            if old is not None and old["status"] == "approved":
                conn.execute(
                    "UPDATE project_rules SET status = 'superseded', superseded_by = ?, decided_at = ? "
                    "WHERE id = ?",
                    (rule_id, now, old["id"]),
                )
    return get_rule(rule_id)


def reject(rule_id: int) -> dict[str, Any] | None:
    rule = get_rule(rule_id)
    if rule is None or rule["status"] != "pending":
        return None
    with _connect() as conn:
        conn.execute("UPDATE project_rules SET status = 'rejected', decided_at = ? WHERE id = ?", (time.time(), rule_id))
    return get_rule(rule_id)


def sweep_lifecycle(project_id: str) -> dict[str, Any]:
    """Auto-aprobă pending cu distinct_chats >= K; expiră pending vechi cu times_seen==1.
    Întoarce {"approved": [rule_id, ...], "expired": [rule_id, ...]}."""
    K = int(_RULES_CFG.get("promote_distinct_chats", 3))
    M = int(_RULES_CFG.get("expire_pending_days", 30))
    cutoff = time.time() - M * 86400

    approved_ids: list[int] = []
    expired_ids: list[int] = []

    for rule in list_for(project_id, status="pending"):
        rid = int(rule["id"])
        if distinct_chats(rid) >= K:
            approve(rid)
            with _connect() as conn:
                conn.execute("UPDATE project_rules SET decided_by = 'auto' WHERE id = ?", (rid,))
            approved_ids.append(rid)
        elif (rule["last_seen_at"] or rule["created_at"]) < cutoff and int(rule["times_seen"]) == 1:
            reject(rid)
            with _connect() as conn:
                conn.execute("UPDATE project_rules SET decided_by = 'auto' WHERE id = ?", (rid,))
            expired_ids.append(rid)

    return {"approved": approved_ids, "expired": expired_ids}


def list_global(status: str = "approved") -> list[dict[str, Any]]:
    """Listează regulile globale (scope='global') filtrate pe status."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_rules WHERE scope = 'global' AND status = ? ORDER BY category, created_at",
            (status,),
        ).fetchall()
    return [dict(r) for r in rows]


def propose_global(text: str, category: str = "", origin_project_id: str | None = None) -> int:
    """Creează o regulă globală pending (scope='global'). Dedup near-match pe globalele
    existente (orice status): dacă există una similară, nu mai adaugă."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_rules WHERE scope = 'global'",
        ).fetchall()
    mine = _normalize(text)
    threshold = float(_RULES_CFG.get("near_match_threshold", 0.85))
    for row in rows:
        if _ratio(mine, row["text"]) >= threshold:
            return int(row["id"])
    rule_id = add_rule(
        project_id=origin_project_id or "",
        text=text,
        category=category,
        source_chat_id=None,
        source_chat_title=None,
    )
    with _connect() as conn:
        conn.execute("UPDATE project_rules SET scope = 'global' WHERE id = ?", (rule_id,))
    return rule_id


def suggest_globals() -> list[int]:
    """Grupează regulile 'approved' scope='project' near-match CROSS-proiect.
    Un grup prezent în ≥N proiecte distincte și fără global near-match existent
    → propose_global (pending scope='global'). Întoarce lista de rule_id propuse."""
    N = int(_RULES_CFG.get("global_min_projects", 3))
    threshold = float(_RULES_CFG.get("near_match_threshold", 0.85))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_rules WHERE scope = 'project' AND status = 'approved'"
        ).fetchall()
    approved = [dict(r) for r in rows]

    # cluster single-linkage pe textele aprobate cross-proiect
    n = len(approved)
    parent = list(range(n))

    def _root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if _ratio(approved[i]["text"], approved[j]["text"]) >= threshold:
                ri, rj = _root(i), _root(j)
                if ri != rj:
                    parent[rj] = ri

    groups: dict[int, list[dict]] = {}
    for idx in range(n):
        groups.setdefault(_root(idx), []).append(approved[idx])

    proposed: list[int] = []
    for cluster in groups.values():
        pids = {r["project_id"] for r in cluster}
        if len(pids) < N:
            continue
        # reprezentantul: regula cea mai lungă din cluster
        rep = max(cluster, key=lambda r: len(r["text"]))
        rule_id = propose_global(rep["text"], rep["category"], rep["project_id"])
        # propose_global întoarce id-ul unui existent dacă era near-match — nu dublăm
        if rule_id not in proposed:
            proposed.append(rule_id)
    return proposed


def delete_project(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_rules WHERE project_id = ?", (project_id,))


def _loop_rule_text(detector: str, tool: str, count: int) -> str:
    mesaje = {
        'failure': (f"Tool-ul '{tool}' a întors erori repetat. Nu relua același apel cu "
                    'aceleași argumente — schimbă sursa/abordarea sau oprește-te și raportează blocajul.'),
        'cycle': (f"Reapare aceeași secvență de apeluri la '{tool}'. Dacă nu avansează, "
                  'schimbă strategia în loc să reiei ciclul.'),
        'noprogress': (f"'{tool}' întoarce mereu același rezultat. Nu-l mai apela cu aceleași "
                       'argumente — schimbă abordarea.'),
        'consecutive': (f"'{tool}' apelat identic de mai multe ori la rând fără progres. "
                        'Schimbă argumentele sau abordarea.'),
        'per_tool_failure': f"Evită să apelez tool-ul {tool!r} mai mult de câteva ori dacă returnează mereu eroare.",
    }
    return mesaje.get(detector, (f"Buclă oprită (detector {detector}) pe tool-ul '{tool}'. "
                                 f'Evită repetarea aceluiași apel {tool} când nu aduce progres — '
                                 'schimbă abordarea sau raportează blocajul.'))


def propose_from_loop_abort(project_id, detector, tool, count, source_chat_id=None, source_chat_title=None):
    '''Etapa C: oprirea de buclă -> regulă candidată pending. Text = lecție GENERALĂ
    (fără argumente/URL din .entries). Etapa 1: dedup-ul e near-match, nu doar text
    identic — un apropiat pending/aprovat primește sighting și nu se mai creează rând
    nou; un apropiat cu alt status (respins/superseded) blochează re-propunerea, ca
    dedup-ul vechi pe orice status.'''
    project_id = (project_id or '').strip()
    tool = (tool or '').strip()
    detector = (detector or '').strip()
    if not project_id or not tool:
        return None
    text = _loop_rule_text(detector, tool, count)
    match = find_near_match(project_id, text)
    if match is not None and match["status"] not in ("pending", "approved"):
        return None
    return add_or_merge(project_id, text, 'buclă', source_chat_id, source_chat_title)


init_db()
