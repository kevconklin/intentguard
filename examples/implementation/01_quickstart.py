"""01 — Quickstart: the smallest possible integration.

Provision a session's intent, then make one allowed call and one out-of-intent
call. This is the whole idea in ~10 lines of integration code.

    python examples/implementation/01_quickstart.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from intentguard_client import connect  # noqa: E402


async def main() -> None:
    # mode="enforce" so we get real decisions. Default server mode is "observe".
    async with connect(mode="enforce") as guard:
        session, subject = "demo-1", "user:alice"

        # TOUCHPOINT A — fix intent up front, on the trusted request.
        n = await guard.provision(session, subject, [
            {"tool": "email.send", "resource": "bob@example.com"},
            {"tool": "calendar.read", "resource": None},
        ])
        print(f"provisioned {n} grants for {subject}\n")

        # TOUCHPOINT B — authorize individual calls.
        allowed = await guard.decide(session, subject, "email.send", {"to": "bob@example.com"})
        print(f"email -> bob@example.com    : {allowed['decision']}  ({allowed['reason']})")

        blocked = await guard.decide(session, subject, "email.send", {"to": "attacker@evil.com"})
        print(f"email -> attacker@evil.com  : {blocked['decision']}  ({blocked['reason']})")


if __name__ == "__main__":
    asyncio.run(main())
