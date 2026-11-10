"""Ablație pe benchmark: cât schimbă o regulă pe un task de benchmark.

usage: python bench/ablation.py <rule_id> <task_id> [--repeat N=2]

Citește regula din project_rules_store, golește regulile proiectului
«Benchmark r0» (backup JSON în bench/results/, baseline curat), rulează
task-ul de N ori FĂRĂ regulă, copiază regula ca regulă `approved` în
proiectul de benchmark, rulează task-ul de N ori CU regulă și șterge copia
în `finally`. Rezultatele: bench/results/ablation-<rule_id>-<task_id>-<ts>.jsonl
(câmp `variant` = without/with), cu media tool_calls, cost_usd și finished
tipărită pe variantă. Nu rula benchmark-ul real fără accept — costă LLM.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(HERE), str(ROOT)):  # run_bench se importă din bench/, magazinele din rădăcină
    if _p not in sys.path:
        sys.path.insert(0, _p)

import project_rules_store
from run_bench import api, backup_and_clear_rules, git_sha, project_id, run_task


def _load_task(task_id):
    for t in json.loads((HERE / "tasks.json").read_text(encoding="utf-8")):
        if t["id"] == task_id:
            return t
    return None


def _check_server():
    try:
        api("GET", "/api/projects")
        return True
    except Exception as err:
        print(f"serverul r0 nu răspunde: {err}", file=sys.stderr)
        return False


def _run_once(task, pid, label, variant, sha, out):
    row = run_task(task, pid, label)
    row["variant"] = variant
    row["git_sha"] = sha
    row["ts"] = time.time()
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{variant} {row['task']}: tool_calls={row['tool_calls']} "
          f"cost_usd={row['cost_usd']} finished={row['finished']}", flush=True)
    return row


def _print_summary(rows):
    for variant in ("without", "with"):
        v = [r for r in rows if r["variant"] == variant]
        if not v:
            print(f"{variant}: fără rulări")
            continue
        n = len(v)

        def mean(key):
            return sum(r[key] for r in v) / n

        finished = sum(1 for r in v if r["finished"]) / n
        print(f"{variant}: tool_calls={mean('tool_calls'):.1f} "
              f"cost_usd={mean('cost_usd'):.5f} finished={finished:.2f} (n={n})")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ablație: efectul unei reguli pe un task")
    parser.add_argument("rule_id", type=int)
    parser.add_argument("task_id")
    parser.add_argument("--repeat", type=int, default=2,
                        help="rulări per variantă (implicit 2)")
    args = parser.parse_args(argv)

    rule = project_rules_store.get_rule(args.rule_id)
    if rule is None:
        print(f"regula {args.rule_id} nu există", file=sys.stderr)
        return 1
    task = _load_task(args.task_id)
    if task is None:
        print(f"task {args.task_id} lipsește din bench/tasks.json", file=sys.stderr)
        return 1
    if not _check_server():
        return 1

    results_dir = HERE / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    pid = project_id()
    backup_and_clear_rules(pid, results_dir)  # baseline fără reguli (B.5)
    sha = git_sha()
    out = results_dir / f"ablation-{args.rule_id}-{args.task_id}-{int(time.time())}.jsonl"

    rows, copy_id = [], None
    try:
        for _ in range(args.repeat):
            rows.append(_run_once(task, pid, "abl-without", "without", sha, out))
        copy_id = project_rules_store.add_rule(pid, str(rule["text"]),
                                               rule.get("category") or "")
        project_rules_store.approve(copy_id)
        for _ in range(args.repeat):
            rows.append(_run_once(task, pid, "abl-with", "with", sha, out))
    finally:
        if copy_id is not None:  # copia dispare mereu, chiar și la eșec
            with project_rules_store._connect() as conn:
                conn.execute("DELETE FROM project_rules WHERE id = ?", (copy_id,))

    _print_summary(rows)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
