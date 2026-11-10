"""Selecția regulilor relevante pentru sarcina curentă prin embeddings robo-brain.

Interfață publică:
  select(task_text, candidates) -> list[tuple[str, dict]] | None

None înseamnă că embedding-ul a eșuat și apelantul trebuie să folosească toți candidații.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent

with open(_PROJECT_ROOT / "conf" / "settings.yaml", "r", encoding="utf-8") as _f:
    _SETTINGS = yaml.safe_load(_f) or {}

_RULES_CFG = _SETTINGS.get("rules") or {}
_REL_CFG = _RULES_CFG.get("relevance") or {}

_ENABLED: bool = _REL_CFG.get("enabled", True)
_BRAIN_URL: str = _REL_CFG.get("brain_url", "http://10.22.22.2:8931")
_BRAIN_TOKEN_ENV: str = _REL_CFG.get("brain_token_env", "ROBO_BRAIN_TOKEN")
_MAX_RULES: int = int(_REL_CFG.get("max_rules", 8))
_MIN_SCORE: float = float(_REL_CFG.get("min_score", 0.30))
_APPROVED_BONUS: float = float(_REL_CFG.get("approved_bonus", 0.10))
_TIMEOUT_S: float = float(_REL_CFG.get("timeout_s", 3))

# DB-ul partajat cu project_rules_store (același data/projects.db)
_DB_PATH: Path = _PROJECT_ROOT / "data" / "projects.db"

_DDL = (
    "CREATE TABLE IF NOT EXISTS rule_embeddings ("
    "text_hash TEXT PRIMARY KEY, "
    "model TEXT, "
    "vec TEXT, "
    "created_at REAL)"
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL)


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def _embed(texts: list[str]) -> list[list[float]] | None:
    """POST la robo-brain /embed; returnează None la orice eșec sau token lipsă."""
    token = os.environ.get(_BRAIN_TOKEN_ENV, "")
    if not token:
        _log.warning("rule_relevance: token lipsă (%s)", _BRAIN_TOKEN_ENV)
        return None
    url = _BRAIN_URL.rstrip("/") + "/embed"
    body = json.dumps({"texts": texts}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
        return data["embeddings"]
    except Exception as exc:
        _log.warning("rule_relevance: _embed eșuat: %s", exc)
        return None


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cached_embeddings(
    conn: sqlite3.Connection, hashes: list[str]
) -> dict[str, list[float]]:
    """Returnează {hash: vec} pentru hash-urile găsite în cache."""
    if not hashes:
        return {}
    placeholders = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT text_hash, vec FROM rule_embeddings WHERE text_hash IN ({placeholders})",
        hashes,
    ).fetchall()
    return {row["text_hash"]: json.loads(row["vec"]) for row in rows}


def _store_embeddings(
    conn: sqlite3.Connection,
    pairs: list[tuple[str, str, list[float]]],
) -> None:
    """Stochează (hash, model, vec) în cache."""
    now = time.time()
    conn.executemany(
        "INSERT OR REPLACE INTO rule_embeddings(text_hash, model, vec, created_at) VALUES(?,?,?,?)",
        [(h, m, json.dumps(v), now) for h, m, v in pairs],
    )
    conn.commit()


def _get_embeddings(
    conn: sqlite3.Connection, texts: list[str]
) -> list[list[float]] | None:
    """Returnează vectorii pentru `texts` (în aceeași ordine), folosind cache-ul.
    Trimite la robo-brain doar textele lipsă. Returnează None dacă embed eșuează."""
    hashes = [_hash(t) for t in texts]
    cached = _cached_embeddings(conn, hashes)

    missing_indices = [i for i, h in enumerate(hashes) if h not in cached]
    if missing_indices:
        missing_texts = [texts[i] for i in missing_indices]
        new_vecs = _embed(missing_texts)
        if new_vecs is None:
            return None
        pairs = [
            (hashes[i], "robo-brain", new_vecs[j])
            for j, i in enumerate(missing_indices)
        ]
        _store_embeddings(conn, pairs)
        for j, i in enumerate(missing_indices):
            cached[hashes[i]] = new_vecs[j]

    return [cached[h] for h in hashes]


def select(
    task_text: str,
    candidates: list[tuple[str, dict]],
) -> list[tuple[str, dict]] | None:
    """Selectează regulile relevante pentru `task_text` din `candidates`.

    candidates: perechi (bloc, regulă) unde bloc ∈ {"global","approved","pending"}.
    Returnează None dacă embedding-ul a eșuat (apelantul folosește toți candidații).
    Returnează candidates neschimbat dacă len(candidates) <= max_rules (fără rețea).
    """
    if not candidates:
        return candidates
    if len(candidates) <= _MAX_RULES:
        return candidates

    all_texts = [task_text] + [r["text"].strip() for _, r in candidates]

    with _connect() as conn:
        _ensure_table(conn)
        vecs = _get_embeddings(conn, all_texts)

    if vecs is None:
        return None

    task_vec = vecs[0]
    rule_vecs = vecs[1:]

    scored: list[tuple[float, int]] = []
    for i, ((bloc, _rule), vec) in enumerate(zip(candidates, rule_vecs)):
        score = _dot(task_vec, vec)
        if bloc in ("global", "approved"):
            score += _APPROVED_BONUS
        scored.append((score, i))

    scored.sort(key=lambda x: x[0], reverse=True)

    result = []
    for score, i in scored:
        if score < _MIN_SCORE:
            break
        result.append(candidates[i])
        if len(result) >= _MAX_RULES:
            break

    return result
