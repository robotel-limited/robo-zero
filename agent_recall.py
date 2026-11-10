"""Auto-recall: markerii de răspuns „nesigur/incomplet" + declanșarea
automată a unui memory_recall în dimag.

Extras din agent.py (un modul = o responsabilitate), fără schimbare de
comportament. `looks_insufficient` hotărăște DACĂ răspunsul final pare
nesigur (și deci merită o căutare automată de memorie); `auto_recall` rulează
efectiv recall-ul și întoarce stringul complet de adăugat în istoric ca mesaj
"user". Bucla (auto_recall_done, tools_used) rămâne în agent.py — e state al
buclei, nu al markerilor.
"""

from typing import Any

# Regulă obligatorie pentru toți agenții (nu doar oss, care are tool-calling
# nativ nesigur pe build-ul curent de llama.cpp): dacă răspunsul final nu are
# tool_calls și pare un răspuns nesigur/incomplet, bucla face automat un
# memory_recall pe mesajul utilizatorului înainte să dea răspunsul final —
# nu lăsăm decizia doar pe modelul ales, care poate "uita" să caute în dimag.
_STRONG_MARKERS = (
    "nu am suficiente informații",
    "nu am suficient context",
    "nu am acces la",
    "nu știu sigur",
    "nu sunt sigur",
    "nu sunt sigură",
    "nu am informații despre",
    "nu pot răspunde cu certitudine",
    "nu am date despre",
    "ar trebui să verific",
    "i don't have enough information",
    "i'm not sure",
    "i don't know",
    "i do not have access to",
    "if you provide",
    "please provide the",
    "let me know if you'd like",
    "once the key is available",
)

# Markeri "slabi" (familia find/exist) — apar firesc și în rapoarte de stare
# corecte după ce tool-urile au fost deja folosite (ex. "vm1 nu există"),
# deci declanșează auto-recall doar dacă NU s-a executat niciun tool în
# turul curent (vezi tools_used în agent.py._run și looks_insufficient).
_WEAK_MARKERS = (
    "is missing",
    "does not exist",
    "cannot find",
    "could not find",
    "nu găsesc",
    "nu există",
    "nu am găsit",
)

# prefixul mesajului de recall din istoric (identic cu textul vechi din agent.py)
_RECALL_PREFIX = (
    "[auto-recall dimag — răspunsul anterior a părut nesigur/incomplet, "
    "s-a căutat automat context relevant]\n"
)


def looks_insufficient(text: str, tools_used: bool) -> bool:
    """Detectează euristic un răspuns final nesigur/incomplet (fără
    tool_calls), ca să declanșeze automat un memory_recall în dimag —
    regulă obligatorie pentru toți agenții, nu doar pentru modelele cu
    tool-calling nativ nesigur (ex. gpt-oss pe build-ul curent).

    Markerii "strong" (epistemici) declanșează mereu. Markerii "weak"
    (familia find/exist, ex. "nu există") apar firesc în rapoarte de
    stare corecte după ce tool-urile au fost deja folosite — declanșează
    doar dacă încă nu s-a executat niciun tool în turul curent (fals
    pozitiv real: raport SSH valid "vm1 inactive / nu există" după 2
    apeluri ssh, 2026-11)."""
    lowered = text.lower()
    if any(marker in lowered for marker in _STRONG_MARKERS):
        return True
    return not tools_used and any(marker in lowered for marker in _WEAK_MARKERS)


async def auto_recall(agent: Any, user_message: str) -> str:
    """Emite evenimentul SSE `auto_recall`, rulează tool-ul memory_recall pe
    mesajul utilizatorului și întoarce stringul complet de adăugat în istoric
    ca mesaj "user" (rol "user", nu "system": template-ul SenseNova (SSN,
    :8093) ridică excepție jinja „System message must be at the beginning" la
    orice system care nu e primul — run-ul cădea cu 500; ca "user" merge pe
    toate modelele locale și gpt-oss îl citea oricum).

    Dacă tool-ul lipsește, fallback-ul e "[memorie dimag: indisponibilă]"."""
    agent._emit("auto_recall", query=user_message)
    recall_tool = agent.tools.get("memory_recall")
    recall_message = "[memorie dimag: indisponibilă]"
    if recall_tool is not None:
        result = await recall_tool.execute(query=user_message)
        recall_message = result.message
    return _RECALL_PREFIX + recall_message
