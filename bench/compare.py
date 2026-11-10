"""Comparație de regresie între două rulări de benchmark (JSONL).

Funcții pure: load(), regressions(). CLI:
  python bench/compare.py <new.jsonl> [--baseline bench/results/baseline.jsonl]
  python bench/compare.py --set-baseline <file.jsonl>   # promovare MANUALĂ
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BASELINE = HERE / "results" / "baseline.jsonl"

TOOL_MULT = 2.0
TOOL_FLAT = 5
COST_MULT = 2.5
COST_FLAT = 0.005


def load(path):
    """Ultimul rând per task dintr-un fișier JSONL -> dict[task_id, row]."""
    rows = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            rows[row["task"]] = row
    return rows


def regressions(base, new):
    """Lista mesajelor RO de regresie (goală = fără regresii).

    Reguli per task prezent în ambele:
      a) finished True -> False;
      b) new tool_calls > max(2*base, base+5);
      c) new cost_usd > 2.5*base + 0.005.
    Task prezent în base și absent din new = regresie („lipsește din rulare”).
    """
    out = []
    for tid, b in base.items():
        if tid not in new:
            out.append(f"{tid}: lipsește din rulare")
            continue
        n = new[tid]
        if b.get("finished") and not n.get("finished"):
            out.append(f"{tid}: finished True -> False (nu mai termină sarcina)")
        bt = int(b.get("tool_calls") or 0)
        nt = int(n.get("tool_calls") or 0)
        tool_limit = max(TOOL_MULT * bt, bt + TOOL_FLAT)
        if nt > tool_limit:
            out.append(f"{tid}: tool_calls {bt} -> {nt} (peste pragul {tool_limit:g})")
        bc = float(b.get("cost_usd") or 0.0)
        nc = float(n.get("cost_usd") or 0.0)
        cost_limit = COST_MULT * bc + COST_FLAT
        if nc > cost_limit:
            out.append(f"{tid}: cost_usd {bc:g} -> {nc:g} (peste pragul {cost_limit:g})")
    return out


def _cell(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _pair(b, n):
    return f"{_cell(b)} → {_cell(n)}"


def render_table(base, new):
    """Tabel text: task, tool_calls base→new, cost base→new, finished."""
    order = list(new) + [t for t in base if t not in new]
    head = f"{'task':<18} {'tool_calls':<18} {'cost_usd':<28} {'finished':<16}"
    lines = [head, "-" * len(head)]
    for tid in order:
        b, n = base.get(tid), new.get(tid)
        tools = _pair(b.get("tool_calls") if b else None, n.get("tool_calls") if n else None)
        cost = _pair(b.get("cost_usd") if b else None, n.get("cost_usd") if n else None)
        fin = _pair(b.get("finished") if b else None, n.get("finished") if n else None)
        lines.append(f"{tid:<18} {tools:<18} {cost:<28} {fin:<16}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compară o rulare de benchmark cu baseline-ul.")
    ap.add_argument("new", nargs="?", help="JSONL-ul rulării noi")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                    help="JSONL-ul baseline (promovat manual)")
    ap.add_argument("--set-baseline", metavar="FILE",
                    help="copiază FILE în bench/results/baseline.jsonl (manual, niciodată automat)")
    args = ap.parse_args(argv)

    if args.set_baseline:
        src = Path(args.set_baseline)
        if not src.is_file():
            print(f"nu există fișierul: {src}", file=sys.stderr)
            return 2
        dst = Path(args.baseline)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"baseline actualizat: {dst}")
        return 0

    if not args.new:
        ap.error("lipsește <new.jsonl> (sau folosește --set-baseline)")

    baseline = Path(args.baseline)
    if not baseline.is_file():
        print("fără baseline")
        return 0

    base = load(baseline)
    new = load(args.new)
    print(render_table(base, new))
    print()
    regs = regressions(base, new)
    if regs:
        print(f"Regresii ({len(regs)}):")
        for r in regs:
            print(f"  - {r}")
        return 1
    print("Fără regresii.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
