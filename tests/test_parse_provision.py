"""The parse-and-provision endpoint: parse -> validate -> provision -> decide."""

from __future__ import annotations

import httpx
import pytest

from engine.api.app import create_app
from engine.audit import AuditLogger
from engine.config import EngineConfig
from engine.intent.base import AllowedAction, ParsedIntent
from engine.intent.mock import MockIntentParser
from engine.schema import Mode


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _app(parser):
    return create_app(
        config=EngineConfig(mode=Mode.enforce, backend="memory"),
        audit=AuditLogger(),
        parser=parser,
    )


async def test_parse_provision_then_decide_end_to_end():
    # The mock stands in for the LLM: it returns these validated actions.
    parser = MockIntentParser([
        AllowedAction(tool="email.send", resource="bob@example.com"),
        AllowedAction(tool="calendar.read", resource=None),
    ])
    app = _app(parser)
    async with _client(app) as client:
        r = await client.post("/v1/sessions:parse", json={
            "session_id": "s1", "subject": "user:alice",
            "request_text": "email the notes to bob and check my calendar",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["grants_written"] == 2
        assert {a["tool"] for a in body["allowed_actions"]} == {"email.send", "calendar.read"}

        allow = await client.post("/v1/decide", json={
            "session_id": "s1", "subject": "user:alice",
            "tool": "email.send", "arguments": {"to": "bob@example.com"}})
        assert allow.json()["decision"] == "allow"

        deny = await client.post("/v1/decide", json={
            "session_id": "s1", "subject": "user:alice",
            "tool": "email.send", "arguments": {"to": "attacker@evil.com"}})
        assert deny.json()["decision"] == "deny"


class _FailingParser:
    async def parse(self, request_text, subject, session_id) -> ParsedIntent:
        raise RuntimeError("upstream LLM unavailable")


async def test_parser_failure_provisions_nothing():
    app = _app(_FailingParser())
    async with _client(app) as client:
        r = await client.post("/v1/sessions:parse", json={
            "session_id": "s2", "subject": "user:alice", "request_text": "do stuff"})
        assert r.status_code == 502
        assert "intent parsing failed" in r.json()["detail"]

        # Nothing was provisioned -> the session does not exist.
        d = await client.post("/v1/decide", json={
            "session_id": "s2", "subject": "user:alice",
            "tool": "email.send", "arguments": {"to": "bob@example.com"}})
        assert d.json()["decision"] == "deny"
        assert d.json()["reason"] == "no_session"


async def test_anthropic_backend_selected_without_network():
    # Selecting the anthropic backend must not require the SDK or a key at
    # startup (the parser builds lazily; the API call is deferred).
    app = create_app(config=EngineConfig(mode=Mode.enforce, intent_parser="anthropic"),
                     audit=AuditLogger())
    async with _client(app) as client:
        assert (await client.get("/healthz")).status_code == 200
