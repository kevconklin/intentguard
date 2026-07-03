# IntentGuard Verification Report

**Date:** 2026-07-03
**Commit / branch:** `refactor/simplify-cleanups` (PR #19)
**Environment:** Python 3.11.12, macOS. Docker unavailable locally (Phase 4 used
CI evidence). No `ANTHROPIC_API_KEY` locally (Phase 5 used the network-free
validation path).

## Verdict

**PASS.** Every claim in the README and `docs/architecture.md` was exercised
with real output. The central security property —

> *A prompt injection can corrupt what the agent wants to do, but cannot expand
> what it is allowed to do.*

— holds: no value placed in a tool call's `arguments` produced an `allow` for a
(tool, resource) that was not provisioned from the trusted request.

---

## Phase 1 — Docs-as-written quickstart (no network, no keys)

| Claim (README) | Command | Result |
|---|---|---|
| Injection demo runs end to end | `python examples/demo-injection/demo.py` | **PASS** — exit 0; ALLOW(bob), DENY(attacker, enforce), ALLOW+would-deny(observe), OWASP-tagged audit |
| Test suite passes | `pytest -q` | **PASS** — 78 passed, 2 skipped (the 2 skips are the opt-in OpenFGA + live-Anthropic tests) |
| Lint gate | `ruff check engine adapters tests` | **PASS** |
| Format gate | `ruff format --check` | **PASS** |
| Type gate | `mypy` | **PASS** — no issues in 25 source files |
| Security gate | `bandit -c pyproject.toml -r engine adapters` | **PASS** — no issues |

## Phase 2 — Live HTTP service against the documented contract

Service: `uvicorn engine.api.server:app`, in-memory backend, enforce mode.
`/healthz` → `{"status":"ok","mode":"enforce","backend":"memory"}`.

| # | Case | Expected | Observed | |
|---|---|---|---|---|
| 2.1 | decide, no session | `deny / no_session` | `deny / no_session` | ✅ |
| 2.2 | provision (email.send→bob, calendar.read) | `grants_written: 2` | `grants_written: 2` | ✅ |
| 2.3 | in-intent email.send→bob | `allow / in_intent` | `allow / in_intent` | ✅ |
| 2.4 | injected email.send→attacker | `deny / not_in_intent` | `deny / not_in_intent` | ✅ |
| 2.5 | injected + `mode_override=observe` | `allow` + `would_have_decided: deny` | exactly that | ✅ |
| 2.6 | unknown tool `file.delete` | `deny / unknown_tool` | `deny / unknown_tool` | ✅ |
| 2.7 | known tool, missing resource arg | `deny / missing_resource` | `deny / missing_resource` | ✅ |
| 2.8 | `GET /v1/audit` | entries correlate by `decision_id` | 7 entries, IDs match, OWASP tags present | ✅ |
| 2.9 | on-disk `INTENTGUARD_AUDIT_PATH` | append-only JSONL | 7 lines, all valid JSON, append order preserved | ✅ |
| 2.10 | escalation (`INTENTGUARD_ESCALATABLE_TOOLS=email.send`) | `escalate` + `escalation_prompt` | exactly that, with human prompt | ✅ |
| 2.11 | provisioning auth: no / wrong / right token | 401 / 401 / 200 | 401 / 401 / 200 | ✅ |
| 2.12 | read path not gated by token | 200 | 200 | ✅ |
| 2.13 | strict auth, no token configured | 503 (fail closed) | 503 | ✅ |

## Phase 3 — The security property itself

**3.1 Injection cannot expand permissions.** Provisioned only
`email.send → bob@example.com`, then attacked over live HTTP:

| Attack via `arguments` | Result |
|---|---|
| granted call (control) | `allow / in_intent` ✅ expected |
| attacker recipient | `deny / not_in_intent` |
| list smuggling `[bob, attacker]` | `deny / not_in_intent` |
| case/space variant `" BOB@Example.com "` | `allow / in_intent` — **correct**: normalizes to the same granted recipient |
| known-but-ungranted tool `calendar.read` | `deny / not_in_intent` |
| unknown tool `file.delete` | `deny / unknown_tool` |
| forged subject `user:mallory` | `deny / no_session` |
| forged session `s-forged` | `deny / no_session` |

No attacker-controlled `arguments` value expanded permissions. **PASS.**

> **Integrator caveat (not a defect):** the optional top-level `resource` field
> is an explicit override that "always wins" by design (documented in the
> contract). In the intended deployment the *trusted adapter* sets it from the
> real tool call. An integrator who pipes attacker-controlled data into
> `resource` (rather than into `arguments`) would bypass binding — so treat
> `resource` as trusted-caller input only.

**3.2 The LLM has no write path.** `pytest tests/test_architecture_invariants.py`
— all 5 pass:
- `test_engine_does_not_import_adapters_or_mcp`
- `test_decide_path_cannot_reach_writer_or_llm`
- `test_only_provisioning_imports_the_writer`
- `test_read_only_store_has_no_write_methods`
- `test_parser_output_is_inert_data`

**3.3 Fail-closed (live).** Backend `openfga` in enforce mode pointed at an
unreachable server (`http://127.0.0.1:9999`, 1s timeout) →
`deny / pdp_error_failclosed` (HTTP 200 — a clean deny, not a 500, not an allow).
**PASS.**

## Phase 4 — Real OpenFGA backend

Docker unavailable locally. Evidence gathered instead:

- **4a** OpenFGA store/writer construct and defer client creation (lazy client
  per PR #19) — verified in-process; `AUTHORIZATION_MODEL` types
  `[user, agent, session, grant]`.
- **4b** Opt-in `tests/test_openfga_integration.py` correctly **SKIPS** without a
  server (keeps the default suite network-free).
- **4c — authoritative:** CI job **`openfga-integration` PASSED** on PR #19
  against a real `openfga/openfga:latest` server (24s), exercising the real
  read/write/decide path *and* the PR's long-lived-client change.

CI on PR #19 — all green: `openfga-integration`, `quality`, `test (3.11)`,
`test (3.12)`, `claude-review`, GitGuardian.

## Phase 5 — Anthropic intent parser

No local key. Security-relevant behavior proven network-free:

- `pytest tests/test_anthropic_parser.py` — 6 passed, 1 skipped (live call).
- Direct demo: an extractor (standing in for a jailbroken LLM) that emits
  `shell.exec` and `file.delete` alongside `email.send` → both un-allowlisted
  tools **dropped** (`tool_not_in_allowlist`); only `email.send` survives.
  Invariant held: parser output contains only allowlisted tools.
- The live end-to-end path (`INTENTGUARD_INTENT_PARSER=anthropic` +
  `/v1/sessions:parse`) is unverified locally for want of a key; it is covered
  by the skipped `test_live_anthropic_extraction` when `ANTHROPIC_API_KEY` +
  `INTENTGUARD_TEST_ANTHROPIC` are set.

## Gaps / not covered

- Live OpenFGA run on *this* machine (no Docker) — covered by CI instead.
- Live Anthropic API extraction locally (no key) — validation logic covered
  network-free; live call covered by the opt-in test.
- Live end-to-end run behind a real ContextForge gateway — the README lists this
  under "Next", so it is out of scope for current claims.
