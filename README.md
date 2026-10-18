# RoboZero

A lightweight [Agent-Zero](https://github.com/agent0ai/agent-zero)-inspired
autonomous agent: a FastAPI backend + a framework-free webUI, chat-based,
with tool-calling, per-project memory, and a knowledge base. Single file
each for most concerns — no heavy agent framework underneath.

## Features

- **Chat with tool-calling** — native function-calling against an
  OpenAI-compatible endpoint (DeepSeek by default; the model catalog is
  swappable per chat and per project).
- **Projects** — each project has its own system prompt, its own model
  override, and three layers of memory:
  - a **fact sheet** (`project_facts`): short facts extracted automatically
    from conversations, with provenance (which chat, which date) — never
    deleted, only superseded;
  - a rolling **chat digest** per conversation;
  - a full-text **archive** (SQLite FTS5) the agent can query with the
    `project_search` tool.
- **Learned rules** — corrections you give the agent get proposed as
  standing rules on the project card; nothing enters the agent's context
  until you approve it from `/projects`.
- **Knowledge base** — upload text/PDF/DOCX/image files per project;
  extracted and made searchable.
- **Linked folders** — give a project direct read-only or read-write access
  to a folder on disk, optionally indexed for `project_search`.
- **Tools**: shell execution, filesystem read/write/list, web page reading,
  a real browser (Playwright), image generation, a sub-agent delegate
  (`call_subordinate`), and project-scoped memory/search tools.
- **Usage tracking** — per-model call count, tokens, latency and cost,
  filterable by date, stored in SQLite (not just a flat log).
- **Session token counter, color-coded** — the sidebar shows the running
  token count for the current chat against its model's context window,
  colored by how full that window is: green under 50%, blue 50-70%,
  amber 70-90%, red above 90%.
- **DeepSeek rate indicator** — a small dot next to the token counter
  shows whether DeepSeek's current off-peak discount applies: green for
  discount (~half price), red for peak hour (no discount). Peak is
  01:00-04:00 and 06:00-10:00 UTC, Monday-Friday; the same rule is used
  server-side (`cost.py`) to price completed calls, and client-side
  (`app.js`) just to display the live state.
- **Task-ready notifications** — Telegram and/or SMS when a run finishes
  while you're not watching the tab (see below).

## Running it

```
python3 -m venv .venv
.venv/bin/pip install fastapi "uvicorn[standard]" httpx python-dotenv PyYAML playwright
.venv/bin/playwright install chromium
cp .env.example .env   # set your LLM endpoint + API key
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8150
```

Config lives in `conf/settings.yaml` (workdir, model endpoint) and
`conf/user_settings.json` (runtime toggles, written by `/settings`).

## Notifications: Telegram and SMS

A run can take a while (browsing, multi-step tool use). `notifications.py`
fires a best-effort, fire-and-forget "Task ready — <chat title>" message
when a run ends (`final`/`error`/`cancelled`), so you don't have to keep a
tab open and watch the typing indicator. Both channels are off by default;
toggle them per-install from `/settings` (`notify_telegram`,
`notify_sms` in `conf/user_settings.json`).

`notifications.py` itself doesn't talk to Telegram or an SMS carrier
directly — it shells out to two small external scripts (one per channel)
and doesn't care how they're implemented, as long as they take the message
as `argv[1]` and exit 0 on success. That boundary is deliberate: it keeps
robo-zero's own code free of any one person's bot tokens or phone-gateway
specifics, so the two notes below are less "fill in this .env var" and
more "here is the shape of the solution we use ourselves."

### Telegram — the solution we use

Telegram's Bot API is the obvious fit: free, push-based, no polling needed
on your side, and a message to a bot you control reaches you instantly.

1. Message [@BotFather](https://t.me/BotFather), `/newbot`, get a bot
   token.
2. Message your new bot once (anything), then call
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to read back your
   numeric `chat_id`.
3. A ~10-line script that POSTs
   `https://api.telegram.org/bot<TOKEN>/sendMessage` with
   `{"chat_id": <id>, "text": message}` is the entire integration.
4. Point `_TELEGRAM_SCRIPT` in `notifications.py` at that script (it's
   invoked as `python3 <script> notify "<message>"`).

We run one such bot already wired for other purposes (approvals, alerts),
so our own deployment reuses that script rather than shipping a second
bot — but the mechanism above is the whole trick; no library is needed
for anything this simple.

### SMS — the solution we use

Commercial SMS APIs (Twilio and similar) charge per message and want a
registered sender. For a single personal "ping me" channel that's a lot
of setup for something a spare phone already does for free. Our solution:
turn an old Android phone into a tiny self-hosted SMS gateway.

**Architecture:**

```
robo-zero  --(private network, e.g. ZeroTier/Tailscale/WireGuard)-->
    spare Android phone running Termux
        -> a small stdlib-only HTTP server (no FastAPI: pydantic-core has
           no prebuilt wheel for aarch64-linux-android) exposing
           POST /sms {number, text}  and  GET /health
        -> calls termux-sms-send (from the Termux:API add-on) to actually
           send the text
```

The server-side notify script does a bearer-token POST to the phone's
`/sms` endpoint over the private network, after a quick `/health` check
(and one retry if the Termux service is asleep). The token lives only on
the phone and is never copied elsewhere.

**Gotchas worth knowing before you try this:**

- Use **Termux from F-Droid**, not the Google Play listing — the Play
  version deliberately disables the Termux:API SMS functions and shows a
  hardcoded "not yet available" message. Termux and the Termux:API add-on
  must be the same signing origin (both F-Droid, or both matching
  side-loaded builds).
- Grant Termux:API the Android **"Display over other apps"** permission.
  Without it, an SMS send triggered by an incoming HTTP request (as
  opposed to you typing in Termux yourself) silently never fires — the
  app needs it to pop background activity.
- Disable battery optimization for Termux:API, or the whole thing goes
  dead after the screen's been off for a while and every call times out.
- Put the phone on the same private network as the server (we use
  ZeroTier) rather than exposing the gateway to the public internet —
  it holds a real SMS-send capability behind a bearer token, and that's
  not something to leave reachable from anywhere.

None of the above is robo-zero-specific — it's a general recipe for
"any self-hosted project that wants one more free, no-API-key
notification channel," and it's the one we'd point at before reaching
for a paid SMS API.
