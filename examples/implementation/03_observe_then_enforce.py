"""03 — Safe rollout: observe first, then enforce.

You never want to turn on enforcement blind and break legitimate flows. Run in
"observe" mode in production first: every call is ALLOWED, but the engine records
the decision it WOULD have made. You read the audit log, find the would-be
denials, fix your provisioning, then flip to enforce.

This example runs the SAME calls in observe and enforce so you can compare.

    python examples/implementation/03_observe_then_enforce.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from intentguard_client import connect  # noqa: E402

CALLS = [
    ("email.send", {"to": "bob@example.com"}),       # in intent
    ("email.send", {"to": "attacker@evil.com"}),     # NOT in intent
    ("calendar.read", {}),                             # in intent
    ("file.delete", {"path": "/etc/passwd"}),          # NOT in intent (unknown tool)
]


async def run_mode(mode: str) -> None:
    print(f"\n===== mode: {mode.upper()} =====")
    async with connect(mode=mode) as guard:
        session, subject = f"sess-{mode}", "user:alice"
        await guard.provision(session, subject, [
            {"tool": "email.send", "resource": "bob@example.com"},
            {"tool": "calendar.read", "resource": None},
        ])

        for tool, args in CALLS:
            v = await guard.decide(session, subject, tool, args)
            would = f" (would_have={v['would_have_decided']})" if v.get("would_have_decided") else ""
            print(f"  {tool:14} {str(args):34} -> {v['decision'].upper()}{would}")

        # In observe mode, mine the audit log for the calls you WOULD have blocked.
        if mode == "observe":
            entries = await guard.audit()
            would_block = [e for e in entries if e["would_have_decided"] in ("deny", "escalate")]
            print(f"\n  audit: {len(would_block)} call(s) would have been blocked in enforce:")
            for e in would_block:
                print(f"    - {e['tool']} -> {e['resource']}  ({e['reason']}, threats={e['owasp_threats']})")


async def main() -> None:
    print("Roll out in OBSERVE first (nothing is blocked, but you learn what would be);")
    print("then switch to ENFORCE once provisioning is correct.")
    await run_mode("observe")
    await run_mode("enforce")
    print("\nIn production this is a single flag: INTENTGUARD_MODE=observe|enforce")


if __name__ == "__main__":
    asyncio.run(main())
