"""In-memory policy store + writer.

Used by the test suite and the runnable demo so the whole system runs with no
network and no OpenFGA. The read/write separation is preserved exactly as in the
OpenFGA backend: ``InMemoryPolicyStore`` exposes only reads, ``InMemoryPolicyWriter``
only writes, and they share an opaque backing store.
"""

from __future__ import annotations

from engine.pdp.writer import grant_tuples


class InMemoryBacking:
    """Shared tuple store. Holds (user, relation, object) triples."""

    def __init__(self) -> None:
        self._tuples: set[tuple[str, str, str]] = set()

    def add(self, t: tuple[str, str, str]) -> None:
        self._tuples.add(t)

    def has(self, t: tuple[str, str, str]) -> bool:
        return t in self._tuples

    def snapshot(self) -> set[tuple[str, str, str]]:
        return set(self._tuples)


class InMemoryPolicyStore:
    """Read-only view over the backing store (decision path)."""

    def __init__(self, backing: InMemoryBacking) -> None:
        self._backing = backing

    async def session_exists(self, session_id: str, subject: str) -> bool:
        from engine.pdp.model import session_object

        return self._backing.has((subject, "principal", session_object(session_id)))

    async def check_grant(self, subject: str, grant_object_id: str) -> bool:
        return self._backing.has((subject, "grantee", grant_object_id))


class InMemoryPolicyWriter:
    """Write-only view over the backing store (trusted provisioning path)."""

    def __init__(self, backing: InMemoryBacking) -> None:
        self._backing = backing

    async def write_grants(
        self, session_id: str, subject: str, grants: list[tuple[str, str]]
    ) -> None:
        for t in grant_tuples(session_id, subject, grants):
            self._backing.add(t)


def make_memory_backend() -> tuple[InMemoryPolicyStore, InMemoryPolicyWriter]:
    """Create a store + writer pair sharing one backing set."""
    backing = InMemoryBacking()
    return InMemoryPolicyStore(backing), InMemoryPolicyWriter(backing)
