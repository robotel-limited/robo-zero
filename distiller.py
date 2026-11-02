"""Distilarea ieșirilor lungi de tool cu SenseNova-Loop-9B (local, SVAI :8093).

Nota distilată înlocuiește ieșirea brută în istoricul agentului; brutul rămâne
pe disc în .obs/ (vezi working_memory.store). Subsolul cu calea și lungimea îl
scrie codul, nu modelul: testul offline din 2026-11-01 a arătat că un model de
9B nu-și poate evalua fiabil completitudinea (confundă ce lipsește din IEȘIRE
cu pașii rămași ai SARCINII). Fidelitatea extracției a fost 22/22 la 9 rulări.
"""
from __future__ import annotations

import llm

DISTILL_MODEL = "sensenova-loop-9b"
NOTE_PREFIX = "[notă distilată]"

_SYSTEM = (
    "Ești distilatorul memoriei de lucru a unui agent. Primești SARCINA agentului și IEȘIREA brută a unui tool. "
    "Scrie o notă care va înlocui ieșirea în istoricul agentului; brutul rămâne pe disc.\n"
    "Reguli:\n"
    "1. Păstrează TOT ce poate fi util sarcinii, copiat EXACT (nume, identificatori, comenzi, opțiuni, căi, cifre, erori). "
    "Nu parafraza identificatorii.\n"
    "2. Listele relevante le copiezi COMPLET, element cu element, apoi scrii 'LISTĂ COMPLETĂ: N elemente'.\n"
    "3. Aruncă doar ce e clar irelevant sarcinii; la final o linie 'ARUNCAT: <ce tip de conținut ai aruncat>'.\n"
    "4. Nu evalua sarcina, nu enumera pașii ei și nu menționa informații care nu apar în IEȘIRE. "
    "Un marcaj de trunchiere existent în IEȘIRE (ex. 'caractere omise') îl copiezi ca atare.\n"
    "5. Scrie numai în română sau copiat exact din IEȘIRE. Nu adăuga nimic ce nu apare în IEȘIRE.\n"
    "Fără introduceri, fără comentarii."
)


async def distill(task: str, raw: str) -> str | None:
    """Întoarce nota distilată sau None dacă distilatorul eșuează/e gol —
    apelantul revine atunci la ciotul determinist (working_memory.make_stub)."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"SARCINA:\n{task}\n\nIEȘIRE:\n{raw}"},
    ]
    try:
        resp = await llm.chat(messages, purpose="distill", model=DISTILL_MODEL)
    except llm.LlmError:
        return None
    note = (resp.content or "").strip()
    # o notă mai lungă decât brutul nu economisește nimic
    if not note or len(note) >= len(raw):
        return None
    return f"{NOTE_PREFIX}\n{note}"
