"""Plasa de siguranță: denylist de comenzi distructive (cer confirmare în UI),
mascarea secretelor cunoscute și trunchierea output-urilor.

Nu blochează execuția — doar decide dacă e nevoie de confirmare înainte,
și curăță ce ajunge în istoric/log/SSE. Decizia reală de aprobare/refuz
vine din afară (UI sau, în etapa 1, harness-ul CLI), via un callback async
primit de Agent.
"""

import os
import re

# Comenzi shell care cer confirmare înainte de execuție (constatarea 1 din plan).
# Potriviri pe cuvinte/paternuri, nu doar substring, ca să nu dea fals-pozitiv
# pe argumente care conțin întâmplător litere similare.
_DENYLIST_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+(-\w*\s+)*-\w*[rf]\w*", re.IGNORECASE),  # rm -r / -rf / -fr ...
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bsystemctl\b", re.IGNORECASE),
    re.compile(r"\b(kill|pkill)\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+-R\b", re.IGNORECASE),
    re.compile(r"\bchown\s+-R\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    # redirectare de output către o cale absolută (scriere posibil în afara workdir/)
    re.compile(r"[>]{1,2}\s*/(?!dev/null)"),
]

# Trunchiere output shell (constatarea plan: 20 KB)
SHELL_OUTPUT_LIMIT = 20 * 1024
# Timeout implicit per comandă shell (constatarea plan: 120s, configurabil)
SHELL_TIMEOUT_SECONDS = 120

_SECRET_ENV_VARS = ("DEEPSEEK_API_KEY", "NVIDIA_API_KEY")


def requires_confirmation(command: str) -> bool:
    """True dacă comanda shell se potrivește cu denylist-ul și trebuie
    confirmată explicit în UI înainte de execuție."""
    return any(p.search(command) for p in _DENYLIST_PATTERNS)


def path_escapes_workdir(path: str, workdir: str) -> bool:
    """True dacă `path` (rezolvat) ajunge în afara `workdir` — folosit de
    fs_tool/shell_tool pentru a decide dacă e nevoie de confirmare la scriere."""
    resolved_workdir = os.path.realpath(workdir)
    resolved_path = os.path.realpath(os.path.join(workdir, path) if not os.path.isabs(path) else path)
    return os.path.commonpath([resolved_workdir, resolved_path]) != resolved_workdir


def _known_secrets() -> list[str]:
    """Valorile curente ale secretelor cunoscute, citite din mediu (încărcate
    din .env la pornirea aplicației). Nu le afișează, nu le loghează — doar
    le folosește ca paternuri de căutat și mascat."""
    values = []
    for var in _SECRET_ENV_VARS:
        val = os.environ.get(var)
        if val:
            values.append(val)
    return values


def mask_secrets(text: str) -> str:
    """Înlocuiește orice apariție a secretelor cunoscute cu [REDACTED].
    Se aplică pe: istoricul trimis/primit de la LLM, stream SSE, mesaje de
    eroare, loguri — oriunde ar putea scăpa o cheie (ex. într-un output de
    shell care face `env` sau `cat .env`)."""
    if not text:
        return text
    masked = text
    for secret in _known_secrets():
        if secret:
            masked = masked.replace(secret, "[REDACTED]")
    return masked


def truncate(text: str, limit: int = SHELL_OUTPUT_LIMIT) -> str:
    """Trunchiază text-ul la `limit` caractere, cu notă explicită la final."""
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    return text[:limit] + f"\n... [trunchiat, {cut} caractere omise]"
