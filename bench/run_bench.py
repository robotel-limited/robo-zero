"""Benchmark de regresie r0: rulează sarcinile din bench/tasks.json, fiecare în
tr-un chat nou din proiectul „Benchmark r0”, și scrie o linie JSON per rulare în
bench/results/<eticheta>.jsonl (fiecare rând cu git_sha + ts).
Utilizare: python bench/run_bench.py <eticheta> [task_id ...]
Nu rula benchmark-ul real fără accept — costă LLM.
"""
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

BASE = "http://127.0.0.1:8150"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TIMEOUT_S = 1200
# shell-ul se aprobă automat doar dacă nu conține nimic distructiv/instalare
DENY = re.compile(r"\b(rm|sudo|mv|kill|pkill|systemctl|pip|npm|apt|dd|chmod|chown|git\s+push|curl[^|]*\|\s*(ba)?sh)\b|>|--yolo")
PROJECT_TITLE = "Benchmark r0"
PROJECT_PROMPT = ("Rulare de benchmark: doar citești și raportezi. Nu instala, nu șterge, "
                  "nu modifica nimic în afara workdir-ului. Termină cu un răspuns concis.")


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def project_id():
    for p in api("GET", "/api/projects"):
        if p.get("title") == PROJECT_TITLE:
            return p["id"]
    return api("POST", "/api/projects", {"title": PROJECT_TITLE, "prompt": PROJECT_PROMPT})["id"]


def git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def backup_and_clear_rules(pid, results_dir):
    """Izolare baseline: la începutul unei rulări, regulile proiectului
    «Benchmark r0» se copiază ca rânduri JSON în results/ (backup), apoi se
    șterg din DB — ca o regulă veche să nu strice măsurătoarea."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import project_rules_store
    rules = project_rules_store.list_for(pid)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    backup = results_dir / f"bench_rules_backup-{int(time.time())}.json"
    backup.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    project_rules_store.delete_project(pid)
    print(f"backup {len(rules)} reguli -> {backup.name}; «Benchmark r0» golit",
          flush=True)
    return backup


def run_task(task, pid, label):
    if task.get("model"):  # modelul pe care s-a blocat sarcina inițial
        api("PATCH", f"/api/projects/{pid}", {"model": task["model"]})
    chat = api("POST", "/api/chats", {"project_id": pid})["id"]
    api("PATCH", f"/api/chats/{chat}", {"title": f"BENCH-{label}-{task['id']}"})
    t0 = time.time()
    api("POST", f"/api/chats/{chat}/messages", {"text": task["text"]})
    seen, approvals, denied = 0, 0, 0
    while True:
        time.sleep(3)
        info = api("GET", f"/api/chats/{chat}")
        log = info["log"]
        for e in log[seen:]:
            if e.get("type") == "approval_required":
                ok = not DENY.search(e.get("arguments", ""))
                try:
                    api("POST", f"/api/chats/{chat}/approve", {"approved": ok})
                except Exception:
                    pass
                approvals += 1
                denied += 0 if ok else 1
        seen = len(log)
        if not info["is_running"] and time.time() - t0 > 5:
            break
        if time.time() - t0 > TIMEOUT_S:
            api("POST", f"/api/chats/{chat}/cancel")
            break
    return summarize(chat, task, label, time.time() - t0, approvals, denied, model=info.get("model"))


def summarize(chat, task, label, elapsed, approvals, denied, model):
    d = json.loads((ROOT / "data" / "chats" / f"{chat}.json").read_text())
    hist = d.get("agent_history") or []
    calls = [(tc["function"]["name"], tc["function"]["arguments"])
             for m in hist for tc in (m.get("tool_calls") or [])]
    results = [m["content"] for m in hist if m.get("role") == "tool" and isinstance(m.get("content"), str)]
    errors = [e for e in d.get("log", []) if e.get("type") == "error"]
    finals = [e for e in d.get("log", []) if e.get("type") == "final"]
    with sqlite3.connect(ROOT / "data" / "projects.db") as c:
        u = c.execute("SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
                      "COALESCE(SUM(cost_usd),0) FROM usage_log WHERE chat_id=?", (chat,)).fetchone()
    return {
        "label": label, "task": task["id"], "chat": chat, "model": model, "elapsed_s": round(elapsed),
        "tool_calls": len(calls), "distinct_calls": len(set(calls)),
        "dup_results": len(results) - len(set(results)),
        "tool_mix": Counter(n for n, _ in calls).most_common(4),
        "llm_requests": u[0], "prompt_tokens": u[1], "completion_tokens": u[2], "cost_usd": round(u[3], 5),
        "approvals": approvals, "denied": denied,
        "finished": bool(finals), "error": errors[-1]["text"][:300] if errors else None,
        "final": (finals[-1].get("text") or "")[:600] if finals else None,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    label = sys.argv[1]
    wanted = set(sys.argv[2:])
    tasks = [t for t in json.loads((HERE / "tasks.json").read_text()) if not wanted or t["id"] in wanted]
    results_dir = HERE / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / f"{label}.jsonl"
    sha = git_sha()
    pid = project_id()
    backup_and_clear_rules(pid, results_dir)  # baseline fără reguli (B.5)
    for t in tasks:  # strict secvențial
        row = run_task(t, pid, label)
        row["git_sha"] = sha
        row["ts"] = time.time()
        with open(out, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(row["task"], row["tool_calls"], row["prompt_tokens"], row["finished"], flush=True)


if __name__ == "__main__":
    main()
