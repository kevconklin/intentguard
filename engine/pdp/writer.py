"""The write-only policy-store interface (trusted provisioning path).

Permission tuples are written here and ONLY here. This module is imported only
by ``engine.intent.provision`` (the trusted path that runs at session setup,
from the user's own request, before any tool executes). It is never imported by
the decision path. The architecture-invariant tests enforce that separation.
"""

from __future__ import annotations

from typing import Protocol

from engine.pdp.model import grant_object, session_object


class PolicyWriter(Protocol):
    """Write-only authorization mutations."""

    async def write_grants(
        self, session_id: str, subject: str, grants: list[tuple[str, str]]
    ) -> None:
        """Write the permission tuples for a session.

        ``grants`` is a list of ``(tool, resource)`` pairs already resolved on
        the trusted path. Writing is idempotent.
        """
        ...


def grant_tuples(
    session_id: str, subject: str, grants: list[tuple[str, str]]
) -> list[tuple[str, str, str]]:
    """Compute the (user, relation, object) tuples for a session's grants.

    Shared by every concrete writer so the on-disk representation is identical
    regardless of backend.
    """
    tuples: list[tuple[str, str, str]] = [
        (subject, "principal", session_object(session_id)),
    ]
    for tool, resource in grants:
        obj = grant_object(session_id, tool, resource)
        tuples.append((subject, "grantee", obj))
        tuples.append((session_object(session_id), "session", obj))
    return tuples
