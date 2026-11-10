#!/usr/bin/env python3
"""Raport săptămânal al regulilor robo-zero.

usage: python3 scripts/weekly_report.py [--days N] [--output PATH]
       [--discord-webhook URL] [--db PATH]

Citește conf/settings.yaml (blocul weekly_report) ca fallback pentru args CLI,
generază raportul text din data/projects.db și îl scrie în reports/.
Trimite opțional pe Discord. Exit code 0 mereu (chiar dacă Discord a eșuat).
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "conf" / "settings.yaml"
DEFAULT_DB = ROOT / "data" / "projects.db"
REPORTS_DIR = ROOT / "reports"

QUERY_A = """
SELECT r.id, r.project_id, r.category, r.text, r.status, r.decided_at, r.decided_by,
       p.title AS project_title
FROM project_rules r
JOIN projects p ON p.id = r.project_id
WHERE r.decided_at >= ? AND r.status IN ('approved', 'rejected')
ORDER BY p.title, r.status, r.decided_at
"""

QUERY_B = """
SELECT id, category, text, status, decided_at, decided_by
FROM project_rules
WHERE scope = 'global' AND (decided_at >= ? OR status = 'pending')
ORDER BY status, decided_at
"""

RULE_LINE = "    • [{category}] {text}  (decided_by: {by})"
RULE_LINE_PENDING = "    • [{category}] {text}"
NONE_LINE = "  (nicio regulă)"


def load_settings():
    """Citește blocul weekly_report din conf/settings.yaml (opțional)."""
    try:
        import yaml

        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if isinstance(data, dict):
            block = data.get("weekly_report")
            if isinstance(block, dict):
                return block
    except Exception:
        pass
    return {}


def _fetch(db_path, days):
    cutoff = time.time() - days * 86400
    rows_a, rows_b = [], []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows_a = conn.execute(QUERY_A, (cutoff,)).fetchall()
            rows_b = conn.execute(QUERY_B, (cutoff,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as err:
        print(f"WARNING: citire DB eșuat: {err}", file=sys.stderr)
    return rows_a, rows_b, cutoff


def _rule_line(row, pending=False):
    text = (row["text"] or "")[:120]
    category = row["category"] or ""
    if pending:
        return RULE_LINE_PENDING.format(category=category, text=text)
    return RULE_LINE.format(
        category=category,
        text=text,
        by=row["decided_by"] or "-",
    )


def _section(title, rows, pending=False):
    lines = [f"  {title} ({len(rows)}):"]
    if not rows:
        lines.append(NONE_LINE)
        return lines
    for row in rows:
        lines.append(_rule_line(row, pending=pending))
    return lines


def generate_report(db_path, days):
    """Construiește raportul text pentru ultimele `days` zile."""
    rows_a, rows_b, cutoff = _fetch(db_path, days)
    data_start = datetime.fromtimestamp(cutoff).strftime("%Y-%m-%d")
    data_end = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d")

    projects = {}
    for row in rows_a:
        entry = projects.setdefault(
            row["project_id"],
            {"title": row["project_title"], "approved": [], "rejected": []},
        )
        if row["status"] == "approved":
            entry["approved"].append(row)
        else:
            entry["rejected"].append(row)

    g_approved = [r for r in rows_b if r["status"] == "approved"]
    g_rejected = [r for r in rows_b if r["status"] == "rejected"]
    g_pending = [r for r in rows_b if r["status"] == "pending"]

    bar = "=" * 60
    lines = [
        bar,
        f"Raport săptămânal robo-zero — {data_start} … {data_end}",
        bar,
        "",
    ]

    for project_id, entry in projects.items():
        lines.append(f"[Proiect: {entry['title']} ({project_id[:8]})]")
        lines.extend(_section("Aprobate", entry["approved"]))
        lines.extend(_section("Respinse", entry["rejected"]))
        lines.append("")

    lines.append("Globale:")
    lines.extend(_section("Aprobate", g_approved))
    lines.extend(_section("Propuse (pending)", g_pending, pending=True))
    if g_rejected:
        lines.extend(_section("Respinse", g_rejected))
    lines.append("")

    decided = [
        r
        for r in list(rows_a) + list(rows_b)
        if r["status"] in ("approved", "rejected")
    ]
    n_approved = sum(1 for r in decided if r["status"] == "approved")
    n_rejected = sum(1 for r in decided if r["status"] == "rejected")
    auto = sum(1 for r in decided if (r["decided_by"] or "") == "auto")
    manual = len(decided) - auto

    lines.append(
        f"Sumar: {n_approved} aprobate, {n_rejected} respinse  "
        f"(auto-aprobate: {auto}, manual: {manual})"
    )
    lines.append(bar)
    return "\n".join(lines) + "\n"


def post_to_discord(url, text):
    """POST raportul pe Discord. Nu ridică excepții; True/False."""
    if not url:
        return False
    truncated = len(text) > 1900
    content = text[:1900] + ("..." if truncated else "")
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        try:
            resp.read()
        finally:
            resp.close()
        return True
    except Exception as err:
        print(f"WARNING: Discord POST eșuat: {err}", file=sys.stderr)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--discord-webhook", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    settings = load_settings()

    days = args.days
    if days is None:
        try:
            days = int(settings.get("report_days"))
        except (TypeError, ValueError):
            days = 7

    webhook = args.discord_webhook
    if webhook is None:
        webhook = settings.get("discord_webhook_url") or ""

    db_path = Path(args.db) if args.db else DEFAULT_DB

    report = generate_report(db_path, days)

    if args.output:
        out = Path(args.output)
    else:
        out = REPORTS_DIR / f"weekly_{datetime.now().strftime('%Y-%m-%d')}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    if webhook:
        post_to_discord(webhook, report)

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
