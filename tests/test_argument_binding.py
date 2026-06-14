"""Schema-driven argument binding: single, compound, list, and missing cases."""

from __future__ import annotations

import pytest

from engine.config import EngineConfig
from engine.core import decide
from engine.intent import provision_session
from engine.intent.base import AllowedAction, ParsedIntent
from engine.pdp.model import (
    RESOURCE_SEPARATOR,
    bind_resource,
    grant_object,
)
from engine.pdp.registry import ToolRegistry, ToolSpec
from engine.schema import Decision, DecideRequest, Mode, Reason
from tests.conftest import SESSION, SUBJECT

# A registry with a single-arg tool, a compound tool, and a no-resource tool.
REG = ToolRegistry([
    ToolSpec(name="email.send", resource_args=["to"]),
    ToolSpec(name="http.request", resource_args=["host", "path"]),
    ToolSpec(name="calendar.read"),
])


# ── bind_resource (pure) ────────────────────────────────────────────────────

def test_single_arg_binds_to_value():
    b = bind_resource("email.send", {"to": "Bob@Example.com"}, registry=REG)
    assert b.resource == "bob@example.com" and b.complete


def test_no_resource_tool_binds_to_any():
    b = bind_resource("calendar.read", {}, registry=REG)
    assert b.resource == "*" and b.complete


def test_compound_binds_to_joined_parts_in_order():
    b = bind_resource("http.request", {"host": "API.example.com", "path": "/Data"}, registry=REG)
    assert b.resource == f"api.example.com{RESOURCE_SEPARATOR}/data" and b.complete


def test_compound_distinguishes_grants():
    a = grant_object(SESSION, "http.request", bind_resource("http.request", {"host": "h", "path": "/a"}, registry=REG).resource)
    b = grant_object(SESSION, "http.request", bind_resource("http.request", {"host": "h", "path": "/b"}, registry=REG).resource)
    assert a != b


def test_missing_required_arg_is_incomplete():
    b = bind_resource("http.request", {"host": "h"}, registry=REG)  # path missing
    assert b.complete is False


def test_blank_required_arg_is_incomplete():
    b = bind_resource("email.send", {"to": "   "}, registry=REG)
    assert b.complete is False


def test_explicit_resource_overrides_and_is_complete():
    b = bind_resource("http.request", {}, explicit_resource="X", registry=REG)
    assert b.resource == "x" and b.complete


def test_list_valued_single_arg_normalized_stably():
    b1 = bind_resource("email.send", {"to": ["b@x.com", "a@x.com"]}, registry=REG)
    b2 = bind_resource("email.send", {"to": ["a@x.com", "b@x.com"]}, registry=REG)
    assert b1.resource == b2.resource == "a@x.com,b@x.com"


# ── ToolSpec backward compatibility ─────────────────────────────────────────

def test_singular_resource_arg_coerced():
    assert ToolSpec(name="t", resource_arg="to").resource_args == ["to"]
    assert ToolSpec(name="t", resource_arg=None).resource_args == []


# ── decision path ───────────────────────────────────────────────────────────

def _req(tool: str, args: dict) -> DecideRequest:
    return DecideRequest(session_id=SESSION, subject=SUBJECT, tool=tool, arguments=args)


async def test_missing_required_arg_denied_failclosed(store, audit, seeded):
    await seeded([AllowedAction(tool="email.send", resource="bob@example.com")])
    cfg = EngineConfig(mode=Mode.enforce, tool_registry=REG)
    resp = await decide(_req("email.send", {"body": "no recipient"}), store, cfg, audit)
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.missing_resource.value
    assert "T2:tool_misuse" in audit.entries()[-1].owasp_threats


async def test_missing_arg_in_observe_logs_would_deny(store, audit, seeded):
    await seeded([AllowedAction(tool="email.send", resource="bob@example.com")])
    cfg = EngineConfig(mode=Mode.observe, tool_registry=REG)
    resp = await decide(_req("email.send", {}), store, cfg, audit)
    assert resp.decision == Decision.allow.value
    assert resp.would_have_decided == Decision.deny.value


async def test_compound_resource_roundtrip(store, writer, audit):
    # Provision a compound grant, then decide matching and non-matching calls.
    intent = ParsedIntent(
        session_id=SESSION, subject=SUBJECT,
        allowed_actions=[AllowedAction(
            tool="http.request",
            resource=f"api.example.com{RESOURCE_SEPARATOR}/data",
        )],
    )
    await provision_session(intent, writer)
    cfg = EngineConfig(mode=Mode.enforce, tool_registry=REG)

    ok = await decide(_req("http.request", {"host": "api.example.com", "path": "/data"}), store, cfg, audit)
    assert ok.decision == Decision.allow.value

    wrong = await decide(_req("http.request", {"host": "api.example.com", "path": "/other"}), store, cfg, audit)
    assert wrong.decision == Decision.deny.value
    assert wrong.reason == Reason.not_in_intent.value
