"""Provider-agnostic intent parser interface and its data types."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class AllowedAction(BaseModel):
    """One action the user authorized: a tool, optionally bound to a resource.

    ``resource`` is the security-relevant value (e.g. an email recipient).
    ``None`` means "any resource" for this tool.
    """

    tool: str
    resource: Optional[str] = None


class ParsedIntent(BaseModel):
    """The structured result of parsing a user's trusted request.

    This is inert data. It carries no capability to mutate authorization — it is
    handed to the trusted ``provision_session`` which performs the writes.
    """

    session_id: str
    subject: str
    allowed_actions: list[AllowedAction] = Field(default_factory=list)


@runtime_checkable
class IntentParser(Protocol):
    """Turns a trusted natural-language request into structured allowed actions.

    Implementations may call an LLM. This is the ONLY place an LLM is consulted,
    and it runs on the trusted path before any tool output is seen.
    """

    async def parse(
        self, request_text: str, subject: str, session_id: str
    ) -> ParsedIntent: ...
