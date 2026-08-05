"""Contract tests for decide(): allow, deny, escalate, no-session, fail-closed."""

from __future__ import annotations

import dataclasses

from engine.core import decide
from engine.schema import Decision, Mode, Reason
from tests.conftest import FailingStore, SlowStore
from tests.conftest import make_request as _req


async def test_allow_in_intent(store, enforce_config, audit, seeded):
    await seeded()
    resp = await decide(
        _req("email.send", {"to": "bob@example.com"}), store, enforce_config, audit
    )
    assert resp.decision == Decision.allow.value
    assert resp.reason == Reason.in_intent.value
    assert resp.effective_mode == Mode.enforce.value
    assert resp.would_have_decided is None
    assert resp.decision_id
    # Audit recorded exactly one entry, correlated by decision_id.
    assert audit.entries()[-1].decision_id == resp.decision_id


async def test_deny_not_in_intent(store, enforce_config, audit, seeded):
    await seeded()
    resp = await decide(
        _req("email.send", {"to": "attacker@evil.com"}), store, enforce_config, audit
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.not_in_intent.value


async def test_deny_same_tool_different_resource_is_distinct_grant(
    store, enforce_config, audit, seeded
):
    """email.send to bob is granted; email.send to carol is a different grant."""
    await seeded()
    resp = await decide(
        _req("email.send", {"to": "carol@example.com"}), store, enforce_config, audit
    )
    assert resp.decision == Decision.deny.value


async def test_explicit_resource_cannot_smuggle_by_default(
    store, enforce_config, audit, seeded
):
    """A request claiming the granted resource while its arguments target an
    attacker must be denied: the explicit resource is not trusted by default."""
    await seeded()  # grants email.send -> bob@example.com
    resp = await decide(
        _req("email.send", {"to": "attacker@evil.com"}, resource="bob@example.com"),
        store,
        enforce_config,
        audit,
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.not_in_intent.value


async def test_explicit_resource_trusted_when_enabled(
    store, enforce_config, audit, seeded
):
    """With trust_explicit_resource enabled, the explicit resource binds — for
    deployments whose /v1/decide caller is fully trusted."""
    await seeded()
    cfg = dataclasses.replace(enforce_config, trust_explicit_resource=True)
    resp = await decide(
        _req("email.send", {"to": "attacker@evil.com"}, resource="bob@example.com"),
        store,
        cfg,
        audit,
    )
    assert resp.decision == Decision.allow.value
    assert resp.reason == Reason.in_intent.value


async def test_no_session(store, enforce_config, audit):
    # Nothing provisioned.
    resp = await decide(
        _req("email.send", {"to": "bob@example.com"}), store, enforce_config, audit
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.no_session.value


async def test_escalate_when_configured(store, enforce_config, audit, seeded):
    await seeded()
    cfg = dataclasses.replace(
        enforce_config, escalatable_tools=frozenset({"email.send"})
    )
    resp = await decide(
        _req("email.send", {"to": "attacker@evil.com"}), store, cfg, audit
    )
    assert resp.decision == Decision.escalate.value
    assert resp.reason == Reason.escalated_for_review.value
    assert (
        resp.escalation_prompt is not None
        and "attacker@evil.com" in resp.escalation_prompt
    )


async def test_fail_closed_on_store_error(enforce_config, audit, seeded):
    await seeded()  # provisioning uses the writer; the decide store is the failing one
    resp = await decide(
        _req("email.send", {"to": "bob@example.com"}),
        FailingStore(),
        enforce_config,
        audit,
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.pdp_error_failclosed.value
    assert audit.entries()[-1].error is not None


async def test_fail_closed_on_timeout(audit):

    from engine.config import EngineConfig

    cfg = EngineConfig(mode=Mode.enforce, pdp_timeout_seconds=0.05)
    resp = await decide(
        _req("email.send", {"to": "bob@example.com"}), SlowStore(delay=2.0), cfg, audit
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.pdp_error_failclosed.value
    assert audit.entries()[-1].error == "pdp_timeout"


async def test_observe_mode_allows_but_logs_would_have(
    store, observe_config, audit, seeded
):
    await seeded()
    resp = await decide(
        _req("email.send", {"to": "attacker@evil.com"}), store, observe_config, audit
    )
    assert resp.decision == Decision.allow.value
    assert resp.would_have_decided == Decision.deny.value
    assert resp.effective_mode == Mode.observe.value


async def test_mode_override_beats_server_default(store, observe_config, audit, seeded):
    await seeded()
    # Server default observe, but this request overrides to enforce.
    resp = await decide(
        _req("email.send", {"to": "attacker@evil.com"}, mode_override=Mode.enforce),
        store,
        observe_config,
        audit,
    )
    assert resp.decision == Decision.deny.value
    assert resp.effective_mode == Mode.enforce.value


async def test_response_schema_version(store, enforce_config, audit, seeded):
    await seeded()
    resp = await decide(_req("calendar.read", {}), store, enforce_config, audit)
    assert resp.schema_version == "1"
    assert resp.decision == Decision.allow.value
