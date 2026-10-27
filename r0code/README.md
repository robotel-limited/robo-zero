# r0code — harness de coding minimal, cu router de modele

Un singur fișier Python (640 linii, doar stdlib), fără dependențe, apelabil de
r0 / Claude / Hermes. Face un lucru: primește un task de coding, îl execută
headless în workdir, întoarce JSON.

## De ce există

Kilo a taxat $7 pentru că a rulat pe kimi în loc de abonamentul MiMo. Uneltele
existente (Kilo, PandaCode) rezolvă problema prin configurație + UI; aici e
rezolvată prin **rutare explicită și promovare pe eșec**, într-un tool care nu
are UI deloc.

## Utilizare

```bash
# task normal, lanț automat (free → sub → paid)
python3 r0code.py --workdir /proiect --task "repară bugul din calc.py și rulează testele"

# pentru alt agent: ieșire JSON, un singur obiect
python3 r0code.py --workdir /proiect --task "..." --json

# forțează un tier sau un lanț anume
python3 r0code.py --workdir /proiect --task "..." --tier free
python3 r0code.py --workdir /proiect --task "..." --chain "mimo-pro,deepseek-flash"

# doar răspuns textual (explicații, review), fără modificări
python3 r0code.py --workdir /proiect --task "explică discount.py" --answer-only

# debug
python3 r0code.py --workdir /proiect --task "..." -v
```

Chei: `KILO_API_KEY` (modelele free din gateway-ul Kilo), `XIAOMI_API_KEY`
(MiMo Token Plan), `NVIDIA_API_KEY` (Nemotron free), `DEEPSEEK_API_KEY` (paid).
Modelele fără cheie sunt **sărite**, nu fatal — lanțul continuă.

Exit: `0` succes, `1` task neîncheiat, `2` configurare (nitel nicio cheie).

## Contractul JSON (ce primește agentul care îl apelează)

```json
{
  "ok": true,
  "summary": "Am adăugat promo() în discount.py și 2 teste; 7 teste trec.",
  "files": [{"path": "shop/discount.py", "action": "edited", "added": 9, "removed": 2}],
  "models": [{"model": "nvidia-lightning:free", "tier": "free", "turns": 8,
              "tool_calls": 8, "ok": true, "why": "done"}],
  "escalations": ["kilo-laguna-s:free → deepseek-flash: apel eșuat: HTTP 401"],
  "tool_calls": 8, "steps": 8, "wall_s": 12.4,
  "usage": {"prompt": 15046, "completion": 959},
  "exit": 0
}
```

`escalations` e jurnalul rutării: se vede exact ce model a lucrat, ce a picat și
de ce s-a promovat. Fără el, un „ok:true" nu spune nimic despre cât a costat.

## Router-ul

Lanț ordonat pe tier (implicit `auto`: kilo free → nvidia free → mimo → deepseek).
Nu încearcă să ghicească din prompt ce model e bun — asta e o predicție care se
înșală exact când contează. Pornește ieftin și **promovează la primul eșec
observabil**:

| semn | acțiune |
|---|---|
| HTTP 4xx/5xx, timeout, rețea | promovare imediată |
| `arguments` nu e JSON valid (2×) | promovare |
| tool inexistent (2×) | promovare |
| același apel identic de 3× | promovare |
| 8 ture fără niciun tool call | promovare |
| răspuns gol la primul tur | promovare |

Istoricul **nu** se pierde la promovare: toate modelele vorbesc același protocol
(chat/completions + `tool_calls`), deci schimbarea de model e un string. Un run
poate începe pe free și termina pe MiMo, cu aceeași conversație.

## Tool-uri

`read` (cu numere de linie), `edit` (înlocuire exactă, refuză dacă textul nu e
unic), `write`, `grep`, `list`, `shell` (allowlist de comenzi + refuz pe
rm/sudo/curl/ssh…), `done`. Șapte, nu treizeci — fiecare tool în plus scade
acuratețea tool-calling la modele mici.

## Măsurători (2026-10-25, SVAI, task: adaugă `promo()` + `apply_promo()` + teste)

`./bench.sh` rulează task-ul pe fiecare tier și **verifică adevărul pe disc**
(rulează testele singur; nu crede `done.ok`).

| tier | claim ok | teste trec | pași | tool calls | timp | tokeni (prompt) |
|---|---|---|---|---|---|---|
| free (nemotron-3.5-lightning-30b-a3b) | da | **da** | 13 | 13 | 27.5s | 35.970 |
| free | da | **da** | 13 | 13 | 25.7s | 34.414 |
| paid (deepseek-flash) | da | **da** | 13 | 19 | 22.0s | 44.183 |
| paid | da | **da** | 11 | 16 | 20.1s | 36.178 |

4/4 rulări corecte, verificate independent. Pe task-uri de această talie, modelul
free de 30B e suficient — deci `auto` trebuie să pornească de acolo.

Escaladare testată separat, cu `KILO_API_KEY` invalid:
`kilo-laguna-s:free → deepseek-flash` după HTTP 401, task-ul s-a terminat corect.
Aceeași rulare confirmă că `https://api.kilo.ai/api/gateway/chat/completions`
e endpoint-ul corect (401 = auth, nu 404).

## Ce NU face (și e intenționat)

Fără UI, fără TUI, fără agent teams, fără boți, fără sandbox-uri cloud, fără
LSP, fără server. Fiecare dintre ele e un mod nou de a greși și nu ajută un
apelant care vrea un rezumat JSON.

## Ce lipsește pentru producție

1. **Poarta de verificare** — `--verify "python3 -m unittest"` rulat automat la
   `done`; dacă pică, `ok=false` și modelul e trimis înapoi. Acum verificarea e
   doar în `bench.sh`, adică în afara tool-ului. E cel mai important lucru
   lipsă: previne „false done".
2. `edit` mai tolerant (context fuzzy, patch multi-hunk), fiindcă modelele mici
   reproduc greșit indentarea.
3. Contorizare de cost pe tier + gardă de buget (cota MiMo nu e documentată).
4. Reluare de sesiune (`--continue`) și import de conversație.
5. `rg` în loc de grep Python la repo-uri mari; respectarea `.gitignore`.
6. Evenimente de progres (robo-zero are deja `on_event`) pentru apelant.

Estimare: ~600–900 linii în plus, 3–5 zile. Bucla în sine e ~120 din cele 640.

## Model local (Ollama pe SVAI)

```bash
python3 r0code.py --list-models          # ce modele au tool calling, ce chei lipsesc
python3 r0code.py --workdir /proiect --tier local --max-steps 10 \
  --verify "python3 -m pytest -q" --task "..."
```

`local` **nu** e în lanțul `auto`: e mai lent decât free-ul din cloud, care e
tot gratuit. Se cere explicit, pentru offline sau date care nu ies din LAN.

### Modelul derivat e obligatoriu

`/v1/chat/completions` din Ollama ignoră `num_ctx` din body, iar modelul de bază
are 4096 — la 4096 Ollama **taie începutul** promptului, adică exact system
prompt-ul și schema de tool-uri. Verificat 2026-10-25: canar la începutul unui
prompt de 2.050 tokeni → răspuns gol.

```bash
ollama create qwen3.5:4b-r0 -f Modelfile.r0     # PARAMETER num_ctx 32768
ollama ps                                        # trebuie: CONTEXT 32768
```

### Viteză măsurată (T400 4GB, 54% CPU / 46% GPU, 2026-10-25)

| | prefill | generare |
|---|---|---|
| rece | 55 tok/s | 5,5 tok/s |
| **prefix cached** | **965 tok/s** | 5,7 tok/s |

Cache-ul de prefix dă 18×: în buclă doar delta se procesează. De aceea contează
puține ture și rezultate de tool scurte (`max_result_chars=2500` la local).

### Poarta timpurie (`--verify` + modificare pe disc)

După fiecare tură care a atins discul, dacă verificarea trece, rularea se
oprește — nu se mai așteaptă ca modelul să apeleze `done`. Modelul local repara
corect dar uita `done`; cu poarta: eșec la 181s → succes la 70s. Efectul nu e
doar la local: pe task-ul de bench, free a trecut de la 38,8s la 5,5s.

### Când NU folosi local

Repo-uri mari, refactorizări multi-fișier, citirea a peste 8-10 fișiere. La
5,5 tok/s fiecare tură de generare costă un minut. După montarea RTX 3060 12GB
se re-măsoară cu `probe_local_speed.py`; atunci local devine candidat serios.
