#!/usr/bin/env python3
"""Etapa 1 — comasarea near-dupe-urilor pending din project_rules.

Rulat o dată, la final de Etapa 1. Reguli (decizie Sorin, prod SQL):
1. ÎNTÂI backup table-copy: CREATE TABLE project_rules_bak_YYYYMMDD AS SELECT *
   din project_rules — abia apoi se șterge ceva.
2. Per proiect, clusterizează regulile pending apropiate (prag din
   conf/settings.yaml), păstrează cea mai nouă/lungă din cluster, mută
   sightings + max(times_seen) pe ea și șterge restul.
3. Raportează câte reguli a comasat.

Clusterizarea folosește exact perechea _normalize + difflib a lui
project_rules_store.find_near_match (rules_store._ratio); find_near_match însuși
nu e apelabil direct aici fiindcă s-ar auto-potrivi pe ea însăși (regula față de
ea însăși are ratio 1.0, mereu cel mai bun).

Uz: python3 scripts/cleanup_near_dupes.py [--db /cale/proiecte.db]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import project_rules_store as rules_store  # noqa: E402  (sys.path rezolvat mai sus)


def _clusterize(project_id: str, threshold: float) -> list[list[dict]]:
    """Clustere single-linkage peste pending-urile unui proiect (doar clustere
    cu ≥2 reguli); perechile se compara cu aceeași formulă ca find_near_match."""
    pending = rules_store.list_for(project_id, status="pending")
    n = len(pending)
    parent = list(range(n))

    def _root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if rules_store._ratio(pending[i]["text"], pending[j]["text"]) >= threshold:
                ri, rj = _root(i), _root(j)
                if ri != rj:
                    parent[rj] = ri

    groups: dict[int, list[dict]] = {}
    for idx in range(n):
        groups.setdefault(_root(idx), []).append(pending[idx])
    return [g for g in groups.values() if len(g) > 1]


def _merge_cluster(cluster: list[dict]) -> tuple[dict, list[int]]:
    """Păstrează cea mai nouă/lungă regulă din cluster, mută sightings +
    max(times_seen) pe ea și șterge restul. Întoarce (păstrată, șterse)."""
    keeper = max(cluster, key=lambda r: (r["created_at"] or 0.0, len(r["text"]), r["id"]))
    ids = [r["id"] for r in cluster if r["id"] != keeper["id"]]
    marks = ",".join("?" for _ in ids)
    with rules_store._connect() as conn:
        conn.execute(
            f"INSERT OR IGNORE INTO rule_sightings (rule_id, source_chat_id, seen_at) "
            f"SELECT ?, source_chat_id, MAX(seen_at) FROM rule_sightings "
            f"WHERE rule_id IN ({marks}) GROUP BY source_chat_id",
            (keeper["id"], *ids),
        )
        conn.execute(f"DELETE FROM rule_sightings WHERE rule_id IN ({marks})", ids)
        count = conn.execute(
            "SELECT COUNT(*) FROM rule_sightings WHERE rule_id = ?", (keeper["id"],)
        ).fetchone()[0]
        times_seen = max([int(r["times_seen"] or 0) for r in cluster] + [count])
        seen = [r["last_seen_at"] for r in cluster if r["last_seen_at"] is not None]
        last_seen_at = max(seen) if seen else keeper["last_seen_at"]
        conn.execute(
            "UPDATE project_rules SET times_seen = ?, last_seen_at = ? WHERE id = ?",
            (times_seen, last_seen_at, keeper["id"]),
        )
        conn.execute(f"DELETE FROM project_rules WHERE id IN ({marks})", ids)
    return keeper, ids


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Comasare near-dupe pending în project_rules (backup table-copy înainte de orice ștergere)."
    )
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parents[1] / "data" / "projects.db"),
        help="calea SQLite-ului (default: data/projects.db al repo-ului)",
    )
    args = parser.parse_args()
    db = Path(args.db)
    rules_store._DB_PATH = db

    with rules_store._connect() as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_rules'"
        ).fetchone() is None:
            print(f"{db}: fără tabel project_rules — nimic de comasat")
            return 0
        backup = f"project_rules_bak_{time.strftime('%Y%m%d')}"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {backup} AS SELECT * FROM project_rules")
        backup_rows = conn.execute(f"SELECT COUNT(*) FROM {backup}").fetchone()[0]
    print(f"backup: {backup} ({backup_rows} reguli)")

    # schema Etapa 1 pe țintă abia DUPĂ backup (backup-ul rămâne fidel la ce era)
    rules_store.init_db()
    threshold = float(rules_store._RULES_CFG.get("near_match_threshold", 0.85))

    with rules_store._connect() as conn:
        project_ids = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT project_id FROM project_rules WHERE status = 'pending' ORDER BY project_id"
            )
        ]

    total_comasate = 0
    total_cluster = 0
    for project_id in project_ids:
        for cluster in _clusterize(project_id, threshold):
            keeper, sters = _merge_cluster(cluster)
            total_cluster += 1
            total_comasate += len(sters)
            ids = ", ".join(f"#{i}" for i in sters)
            print(
                f"proiect {project_id}: cluster de {len(cluster)} (păstrat #{keeper['id']}), "
                f"comasate {len(sters)}: {ids}"
            )

    print(f"TOTAL: {total_comasate} near-dupe comasate în {total_cluster} cluster(e)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
