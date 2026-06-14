# Implementing IntentGuard — a developer's guide

This folder is a self-contained, runnable guide to wiring IntentGuard into your
own AI-agent application. Everything here runs with **no server, no API key, and
no LLM** — the examples spin the engine up in-process — so you can read, run, and
adapt them immediately.

```
examples/implementation/
├── intentguard_client.py   ← the only integration code you need (copy this)
├── 01_quickstart.py        ← provision + one allow + one deny
├── 02_guarded_agent_loop.py← wrap your tool dispatcher (the common case)
├── 03_observe_then_enforce ← safe rollout: observe first, then enforce
└── 04_escalation_...py     ← human-in-the-loop approval instead of hard deny
```

Run any of them:

```bash
pip install -e ".[dev]"                       # from the repo root, once
python examples/implementation/01_quickstart.py
```

---

## The one idea

IntentGuard is a **sidecar authorization service**. Your agent keeps doing what
it does; you add two calls:

```
   USER REQUEST (trusted)
        │
   (A) provision intent ──▶ POST /v1/sessions     ← do this ONCE, up front,
        │                                            BEFORE any untrusted input
   ══════ the agent now reads web pages / tool outputs (untrusted) ══════
        │
   (B) for each tool call ─▶ POST /v1/decide ─▶ allow | deny | escalate
        │                                         (deterministic, no LLM)
        ▼
   run the tool only if allowed
```

The security comes entirely from the ordering: permissions are frozen from the
**trusted** request *before* untrusted content can influence the agent. An
injection can change what the agent *wants*; it cannot change what it's *allowed*
to do.

---

## Step by step

### Step 1 — Run the engine

The engine is a standalone service your agent talks to over HTTP.

```bash
# Dev: in-memory store, observe mode (the safe default)
uvicorn engine.api.server:app                       # serves on :8000
```

For production use the OpenFGA backend so permissions persist and scale:

```bash
docker compose up -d                                 # start OpenFGA
python -m engine.pdp.bootstrap                       # create store + model
export INTENTGUARD_BACKEND=openfga
export INTENTGUARD_OPENFGA_STORE_ID=...  INTENTGUARD_OPENFGA_MODEL_ID=...
export INTENTGUARD_MODE=enforce
uvicorn engine.api.server:app
```

In your app, point the client at it:

```python
from intentguard_client import connect
async with connect(engine_url="http://localhost:8000") as guard:
    ...
```

(The examples omit `engine_url` to run the engine in-process for zero setup.)

### Step 2 — Model your tools and resources

Decide, for each tool your agent can call, **what the security-relevant
"resource" is** — the thing that distinguishes a safe call from a dangerous one.

| Tool          | Resource (what to bind)        | Example grant                         |
|---------------|--------------------------------|---------------------------------------|
| `email.send`  | the recipient                  | `email.send → bob@example.com`        |
| `file.write`  | the path                       | `file.write → /home/alice/report.txt` |
| `http.get`    | the host/URL                   | `http.get → api.github.com`           |
| `calendar.read`| none (whole tool is fine)     | `calendar.read → *`                   |

IntentGuard binds that resource into the grant identity, so `email.send → bob`
is a *different* permission from `email.send → attacker`. (In Milestone 1 the
engine knows which argument is the resource via a small built-in registry;
Milestone 2 makes this schema-driven — see the repo issues.)

### Step 3 — Provision intent on the trusted request (Touchpoint A)

When the user's request arrives — and **before** you let the agent read anything
untrusted — turn the request into a concrete allow-list and provision it:

```python
await guard.provision(
    session_id="conv-42",          # your conversation / request id
    subject="user:alice",          # who is acting
    allowed_actions=[
        {"tool": "email.send",   "resource": "bob@example.com"},
        {"tool": "calendar.read", "resource": None},   # None = any
    ],
)
```

Where does `allowed_actions` come from today?
- You build it from a consent UI, your own policy, or scope selection, **or**
- you use the deterministic mock parser, **or**
- (Milestone 2) the real LLM intent parser extracts it from the request text.

> ⚠️ **The critical discipline:** provision on the trusted turn, before untrusted
> content reaches the agent. Provisioning *after* an injection has already
> steered the agent defeats the purpose.

### Step 4 — Guard every tool call (Touchpoint B)

Wrap your tool dispatcher so a tool only runs when authorized. See
[`02_guarded_agent_loop.py`](02_guarded_agent_loop.py):

```python
from intentguard_client import IntentDenied

async def call_tool(tool, **arguments):
    try:
        await guard.enforce(session, subject, tool, arguments)  # raises on deny
    except IntentDenied as d:
        log.warning("blocked %s: %s", tool, d.verdict["reason"])
        return None
    return await TOOLS[tool](**arguments)     # only now does the tool execute
```

That wrapper is the entire integration. Your agent, LLM, and tools are unchanged.

**Using the IBM ContextForge MCP gateway?** Skip Step 4's code entirely: register
the provided plugin (`adapters/contextforge/`) and the gateway calls IntentGuard
on every `tool_pre_invoke` automatically — allow proceeds, deny blocks, escalate
prompts.

### Step 5 — Roll out: observe, then enforce

Never flip enforcement on blind. Ship in **observe** mode first: every call is
allowed, but the engine logs the decision it *would* have made and tags it to
OWASP Agentic threats. Mine the audit log for would-be denials, fix your
provisioning, then switch to **enforce** — one flag, no code change. See
[`03_observe_then_enforce.py`](03_observe_then_enforce.py).

```python
would_block = [e for e in await guard.audit() if e["would_have_decided"] == "deny"]
```

### Step 6 — Production hardening

- **Authenticate the write path.** `POST /v1/sessions` is the only way to grant
  permissions; only your trusted orchestrator should reach it. (Tracked as a
  Milestone 2 issue — do this before deploying.)
- **Keep fail-closed on.** If the store errors or times out, enforce mode denies.
  Tune `INTENTGUARD_PDP_TIMEOUT_SECONDS`; don't disable it.
- **Treat the audit log as security telemetry.** Ship `INTENTGUARD_AUDIT_PATH`
  (JSONL) to your SIEM; every deny/escalate is OWASP-tagged.

---

## Common usage patterns

| You want to… | See | Key call |
|--------------|-----|----------|
| Try the smallest integration | `01_quickstart.py` | `provision` + `decide` |
| Authorize your own agent's tool calls | `02_guarded_agent_loop.py` | `enforce` + `IntentDenied` |
| Roll out without breaking flows | `03_observe_then_enforce.py` | `mode_override`, `audit` |
| Require human approval for some tools | `04_escalation_and_human_approval.py` | `escalatable_tools`, `escalation_prompt` |

---

## API reference (the decision contract, v1)

**`POST /v1/sessions`** — provision (trusted write path)
```jsonc
{ "session_id":"conv-42", "subject":"user:alice",
  "allowed_actions":[ {"tool":"email.send","resource":"bob@example.com"} ] }
```

**`POST /v1/decide`** — authorize a tool call
```jsonc
// request
{ "session_id":"conv-42", "subject":"user:alice",
  "tool":"email.send", "arguments":{"to":"bob@example.com"},
  "resource": null, "mode_override": null }
// response
{ "decision":"allow|deny|escalate",
  "reason":"in_intent|not_in_intent|no_session|pdp_error_failclosed|escalated_for_review",
  "effective_mode":"observe|enforce",
  "would_have_decided":"...",   // observe mode only
  "escalation_prompt":"...",    // escalate only
  "decision_id":"..." }
```

**`GET /v1/audit?limit=100`** — recent append-only decisions.

### Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `INTENTGUARD_MODE` | `observe` | `observe` (allow + log) or `enforce` |
| `INTENTGUARD_PDP_TIMEOUT_SECONDS` | `2.0` | per-call store budget; fail closed past it |
| `INTENTGUARD_BACKEND` | `memory` | `memory` or `openfga` |
| `INTENTGUARD_OPENFGA_STORE_ID` / `_MODEL_ID` | — | from `bootstrap` |
| `INTENTGUARD_ESCALATABLE_TOOLS` | — | comma-separated tools that escalate instead of deny |
| `INTENTGUARD_AUDIT_PATH` | — | JSONL audit log path (in-memory if unset) |

---

## Adapting this to your project

1. Copy `intentguard_client.py` into your codebase (it only needs `httpx`).
2. Deploy the engine (Step 1) and point the client at it with `engine_url=`.
3. Call `provision()` where you handle the incoming user request.
4. Call `enforce()`/`decide()` in your tool-execution path.
5. Start in observe mode; graduate to enforce.
