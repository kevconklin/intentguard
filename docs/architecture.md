# IntentGuard Architecture

## Problem

An AI agent receives a trusted instruction from a user, then processes untrusted
content (tool outputs, web pages, emails) to carry it out. A prompt injection
hidden in that untrusted content can hijack the agent into taking actions the
user never asked for — exfiltrating data, sending mail to an attacker, deleting
files. The agent's *intent* is corruptible because it is derived from untrusted
input at run time.

## Security property

IntentGuard separates **what the agent wants to do** from **what it is allowed
to do**:

- Permissions are derived from the user's **trusted request** and written to the
  policy store **before** any untrusted content is processed.
- The language model has **no write path** to the policy store.
- Per-call authorization is a **deterministic binary check** against concrete
  stored permissions — never an LLM judging whether a call "looks reasonable".

So a prompt injection can corrupt what the agent *wants* to do, but cannot
expand what it is *allowed* to do.

## Trusted vs untrusted timeline

```
   TRUSTED PATH (once per request, before tools run)
   ─────────────────────────────────────────────────────────────
   user request ─▶ intent parser (LLM) ─▶ ParsedIntent (inert data)
                                               │
                                               ▼  provision_session()  ◀── ONLY writer
                                         ┌─────────────┐
                                         │ policy store │  (OpenFGA)
                                         └─────────────┘
   ═══════════════════════ time barrier ═══════════════════════════
   UNTRUSTED PATH (per tool call, after untrusted content is in play)
   ─────────────────────────────────────────────────────────────
   agent tool call ─▶ adapter ─▶ decide()  ──read-only──▶ policy store
                                    │  (pure, deterministic, no LLM)
                                    ▼
                          allow / deny / escalate ─▶ append-only audit log
```

The LLM appears only on the trusted path. The decision path is pure and
read-only.

## Components

```
                         ┌──────────────────────────────────────────┐
   MCP Gateway           │                ENGINE (gateway-agnostic)  │
  (ContextForge)         │                                           │
  ┌───────────┐  HTTP    │  engine/api      FastAPI  /v1/decide      │
  │  tool_pre │ ───────▶ │                            /v1/sessions   │
  │  _invoke  │          │  engine/core     pure decide() function   │
  └───────────┘          │  engine/pdp      PolicyStore (read-only)  │
        ▲                │                  PolicyWriter (write-only)│
        │ adapter        │                  OpenFGA model + client   │
  ┌───────────┐          │  engine/intent   parser iface + mock/real │
  │ adapters/ │          │                  provision (trusted write)│
  │contextforge          │  engine/audit    append-only + OWASP tags │
  └───────────┘          │  engine/schema   versioned decide contract│
                         └──────────────────────────────────────────┘
```

- **`engine/` is gateway-agnostic.** It imports nothing from `adapters/` and no
  MCP/ContextForge library. Enforced by `tests/test_architecture_invariants.py`.
- **`adapters/contextforge/`** is a native `cpex.framework.Plugin` (the
  ContextForge plugin framework, extracted into the `cpex` package). On
  `tool_pre_invoke` it builds a decide request, calls the engine over HTTP, and
  maps the verdict: allow → proceed, deny → block, escalate → human prompt.

## The authorization model

A permission is keyed on the combination **(session, tool, resource)**. The
engine extracts the security-relevant argument (e.g. an email recipient) and
binds it into a deterministic `grant` object id, so:

- `email.send` → `bob@example.com` is a *different* grant from
- `email.send` → `attacker@evil.com`, and from
- `email.send` → `*` (any recipient).

OpenFGA model (`engine/pdp/model.fga`):

```
type session
  relations
    define principal: [user, agent]

type grant
  relations
    define session: [session]
    define grantee: [user, agent]
    define can_invoke: grantee
```

- `session_exists` → check `principal` on `session:<id>` (distinguishes
  *no session* from *not in intent*).
- `check_grant` → check `can_invoke` on `grant:<hash(session,tool,resource)>`.

## Read/write separation (defense in depth)

| Surface        | Type                 | Methods            | Reachable from |
|----------------|----------------------|--------------------|----------------|
| `PolicyStore`  | read-only protocol   | `session_exists`, `check_grant` | decide() path |
| `PolicyWriter` | write-only protocol  | `write_grants`     | `provision_session` only |

`engine/core.py` (the decision path) statically cannot reach `PolicyWriter`,
`provision`, the OpenFGA write backend, or any parser. This is verified by a
static import-graph test, not just by convention.

## Modes & failure

- **`observe`** (default): always returns `allow`, records the decision it
  *would* have made. Safe to roll out in production traffic.
- **`enforce`**: returns the real decision.
- **Fail closed**: if the store errors or exceeds `pdp_timeout_seconds`, enforce
  mode returns `deny` with reason `pdp_error_failclosed`. The timeout is
  configurable; the secure default is fail-closed.

A single config flag (`INTENTGUARD_MODE`) or per-request `mode_override` selects
the mode.
