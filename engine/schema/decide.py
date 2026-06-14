"""The versioned decide() request/response contract.

This module is pure data definitions (pydantic). It has no dependency on the
policy store, the LLM, or any gateway. Changing the wire contract means bumping
``SCHEMA_VERSION`` and versioning the models.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# Wire contract version. Bump on any breaking change to the models below.
SCHEMA_VERSION = "1"


class Decision(str, Enum):
    """The verdict the engine returns for a tool call."""

    allow = "allow"
    deny = "deny"
    escalate = "escalate"


class Mode(str, Enum):
    """Enforcement mode.

    ``observe`` always returns ``allow`` but records the decision it *would*
    have made. ``enforce`` returns the real decision.
    """

    observe = "observe"
    enforce = "enforce"


class Reason(str, Enum):
    """Machine-readable explanation for a decision."""

    in_intent = "in_intent"
    not_in_intent = "not_in_intent"
    no_session = "no_session"
    unknown_tool = "unknown_tool"
    pdp_error_failclosed = "pdp_error_failclosed"
    escalated_for_review = "escalated_for_review"


class DecideRequest(BaseModel):
    """Request to the engine's decide endpoint."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    session_id: str = Field(..., description="Identifies the parsed-intent session.")
    subject: str = Field(..., description="e.g. user:alice or agent:planner")
    tool: str = Field(..., description="e.g. email.send")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Raw tool arguments."
    )
    resource: Optional[str] = Field(
        default=None,
        description="Optional explicit resource id. If omitted, the engine "
        "extracts the security-relevant argument for the tool.",
    )
    mode_override: Optional[Mode] = Field(
        default=None,
        description="Optional per-request mode. Falls back to the server default.",
    )


class DecideResponse(BaseModel):
    """Response from the engine's decide endpoint."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    decision: Decision
    reason: Reason
    effective_mode: Mode
    would_have_decided: Optional[Decision] = Field(
        default=None,
        description="Present in observe mode: the decision enforce mode "
        "would have returned.",
    )
    escalation_prompt: Optional[str] = Field(
        default=None,
        description="Human-readable prompt, present only when decision is escalate.",
    )
    decision_id: str = Field(
        ..., description="Correlates to the append-only audit log entry."
    )

    model_config = {"use_enum_values": True}
