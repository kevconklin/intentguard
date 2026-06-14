"""The demo runs green, and the audit log is genuinely append-only."""

from __future__ import annotations

import importlib.util
import pathlib

from engine.audit import AuditLogger

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEMO_PATH = REPO_ROOT / "examples" / "demo-injection" / "demo.py"


async def test_injection_demo_runs_green():
    spec = importlib.util.spec_from_file_location("demo_injection", DEMO_PATH)
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    rc = await demo.run()
    assert rc == 0


def test_audit_log_is_append_only_on_disk(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLogger(str(path))
    common = dict(
        session_id="s",
        subject="user:a",
        tool="email.send",
        resource="bob@x",
        grant_key="s|email.send|bob@x",
        decision="deny",
        reason="not_in_intent",
        effective_mode="enforce",
        would_have_decided=None,
        owasp_threats=[],
        error=None,
    )
    log.record(decision_id="1", **common)
    size_after_first = path.stat().st_size
    log.record(decision_id="2", **common)
    size_after_second = path.stat().st_size

    # Second write only grows the file; the first line is untouched.
    assert size_after_second > size_after_first
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"decision_id":"1"' in lines[0]
    assert '"decision_id":"2"' in lines[1]
