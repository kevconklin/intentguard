# IntentGuard × Claude Code

Guards every Claude Code tool call with IntentGuard's decision pipeline, using
Claude Code's native hook system — no gateway required.

## How it maps

| Claude Code | IntentGuard |
|---|---|
| PreToolUse hook (`tool_name`, `tool_input`) | `POST /v1/decide` |
| hook `allow` / `deny` | `Decision.allow` / `deny` (deny reason shown to the model) |
| hook `ask` → native permission prompt | `Decision.escalate` — the human-approval step, for free |
| UserPromptSubmit hook (the user's typed prompt) | `POST /v1/sessions:parse` — the trusted moment, before any untrusted tool output exists |

## Install

```bash
pip install -e .                      # from the repo root; registers `intentguard`
intentguard install --claude-code     # edits ~/.claude/settings.json (backs it up first)

# options:
#   --project           edit ./.claude/settings.json for this repo only
#   --with-prompt-hook  also provision each user prompt into session grants
#   --settings PATH     explicit settings file
#   --remove            uninstall IntentGuard's entries (leaves other hooks alone)
```

The hook entries run `python -m adapters.claude_code.hooks` with the
interpreter that performed the install, so the venv resolves correctly. The
hooks themselves are stdlib-only.

## Run the engine

```bash
export INTENTGUARD_TOOL_REGISTRY_PATH=$(pwd)/adapters/claude_code/tools-claude-code.json
uvicorn engine.api.server:app         # observe mode by default — safe rollout
```

`tools-claude-code.json` is a registry profile for Claude Code's built-in
tools (`Write → file_path`, `WebFetch → url`, …). Extend it with your
`mcp__server__tool` names; anything not listed is denied `unknown_tool`.

## Hook configuration (environment, read by the hooks)

| Variable | Default | Meaning |
|---|---|---|
| `INTENTGUARD_URL` | `http://127.0.0.1:8000` | engine base URL |
| `INTENTGUARD_CC_SUBJECT` | `user:<os user>` | decide subject |
| `INTENTGUARD_CC_FAIL_OPEN` | `false` | allow calls when the engine is unreachable (keep false) |
| `INTENTGUARD_PROVISIONING_TOKEN` | unset | bearer token for the prompt hook's write path |

## Rollout

1. Start in observe mode: everything is allowed, the ledger records what
   enforce would have denied (`GET /v1/audit`).
2. Put `Bash` in `INTENTGUARD_ESCALATABLE_TOOLS` — a shell command has no
   single resource to grant on, so every call becomes Claude Code's native
   permission prompt until pattern grants land.
3. Flip `INTENTGUARD_MODE=enforce` when the would-deny telemetry looks right.

Known limits (tracked on the production-readiness milestone): escalation
approvals aren't written back as grants, so repeated calls re-ask; grants are
exact-match, so resources discovered mid-task escalate rather than match.
