"""The read-only policy-store interface used by the decision path.

CRITICAL INVARIANT: this Protocol exposes only reads. The per-call decide()
path depends on this type and nothing else from the store layer, so there is no
write path reachable from a decision. Writes live in ``engine.pdp.writer``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PolicyStore(Protocol):
    """Read-only authorization queries."""

    async def session_exists(self, session_id: str, subject: str) -> bool:
        """True if ``subject`` has a provisioned session ``session_id``.

        Used to distinguish "no session" (never provisioned) from
        "action not in intent" (provisioned, but this action was not granted).
        """
        ...

    async def check_grant(self, subject: str, grant_object_id: str) -> bool:
        """True if ``subject`` may invoke the grant identified by the object id."""
        ...


class CheckOutcome:
    """Internal outcome of a store interaction, including error signalling."""

    __slots__ = ("session_exists", "allowed", "errored", "error")

    def __init__(
        self,
        session_exists: bool = False,
        allowed: bool = False,
        errored: bool = False,
        error: str | None = None,
    ) -> None:
        self.session_exists = session_exists
        self.allowed = allowed
        self.errored = errored
        self.error = error
