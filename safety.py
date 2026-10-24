"""Plasa de siguranță: denylist de comenzi cu risc înalt (cer confirmare în UI),
mascarea secretelor cunoscute și trunchierea output-urilor.

robo-zero trebuie să fie versatil: poate clona repo-uri, instala dependențe,
diagnostica servicii, etc. Denylist-ul permite toate operațiile uzuale:
- Comenzi read-only de diagnostică: systemctl status, journalctl, ps, find, grep
- Gestionare dependențe: apt, pip, git (inclus git reset --hard pentru cleanup)
- File system ops în workdir: rm, mkdir, etc.
- Doar BLOCHEAZĂ: kill/pkill (prea riscant), sudo (robo-zero deja rulează ca sorin),
  dd/mkfs (operații pe disc cruda, zero caz legitim)

Decizia reală de aprobare/refuz vine din afară (UI sau harness CLI),
via un callback async primit de Agent. Confirmarea e cerută pentru comenzi
care schimbă stare (apt install, git push, etc.).
"""

import os
import re

# Comenzi care cer CONFIRMĂ în UI (risc înalt sau schimbă stare).
# Sunt excluse: comenzi read-only (systemctl status), git ops benefice (reset --hard).
_DENYLIST_PATTERNS: list[re.Pattern] = [
    # Procese — prea riscant, chiar cu confirmare
    re.compile(r"\b(kill|pkill|killall)\b", re.IGNORECASE),
    # Operații pe disc cruda — zero caz de uz legit
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    # sudo — robo-zero rulează deja ca sorin; nu e nevoie
    re.compile(r"\bsudo\b", re.IGNORECASE),
    # Schimbări de stare care merita confirmare (dar nu blocheaza)
    # apt install trebuie confirmat (apt update/list e OK)
    re.compile(r"\bapt\s+install\b", re.IGNORECASE),
    re.compile(r"\bpip\s+install\b", re.IGNORECASE),
    # git push — schimbă repo-uri externe
    re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    # chmod/chown recursiv — poate schimba permisiuni în afara workdir
    re.compile(r"\bchmod\s+-R\b", re.IGNORECASE),
    re.compile(r"\bchown\s+-R\b", re.IGNORECASE),
]

# redirectare de output către o cale absolută în afara workdir/ + /tmp
# — permite /tmp si workdir (sigur), blocheaza /etc, /root, etc.
# (regex: blocheaza doar redirectare in root path care nu e /tmp, /srv, /dev/null)
_REDIRECT_PATTERN = re.compile(r"[>]{1,2}\s*/(?!dev/null|tmp|srv)")

_QUOTED_PATTERN = re.compile(r"'[^']*'|\"[^\"]*\"")


def _strip_quoted(command: str) -> str:
    """Elimină conținutul dintre ghilimele single/double, păstrând restul
    comenzii — redirecționările shell reale nu apar niciodată în interiorul
    unui literal citat."""
    return _QUOTED_PATTERN.sub("", command)

# Trunchiere output shell (constatarea plan: 20 KB)
SHELL_OUTPUT_LIMIT = 20 * 1024
# Timeout implicit per comandă shell (constatarea plan: 120s, configurabil)
SHELL_TIMEOUT_SECONDS = 120

_SECRET_ENV_VARS = ("DEEPSEEK_API_KEY", "NVIDIA_API_KEY")


def requires_confirmation(command: str) -> bool:
    """True dacă comanda shell se potrivește cu denylist-ul și trebuie
    confirmată explicit în UI înainte de execuție."""
    if any(p.search(command) for p in _DENYLIST_PATTERNS):
        return True
    return bool(_REDIRECT_PATTERN.search(_strip_quoted(command)))


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
