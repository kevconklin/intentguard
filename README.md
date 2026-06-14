# IntentGuard

**An intent-authorization engine for AI agents.** It decides, for each tool call
an agent attempts, whether that action is within what the user actually asked
for — and it fixes those permissions from the user's *trusted* request *before*
any untrusted content (tool outputs, web pages, emails) is ever processed.

IntentGuard is **not** a gateway. It is a standalone, gateway-agnostic service
that plugs in behind existing MCP gateways through thin adapters.

## The security property

> A prompt injection can corrupt what the agent *wants* to do, but cannot expand
> what it is *allowed* to do.

Three things make that true:

1. **Permissions are derived from the trusted request, before untrusted input.**
   The user's intent is parsed once, up front, and written to the policy store
   before any tool runs.
2. **The LLM has no write path to the policy store.** The parser produces inert
   data; a separate trusted path does the writing. (Enforced by tests, not just
   convention.)
3. **Per-call decisions are deterministic.** A binary check against concrete
   stored permissions — never an LLM judging whether a call "looks reasonable".

## Architecture

```
   TRUSTED (once, before tools run)          UNTRUSTED (per tool call)
   ────────────────────────────────          ─────────────────────────
   user request                              agent tool call
        │                                          │
        ▼                                          ▼
   intent parser (LLM)                        MCP gateway (ContextForge)
        │  ParsedIntent (data)                     │ tool_pre_invoke
        ▼                                          ▼
   provision_session() ──┐                    adapter ──HTTP──▶ /v1/decide
        (ONLY writer)    │                                         │
                         ▼                                read-only │ (pure,
                   ┌───────────┐  ◀───────────────────────────────┘  no LLM,
                   │  OpenFGA   │      check (session,tool,resource)   fail-closed)
                   └───────────┘                                       │
                                                          allow / deny / escalate
                                                                  │
                                                          append-only audit log
                                                          (OWASP Agentic tagged)
```

The engine core is gateway-agnostic; all MCP/ContextForge code lives in
`adapters/`. See [docs/architecture.md](docs/architecture.md) for detail.

## Quickstart

No network, no API key, no OpenFGA required for the demo and tests — they use a
deterministic mock parser and an in-memory policy store.

```bash
# 1. Install (Python 3.11+)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Run the prompt-injection demo end to end
python examples/demo-injection/demo.py

# 3. Run the test suite
pytest -q
```

The demo seeds a session whose intent allows `email.send → bob@example.com` and
`calendar.read`, then shows:

- `email.send → bob@example.com` → **ALLOW** (in intent)
- `email.send → attacker@evil.com` (a simulated injection) → **DENY** in enforce
- the same call in **observe** mode → **ALLOW**, logged as would-have-denied
- the append-only audit log, tagged to OWASP Agentic Top 10 threats

## Run the service

```bash
# In-memory backend (default), observe mode:
uvicorn engine.api.server:app

# Decide on a tool call:
curl -s localhost:8000/v1/decide -H 'content-type: application/json' -d '{
  "session_id":"s1","subject":"user:alice",
  "tool":"email.send","arguments":{"to":"attacker@evil.com"}
}'
```

### With the real OpenFGA policy store

```bash
docker compose up -d                         # starts OpenFGA locally
python -m engine.pdp.bootstrap               # creates store + writes the model
export INTENTGUARD_BACKEND=openfga
export INTENTGUARD_OPENFGA_STORE_ID=...       # printed by bootstrap
export INTENTGUARD_OPENFGA_MODEL_ID=...
export INTENTGUARD_MODE=enforce
uvicorn engine.api.server:app
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTENTGUARD_MODE` | `observe` | `observe` (always allow + log) or `enforce` |
| `INTENTGUARD_PDP_TIMEOUT_SECONDS` | `2.0` | per-call store budget; fail closed past it |
| `INTENTGUARD_BACKEND` | `memory` | `memory` or `openfga` |
| `INTENTGUARD_OPENFGA_API_URL` | `http://localhost:8080` | OpenFGA HTTP API |
| `INTENTGUARD_OPENFGA_STORE_ID` / `_MODEL_ID` | — | from `bootstrap` |
| `INTENTGUARD_ESCALATABLE_TOOLS` | — | comma-separated tools that escalate instead of deny |
| `INTENTGUARD_AUDIT_PATH` | — | JSONL audit log path (in-memory if unset) |
| `INTENTGUARD_TOOL_REGISTRY_PATH` | bundled `tools.json` | known-tools allowlist + per-tool resource binding |
| `INTENTGUARD_ENFORCE_TOOL_ALLOWLIST` | `true` | deny tools not in the registry (`unknown_tool`) |
| `INTENTGUARD_INTENT_PARSER` | `mock` | parser for `/v1/sessions:parse` — `mock` or `anthropic` |
| `INTENTGUARD_ANTHROPIC_MODEL` | `claude-sonnet-4-6` | model for the Anthropic intent parser |
| `INTENTGUARD_PROVISIONING_TOKEN` | — | Bearer token required to call the provisioning (write) endpoints |
| `INTENTGUARD_REQUIRE_PROVISIONING_AUTH` | `false` | fail closed (refuse writes) if no provisioning token is set |

## The decision contract (v1)

`POST /v1/decide`

```jsonc
// request
{ "schema_version":"1", "session_id":"s1", "subject":"user:alice",
  "tool":"email.send", "arguments":{"to":"bob@example.com"},
  "resource": null, "mode_override": null }

// response
{ "schema_version":"1", "decision":"allow|deny|escalate",
  "reason":"in_intent|not_in_intent|no_session|unknown_tool|missing_resource|pdp_error_failclosed|escalated_for_review",
  "effective_mode":"observe|enforce",
  "would_have_decided":"...",      // present in observe mode
  "escalation_prompt":"...",       // present only when decision is escalate
  "decision_id":"..." }            // correlates to the audit log entry
```

### Provisioning (the trusted write path)

Two ways to seed a session's permissions, **before** any tool runs. Both require
`Authorization: Bearer $INTENTGUARD_PROVISIONING_TOKEN` when a token is configured.

```bash
# Explicit allow-list (config / your own logic):
curl -s localhost:8000/v1/sessions -H 'content-type: application/json' \
  -H "authorization: Bearer $TOKEN" -d '{
  "session_id":"s1","subject":"user:alice",
  "allowed_actions":[{"tool":"email.send","resource":"bob@example.com"}]}'

# Parse a natural-language request with the LLM, then provision (parser=anthropic):
curl -s localhost:8000/v1/sessions:parse -H 'content-type: application/json' \
  -H "authorization: Bearer $TOKEN" -d '{
  "session_id":"s1","subject":"user:alice",
  "request_text":"Email the notes to bob@example.com and check my calendar."}'
```

`/v1/sessions:parse` runs the configured intent parser, validates every extracted
`(tool, resource)` against the tool-registry allowlist (dropping anything not
known), and provisions the result. A parser failure provisions nothing (502).

## ContextForge integration

`adapters/contextforge/` is a native plugin for the IBM ContextForge MCP Gateway
plugin framework (the `cpex` package). On `tool_pre_invoke` it calls this
engine's `/v1/decide` and acts on the verdict. Register it with
[`gateway-plugins.yaml`](adapters/contextforge/gateway-plugins.yaml) and host it
with [`external-server.yaml`](adapters/contextforge/external-server.yaml).

## Project layout

```
engine/
  api/      FastAPI app: /v1/decide, /v1/sessions(:parse), /v1/audit + auth
  core.py   the pure, deterministic decide() function
  pdp/      PolicyStore (read) + PolicyWriter (write) + registry + OpenFGA model
  intent/   parser interface + deterministic mock + Anthropic parser + provisioning
  audit/    append-only logger + OWASP Agentic Top 10 tagging
  schema/   the versioned decide request/response models
adapters/contextforge/   the tool_pre_invoke plugin/adapter
examples/                demo-injection, implementation guide, browser demo-ui
tests/                   contract, injection, registry, binding, parser, auth tests
```

## Adding a new tool

A tool the agent can call must be in the registry to be authorizable. Add an
entry to `engine/pdp/tools.json` (or your own file via `INTENTGUARD_TOOL_REGISTRY_PATH`):

```jsonc
{ "name": "slack.post", "description": "Post to a Slack channel",
  "resource_args": ["channel"] }   // the security-relevant argument(s)
```

- `resource_args: []` (or omit) → the whole tool is bound to "any resource".
- One key → that argument is the resource (e.g. `["to"]` for `email.send`).
- Several keys → a **compound** resource bound from all of them, in order; a call
  missing any one is denied (`missing_resource`, fail closed).
- A tool **not** in the registry is denied (`unknown_tool`) — the allowlist is the
  outer gate. Disable with `INTENTGUARD_ENFORCE_TOOL_ALLOWLIST=false`.

## Status

**Milestone 2 (current):** real Anthropic intent parser behind a provider-agnostic
interface with allowlist validation; a known-tools registry; schema-driven and
compound argument binding; a parse-and-provision endpoint; an authenticated
provisioning write path; and OpenFGA integration tests in CI. The deterministic
mock parser still drives the demo and the network-free test suite.

**Next:** richer tool schemas (argument-level validation), session lifecycle
(TTL / revocation), and a live end-to-end run against a real ContextForge gateway.

## License

[Apache-2.0](LICENSE). Security policy: [SECURITY.md](SECURITY.md).
