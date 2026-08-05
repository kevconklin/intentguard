# Verification Plan: Prove IntentGuard Works as Documented

## Requirements Restatement

The documentation (README + `docs/architecture.md`) makes one central security
claim — *"a prompt injection can corrupt what the agent **wants** to do, but
cannot expand what it is **allowed** to do"* — backed by three properties and a
set of documented workflows. "Proof" means executing each documented claim and
capturing real output as evidence, not just pointing at green unit tests:

1. Permissions are fixed from the trusted request **before** untrusted content
   is processed
2. The LLM has **no write path** to the policy store (enforced by tests)
3. Per-call decisions are **deterministic, read-only, and fail-closed** — never
   an LLM judgment

Deliverable: a **verification report** mapping every documented claim to the
command that proved it and its captured output.

## Verification Phases

### Phase 1 — Docs-as-written quickstart (no network, no keys)

Run the README Quickstart commands *verbatim* in a clean environment:

- `pip install -e ".[dev]"` in a fresh venv (proves install docs work)
- `python examples/demo-injection/demo.py` → expect the documented
  ALLOW / DENY / observe-logged sequence and OWASP-tagged audit log, exit 0
- `pytest -q` → expect 78 passed (proves the network-free suite is genuinely
  network-free)
- CI-parity gates: `ruff check`, `ruff format --check`, `mypy`, `bandit`

### Phase 2 — Live HTTP service against the documented contract

Start `uvicorn engine.api.server:app` and exercise every documented endpoint
with `curl`, checking responses field-by-field against the README's contract
table:

- Decide with no session → `deny / no_session`
- Provision via `POST /v1/sessions` (email.send→bob, calendar.read), then:
  in-intent call → `allow / in_intent`; injected call (attacker@evil.com) →
  `deny / not_in_intent`
- Observe mode → `allow` + `would_have_decided: deny`
- Unknown tool → `deny / unknown_tool`; missing resource arg →
  `deny / missing_resource`
- Escalation: restart with `INTENTGUARD_ESCALATABLE_TOOLS=email.send` →
  `escalate` + `escalation_prompt`
- Provisioning auth: with a token configured → 401 without/with wrong bearer,
  200 with correct; strict mode without token → 503
- `GET /v1/audit` → entries correlate to `decision_id`s; with
  `INTENTGUARD_AUDIT_PATH` set, verify the on-disk JSONL is append-only

### Phase 3 — The security property itself

- **Injection cannot expand permissions**: over live HTTP, provision intent
  first, then fire injected calls with novel tools/resources — show *nothing*
  an attacker sends via arguments can produce `allow` for an ungranted
  (tool, resource)
- **LLM has no write path**: run `tests/test_architecture_invariants.py`
  verbosely and show what it enforces (import-graph proof that `decide()`
  cannot reach the writer or any parser, engine imports no gateway code)
- **Fail-closed**: point the engine at an unreachable OpenFGA backend in
  enforce mode → `deny / pdp_error_failclosed` (not an error 500, not an allow)

### Phase 4 — Real OpenFGA backend (documented production path)

- `docker compose up -d` → `python -m engine.pdp.bootstrap` → export ids,
  `INTENTGUARD_BACKEND=openfga`, `INTENTGUARD_MODE=enforce`
- Repeat the Phase-2 allow/deny/no-session checks against the real store
- Run the opt-in integration test
  (`INTENTGUARD_TEST_OPENFGA_URL=... pytest tests/test_openfga_integration.py`)
- This also validates the long-lived-client change from PR #19 against a real
  server
- **Requires:** Docker running locally

### Phase 5 (optional) — Live Anthropic intent parser

- `INTENTGUARD_INTENT_PARSER=anthropic` + `ANTHROPIC_API_KEY`, then
  `POST /v1/sessions:parse` with the README's example sentence → show
  extracted actions are allowlist-validated and provisioned; a
  hallucinated/unknown tool gets dropped
- **Requires:** your API key; costs a few LLM calls. Skippable — the mock
  parser path is already proven in Phases 1–2.

### Phase 6 — Proof report

Compile `docs/verification-report.md` (or scratchpad-only, your call): a table
of *documented claim → command → observed output → PASS/FAIL*, and send it to
you.

## Dependencies

- Phases 1–3: nothing external (matches the README's "no network, no API key"
  claim)
- Phase 4: Docker
- Phase 5: `ANTHROPIC_API_KEY`

## Risks

| Level | Risk | Mitigation |
|---|---|---|
| MEDIUM | Ports 8000/8080 already in use locally | pick alternate ports |
| MEDIUM | Docker not running | Phase 4 blocked; CI's `openfga-integration` job is fallback evidence |
| LOW | Live LLM output is non-deterministic | Phase 5 asserts on validation behavior, not exact extractions |
| LOW | Background uvicorn processes | managed and killed cleanly |

## Estimated Complexity: LOW-MEDIUM

- Phases 1–3: ~10 min of execution
- Phase 4: ~5 min (if Docker is up)
- Phase 5: ~5 min

## Open Decisions

1. Include **Phase 4 (OpenFGA/Docker)** — is Docker available on this machine?
2. Include **Phase 5 (live Anthropic parser)** — provide `ANTHROPIC_API_KEY`,
   or skip?
