"""Trusted session provisioning: the ONLY write path to the policy store.

This is invoked at session setup, from the user's trusted request, before any
tool executes and before any untrusted content (tool outputs, web pages, emails)
is processed. It takes inert ``ParsedIntent`` data and writes the permission
tuples via the write-only ``PolicyWriter``.

Because the parser produces only data and this function is the sole caller of
the writer, a prompt injection that corrupts what the agent *wants* to do cannot
expand what it is *allowed* to do.
"""

from __future__ import annotations

from engine.intent.base import ParsedIntent
from engine.pdp.model import normalize_resource
from engine.pdp.writer import PolicyWriter


async def provision_session(intent: ParsedIntent, writer: PolicyWriter) -> int:
    """Write permission tuples for a parsed session's allowed actions.

    Returns the number of (tool, resource) grants written.
    """
    grants: list[tuple[str, str]] = [
        (action.tool, normalize_resource(action.resource))
        for action in intent.allowed_actions
    ]
    await writer.write_grants(intent.session_id, intent.subject, grants)
    return len(grants)
