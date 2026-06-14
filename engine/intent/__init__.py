"""Intent parsing and trusted session provisioning.

The parser runs ONCE per request, on the trusted path, before any tool executes.
It returns plain data (``ParsedIntent``) — it has no reference to the policy
writer and cannot write authorization. The separate, trusted ``provision_session``
is the only thing that turns parsed intent into stored permission tuples.
"""

from engine.intent.anthropic import AnthropicIntentParser
from engine.intent.base import AllowedAction, IntentParser, ParsedIntent
from engine.intent.mock import MockIntentParser
from engine.intent.provision import provision_session

__all__ = [
    "AllowedAction",
    "ParsedIntent",
    "IntentParser",
    "MockIntentParser",
    "AnthropicIntentParser",
    "provision_session",
]
