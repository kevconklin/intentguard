"""Pure mapping from an engine decide() response to a ContextForge hook result.

Kept separate from the plugin (which does I/O) so the verdict logic is unit
tested with no gateway and no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from adapters.contextforge._cpex import (
    PluginViolation,
    ToolPreInvokePayload,
    ToolPreInvokeResult,
)


@dataclass(frozen=True)
class EngineVerdict:
    """The fields of an engine DecideResponse the adapter acts on."""

    decision: str  # allow | deny | escalate
    reason: str
    decision_id: str
    escalation_prompt: Optional[str] = None


def verdict_to_result(
    verdict: EngineVerdict, payload: ToolPreInvokePayload
) -> ToolPreInvokeResult:
    """Translate an engine verdict into a tool_pre_invoke result.

    * allow    -> continue_processing=True (the call proceeds)
    * deny     -> continue_processing=False + violation (the call is blocked)
    * escalate -> continue_processing=False + violation carrying the human
                  escalation prompt (the gateway/UI prompts the user)
    """
    meta = {
        "intentguard_decision_id": verdict.decision_id,
        "intentguard_reason": verdict.reason,
    }

    if verdict.decision == "allow":
        return ToolPreInvokeResult(continue_processing=True, metadata=meta)

    if verdict.decision == "escalate":
        return ToolPreInvokeResult(
            continue_processing=False,
            violation=PluginViolation(
                reason="intent_escalation_required",
                description=verdict.escalation_prompt
                or "This action requires human approval.",
                code="INTENTGUARD_ESCALATE",
                details={"reason": verdict.reason, "decision_id": verdict.decision_id},
            ),
            metadata={**meta, "intentguard_escalation": True},
        )

    # deny (and any unknown decision -> fail closed to block)
    return ToolPreInvokeResult(
        continue_processing=False,
        violation=PluginViolation(
            reason="not_in_intent",
            description="Action is outside the user's authorized intent for this session.",
            code="INTENTGUARD_DENY",
            details={"reason": verdict.reason, "decision_id": verdict.decision_id},
        ),
        metadata=meta,
    )
