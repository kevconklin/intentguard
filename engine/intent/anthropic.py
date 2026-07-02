"""Real intent parser backed by the Anthropic API.

Extracts the discrete ``(tool, resource)`` actions a user authorized in their
*trusted* request, using the Messages API with forced tool use, and validates
every extracted action against the known-tools allowlist. Anything not in the
allowlist is dropped and logged — the parser can never grant an action the
registry doesn't know about.

Security posture:
* Runs only on the trusted path (the user's own request), once per session,
  before any untrusted content is processed.
* Returns inert ``ParsedIntent`` data; it has no access to the policy writer.
* The Anthropic SDK is imported lazily so it stays an optional dependency, and
  the actual API call sits behind an injectable ``extractor`` so the validation
  logic and ``parse()`` are unit-tested with no network and no API key.
"""

from __future__ import annotations

import copy
import logging
import os
from typing import Awaitable, Callable, Optional

from engine.intent.base import AllowedAction, ParsedIntent
from engine.pdp.registry import ToolRegistry, default_registry

logger = logging.getLogger("intentguard.intent")

# Balanced default for structured extraction. Override via the constructor or
# INTENTGUARD_ANTHROPIC_MODEL (read at construction time, not import time).
# Use a Haiku model for cheaper/faster extraction.
DEFAULT_MODEL = "claude-sonnet-4-6"

# An extractor turns (request_text, allowed_tool_names) into raw {tool, resource}
# dicts. The default calls Anthropic; tests inject a deterministic stand-in.
RawExtractor = Callable[[str, list[str]], Awaitable[list[dict]]]

INTENT_TOOL: dict = {
    "name": "record_authorized_actions",
    "description": "Record the discrete actions the user explicitly authorized.",
    "input_schema": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "description": "Each action the user explicitly authorized.",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {
                            "type": "string",
                            "description": "Tool name, chosen from the allowed list.",
                        },
                        "resource": {
                            "type": ["string", "null"],
                            "description": "The specific target the user named "
                            "(email recipient, file path, URL). null if the user "
                            "did not restrict it to a specific target.",
                        },
                    },
                    "required": ["tool"],
                },
            }
        },
        "required": ["actions"],
    },
}

SYSTEM_PROMPT = (
    "You extract the actions a user has authorized from their request, for a "
    "security authorization system. Rules:\n"
    "- Record ONLY actions the user explicitly requested. Never infer, expand, "
    "or add actions they did not ask for.\n"
    "- Use only tools from the allowed list. If the request implies a tool not "
    "on the list, omit it.\n"
    "- Set 'resource' to the specific target the user named (email recipient, "
    "file path, URL). If they did not name a specific target, set it to null.\n"
    "- Call record_authorized_actions exactly once with all authorized actions."
)


def _build_intent_tool(tool_names: list[str]) -> dict:
    """Copy the tool schema and constrain the tool enum to the known tools."""
    tool = copy.deepcopy(INTENT_TOOL)
    tool["input_schema"]["properties"]["actions"]["items"]["properties"]["tool"][
        "enum"
    ] = list(tool_names)
    return tool


def validate_extracted(
    raw: list[dict], registry: ToolRegistry
) -> tuple[list[AllowedAction], list[dict]]:
    """Split raw extracted actions into allowlist-valid kept and dropped lists.

    Pure and deterministic. An action is dropped if it is malformed or its tool
    is not in the registry — the parser never grants an unknown tool.
    """
    kept: list[AllowedAction] = []
    dropped: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            dropped.append({"item": item, "why": "not_an_object"})
            continue
        tool = item.get("tool")
        if not isinstance(tool, str) or not registry.is_known(tool):
            dropped.append({"item": item, "why": "tool_not_in_allowlist"})
            continue
        resource = item.get("resource")
        resource = resource if isinstance(resource, str) and resource.strip() else None
        kept.append(AllowedAction(tool=tool, resource=resource))
    return kept, dropped


class AnthropicIntentParser:
    """Parses a trusted request into allowlist-validated authorized actions."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        extractor: Optional[RawExtractor] = None,
        max_tokens: int = 1024,
    ) -> None:
        self._registry = registry or default_registry()
        self._model = model or os.environ.get(
            "INTENTGUARD_ANTHROPIC_MODEL", DEFAULT_MODEL
        )
        self._api_key = api_key
        self._extractor = extractor or self._anthropic_extract
        self._max_tokens = max_tokens
        # The registry is immutable, so the tool list and the enum-constrained
        # extraction tool are fixed at construction — no per-parse rebuild.
        self._tool_names = self._registry.tool_names()
        self._intent_tool = _build_intent_tool(self._tool_names)
        self._client = None

    async def parse(
        self, request_text: str, subject: str, session_id: str
    ) -> ParsedIntent:
        raw = await self._extractor(request_text, self._tool_names)
        kept, dropped = validate_extracted(raw, self._registry)
        if dropped:
            logger.warning(
                "intent parser dropped %d out-of-allowlist action(s): %s",
                len(dropped),
                dropped,
            )
        return ParsedIntent(
            session_id=session_id, subject=subject, allowed_actions=kept
        )

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic  # lazy, optional dependency

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def _anthropic_extract(
        self, request_text: str, tool_names: list[str]
    ) -> list[dict]:
        # One SDK client (and connection pool) per parser, created on first use.
        message = await self._get_client().messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            tools=[self._intent_tool],
            tool_choice={"type": "tool", "name": "record_authorized_actions"},
            messages=[
                {
                    "role": "user",
                    "content": f"Allowed tools: {', '.join(tool_names)}\n\n"
                    f"User request:\n{request_text}",
                }
            ],
        )
        for block in message.content:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == "record_authorized_actions"
            ):
                return list(block.input.get("actions", []))
        return []
