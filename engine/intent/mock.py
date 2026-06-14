"""Deterministic mock intent parser.

Used by the test suite and the demo so no network or API key is required. It is
fully deterministic: given the same configuration it always returns the same
allowed actions.
"""

from __future__ import annotations

from engine.intent.base import AllowedAction, ParsedIntent


class MockIntentParser:
    """Returns a fixed set of allowed actions, regardless of the request text.

    Configure with the actions a session's intent should grant. This mirrors
    what a real parser would extract, without any inference.
    """

    def __init__(self, allowed_actions: list[AllowedAction] | None = None) -> None:
        self._allowed_actions = list(allowed_actions or [])

    async def parse(
        self, request_text: str, subject: str, session_id: str
    ) -> ParsedIntent:
        return ParsedIntent(
            session_id=session_id,
            subject=subject,
            allowed_actions=list(self._allowed_actions),
        )
