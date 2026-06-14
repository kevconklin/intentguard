"""ContextForge adapter tests (verdict mapping + fail-closed), no gateway needed.

These exercise the adapter against the isolated cpex shim, so they run whether
or not cpex is installed.
"""

from __future__ import annotations

import pytest

from adapters.contextforge._cpex import ToolPreInvokePayload
from adapters.contextforge.plugin import IntentGuardPlugin
from adapters.contextforge.verdict import EngineVerdict, verdict_to_result


def _payload(tool="email.send", args=None):
    return ToolPreInvokePayload(name=tool, args=args or {"to": "bob@example.com"})


def test_allow_lets_call_proceed():
    result = verdict_to_result(EngineVerdict("allow", "in_intent", "d1"), _payload())
    assert result.continue_processing is True
    assert result.violation is None
    assert result.metadata["intentguard_decision_id"] == "d1"


def test_deny_blocks_with_violation():
    result = verdict_to_result(EngineVerdict("deny", "not_in_intent", "d2"), _payload())
    assert result.continue_processing is False
    assert result.violation is not None
    assert result.violation.code == "INTENTGUARD_DENY"


def test_escalate_blocks_and_carries_prompt():
    result = verdict_to_result(
        EngineVerdict("escalate", "escalated_for_review", "d3", escalation_prompt="Approve?"),
        _payload(),
    )
    assert result.continue_processing is False
    assert result.violation.code == "INTENTGUARD_ESCALATE"
    assert result.violation.description == "Approve?"


def test_unknown_decision_fails_closed():
    result = verdict_to_result(EngineVerdict("???", "weird", "d4"), _payload())
    assert result.continue_processing is False


async def test_plugin_calls_engine_and_allows(monkeypatch):
    plugin = IntentGuardPlugin()

    async def fake_call(self, body):
        assert body["tool"] == "email.send"
        assert body["arguments"] == {"to": "bob@example.com"}
        return EngineVerdict("allow", "in_intent", "d-allow")

    monkeypatch.setattr(IntentGuardPlugin, "_call_engine", fake_call)

    from adapters.contextforge._cpex import PluginContext

    result = await plugin.tool_pre_invoke(_payload(), PluginContext())
    assert result.continue_processing is True


async def test_plugin_fails_closed_when_engine_unreachable(monkeypatch):
    plugin = IntentGuardPlugin()

    async def boom(self, body):
        raise ConnectionError("engine down")

    monkeypatch.setattr(IntentGuardPlugin, "_call_engine", boom)

    from adapters.contextforge._cpex import PluginContext

    result = await plugin.tool_pre_invoke(_payload(), PluginContext())
    assert result.continue_processing is False
    assert result.violation is not None
