"""Shared test fixtures. No network, no API key, no OpenFGA, no LLM."""

from __future__ import annotations

import asyncio

import pytest

from engine.audit import AuditLogger
from engine.config import EngineConfig
from engine.intent import provision_session
from engine.intent.base import AllowedAction, ParsedIntent
from engine.pdp.memory import make_memory_backend
from engine.schema import Mode

SESSION = "sess-1"
SUBJECT = "user:alice"


@pytest.fixture
def audit() -> AuditLogger:
    return AuditLogger()  # in-memory mirror only


@pytest.fixture
def backend():
    """A fresh in-memory (store, writer) pair sharing one backing set."""
    return make_memory_backend()


@pytest.fixture
def store(backend):
    return backend[0]


@pytest.fixture
def writer(backend):
    return backend[1]


@pytest.fixture
def enforce_config() -> EngineConfig:
    return EngineConfig(mode=Mode.enforce, backend="memory")


@pytest.fixture
def observe_config() -> EngineConfig:
    return EngineConfig(mode=Mode.observe, backend="memory")


@pytest.fixture
def seeded(writer):
    """Provision the standard demo session: email.send->bob, calendar.read."""

    async def _seed(actions=None):
        intent = ParsedIntent(
            session_id=SESSION,
            subject=SUBJECT,
            allowed_actions=actions
            or [
                AllowedAction(tool="email.send", resource="bob@example.com"),
                AllowedAction(tool="calendar.read", resource=None),
            ],
        )
        await provision_session(intent, writer)
        return intent

    return _seed


class FailingStore:
    """A store whose reads always raise (to exercise fail-closed)."""

    async def session_exists(self, session_id: str, subject: str) -> bool:
        raise RuntimeError("pdp down")

    async def check_grant(self, subject: str, grant_object_id: str) -> bool:
        raise RuntimeError("pdp down")


class SlowStore:
    """A store whose reads exceed the configured timeout."""

    def __init__(self, delay: float = 5.0) -> None:
        self._delay = delay

    async def session_exists(self, session_id: str, subject: str) -> bool:
        await asyncio.sleep(self._delay)
        return True

    async def check_grant(self, subject: str, grant_object_id: str) -> bool:
        await asyncio.sleep(self._delay)
        return True
