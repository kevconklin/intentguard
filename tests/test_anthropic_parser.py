"""AnthropicIntentParser: validation + parse() with no network, plus a gated live test."""

from __future__ import annotations

import os

import pytest

from engine.config import EngineConfig
from engine.core import decide
from engine.intent import provision_session
from engine.intent.anthropic import AnthropicIntentParser, validate_extracted
from engine.intent.base import IntentParser
from engine.pdp.registry import ToolRegistry, ToolSpec
from engine.schema import Decision, DecideRequest, Mode
from tests.conftest import SESSION, SUBJECT

REG = ToolRegistry([
    ToolSpec(name="email.send", resource_args=["to"]),
    ToolSpec(name="calendar.read"),
])


# ── pure validation ─────────────────────────────────────────────────────────

def test_validate_keeps_known_drops_unknown():
    raw = [
        {"tool": "email.send", "resource": "bob@example.com"},
        {"tool": "calendar.read", "resource": None},
        {"tool": "file.delete", "resource": "/etc/passwd"},   # not in REG
    ]
    kept, dropped = validate_extracted(raw, REG)
    assert {a.tool for a in kept} == {"email.send", "calendar.read"}
    assert len(dropped) == 1 and dropped[0]["item"]["tool"] == "file.delete"


def test_validate_blank_resource_becomes_none():
    kept, _ = validate_extracted([{"tool": "email.send", "resource": "   "}], REG)
    assert kept[0].resource is None


def test_validate_rejects_malformed_items():
    kept, dropped = validate_extracted(["nope", {"no_tool": 1}, {"tool": 5}], REG)
    assert kept == []
    assert len(dropped) == 3


# ── parse() via an injected extractor (no network, no API key) ──────────────

async def test_parse_validates_against_allowlist():
    async def fake_extractor(request_text, tool_names):
        # The parser must hand the extractor the registry's tool names.
        assert set(tool_names) == {"email.send", "calendar.read"}
        return [
            {"tool": "email.send", "resource": "bob@example.com"},
            {"tool": "file.delete", "resource": "/etc/passwd"},  # injected/hallucinated
        ]

    parser = AnthropicIntentParser(registry=REG, extractor=fake_extractor)
    intent = await parser.parse("email bob and delete passwd", SUBJECT, SESSION)

    assert intent.session_id == SESSION and intent.subject == SUBJECT
    assert [a.tool for a in intent.allowed_actions] == ["email.send"]  # file.delete dropped


def test_parser_satisfies_intent_parser_protocol():
    assert isinstance(AnthropicIntentParser(registry=REG, extractor=_noop), IntentParser)


async def _noop(request_text, tool_names):
    return []


# ── end-to-end on the trusted path (parse -> provision -> decide) ───────────

async def test_parse_then_provision_then_decide(store, writer, audit):
    async def fake_extractor(request_text, tool_names):
        return [{"tool": "email.send", "resource": "bob@example.com"}]

    parser = AnthropicIntentParser(registry=REG, extractor=fake_extractor)
    intent = await parser.parse("email the notes to bob", SUBJECT, SESSION)
    await provision_session(intent, writer)

    cfg = EngineConfig(mode=Mode.enforce, tool_registry=REG)
    allowed = await decide(
        DecideRequest(session_id=SESSION, subject=SUBJECT, tool="email.send",
                      arguments={"to": "bob@example.com"}),
        store, cfg, audit,
    )
    assert allowed.decision == Decision.allow.value

    blocked = await decide(
        DecideRequest(session_id=SESSION, subject=SUBJECT, tool="email.send",
                      arguments={"to": "attacker@evil.com"}),
        store, cfg, audit,
    )
    assert blocked.decision == Decision.deny.value


# ── live test (opt-in; needs a real API key) ────────────────────────────────

@pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("INTENTGUARD_TEST_ANTHROPIC")),
    reason="set ANTHROPIC_API_KEY and INTENTGUARD_TEST_ANTHROPIC to run",
)
async def test_live_anthropic_extraction():
    pytest.importorskip("anthropic")
    parser = AnthropicIntentParser()  # default registry + real Anthropic call
    intent = await parser.parse(
        "Email the meeting notes to bob@example.com and check my calendar.",
        SUBJECT, SESSION,
    )
    tools = {a.tool for a in intent.allowed_actions}
    assert "email.send" in tools
    assert "calendar.read" in tools
