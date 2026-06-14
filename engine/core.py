"""The per-call decision engine core.

This is the security-critical hot path. Properties enforced here and by tests:

* Pure & deterministic: a binary check against concrete stored permissions.
  No LLM is consulted on this path (this module imports no parser, no LLM SDK).
* Gateway-agnostic: no adapter / MCP imports.
* Read-only: depends only on the read-only ``PolicyStore``; there is no write
  path reachable from a decision.
* Fail closed: if the store errors or times out, in enforce mode the decision
  is deny, with a clear reason. The timeout is configurable; the default is
  fail-closed.
* observe/enforce: a single config flag (or per-request override) flips between
  always-allow-and-log and real decisions.
"""

from __future__ import annotations

import asyncio
import uuid

from engine.audit import AuditLogger, threats_for_reason
from engine.config import EngineConfig
from engine.pdp.model import bind_resource, grant_key, grant_object
from engine.pdp.store import PolicyStore
from engine.schema import Decision, DecideRequest, DecideResponse, Mode, Reason


async def _evaluate_enforce(
    request: DecideRequest,
    store: PolicyStore,
    config: EngineConfig,
    grant_object_id: str,
    resource_complete: bool,
) -> tuple[Decision, Reason, str | None]:
    """Compute the decision enforce mode would make. Fails closed on error."""
    # Allowlist gate: a tool not in the registry is unknown and denied before any
    # store lookup. This is deterministic and needs no session.
    if config.enforce_tool_allowlist and not config.tool_registry.is_known(request.tool):
        return Decision.deny, Reason.unknown_tool, None

    # Fail closed when a required resource argument is missing: we cannot bind the
    # call to a specific grant, so we do not let it match an "any" grant.
    if not resource_complete:
        return Decision.deny, Reason.missing_resource, None

    try:
        exists = await asyncio.wait_for(
            store.session_exists(request.session_id, request.subject),
            timeout=config.pdp_timeout_seconds,
        )
        if not exists:
            return Decision.deny, Reason.no_session, None

        allowed = await asyncio.wait_for(
            store.check_grant(request.subject, grant_object_id),
            timeout=config.pdp_timeout_seconds,
        )
    except asyncio.TimeoutError:
        return Decision.deny, Reason.pdp_error_failclosed, "pdp_timeout"
    except Exception as exc:  # noqa: BLE001 - fail closed on any store error
        return Decision.deny, Reason.pdp_error_failclosed, f"pdp_error:{type(exc).__name__}"

    if allowed:
        return Decision.allow, Reason.in_intent, None

    # Provisioned session, but this action was not granted. Either escalate to a
    # human (configured tools) or hard-deny.
    if request.tool in config.escalatable_tools:
        return Decision.escalate, Reason.escalated_for_review, None
    return Decision.deny, Reason.not_in_intent, None


async def decide(
    request: DecideRequest,
    store: PolicyStore,
    config: EngineConfig,
    audit: AuditLogger,
) -> DecideResponse:
    """Authorize a single tool call against stored intent. Pure decision path."""
    effective_mode = request.mode_override or config.mode

    binding = bind_resource(
        request.tool, request.arguments, request.resource, config.tool_registry
    )
    resource = binding.resource
    grant_object_id = grant_object(request.session_id, request.tool, resource)

    enforce_decision, reason, error = await _evaluate_enforce(
        request, store, config, grant_object_id, binding.complete
    )

    # observe mode always returns allow, but records what it would have done.
    if effective_mode == Mode.observe:
        decision = Decision.allow
        would_have = enforce_decision
    else:
        decision = enforce_decision
        would_have = None

    escalation_prompt = None
    if decision == Decision.escalate:
        escalation_prompt = (
            f"Agent requested '{request.tool}' on '{resource}', which is outside "
            f"the authorized intent for this session. Approve this action?"
        )

    decision_id = uuid.uuid4().hex
    audit.record(
        decision_id=decision_id,
        session_id=request.session_id,
        subject=request.subject,
        tool=request.tool,
        resource=resource,
        grant_key=grant_key(request.session_id, request.tool, resource),
        decision=decision.value,
        reason=reason.value,
        effective_mode=effective_mode.value,
        would_have_decided=would_have.value if would_have else None,
        owasp_threats=threats_for_reason(reason),
        error=error,
    )

    return DecideResponse(
        decision=decision,
        reason=reason,
        effective_mode=effective_mode,
        would_have_decided=would_have,
        escalation_prompt=escalation_prompt,
        decision_id=decision_id,
    )
