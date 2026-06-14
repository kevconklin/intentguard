# Security Policy

IntentGuard is a security tool. Its job is to bound what an AI agent can do to
the user's trusted intent. Bugs here are security bugs.

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Open a GitHub Security
Advisory ("Report a vulnerability") on this repository, or email the maintainers
listed in `pyproject.toml`. Do **not** open a public issue for an unfixed
vulnerability.

We aim to acknowledge reports within 3 business days and to provide a
remediation timeline after triage.

## Threat model

**In scope — what IntentGuard defends against:**

- **Prompt injection via untrusted content.** A tool output, web page, or email
  that hijacks the agent into actions outside the user's request. IntentGuard
  fixes permissions from the trusted request *before* untrusted content is
  processed, so injection cannot expand authorization.
- **Excessive agency / tool misuse.** An agent attempting tools or resources the
  user never authorized for this session.
- **Authorization tampering by the model.** The LLM cannot write to the policy
  store; there is no write path reachable from the decision path or from parsed
  untrusted content. This is enforced by automated tests.

**Out of scope (for this component):**

- Authenticating the *user* or the *agent* identity (the gateway / IdP owns
  this; IntentGuard trusts the `subject` it is given).
- Confidentiality of tool *outputs* (IntentGuard authorizes *calls*, not data
  flowing back).
- Correctness of the upstream intent parser's extraction (a Milestone 2 concern;
  a wrong grant is a wrong permission — keep the parser on the trusted path and
  the allowlist tight).

## Security invariants (enforced by tests)

These are treated as the spec and verified in `tests/`:

1. **Gateway-agnostic core.** `engine/` imports nothing from `adapters/` and no
   MCP library. (`test_architecture_invariants.py`)
2. **Deterministic decision path.** `decide()` performs a binary check against
   the policy store and consults no LLM. (`test_architecture_invariants.py`,
   `test_decide_contract.py`)
3. **No write path from the LLM / untrusted input.** Only the trusted
   `provision_session` imports the writer; the decision path statically cannot
   reach it. (`test_architecture_invariants.py`)
4. **Fail closed.** Store errors or timeouts deny in enforce mode.
   (`test_decide_contract.py`)
5. **Observe vs enforce is one flag.** Default `observe` always allows and logs;
   `enforce` returns real decisions. (`test_injection_scenario.py`)

## Operational guidance

- Run in `observe` mode first to measure would-be denials, then switch to
  `enforce`.
- Keep `INTENTGUARD_PDP_TIMEOUT_SECONDS` low enough that a stalled store fails
  closed quickly; never disable fail-closed in production.
- Treat the append-only audit log as security telemetry: every deny/escalate is
  tagged with OWASP Agentic Top 10 categories.
- The ContextForge adapter fails closed (blocks) if the engine is unreachable;
  keep `fail_open: false`.

## Supported versions

This is pre-1.0 software (Milestone 1). Security fixes are applied to `main`.
