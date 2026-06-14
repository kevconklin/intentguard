"""04 — Escalate to a human instead of hard-denying.

For some tools you don't want a silent block — you want a human in the loop.
Mark those tools "escalatable" on the engine, and an out-of-intent call returns
decision="escalate" with a human-readable ``escalation_prompt`` instead of deny.
Your UI prompts the user; you proceed only on approval.

Escalatable tools are an engine config (INTENTGUARD_ESCALATABLE_TOOLS, comma
separated). This example configures the in-process engine directly to show it.

    python examples/implementation/04_escalation_and_human_approval.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from intentguard_client import IntentGuardClient  # noqa: E402


async def fake_human_approves(prompt: str) -> bool:
    print(f"        🧑  HUMAN PROMPT: {prompt}")
    print("        🧑  (auto-approving for the demo)")
    return True


async def main() -> None:
    # Build an in-process engine with email.send marked escalatable.
    from engine.api.app import create_app
    from engine.config import EngineConfig
    from engine.schema import Mode

    config = EngineConfig(
        mode=Mode.enforce,
        backend="memory",
        escalatable_tools=frozenset({"email.send"}),
    )
    app = create_app(config=config)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://intentguard") as http:
        guard = IntentGuardClient(http)
        session, subject = "conv-99", "user:alice"
        await guard.provision(session, subject, [
            {"tool": "email.send", "resource": "bob@example.com"},
        ])

        # Out-of-intent email -> escalate (not deny), because the tool is escalatable.
        v = await guard.decide(session, subject, "email.send", {"to": "newperson@example.com"})
        print(f"decision: {v['decision'].upper()}  ({v['reason']})\n")

        if v["decision"] == "escalate":
            if await fake_human_approves(v["escalation_prompt"]):
                print("\n        ➡️  approved — your code now executes the tool")
            else:
                print("\n        ⛔  declined — tool not executed")


if __name__ == "__main__":
    asyncio.run(main())
