"""Etapa 1 (G2) — teste pentru bench/compare.py: load() + regressions().

Rulează din rădăcina repo-ului: python3 -m pytest tests/test_bench_compare.py -q
"""

import json
from pathlib import Path

from bench.compare import load, regressions


def _row(task, finished=True, tool_calls=10, cost_usd=0.02, **extra):
    row = {"task": task, "finished": finished, "tool_calls": tool_calls, "cost_usd": cost_usd}
    row.update(extra)
    return row


def _write(tmp_path, rows, name="run.jsonl"):
    p = Path(tmp_path) / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


# --- load() -----------------------------------------------------------------


def test_load_last_row_per_task(tmp_path):
    p = _write(tmp_path, [
        _row("T1", tool_calls=10),
        _row("T2", tool_calls=20),
        _row("T1", tool_calls=99, cost_usd=0.5),
    ])
    rows = load(p)
    assert set(rows) == {"T1", "T2"}
    assert rows["T1"]["tool_calls"] == 99
    assert rows["T1"]["cost_usd"] == 0.5
    assert rows["T2"]["tool_calls"] == 20


def test_load_ignores_linii_goale(tmp_path):
    p = Path(tmp_path) / "run.jsonl"
    p.write_text(json.dumps(_row("T1")) + "\n\n   \n", encoding="utf-8")
    assert set(load(p)) == {"T1"}


# --- regressions(): fără regresie -------------------------------------------


def test_fara_regresie():
    base = {"T1": _row("T1", tool_calls=10, cost_usd=0.020),
            "T2": _row("T2", tool_calls=4, cost_usd=0.001)}
    new = {"T1": _row("T1", tool_calls=11, cost_usd=0.021),
           "T2": _row("T2", tool_calls=9, cost_usd=0.002)}
    assert regressions(base, new) == []


def test_scuteri_mai_mici_nu_sunt_regresie():
    # base=5: max(2*5, 5+5) = 10; 9 <= 10 -> fără regresie
    base = {"T1": _row("T1", tool_calls=5, cost_usd=0.0002)}
    new = {"T1": _row("T1", tool_calls=9, cost_usd=0.0009)}
    assert regressions(base, new) == []


# --- regressions(): finished True -> False ----------------------------------


def test_finished_true_to_false():
    base = {"T1": _row("T1", finished=True)}
    new = {"T1": _row("T1", finished=False)}
    msgs = regressions(base, new)
    assert len(msgs) == 1
    assert "T1" in msgs[0] and "finished" in msgs[0]


def test_finished_false_ramas_false_nu_e_regresie_de_finished():
    base = {"T1": _row("T1", finished=False)}
    new = {"T1": _row("T1", finished=False)}
    assert not any("finished" in m for m in regressions(base, new))


# --- regressions(): tool_calls explodat --------------------------------------


def test_tool_calls_explodat():
    # prag = max(2*10, 10+5) = 20; 30 > 20 -> regresie
    base = {"T1": _row("T1", tool_calls=10)}
    new = {"T1": _row("T1", tool_calls=30)}
    msgs = regressions(base, new)
    assert len(msgs) == 1
    assert "tool_calls" in msgs[0]


def test_prag_exact_2x_nu_e_regresie():
    # base=10: max(2*10, 10+5) = max(20, 15) = 20; 20 > 20 este fals
    base = {"T1": _row("T1", tool_calls=10)}
    assert regressions(base, {"T1": _row("T1", tool_calls=20)}) == []


def test_prag_exact_2x_mai_mare_decat_plus_5_ramane_prag():
    # base=100: max(200, 105) = 200 -> 200 ok, 201 regresie
    base = {"T1": _row("T1", tool_calls=100)}
    assert regressions(base, {"T1": _row("T1", tool_calls=200)}) == []
    assert len(regressions(base, {"T1": _row("T1", tool_calls=201)})) == 1


def test_prag_base_plus_5_dominant_cand_base_mic():
    # base=3: max(2*3, 3+5) = max(6, 8) = 8 -> 8 ok, 9 regresie
    base = {"T1": _row("T1", tool_calls=3)}
    assert regressions(base, {"T1": _row("T1", tool_calls=8)}) == []
    assert len(regressions(base, {"T1": _row("T1", tool_calls=9)})) == 1


# --- regressions(): cost explodat --------------------------------------------


def test_cost_explodat():
    # prag = 2.5*0.02 + 0.005 = 0.055; 0.20 > 0.055 -> regresie
    base = {"T1": _row("T1", cost_usd=0.020)}
    new = {"T1": _row("T1", cost_usd=0.200)}
    msgs = regressions(base, new)
    assert len(msgs) == 1
    assert "cost_usd" in msgs[0]


def test_cost_prag_la_limita_nu_e_regresie():
    # prag = 2.5*0.02 + 0.005 = 0.055; exact pragul nu e regresie
    base = {"T1": _row("T1", cost_usd=0.020)}
    assert regressions(base, {"T1": _row("T1", cost_usd=0.055)}) == []
    assert len(regressions(base, {"T1": _row("T1", cost_usd=0.0551)})) == 1


def test_cost_prag_un_deasupra_fara_flat():
    # base=0 -> prag = 0.005; orice peste el e regresie
    base = {"T1": _row("T1", cost_usd=0.0)}
    assert regressions(base, {"T1": _row("T1", cost_usd=0.005)}) == []
    assert len(regressions(base, {"T1": _row("T1", cost_usd=0.006)})) == 1


# --- regressions(): task lipsă -----------------------------------------------


def test_task_lipsit_din_rularea_noua():
    base = {"T1": _row("T1"), "T2": _row("T2")}
    new = {"T1": _row("T1")}
    msgs = regressions(base, new)
    assert len(msgs) == 1
    assert "T2" in msgs[0] and "lipsește din rulare" in msgs[0]


def test_task_nou_in_rularea_noua_nu_e_regresie():
    base = {"T1": _row("T1")}
    new = {"T1": _row("T1"), "T9": _row("T9", tool_calls=999, cost_usd=9.9)}
    assert regressions(base, new) == []


def test_regresii_multiple_ordonate_dupa_base():
    base = {"T1": _row("T1", finished=True), "T2": _row("T2", tool_calls=10),
            "T3": _row("T3", cost_usd=0.020)}
    new = {"T1": _row("T1", finished=False), "T2": _row("T2", tool_calls=40),
           "T3": _row("T3", cost_usd=0.900)}
    msgs = regressions(base, new)
    assert len(msgs) == 3
    assert msgs[0].startswith("T1")
    assert msgs[1].startswith("T2")
    assert msgs[2].startswith("T3")
