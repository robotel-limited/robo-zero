#!/usr/bin/env python3
"""Harness SSSM: testează variantele de gestionare a contextului (V1/A/B/AB)
pe agentul SSN (sensenova-loop-9b) cu sarcina T3 Tecnomatix.

NU trece prin serviciul live :8150. Subclasează Agent, forțează modelul SSN,
memoria de lucru pornită, auto-aprobare. Variantele se aplică prin monkeypatch
la history.compact_if_needed / history._summarize, fără modificări în producție.

Uz: python harness.py <V1|A|B|AB> <run_index>
Scrie rezultatul în scratchpad/sssm/results/<variant>_<run>.json
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path("/srv/robo-zero")
sys.path.insert(0, str(ROOT))

import history
import llm
import usage_log
import working_memory
from agent import Agent

SSN = "sensenova-loop-9b"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Sarcina T3 (verbatim) + răspunsul corect pentru punctare
# ---------------------------------------------------------------------------
TASK = (
    "În folderul /srv/robo-zero/workdir/projects/103c56da730a/tx este documentația "
    "XML a API-ului .NET Tecnomatix Process Simulate 2408 (Tecnomatix.Engineering.xml, "
    "~125k linii, 21k membri). Pe baza fișierului, nu din memorie: "
    "(1) Câte tipuri (T:) sunt direct în namespace-ul Tecnomatix.Engineering (nu în "
    "sub-namespace-uri, nu tipuri imbricate) și conțin „Robot” în nume (case-sensitive, "
    "deci și „Robotic”)? "
    "(2) Dintre ele, care au cel puțin o metodă care NU e constructor (#ctor) și care "
    "primește un parametru de tip exact Tecnomatix.Engineering.TxTransformation? "
    "(3) Pentru fiecare astfel de tip, listează numele acelor metode (fără duplicate de overload). "
    "(4) Câte tipuri și câte metode unice ies în total? Arată comenzile folosite."
)

# sub-întrebările numerotate, pentru rubrica „GATA CÂND” (scrisă de cod)
GATA_CAND = (
    "(1) numărul de tipuri T: direct în namespace Tecnomatix.Engineering cu „Robot” în nume (case-sensitive);\n"
    "(2) care dintre ele au ≥1 metodă non-#ctor cu un parametru de tip exact "
    "Tecnomatix.Engineering.TxTransformation;\n"
    "(3) numele acelor metode per tip, fără duplicate de overload;\n"
    "(4) totalul de tipuri și de metode unice, plus comenzile folosite."
)

GT_TYPES = [
    "ITxRobot",
    "TxGenericRoboticLocationOperation",
    "TxRobot",
    "TxRobotSimulationEngineServicesProvider",
    "TxRobotSimulationRRSServicesProvider",
    "TxRoboticPaintTrigger",
    "TxRoboticSeamLocationOperation",
    "TxRoboticViaLocationOperation",
]
GT_METHODS = [
    "MountTool",
    "Locate",
    "GetLocationAxisIntersectionPoints",
    "GetPerpendicularLocationAxisIntersectionPoints",
    "Relocate",
    "SetSimulatedRobotReferenceFrame",
    "SetSimulatedRobotTcpf",
    "ModifyCellFrame",
]

# ---------------------------------------------------------------------------
# Captură de metrici: înlocuim usage_log.record (fără scriere în DB-ul de prod)
# ---------------------------------------------------------------------------
LLM_CALLS: list[dict] = []


def _capture_record(model, purpose, usage, latency_ms=None):
    LLM_CALLS.append(
        {
            "model": model,
            "purpose": purpose,
            "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
            "completion_tokens": (usage or {}).get("completion_tokens", 0),
            "latency_ms": latency_ms,
        }
    )


usage_log.record = _capture_record

# ---------------------------------------------------------------------------
# Helperi comuni de parsare istoric
# ---------------------------------------------------------------------------
MARKER = working_memory.MARKER_PREFIX


def _pointer_of(content: str) -> str:
    if not isinstance(content, str):
        return ""
    for line in reversed(content.splitlines()):
        if line.startswith(MARKER):
            return " " + line
    return ""


def _short_args(raw: str, limit: int = 140) -> str:
    raw = (raw or "").replace("\n", " ")
    return raw if len(raw) <= limit else raw[:limit] + "…"


def _parse_steps(hist: list[dict]) -> list[dict]:
    """Pași = mesaj assistant cu tool_calls + mesajele tool care-l urmează."""
    steps = []
    i = 0
    while i < len(hist):
        m = hist[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            sid = m["tool_calls"][0]["id"]
            j = i + 1
            while j < len(hist) and hist[j].get("role") == "tool":
                j += 1
            steps.append({"sid": sid, "asst": m, "tools": hist[i + 1 : j]})
            i = j
        else:
            i += 1
    return steps


def _result_note(tools: list[dict], limit: int = 300) -> str:
    texts = []
    pointer = ""
    for t in tools:
        c = t.get("content")
        if isinstance(c, str):
            texts.append(c)
            p = _pointer_of(c)
            if p:
                pointer = p
    joined = " | ".join(texts)
    head = joined[:limit].replace("\n", " ")
    if len(joined) > limit:
        head += "…"
    return head + pointer


async def _ssn_sections(system: str, user: str, purpose: str) -> str:
    try:
        resp = await llm.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            purpose=purpose,
            model=SSN,
        )
    except llm.LlmError:
        return "(indisponibil)"
    return (resp.content or "(gol)").strip()


_SHEET_SYS = (
    "Ești modulul de memorie al unui agent cu tool-uri. Primești SARCINA agentului "
    "și TRANSCRIPTUL pașilor de până acum. Scrie EXACT trei secțiuni, în română, fără "
    "niciun alt text, fiecare pe linia ei:\n"
    "ACUM: ce s-a stabilit concret până acum, inclusiv rezultatele cu valori EXACTE "
    "copiate din transcript (cifre, nume de tipuri/metode, comenzi). Nu parafraza "
    "identificatorii și cifrele.\n"
    "PLAN: pașii care mai rămân de făcut.\n"
    "CĂI ABANDONATE: ce s-a încercat și nu a mers (dacă e cazul).\n"
    "Nu inventa nimic care nu apare în transcript. Nu enumera sub-întrebările sarcinii."
)


def _collect_pointers(hist: list[dict]) -> list[str]:
    seen = []
    for m in hist:
        c = m.get("content")
        if isinstance(c, str):
            for line in c.splitlines():
                if line.startswith(MARKER) and line not in seen:
                    seen.append(line)
    return seen


def _sheet_header(pointers: list[str]) -> str:
    ptxt = "\n".join(pointers) if pointers else "(niciunul încă)"
    return (
        "[FIȘĂ DE LUCRU]\n"
        f"1. SARCINĂ (verbatim):\n{TASK}\n\n"
        f"2. GATA CÂND (sub-întrebările de răspuns):\n{GATA_CAND}\n\n"
        f"3. ARTEFACTE ȘI POINTERI .obs:\n{ptxt}\n\n"
    )


# ---------------------------------------------------------------------------
# VARIANTA V1: compactare liberă la 0.7, rezumat pe SSN
# ---------------------------------------------------------------------------
async def _summarize_ssn(messages):
    transcript = "\n".join(history._format_message(m) for m in messages)
    prompt = [
        {
            "role": "system",
            "content": (
                "Rezumă concis, în română, conversația de mai jos dintre un utilizator "
                "și un agent AI cu tool-uri. Păstrează deciziile, faptele și rezultatele "
                "importante ale tool-urilor (cifre, nume, comenzi — exact); elimină "
                "detaliile redundante. Răspunde doar cu rezumatul, fără alt text."
            ),
        },
        {"role": "user", "content": transcript},
    ]
    resp = await llm.chat(prompt, purpose="compaction", model=SSN)
    return resp.content or "(rezumat indisponibil)"


def install_V1():
    history._summarize = _summarize_ssn  # compact_if_needed rămâne cel din producție (0.7)


# ---------------------------------------------------------------------------
# VARIANTA A: prag 0.6, compactare într-o fișă de 6 rubrici
# ---------------------------------------------------------------------------
A_TRIGGER = 0.6


def install_A():
    async def a_compact(hist, model=None, reserved_tokens=0):
        ctx = llm.context_tokens_for(model)
        if history._estimate_tokens(hist) + reserved_tokens < ctx * A_TRIGGER:
            return hist
        if len(hist) <= 1:
            return hist
        tail_start = history._safe_tail_start(
            hist, max(1, len(hist) - history.KEEP_LAST_MESSAGES)
        )
        middle = hist[1:tail_start]
        if not middle:
            return hist
        transcript = "\n".join(history._format_message(m) for m in middle)
        sections = await _ssn_sections(
            _SHEET_SYS, f"SARCINA:\n{TASK}\n\nTRANSCRIPT:\n{transcript}", "sheet_A"
        )
        sheet = _sheet_header(_collect_pointers(middle)) + "4-6 (scris de agent):\n" + sections
        result = [hist[0], {"role": "user", "content": sheet}, *hist[tail_start:]]
        hard = ctx * history.HARD_FRACTION
        if history._estimate_tokens(result) + reserved_tokens >= hard:
            result = history._shrink_tool_outputs(result, hard, reserved_tokens)
        return result

    history.compact_if_needed = a_compact


# ---------------------------------------------------------------------------
# VARIANTA B: o acțiune pe context — jurnal de pași + ultimul pas brut
# ---------------------------------------------------------------------------
B_CAP_CHARS = 4000 * history.CHARS_PER_TOKEN  # ~4k tokeni


def _build_journal(entries: list[dict], cap_chars: int) -> str:
    forms = [e["full"] for e in entries]

    def total():
        return sum(len(f) for f in forms) + len(forms)

    i = 0
    while total() > cap_chars and i < len(forms):
        forms[i] = entries[i]["short"]
        i += 1
    return "\n".join(forms) if forms else "(niciun pas încă)"


def install_B(agent: "TestAgent"):
    agent._b_journal = []
    agent._b_folded = set()

    def _entry(n, step):
        calls = "; ".join(
            f"{tc['function']['name']}({_short_args(tc['function']['arguments'])})"
            for tc in step["asst"]["tool_calls"]
        )
        note = _result_note(step["tools"])
        ptr = _pointer_of(" ".join(t.get("content", "") for t in step["tools"] if isinstance(t.get("content"), str)))
        return {
            "full": f"PAS {n}: {calls} → {note}",
            "short": f"PAS {n}: {calls}{ptr}",
        }

    async def b_compact(hist, model=None, reserved_tokens=0):
        steps = _parse_steps(hist)
        if not steps:
            return hist
        for st in steps[:-1]:
            if st["sid"] not in agent._b_folded:
                agent._b_folded.add(st["sid"])
                agent._b_journal.append(_entry(len(agent._b_journal) + 1, st))
        last = steps[-1]
        journal = _build_journal(agent._b_journal, B_CAP_CHARS)
        jmsg = {
            "role": "user",
            "content": f"[JURNAL DE PAȘI — context comprimat de harness]\n{journal}",
        }
        return [hist[0], hist[1], jmsg, last["asst"], *last["tools"]]

    history.compact_if_needed = b_compact


# ---------------------------------------------------------------------------
# VARIANTA A+B: ca B, iar la depășirea plafonului SSN strânge jurnalul în fișa A
# ---------------------------------------------------------------------------
def install_AB(agent: "TestAgent"):
    agent._b_journal = []
    agent._b_folded = set()
    agent._ab_sheet = "(gol încă)"
    agent._ab_pointers = []

    def _entry(n, step):
        calls = "; ".join(
            f"{tc['function']['name']}({_short_args(tc['function']['arguments'])})"
            for tc in step["asst"]["tool_calls"]
        )
        note = _result_note(step["tools"])
        return {"full": f"PAS {n}: {calls} → {note}", "step": step}

    async def ab_compact(hist, model=None, reserved_tokens=0):
        steps = _parse_steps(hist)
        if not steps:
            return hist
        for st in steps[:-1]:
            if st["sid"] not in agent._b_folded:
                agent._b_folded.add(st["sid"])
                agent._b_journal.append(_entry(len(agent._b_journal) + 1, st))
                for ptr in _collect_pointers(st["tools"]):
                    if ptr not in agent._ab_pointers:
                        agent._ab_pointers.append(ptr)
        last = steps[-1]
        # dacă jurnalul (full) depășește plafonul, SSN strânge partea veche în fișa A
        full_chars = sum(len(e["full"]) for e in agent._b_journal)
        if full_chars > B_CAP_CHARS and len(agent._b_journal) > 2:
            keep = agent._b_journal[-2:]
            overflow = agent._b_journal[:-2]
            overflow_txt = "\n".join(e["full"] for e in overflow)
            agent._ab_sheet = await _ssn_sections(
                _SHEET_SYS,
                f"SARCINA:\n{TASK}\n\nFIȘĂ CURENTĂ (secțiunile ACUM/PLAN/CĂI ABANDONATE):\n"
                f"{agent._ab_sheet}\n\nPAȘI NOI DE INTEGRAT ÎN FIȘĂ:\n{overflow_txt}",
                "sheet_AB",
            )
            agent._b_journal = keep
        header = _sheet_header(agent._ab_pointers) + "4-6 (scris de agent):\n" + agent._ab_sheet
        recent = "\n".join(e["full"] for e in agent._b_journal) or "(niciun pas recent)"
        jmsg = {
            "role": "user",
            "content": f"{header}\n\n[JURNAL RECENT]\n{recent}",
        }
        return [hist[0], hist[1], jmsg, last["asst"], *last["tools"]]

    history.compact_if_needed = ab_compact


# ---------------------------------------------------------------------------
# Agentul de test
# ---------------------------------------------------------------------------
class TestAgent(Agent):
    def _chat_model(self):
        return SSN

    def _wm_active(self):
        return True


async def _auto_confirm(name, detail):
    return True


# ---------------------------------------------------------------------------
# Punctare
# ---------------------------------------------------------------------------
def score(final_text: str) -> dict:
    t = final_text or ""
    return {
        "has_242": "242" in t,
        "types_found": sorted([x for x in GT_TYPES if x in t]),
        "n_types_found": sum(1 for x in GT_TYPES if x in t),
        "methods_found": sorted([x for x in set(GT_METHODS) if x in t]),
        "n_methods_found": sum(1 for x in set(GT_METHODS) if x in t),
        "says_8_types": ("8 tip" in t.lower()),
        "says_13_methods": ("13 met" in t.lower()),
    }


# ---------------------------------------------------------------------------
# Rulare
# ---------------------------------------------------------------------------
async def run_variant(variant: str, run_idx: int):
    LLM_CALLS.clear()
    tag = f"sssm-{variant}-{run_idx}"
    workdir = str(ROOT / "workdir" / "projects" / tag)
    Path(workdir).mkdir(parents=True, exist_ok=True)

    agent = TestAgent(confirm=_auto_confirm, workdir=workdir, working_memory=True)

    if variant == "V1":
        install_V1()
    elif variant == "A":
        install_A()
    elif variant == "B":
        install_B(agent)
    elif variant == "AB":
        install_AB(agent)
    else:
        raise SystemExit(f"variantă necunoscută: {variant}")

    t0 = time.time()
    error = None
    final_text = ""
    try:
        final_text = await agent.run(TASK)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    elapsed = time.time() - t0

    chat_calls = [c for c in LLM_CALLS if c["purpose"] == "chat"]
    compaction_calls = [c for c in LLM_CALLS if c["purpose"] in ("compaction", "sheet_A", "sheet_AB")]
    distill_calls = [c for c in LLM_CALLS if c["purpose"] == "distill"]
    max_ctx = max((c["prompt_tokens"] for c in chat_calls), default=0)

    result = {
        "variant": variant,
        "run": run_idx,
        "elapsed_sec": round(elapsed, 1),
        "iterations": len(chat_calls),
        "compactions": len(compaction_calls),
        "distill_calls": len(distill_calls),
        "total_llm_calls": len(LLM_CALLS),
        "max_context_tokens": max_ctx,
        "total_prompt_tokens": sum(c["prompt_tokens"] for c in LLM_CALLS),
        "total_completion_tokens": sum(c["completion_tokens"] for c in LLM_CALLS),
        "error": error,
        "empty": not bool((final_text or "").strip()),
        "score": score(final_text),
        "final_text": final_text,
    }
    out = RESULTS_DIR / f"{variant}_{run_idx}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{tag}] gata în {elapsed:.0f}s — iter={result['iterations']} "
          f"compact={result['compactions']} ctxmax={max_ctx} "
          f"242={result['score']['has_242']} tipuri={result['score']['n_types_found']}/8 "
          f"err={error}")
    return result


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("uz: harness.py <V1|A|B|AB> <run_index>")
    asyncio.run(run_variant(sys.argv[1], int(sys.argv[2])))
