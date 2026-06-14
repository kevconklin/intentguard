"""Real intent parser backed by the Anthropic API (Milestone 1 stub).

This is intentionally thin. It establishes the provider-agnostic seam: a real
parser lives behind the same ``IntentParser`` interface as the mock. The actual
prompt/tool-use extraction is Milestone 2 work.

TODO(Milestone 2): implement structured extraction of (tool, resource) pairs
from the user's request using the Anthropic Messages API with tool use, plus an
allowlist of known tools and per-tool argument schemas. Until then this raises
unless explicitly stubbed, so it cannot silently grant nothing.
"""

from __future__ import annotations

from engine.intent.base import AllowedAction, ParsedIntent

# Latest Claude model id at time of writing. See the claude-api reference.
DEFAULT_MODEL = "claude-opus-4-8"


class AnthropicIntentParser:
    """Parses intent via Claude. Stubbed for Milestone 1."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key

    async def parse(
        self, request_text: str, subject: str, session_id: str
    ) -> ParsedIntent:
        # TODO(Milestone 2): call the Anthropic Messages API with a tool-use
        # schema that returns a list of {tool, resource} the user authorized,
        # validated against a known-tools allowlist, then map to AllowedAction.
        raise NotImplementedError(
            "AnthropicIntentParser is a Milestone 1 stub. Use MockIntentParser "
            "for now, or implement structured extraction (see TODO)."
        )

    @staticmethod
    def _to_actions(raw: list[dict]) -> list[AllowedAction]:
        """Map raw extraction output to validated AllowedAction objects."""
        return [
            AllowedAction(tool=item["tool"], resource=item.get("resource"))
            for item in raw
        ]
