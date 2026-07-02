"""The injection scenario, end to end over HTTP (ASGI), enforce and observe."""

from __future__ import annotations

from engine.schema import Mode
from tests.conftest import asgi_client, make_test_app


async def _seed(client):
    return await client.post(
        "/v1/sessions",
        json={
            "session_id": "s1",
            "subject": "user:alice",
            "allowed_actions": [
                {"tool": "email.send", "resource": "bob@example.com"},
                {"tool": "calendar.read", "resource": None},
            ],
        },
    )


def _decide(tool, args, mode=None):
    body = {
        "session_id": "s1",
        "subject": "user:alice",
        "tool": tool,
        "arguments": args,
    }
    if mode:
        body["mode_override"] = mode
    return body


async def test_injection_denied_in_enforce():
    app, audit = make_test_app(Mode.enforce)
    async with asgi_client(app) as client:
        assert (await _seed(client)).status_code == 200

        allowed = await client.post(
            "/v1/decide", json=_decide("email.send", {"to": "bob@example.com"})
        )
        assert allowed.json()["decision"] == "allow"

        injected = await client.post(
            "/v1/decide", json=_decide("email.send", {"to": "attacker@evil.com"})
        )
        body = injected.json()
        assert body["decision"] == "deny"
        assert body["reason"] == "not_in_intent"

    # The denied out-of-intent attempt is in the append-only audit log, tagged.
    last = audit.entries()[-1]
    assert last.decision == "deny"
    assert last.resource == "attacker@evil.com"
    assert "T6:intent_breaking_and_goal_manipulation" in last.owasp_threats


async def test_injection_logged_but_allowed_in_observe():
    app, audit = make_test_app(Mode.observe)
    async with asgi_client(app) as client:
        await _seed(client)
        injected = await client.post(
            "/v1/decide", json=_decide("email.send", {"to": "attacker@evil.com"})
        )
        body = injected.json()
        assert body["decision"] == "allow"
        assert body["would_have_decided"] == "deny"

    last = audit.entries()[-1]
    assert last.decision == "allow"
    assert last.would_have_decided == "deny"


async def test_per_request_override_enforces_inside_observe_server():
    app, _ = make_test_app(Mode.observe)
    async with asgi_client(app) as client:
        await _seed(client)
        injected = await client.post(
            "/v1/decide",
            json=_decide("email.send", {"to": "attacker@evil.com"}, mode="enforce"),
        )
        assert injected.json()["decision"] == "deny"
