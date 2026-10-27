#!/usr/bin/env python3
"""r0code — harness de coding minimal, headless, apelabil de r0 / Claude / Hermes.

Ideea: bucla e banală (chat + tool calls + stop). Ce contează e (1) router-ul
care schimbă modelul la mijlocul rulării și (2) contractul de ieșire JSON.

Rutare: lanț de modele ordonat pe tier (free -> sub -> paid). Nu ghicim din
prompt ce model e bun: pornim ieftin și PROMOVĂM la primul semn de eșec
(HTTP, JSON invalid în arguments, tool inexistent, repetiție, buclă). Istoricul
se păstrează la schimbare, fiindcă toate modelele vorbesc același protocol
(chat/completions + tool_calls), deci schimbarea de model = un string.

Utilizare:
  r0code --task "repară bugul din calc.py și rulează testele" --workdir /tmp/x
  r0code --task "explică ce face modulul" --tier free --json
  r0code --task "..." --tier sub --answer-only

Exit: 0 ok, 1 task eșuat, 2 eroare de configurare.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------- bootstrap ----
# Cheile stau in ~/.config/r0code/env (chmod 600), NU in workdir: workdir-ul e
# partajat pe SMB si orice fisier de acolo e vizibil tuturor.
_ENV_FILE = Path(os.environ.get("R0CODE_ENV", Path.home() / ".config" / "r0code" / "env"))


def _load_env(path: Path) -> None:
    """Parser .env minimal (KEY=VALUE, # comentariu). Variabilele deja setate castiga."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(_ENV_FILE)

# ---------------------------------------------------------------- modele ----
# tier: free (0 cost marginal) | sub (abonament plătit) | paid (per token)
# Fiecare: (tier, endpoint, model_id, env_key)
MODELS: dict[str, dict] = {
    # --- free ---
    "kilo-laguna-s:free": {
        "tier": "free",
        "endpoint": "https://api.kilo.ai/api/gateway/chat/completions",
        "model": "poolside/laguna-s-2.1:free",
        "key_env": "KILO_API_KEY",
        "ctx": 262144,
    },
    "kilo-lfm:free": {
        "tier": "free",
        "endpoint": "https://api.kilo.ai/api/gateway/chat/completions",
        "model": "liquid/lfm-2.5-2.6b:free",
        "key_env": "KILO_API_KEY",
        "ctx": 65536,
    },
    # verificat live 2026-10-25: tool_calls corecte; varianta -super-120b răspunde 404
    # la chat/completions pe contul nostru, deși apare în /v1/models
    "nvidia-lightning:free": {
        "tier": "free",
        "endpoint": "https://integrate.api.nvidia.com/v1/chat/completions",
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "key_env": "NVIDIA_API_KEY",
        "ctx": 131072,
    },
    # --- sub (abonament MiMo Token Plan, deja plătit) ---
    # Cheia din "api keys.txt" e emisă pe clusterul AMS: /v1/models răspunde pe
    # token-plan-ams, iar token-plan-cn și -sgp întorc 401 invalid_key cu ea.
    # Verificat live 2026-10-25. Dacă apare o cheie CN, se adaugă un model nou
    # cu endpoint-ul https://token-plan-cn.xiaomimimo.com/v1/chat/completions.
    "mimo-flash": {
        "tier": "sub",
        "endpoint": "https://token-plan-ams.xiaomimimo.com/v1/chat/completions",
        "model": "mimo-v2.6-flash",
        "key_env": "XIAOMI_API_KEY",
        "ctx": 1048576,
    },
    "mimo-pro": {
        "tier": "sub",
        "endpoint": "https://token-plan-ams.xiaomimimo.com/v1/chat/completions",
        "model": "mimo-v2.6-pro",
        "key_env": "XIAOMI_API_KEY",
        "ctx": 1048576,
    },
    # --- local (Ollama pe SVAI, 0 cost, 0 rețea externă) ---
    # qwen3.5:4b-r0 e un model derivat din qwen3.5:4b cu PARAMETER num_ctx 32768;
    # modelul de bază are 4096 și taie începutul promptului (verificat 2026-10-25).
    "local-qwen": {
        "tier": "local",
        "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
        "model": "qwen3.5:4b-r0",
        "key_env": None,          # Ollama nu cere cheie
        "ctx": 32768,
        "slow": True,             # prefill 55 tok/s rece / 965 tok/s cu cache, gen 5.5 tok/s (T400 4GB)
        "max_result_chars": 2500,  # 32k ctx se umple în 5-6 citiri la 6000 cifre/rezultat
    },
    # Qwen2.5-Coder 14B Q4_K_M pe SVAI (RTX 3060 12GB), derivat cu num_ctx 24576:
    # la 32768 nu mai încape integral în VRAM (12% CPU, verificat 2026-10-27).
    "svai-coder14b": {
        "tier": "local",
        "endpoint": "http://192.168.0.152:11434/v1/chat/completions",
        "model": "qwen2.5-coder-14b-r0",
        "key_env": None,
        "ctx": 24576,
        "slow": True,
        "max_result_chars": 3000,
    },
    # --- paid (fallback, per token) ---
    "deepseek-flash": {
        "tier": "paid",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-flash",
        "key_env": "DEEPSEEK_API_KEY",
        "ctx": 131072,
    },
}

CHAINS = {
    "free": ["kilo-laguna-s:free", "nvidia-lightning:free"],
    "sub": ["mimo-flash", "mimo-pro"],
    "paid": ["deepseek-flash"],
    # local NU intră în "auto": e mai lent decât free-ul din cloud, care e tot
    # gratuit. Se cere explicit cu --tier local (offline, date care nu ies din casă).
    "local": ["local-qwen"],
    # auto: ieftin întâi, promovare automată (lanțul complet, în ordine)
    "auto": [
        "kilo-laguna-s:free",
        "nvidia-lightning:free",
        "mimo-flash",
        "mimo-pro",
        "deepseek-flash",
    ],
}

# -------------------------------------------------------- scheme tool-uri ----
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Citește un fișier (cu numere de linie). Folosește-l înainte de orice editare.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "cale relativă la workdir"},
                    "offset": {"type": "integer", "description": "prima linie (1-based)"},
                    "limit": {"type": "integer", "description": "câte linii (implicit 200)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": (
                "Înlocuiește în fișier un text exact, o singură dată. "
                "`old` trebuie să apară exact o dată; altfel tool-ul refuză. "
                "Preferă-l în locul rescrierii fișierului întreg."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string", "description": "textul existent, exact, cu indentare"},
                    "new": {"type": "string", "description": "textul de înlocuire (poate fi gol = ștergere)"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Scrie un fișier nou (sau îl rescrie complet). Pentru modificări mici folosește `edit`.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Caută un regex în fișierele din workdir; întoarce fișier:linie:text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "subdirector (implicit '.')"},
                    "glob": {"type": "string", "description": "ex. '*.py' (implicit toate fișierele text)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list",
            "description": "Listează un director (nerecursiv).",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "Rulează o comandă în workdir. Permise: python, pytest, ls, cat, head, tail, grep, "
                "find, wc, git (status/diff/log/show/add/commit), ruff, node, npm test, make. "
                "Interzise: rm, sudo, curl, ssh, dd, mkfs, > în afara workdir."
            ),
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}, "timeout": {"type": "integer"}},
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Încheie task-ul. Mesajul e rezumatul pentru cel care te-a apelat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "ok": {"type": "boolean", "description": "false dacă task-ul nu s-a putut termina"},
                },
                "required": ["summary", "ok"],
            },
        },
    },
]

SYSTEM = """Ești r0code, un agent de coding non-interactiv, apelat de alt agent.
Lucrezi exclusiv în directorul curent. Reguli:
- citește fișierul înainte să-l modifici; folosește `edit` cu text exact, nu rescrie fișiere întregi;
- rulează testele după fiecare modificare logică;
- nu cere confirmări, nu pune întrebări: decizi singur;
- când ai terminat (reușit sau nu), apelează `done` cu un rezumat de 1-3 fraze.
Nu inventa rezultate: dacă o comandă a eșuat, spune ce a eșuat.
Citește doar porțiunile de care ai nevoie (offset/limit); nu citi fișiere întregi fără motiv."""

# ------------------------------------------------------------- constrângeri --
MAX_STEPS = 24
MAX_CHARS_RESULT = 6000
SHELL_TIMEOUT = 120
ALLOWED_CMDS = {
    "python", "python3", "pytest", "ls", "cat", "head", "tail", "grep", "rg",
    "find", "wc", "git", "ruff", "node", "npm", "make", "jq", "sed", "awk", "tar", "diff",
}
DENY_RE = re.compile(r"(^|[;&|]\s*)(rm|sudo|su|dd|mkfs|shutdown|reboot|chmod|chown|kill|systemctl|curl|wget|ssh|scp)\b")


class ConfigError(Exception):
    pass


@dataclass
class ModelAttempt:
    key: str
    tier: str
    turns: int = 0
    tool_calls: int = 0
    text_calls: int = 0       # ture în care apelul a venit ca text JSON (recuperat)
    ok: bool = False
    why: str = ""
    usage: dict = field(default_factory=dict)


# ------------------------------------------------------------------ tools ----
class Tools:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.changed: dict[str, dict] = {}

    def _p(self, path: str) -> Path:
        p = (self.root / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
        if not str(p).startswith(str(self.root)):
            raise ValueError(f"cale în afara workdir: {path}")
        return p

    def read(self, path: str, offset: int = 1, limit: int = 200) -> str:
        p = self._p(path)
        if not p.is_file():
            return f"Eroare: '{path}' nu există sau nu e fișier."
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(offset)) - 1
        chunk = lines[start:start + int(limit)]
        out = "\n".join(f"{start + i + 1:>5}| {l}" for i, l in enumerate(chunk))
        tail = "" if start + len(chunk) >= len(lines) else f"\n... ({len(lines)} linii în total)"
        return out + tail

    def write(self, path: str, content: str) -> str:
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        before = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        p.write_text(content, encoding="utf-8")
        self._mark(path, before, content, "written")
        return f"OK: scris {path} ({len(content)} caractere)"

    def edit(self, path: str, old: str, new: str) -> str:
        p = self._p(path)
        if not p.is_file():
            return f"Eroare: '{path}' nu există."
        src = p.read_text(encoding="utf-8")
        n = src.count(old)
        if n == 0:
            return "Eroare: textul `old` nu apare în fișier. Citește fișierul și trimite textul exact."
        if n > 1:
            return f"Eroare: textul `old` apare de {n} ori. Adaugă context ca să fie unic."
        after = src.replace(old, new, 1)
        p.write_text(after, encoding="utf-8")
        self._mark(path, src, after, "edited",
                   added=max(1, len(new.splitlines())) if new.strip() else 0,
                   removed=max(1, len(old.splitlines())) if old.strip() else 0)
        return f"OK: {path} modificat ({src.count(chr(10))} → {p.read_text(encoding='utf-8').count(chr(10))} linii)"

    def _mark(self, path: str, before: str, after: str, action: str,
              added: int | None = None, removed: int | None = None) -> None:
        if added is None:
            b, a = before.splitlines(), after.splitlines()
            added = max(0, len(a) - len(b))
            removed = max(0, len(b) - len(a))
        cur = self.changed.get(path)
        if cur:
            cur["action"] = "edited" if cur["action"] != "written" else cur["action"]
            cur["added"] += added
            cur["removed"] += removed
        else:
            self.changed[path] = {"path": path, "action": action, "added": added, "removed": removed}

    def grep(self, pattern: str, path: str = ".", glob: str = "*") -> str:
        base = self._p(path)
        rx = re.compile(pattern)
        hits, scanned = [], 0
        for f in base.rglob(glob):
            if not f.is_file() or f.stat().st_size > 400_000:
                continue
            if any(part in {".git", "node_modules", "__pycache__", ".venv"} for part in f.parts):
                continue
            scanned += 1
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f.relative_to(self.root)}:{i}: {line.strip()[:160]}")
                        if len(hits) >= 60:
                            return "\n".join(hits) + "\n... (trunchiat la 60)"
            except OSError:
                continue
        return "\n".join(hits) if hits else f"Niciun rezultat (fișiere scanate: {scanned})"

    def list(self, path: str = ".") -> str:
        p = self._p(path)
        if not p.is_dir():
            return f"Eroare: '{path}' nu e director."
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        return "\n".join(f"{x.name}/" if x.is_dir() else f"{x.name} ({x.stat().st_size}b)" for x in items) or "(gol)"

    def shell(self, cmd: str, timeout: int = SHELL_TIMEOUT) -> str:
        if DENY_RE.search(cmd):
            return "Refuzat: comandă din lista interzisă (rm/sudo/curl/...)."
        first = cmd.strip().split()[0] if cmd.strip() else ""
        if first == "cd":
            return ("Refuzat: lucrezi deja în rădăcina proiectului, nu folosi `cd`. "
                    "Dacă vrei alt subdirector, dă calea ca argument (ex. `python3 -m unittest shop.test_shop`).")
        if first not in ALLOWED_CMDS:
            return f"Refuzat: '{first}' nu e în allowlist ({', '.join(sorted(ALLOWED_CMDS))})."
        try:
            r = subprocess.run(
                cmd, shell=True, cwd=self.root, capture_output=True, text=True,
                timeout=min(int(timeout), SHELL_TIMEOUT),
            )
        except subprocess.TimeoutExpired:
            return f"Timeout după {timeout}s."
        out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
        out = out[:MAX_CHARS_RESULT]
        return f"exit={r.returncode}\n{out}".strip()

    def dispatch(self, name: str, args: dict) -> str:
        fn = getattr(self, name, None)
        if fn is None or name not in {t["function"]["name"] for t in TOOLS}:
            raise KeyError(name)
        return str(fn(**args))


# ------------------------------------------------------------------ router ----
class Router:
    """Lanț de modele cu promovare pe eșec observat. Nu prezice, măsoară."""

    def __init__(self, chain: list[str], max_turns_per_model: int = 8) -> None:
        self.chain = chain
        self.max_turns = max_turns_per_model
        self.i = -1
        self.trace: list[ModelAttempt] = []
        self.budget_events: list[str] = []
        self._att: ModelAttempt | None = None
        self._stale = True  # trebuie ales următorul model disponibil

    def _available(self, key: str) -> bool:
        env = MODELS[key]["key_env"]
        return True if env is None else bool(os.environ.get(env))

    def _advance(self) -> None:
        """Alege următorul model disponibil din lanț (o singură dată per promovare)."""
        while self.i + 1 < len(self.chain):
            self.i += 1
            key = self.chain[self.i]
            if self._available(key):
                self._stale = False
                return
            self.budget_events.append(f"sărit {key}: lipsește {MODELS[key]['key_env']}")
        raise ConfigError(
            "niciun model disponibil din lanțul "
            + " → ".join(self.chain)
            + " (setează măcar o cheie)"
        )

    @property
    def current(self) -> dict:
        """Modelul curent; avansează doar dacă a fost promovat. ConfigError dacă niciunul nu e disponibil."""
        if self._stale:
            self._advance()
        return {"key": self.chain[self.i], **MODELS[self.chain[self.i]]}

    def attempt(self) -> ModelAttempt:
        """Attempt-ul modelului curent (același obiect pe toată durata lui)."""
        if self._att is None or self._att.key != self.chain[self.i]:
            self._att = ModelAttempt(key=self.chain[self.i], tier=MODELS[self.chain[self.i]]["tier"])
            self.trace.append(self._att)
        return self._att

    def escalate(self, why: str) -> bool:
        """Trece la următorul model. False dacă lanțul s-a terminat."""
        if self._att is not None:
            self._att.ok = False
            self._att.why = why
        if self.i + 1 >= len(self.chain):
            return False
        prev = self.chain[self.i]
        self._att = None
        self._stale = True
        nxt = self.chain[self.i + 1]
        self.budget_events.append(f"{prev} → {nxt}: {why}")
        return True


# -------------------------------------------------------------------- llm ----
def call_model(spec: dict, messages: list[dict], timeout: int | None = None) -> dict:
    if timeout is None:
        timeout = 900 if spec.get("slow") else 180
    body = json.dumps({
        "model": spec["model"],
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.2,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if spec.get("key_env"):
        headers["Authorization"] = f"Bearer {os.environ[spec['key_env']]}"
    req = urllib.request.Request(spec["endpoint"], data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"rețea: {e.reason}") from e


def recover_text_tool_calls(msg: dict) -> int:
    """Unele modele locale (Qwen2.5-Coder prin Ollama, verificat 2026-10-27) pun
    apelul ca text JSON în content, cu tool_calls=None. Dacă tool_calls lipsește,
    caută în content obiecte {"name": <tool cunoscut>, "arguments": {...}} și le
    mută în msg["tool_calls"]. Doar nume de tool existente, ca text obișnuit
    care conține JSON să nu fie luat drept comandă. Întoarce câte a recuperat."""
    content = msg.get("content")
    if msg.get("tool_calls") or not isinstance(content, str) or "{" not in content:
        return 0
    known = {t["function"]["name"] for t in TOOLS}
    dec = json.JSONDecoder()
    found, spans, i = [], [], 0
    while (i := content.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(content, i)
        except json.JSONDecodeError:
            i += 1
            continue
        args = obj.get("arguments", obj.get("parameters")) if isinstance(obj, dict) else None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = None
        if isinstance(obj, dict) and obj.get("name") in known and isinstance(args, dict):
            found.append({"id": f"txt_{len(found)}", "type": "function",
                          "function": {"name": obj["name"], "arguments": json.dumps(args, ensure_ascii=False)}})
            spans.append((i, end))
            i = end
        else:
            i += 1
    if not found:
        return 0
    rest = content
    for a, b in reversed(spans):
        rest = rest[:a] + rest[b:]
    rest = re.sub(r"```(?:json)?\s*```|</?tool_call>", "", rest).strip()
    msg["tool_calls"] = found
    msg["content"] = rest or None
    return len(found)


def parse_tool_calls(msg: dict) -> tuple[list[dict], str]:
    """Validează tool_calls: JSON parsabil + tool existent. Întoarce (apeluri, eroare)."""
    raw = msg.get("tool_calls") or []
    known = {t["function"]["name"] for t in TOOLS}
    out = []
    for tc in raw:
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        if name not in known:
            return [], f"tool inexistent: {name!r}"
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError as e:
            return [], f"arguments nu e JSON valid: {e}"
        if not isinstance(args, dict):
            return [], "arguments nu e obiect JSON"
        out.append({"id": tc.get("id") or f"call_{len(out)}", "name": name, "args": args})
    return out, ""


# ---------------------------------------------------------------- jurnal ----
LOG_PATH = Path(os.environ.get(
    "R0CODE_LOG", Path.home() / ".local" / "state" / "r0code" / "runs.jsonl"))


def log_run(result: dict, task: str, tier: str, chain_used: list[str]) -> None:
    """O linie JSON per rulare, ca să existe date pentru reglarea router-ului.
    Scrierea nu are voie să strice rularea (discul poate fi plin, calea poate
    lipsi) — de aceea try/except mut."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "task": task[:300],
            "tier": tier,
            "chain": chain_used,
            "ok": result.get("ok"),
            "verified": result.get("verified"),
            "verified_cmd": (result.get("verify") or {}).get("cmd"),
            "false_done": bool(result.get("ok") is False and result.get("verified") is False),
            "steps": result.get("steps"),
            "tool_calls": result.get("tool_calls"),
            "wall_s": result.get("wall_s"),
            "usage": result.get("usage"),
            "models": [{"model": m.get("model"), "tier": m.get("tier"),
                        "turns": m.get("turns"), "ok": m.get("ok"), "why": m.get("why")}
                       for m in result.get("models") or []],
            "escalations": result.get("escalations"),
            "files": len(result.get("files") or []),
        }
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ------------------------------------------------------------- verificare ----
def run_verify(cmd: str, workdir: Path, timeout: int = 300) -> dict:
    """Rulează comanda de verificare în workdir și întoarce exit code + output.

    Aici e diferența dintre „agentul zice că a terminat" și „s-a terminat":
    `ok` se ia din exit code, nu din ce a declarat modelul în `done`."""
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=str(workdir), capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "exit": -9, "output": f"timeout după {timeout}s", "pass": False}
    out = ((r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")).strip()
    return {
        "cmd": cmd,
        "exit": r.returncode,
        "output": out[-4000:],
        "pass": r.returncode == 0,
    }


# ------------------------------------------------------------------- main ----
def run(task: str, workdir: Path, tier: str, max_steps: int = MAX_STEPS,
        answer_only: bool = False, verbose: bool = False,
        chain: list[str] | None = None, verify: str | None = None) -> dict:
    tools = Tools(workdir)
    router = Router(chain or CHAINS[tier])
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task},
    ]
    started = time.time()
    steps = 0
    summary, ok = "", False
    protocol_errors = 0
    seen: list[str] = []
    verified: bool | None = None      # None = nu s-a cerut verificare
    verify_result: dict | None = None
    verify_failures = 0
    no_tool_streak = 0

    while steps < max_steps:
        steps += 1
        try:
            spec = router.current
        except ConfigError as e:
            return {"ok": False, "summary": str(e), "error": str(e), "exit": 2,
                "files": [], "models": _trace(router), "escalations": router.budget_events,
                "tool_calls": 0, "steps": 0, "wall_s": 0, "usage": {"prompt": 0, "completion": 0}}
        att = router.attempt()
        try:
            resp = call_model(spec, messages)
        except RuntimeError as e:
            att.why = str(e)[:200]
            if router.escalate(f"apel eșuat: {att.why}"):
                continue
            break

        usage = resp.get("usage") or {}
        att.usage["prompt"] = att.usage.get("prompt", 0) + usage.get("prompt_tokens", 0)
        att.usage["completion"] = att.usage.get("completion", 0) + usage.get("completion_tokens", 0)
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        if recover_text_tool_calls(msg):
            att.text_calls += 1
        calls, err = parse_tool_calls(msg)

        if err:
            protocol_errors += 1
            att.why = err
            messages.append({"role": "assistant", "content": msg.get("content") or ""})
            messages.append({
                "role": "user",
                "content": f"[router] Eroare de protocol: {err}. Reîncearcă apelul corect sau apelează `done`.",
            })
            if protocol_errors >= 2 and router.escalate(f"{protocol_errors}× {err}"):
                protocol_errors = 0
            continue

        if not calls:
            text = (msg.get("content") or "").strip()
            messages.append({"role": "assistant", "content": text})
            if att.turns == 0 and not text:
                if router.escalate("răspuns gol"):
                    continue
                break
            messages.append({"role": "user", "content": "[router] Nu ai apelat niciun tool. Continuă cu un tool sau apelează `done`."})
            att.turns += 1
            # Două ture consecutive doar-text = modelul s-a oprit din lucru fără
            # să declare `done`. Un singur tur e normal (gândește cu voce tare),
            # deci promovăm abia la al doilea.
            no_tool_streak += 1
            if no_tool_streak >= 2 and router.escalate("2 ture consecutive fără tool call"):
                no_tool_streak = 0
                continue
            continue

        att.turns += 1
        no_tool_streak = 0
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": msg["tool_calls"]})
        stop = False       # ieșim din bucla while la finalul turei
        touched = False    # tura asta a modificat ceva pe disc
        for c in calls:
            att.tool_calls += 1
            if c["name"] == "done":
                summary = str(c["args"].get("summary", ""))
                claim = bool(c["args"].get("ok", True))
                if not verify:
                    ok = claim
                    att.ok, att.why = True, "done"
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": "încheiat"})
                    stop = True
                    break
                # poarta: modelul declară, comanda decide
                verify_result = run_verify(verify, workdir)
                verified = verify_result["pass"]
                if verified:
                    ok = True
                    att.ok, att.why = True, "done+verify"
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": "verificare trecută"})
                    stop = True
                    break
                verify_failures += 1
                att.why = f"verificare picată (exit {verify_result['exit']})"
                messages.append({
                    "role": "tool", "tool_call_id": c["id"],
                    "content": (f"Verificarea `{verify}` a PICAT (exit {verify_result['exit']}), "
                                f"deci task-ul nu e terminat:\n{verify_result['output'][-2000:]}\n\n"
                                f"Repară cauza și apelează din nou `done` când trece."),
                })
                ok = False
                if verify_failures >= 2 and router.escalate(
                        f"verificare picată de {verify_failures}× (exit {verify_result['exit']})"):
                    verify_failures = 0
                stop = True
                break
            sig = f"{c['name']}:{json.dumps(c['args'], sort_keys=True)[:120]}"
            seen.append(sig)
            if seen.count(sig) >= 3:
                if router.escalate(f"repetiție: {c['name']} identic de 3×"):
                    break
            if verbose:
                short = json.dumps(c["args"], ensure_ascii=False)[:160]
                print(f"  [{spec['key']}] {c['name']} {short}", file=sys.stderr)
            try:
                result = tools.dispatch(c["name"], c["args"])
            except (KeyError, TypeError, ValueError) as e:
                result = f"Eroare la apel: {e}"
            limit = spec.get("max_result_chars", MAX_CHARS_RESULT)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result[:limit]})
            if c["name"] in ("write", "edit", "shell"):
                touched = True
        if stop:
            break

        # poarta timpurie: dacă verificarea trece după o tură care a modificat
        # ceva, task-ul e terminat — nu mai așteptăm ca modelul să declare.
        if verify and touched:
            verify_result = run_verify(verify, workdir)
            if verify_result["pass"]:
                verified = ok = True
                att.ok, att.why = True, "verify (fără done)"
                summary = summary or "Verificarea a trecut după ultima modificare."
                break
            verify_result = None

    if not ok and not summary and router.trace:
        last = router.trace[-1]
        summary = f"Neîncheiat după {steps} pași (ultimul model: {last.key}, motiv: {last.why or 'buget epuizat'})."

    files = list(tools.changed.values())
    return {
        "ok": ok,
        "verified": verified,
        "verify": verify_result,
        "summary": summary if not answer_only else _last_text(messages),
        "files": files,
        "models": _trace(router),
        "escalations": router.budget_events,
        "tool_calls": sum(a.tool_calls for a in router.trace),
        "steps": steps,
        "wall_s": round(time.time() - started, 1),
        "usage": {
            "prompt": sum(a.usage.get("prompt", 0) for a in router.trace),
            "completion": sum(a.usage.get("completion", 0) for a in router.trace),
        },
        "exit": 0 if ok else 1,
    }


def _last_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            return m["content"].strip()
    return ""


def _trace(router: Router) -> list[dict]:
    return [
        {"model": a.key, "tier": a.tier, "turns": a.turns, "tool_calls": a.tool_calls, "text_calls": a.text_calls,
         "ok": a.ok, "why": a.why, "usage": a.usage}
        for a in router.trace
    ]


# ------------------------------------------------------ inventar de modele --
def ollama_models(host: str = "http://127.0.0.1:11434") -> list[dict]:
    """Modelele locale, cu capabilitățile declarate. `tools` e obligatoriu
    pentru r0code — un model fără tool calling nu poate lucra în buclă."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []
    out = []
    for m in data.get("models") or []:
        caps = m.get("capabilities") or []
        det = m.get("details") or {}
        out.append({
            "name": m.get("name"),
            "size_gb": round((m.get("size") or 0) / 1e9, 1),
            "params": det.get("parameter_size"),
            "ctx": det.get("context_length"),
            "tools": "tools" in caps,
        })
    return sorted(out, key=lambda x: (-x["size_gb"]))


def print_models() -> None:
    print("MODELE CONFIGURATE:")
    for key, spec in MODELS.items():
        env = spec["key_env"]
        has = env is None or bool(os.environ.get(env))
        mark = "gata" if has else f"lipsește {env}"
        print(f"  {key:22s} tier={spec['tier']:6s} {spec['model']:38s} {mark}")
    print("\nLANȚURI:")
    for name, chain in CHAINS.items():
        print(f"  {name:6s} {' → '.join(chain)}")

    local = ollama_models()
    print(f"\nOLLAMA LOCAL ({len(local)} modele):")
    if not local:
        print("  (nu răspunde pe 127.0.0.1:11434)")
        return
    usable = [m for m in local if m["tools"]]
    print(f"  cu tool calling: {len(usable)}")
    for m in usable:
        note = ""
        if m["name"].endswith("-r0"):
            note = "  ← derivat cu num_ctx mărit"
        print(f"    {m['name']:30s} {m['size_gb']:4.1f}GB {str(m['params']):>6s} ctx={m['ctx']}{note}")
    print("  Notă: modelul de bază are 4096 ctx în Ollama, indiferent ce declară")
    print("  metadata; fără un derivat cu PARAMETER num_ctx, promptul e trunchiat.")


def main() -> None:
    ap = argparse.ArgumentParser(description="r0code — coding harness headless, cu router de modele")
    ap.add_argument("--task", help="ce trebuie făcut (în limbaj natural)")
    ap.add_argument("--list-models", action="store_true", help="inventar: modele configurate, lanțuri, Ollama local")
    ap.add_argument("--workdir", default=".", help="directorul de lucru (implicit: curent)")
    ap.add_argument("--tier", default="auto", choices=sorted(CHAINS), help="lanțul de modele")
    ap.add_argument("--chain", help="lanț explicit, ex. 'kilo-laguna-s:free,deepseek-flash' (suprascrie --tier)")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    ap.add_argument("--json", action="store_true", help="ieșire JSON (pentru alt agent)")
    ap.add_argument("--answer-only", action="store_true", help="întoarce doar textul, fără raport")
    ap.add_argument("--verify", help="comandă care decide succesul (exit 0 = ok); "
                                     "rulează în workdir după `done` și, dacă pică, agentul e trimis înapoi")
    ap.add_argument("--no-log", action="store_true", help="nu scrie în runs.jsonl")
    ap.add_argument("-v", "--verbose", action="store_true", help="loghează fiecare tool call pe stderr")
    args = ap.parse_args()

    if args.list_models:
        print_models()
        return
    if not args.task:
        ap.error("--task e obligatoriu (sau folosește --list-models)")

    root = Path(args.workdir).resolve()
    if not root.is_dir():
        print(f"r0code: workdir inexistent: {root}", file=sys.stderr)
        sys.exit(2)

    chain = None
    if args.chain:
        chain = [c.strip() for c in args.chain.split(",") if c.strip()]
        unknown = [c for c in chain if c not in MODELS]
        if unknown:
            print(f"r0code: model necunoscut în --chain: {', '.join(unknown)}", file=sys.stderr)
            sys.exit(2)
    result = run(args.task, root, args.tier, args.max_steps, args.answer_only,
                 args.verbose, chain, args.verify)
    if not args.no_log and not args.answer_only:
        log_run(result, args.task, args.tier, chain or CHAINS[args.tier])

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.answer_only:
        print(result.get("summary", ""))
    else:
        status = ("OK (verificat)" if result.get("verified")
                  else "OK (nedeclarat de model, NEVERIFICAT)" if result["ok"] else "EȘEC")
        print(f"{status} — {result['summary']}\n")
        if result.get("verify"):
            v = result["verify"]
            print(f"  verificare: `{v['cmd']}` → exit {v['exit']} "
                  f"({'trece' if v['pass'] else 'PICĂ'})")
        for f in result.get("files", []):
            print(f"  {f['action']:9s} {f['path']}  +{f['added']}/-{f['removed']}")
        print(f"\n  modele: {', '.join(f'{m['model']}({m['turns']}t/{m['tool_calls']}c)' for m in result.get('models', []))}")
        for e in result.get("escalations", []):
            print(f"  escaladare: {e}")
        print(f"  {result['steps']} pași, {result['wall_s']}s, "
              f"{result['usage']['prompt']}+{result['usage']['completion']} tokeni")

    sys.exit(result.get("exit", 1))


if __name__ == "__main__":
    main()
