"""Tests for the known-tools registry and unknown-tool deny-by-default."""

from __future__ import annotations

import dataclasses
import json

import pytest
from pydantic import ValidationError

from engine.config import EngineConfig
from engine.core import decide
from engine.pdp.registry import ToolRegistry, ToolSpec, default_registry
from engine.schema import DecideRequest, Decision, Mode, Reason
from tests.conftest import SESSION, SUBJECT

# ── registry loading & validation ──────────────────────────────────────────


def test_default_registry_knows_shipped_tools():
    reg = default_registry()
    assert reg.is_known("email.send")
    assert reg.is_known("calendar.read")
    assert not reg.is_known("file.delete")


def test_default_registry_resource_args():
    reg = default_registry()
    assert reg.resource_arg("email.send") == "to"
    assert reg.resource_arg("calendar.read") is None  # no resource
    assert reg.resource_arg("file.write") == "path"
    assert reg.resource_arg("nope.unknown") is None  # unknown


def test_load_from_custom_json_file(tmp_path):
    path = tmp_path / "tools.json"
    path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "slack.post",
                        "description": "Post to slack",
                        "resource_arg": "channel",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    reg = ToolRegistry.load(path)
    assert reg.is_known("slack.post")
    assert reg.resource_arg("slack.post") == "channel"
    assert not reg.is_known("email.send")


def test_invalid_spec_rejected():
    with pytest.raises(ValidationError):
        ToolSpec(name="")  # name must be non-empty


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        ToolSpec(name="x", bogus="y")  # extra fields forbidden


def test_duplicate_tool_rejected():
    with pytest.raises(ValueError):
        ToolRegistry([ToolSpec(name="a"), ToolSpec(name="a")])


# ── decision-path behaviour ─────────────────────────────────────────────────


def _req(tool: str, args: dict | None = None, **kw) -> DecideRequest:
    return DecideRequest(
        session_id=SESSION, subject=SUBJECT, tool=tool, arguments=args or {}, **kw
    )


async def test_unknown_tool_denied_by_default(store, enforce_config, audit, seeded):
    await seeded()
    resp = await decide(
        _req("file.delete", {"path": "/etc/passwd"}), store, enforce_config, audit
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.unknown_tool.value
    assert "T2:tool_misuse" in audit.entries()[-1].owasp_threats


async def test_unknown_tool_denied_even_without_session(store, enforce_config, audit):
    # No session provisioned; allowlist deny takes precedence over no_session.
    resp = await decide(
        _req("file.delete", {"path": "/x"}), store, enforce_config, audit
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.unknown_tool.value


async def test_unknown_tool_in_observe_logs_would_deny(
    store, observe_config, audit, seeded
):
    await seeded()
    resp = await decide(
        _req("file.delete", {"path": "/x"}), store, observe_config, audit
    )
    assert resp.decision == Decision.allow.value
    assert resp.would_have_decided == Decision.deny.value


async def test_allowlist_can_be_disabled(store, enforce_config, audit, seeded):
    await seeded()
    cfg = dataclasses.replace(enforce_config, enforce_tool_allowlist=False)
    # With the allowlist off, an unregistered tool falls through to the normal
    # store check -> denied as not_in_intent (it was never granted), not unknown_tool.
    resp = await decide(_req("file.delete", {"path": "/x"}), store, cfg, audit)
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.not_in_intent.value


async def test_known_tool_still_flows_normally(store, enforce_config, audit, seeded):
    await seeded()
    resp = await decide(
        _req("email.send", {"to": "bob@example.com"}), store, enforce_config, audit
    )
    assert resp.decision == Decision.allow.value
    assert resp.reason == Reason.in_intent.value


async def test_custom_registry_via_config(store, audit, seeded):
    await seeded()
    custom = ToolRegistry([ToolSpec(name="email.send", resource_arg="to")])
    cfg = EngineConfig(mode=Mode.enforce, tool_registry=custom)
    # calendar.read is absent from the custom registry -> unknown_tool, even though
    # it was provisioned, because the allowlist is the outer gate.
    resp = await decide(_req("calendar.read", {}), store, cfg, audit)
    assert resp.reason == Reason.unknown_tool.value
