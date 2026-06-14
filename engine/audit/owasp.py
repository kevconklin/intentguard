"""OWASP Agentic AI threat tagging.

Tags drawn from the OWASP "Agentic AI - Threats and Mitigations" taxonomy
(T1-T15). We tag the threats an authorization decision is defending against, so
the audit log doubles as a security-relevant event stream.

Reference: OWASP GenAI Security Project, Agentic AI Threats and Mitigations.
"""

from __future__ import annotations

from enum import Enum

from engine.schema import Reason


class AgenticThreat(str, Enum):
    """Subset of the OWASP Agentic AI threat taxonomy we map decisions to."""

    TOOL_MISUSE = "T2:tool_misuse"
    PRIVILEGE_COMPROMISE = "T3:privilege_compromise"
    INTENT_BREAKING = "T6:intent_breaking_and_goal_manipulation"
    REPUDIATION = "T8:repudiation_and_untraceability"


# What each decision reason is defending against. ``in_intent`` defends nothing
# (the action was authorized); every other reason is a blocked/escalated event.
_REASON_THREATS: dict[Reason, tuple[AgenticThreat, ...]] = {
    Reason.in_intent: (),
    Reason.not_in_intent: (
        AgenticThreat.INTENT_BREAKING,
        AgenticThreat.TOOL_MISUSE,
    ),
    Reason.no_session: (AgenticThreat.PRIVILEGE_COMPROMISE,),
    Reason.unknown_tool: (AgenticThreat.TOOL_MISUSE,),
    Reason.escalated_for_review: (AgenticThreat.INTENT_BREAKING,),
    Reason.pdp_error_failclosed: (
        AgenticThreat.PRIVILEGE_COMPROMISE,
        AgenticThreat.REPUDIATION,
    ),
}


def threats_for_reason(reason: Reason) -> list[str]:
    """Return the OWASP Agentic threat tags for a decision reason."""
    return [t.value for t in _REASON_THREATS.get(reason, ())]
