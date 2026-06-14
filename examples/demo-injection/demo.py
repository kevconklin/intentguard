"""Runnable prompt-injection demo for IntentGuard.

Runs end to end against the engine over HTTP, with NO live LLM and NO OpenFGA:
the engine runs in-process with the in-memory backend, reached via an ASGI HTTP
transport. Permissions are seeded from a config file through the deterministic
mock parser on the trusted path.

Scenario:
  Alice's trusted request authorizes "email.send to bob@example.com" and
  "calendar.read". Then:
    (a) the agent sends email to bob@example.com          -> ALLOW (in intent)
    (b) a prompt injection makes the agent email           -> DENY  (not in intent)
        attacker@evil.com
    (c) the same injected call in observe mode             -> ALLOW (logged as
                                                                      would-deny)
  Finally the append-only audit log is printed.

Run:  python examples/demo-injection/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

# Allow running as a plain script: add repo root to sys.path.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from engine.api.app import create_app  # noqa: E402
from engine.audit import AuditLogger  # noqa: E402
from engine.config import EngineConfig  # noqa: E402
from engine.intent import MockIntentParser  # noqa: E402
from engine.intent.base import AllowedAction  # noqa: E402
from engine.schema import Mode  # noqa: E402

HERE = os.path.dirname(__file__)


def _load_intent() -> dict:
    with open(os.path.join(HERE, "intent.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _banner(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


async def _client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://engine")


async def run() -> int:
    cfg = _load_intent()
    audit = AuditLogger()  # in-memory; shared so we can print it at the end

    # Engine in ENFORCE mode, in-memory backend, fail-closed timeout default.
    config = EngineConfig(mode=Mode.enforce, backend="memory")
    app = create_app(config=config, audit=audit)

    _banner("1. TRUSTED PARSE + PROVISION (before any tool runs)")
    # The mock parser stands in for the LLM. It returns inert data; it cannot
    # write to the policy store. Provisioning (the trusted write path) does.
    parser = MockIntentParser(
        allowed_actions=[AllowedAction(**a) for a in cfg["allowed_actions"]]
    )
    intent = await parser.parse(cfg["request_text"], cfg["subject"], cfg["session_id"])
    print(f"  request : {cfg['request_text']!r}")
    print(f"  subject : {intent.subject}")
    for a in intent.allowed_actions:
        print(f"  granted : {a.tool} -> {a.resource or '*'}")

    async with await _client(app) as client:
        seed = await client.post(
            "/v1/sessions",
            json={
                "session_id": intent.session_id,
                "subject": intent.subject,
                "allowed_actions": [a.model_dump() for a in intent.allowed_actions],
            },
        )
        print(f"  provisioned {seed.json()['grants_written']} grants "
              f"(HTTP {seed.status_code})")

        def decide_body(tool: str, args: dict, mode: str | None = None) -> dict:
            body = {
                "schema_version": "1",
                "session_id": intent.session_id,
                "subject": intent.subject,
                "tool": tool,
                "arguments": args,
            }
            if mode:
                body["mode_override"] = mode
            return body

        _banner("2. (a) ALLOWED CALL: email.send -> bob@example.com")
        r = await client.post("/v1/decide", json=decide_body("email.send", {"to": "bob@example.com", "body": "notes"}))
        _print_decision(r.json())
        ok_a = r.json()["decision"] == "allow"

        _banner("3. (b) INJECTED CALL (enforce): email.send -> attacker@evil.com")
        print("  (Simulates a prompt injection from untrusted tool output that")
        print("   redirects the agent to exfiltrate to an attacker address.)")
        r = await client.post("/v1/decide", json=decide_body("email.send", {"to": "attacker@evil.com", "body": "secrets"}))
        _print_decision(r.json())
        ok_b = r.json()["decision"] == "deny"

        _banner("4. (b') SAME INJECTED CALL in OBSERVE mode")
        print("  observe mode always allows but records the would-be decision.")
        r = await client.post("/v1/decide", json=decide_body("email.send", {"to": "attacker@evil.com"}, mode="observe"))
        _print_decision(r.json())
        ok_c = r.json()["decision"] == "allow" and r.json()["would_have_decided"] == "deny"

        _banner("5. APPEND-ONLY AUDIT LOG (OWASP Agentic Top 10 tagged)")
        audit_resp = await client.get("/v1/audit")
        for e in audit_resp.json()["entries"]:
            threats = ",".join(e["owasp_threats"]) or "-"
            wb = f" would={e['would_have_decided']}" if e["would_have_decided"] else ""
            print(f"  [{e['decision'].upper():8}] {e['tool']} -> {e['resource']:20} "
                  f"reason={e['reason']}{wb}  threats={threats}")

    _banner("RESULT")
    passed = ok_a and ok_b and ok_c
    print(f"  (a) allowed send  : {'PASS' if ok_a else 'FAIL'}")
    print(f"  (b) injected deny : {'PASS' if ok_b else 'FAIL'}")
    print(f"  (b') observe log  : {'PASS' if ok_c else 'FAIL'}")
    print(f"\n  DEMO {'PASSED' if passed else 'FAILED'}")
    return 0 if passed else 1


def _print_decision(d: dict) -> None:
    print(f"  decision        : {d['decision'].upper()}")
    print(f"  reason          : {d['reason']}")
    print(f"  effective_mode  : {d['effective_mode']}")
    if d.get("would_have_decided"):
        print(f"  would_have      : {d['would_have_decided']}")
    if d.get("escalation_prompt"):
        print(f"  escalation      : {d['escalation_prompt']}")
    print(f"  decision_id     : {d['decision_id']}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
