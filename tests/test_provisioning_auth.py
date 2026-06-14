"""Authentication on the provisioning write path (the only path that grants)."""

from __future__ import annotations

import httpx
import pytest

from engine.api.app import create_app
from engine.api.auth import parse_bearer
from engine.audit import AuditLogger
from engine.config import EngineConfig
from engine.intent.base import AllowedAction
from engine.intent.mock import MockIntentParser
from engine.schema import Mode

TOKEN = "s3cr3t-provisioning-token"

SEED = {
    "session_id": "s1", "subject": "user:alice",
    "allowed_actions": [{"tool": "email.send", "resource": "bob@example.com"}],
}
PARSE = {"session_id": "s1", "subject": "user:alice", "request_text": "email bob"}


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _app(**cfg_kwargs):
    parser = MockIntentParser([AllowedAction(tool="email.send", resource="bob@example.com")])
    return create_app(
        config=EngineConfig(mode=Mode.enforce, backend="memory", **cfg_kwargs),
        audit=AuditLogger(),
        parser=parser,
    )


# ── header parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("header,expected", [
    ("Bearer abc", "abc"),
    ("bearer abc", "abc"),
    ("Bearer  abc ", "abc"),
    ("Basic abc", None),
    ("abc", None),
    ("", None),
    (None, None),
])
def test_parse_bearer(header, expected):
    assert parse_bearer(header) == expected


# ── token configured: writes require it ─────────────────────────────────────

async def test_provision_requires_token_when_configured():
    app = _app(provisioning_token=TOKEN)
    async with _client(app) as client:
        assert (await client.post("/v1/sessions", json=SEED)).status_code == 401
        assert (await client.post("/v1/sessions", json=SEED,
                headers={"Authorization": "Bearer wrong"})).status_code == 401
        ok = await client.post("/v1/sessions", json=SEED,
                                headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200


async def test_parse_endpoint_also_requires_token():
    app = _app(provisioning_token=TOKEN)
    async with _client(app) as client:
        assert (await client.post("/v1/sessions:parse", json=PARSE)).status_code == 401
        ok = await client.post("/v1/sessions:parse", json=PARSE,
                               headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200


async def test_decide_is_not_gated_by_provisioning_token():
    # The read path must work without the provisioning credential.
    app = _app(provisioning_token=TOKEN)
    async with _client(app) as client:
        await client.post("/v1/sessions", json=SEED, headers={"Authorization": f"Bearer {TOKEN}"})
        d = await client.post("/v1/decide", json={
            "session_id": "s1", "subject": "user:alice",
            "tool": "email.send", "arguments": {"to": "bob@example.com"}})
        assert d.status_code == 200 and d.json()["decision"] == "allow"


# ── no token configured ─────────────────────────────────────────────────────

async def test_open_by_default_for_dev():
    # No token, not strict -> writes are open (a startup warning is logged).
    app = _app()
    async with _client(app) as client:
        assert (await client.post("/v1/sessions", json=SEED)).status_code == 200


async def test_strict_mode_without_token_fails_closed():
    # require auth but no token configured -> refuse writes (503), never open.
    app = _app(require_provisioning_auth=True)
    async with _client(app) as client:
        assert (await client.post("/v1/sessions", json=SEED)).status_code == 503
