"""Opt-in OpenFGA integration test.

Skipped unless ``INTENTGUARD_TEST_OPENFGA_URL`` is set AND openfga-sdk is
installed AND the server is reachable. Keeps the default suite network-free
while letting CI / developers exercise the real backend:

    docker compose up -d
    python -m engine.pdp.bootstrap            # not required; the test bootstraps
    INTENTGUARD_TEST_OPENFGA_URL=http://localhost:8080 pytest tests/test_openfga_integration.py
"""

from __future__ import annotations

import os

import pytest

URL = os.environ.get("INTENTGUARD_TEST_OPENFGA_URL")

pytestmark = pytest.mark.skipif(not URL, reason="set INTENTGUARD_TEST_OPENFGA_URL to run")


@pytest.fixture
async def backend_ids():
    pytest.importorskip("openfga_sdk")

    from engine.pdp.openfga import bootstrap

    try:
        return await bootstrap(URL)
    except Exception as exc:  # server unreachable -> skip rather than fail
        pytest.skip(f"OpenFGA unreachable: {exc}")


async def test_real_openfga_decide_path(backend_ids):
    from engine.pdp.openfga import OpenFgaPolicyStore, OpenFgaPolicyWriter
    from engine.pdp.model import grant_object

    store_id, model_id = backend_ids
    writer = OpenFgaPolicyWriter(URL, store_id, model_id)
    store = OpenFgaPolicyStore(URL, store_id, model_id)

    await writer.write_grants("s1", "user:alice", [("email.send", "bob@example.com")])
    # Idempotent re-provisioning must not raise.
    await writer.write_grants("s1", "user:alice", [("email.send", "bob@example.com")])

    assert await store.session_exists("s1", "user:alice") is True
    assert await store.session_exists("ghost", "user:alice") is False
    assert await store.check_grant("user:alice", grant_object("s1", "email.send", "bob@example.com")) is True
    assert await store.check_grant("user:alice", grant_object("s1", "email.send", "attacker@evil.com")) is False
