"""Claude Code adapter: hook verdict mapping, installer edits, registry profile.

The hooks are stdlib-only with an injectable transport, so everything here runs
with no network and no running engine; one integration test wires the fake
transport to the real in-process engine app.
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib

import httpx

from adapters.claude_code import cli, hooks
from engine.pdp.registry import ToolRegistry

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "adapters" / "claude_code" / "tools-claude-code.json"


def _run_hook(fn, hook_input: dict, post) -> tuple[int, dict | None]:
    stdin = io.StringIO(json.dumps(hook_input))
    stdout = io.StringIO()
    rc = fn(stdin=stdin, stdout=stdout, post=post)
    raw = stdout.getvalue()
    return rc, (json.loads(raw) if raw.strip() else None)


PRE_INPUT = {
    "session_id": "cc-1",
    "tool_name": "Write",
    "tool_input": {"file_path": "/tmp/x", "content": "hi"},
}


# ── verdict mapping ─────────────────────────────────────────────────────────


def test_build_decide_request_maps_hook_fields(monkeypatch):
    monkeypatch.setenv("INTENTGUARD_CC_SUBJECT", "user:tester")
    req = hooks.build_decide_request(PRE_INPUT)
    assert req["session_id"] == "cc-1"
    assert req["subject"] == "user:tester"
    assert req["tool"] == "Write"
    assert req["arguments"] == {"file_path": "/tmp/x", "content": "hi"}


def test_allow_deny_escalate_map_to_permission_decisions():
    def out(verdict):
        return hooks.verdict_to_hook_output(verdict)["hookSpecificOutput"]

    assert (
        out({"decision": "allow", "reason": "in_intent"})["permissionDecision"]
        == "allow"
    )
    deny = out({"decision": "deny", "reason": "not_in_intent"})
    assert deny["permissionDecision"] == "deny"
    assert "not_in_intent" in deny["permissionDecisionReason"]
    ask = out(
        {
            "decision": "escalate",
            "reason": "escalated_for_review",
            "escalation_prompt": "Approve email.send to X?",
        }
    )
    assert ask["permissionDecision"] == "ask"
    assert "Approve email.send" in ask["permissionDecisionReason"]


def test_pre_tool_use_fails_closed_when_engine_unreachable(monkeypatch):
    monkeypatch.delenv("INTENTGUARD_CC_FAIL_OPEN", raising=False)

    def boom(path, payload, **kw):
        raise OSError("connection refused")

    rc, out = _run_hook(hooks.pre_tool_use, PRE_INPUT, boom)
    assert rc == 0
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "unreachable" in hso["permissionDecisionReason"]


def test_pre_tool_use_fail_open_opt_in(monkeypatch):
    monkeypatch.setenv("INTENTGUARD_CC_FAIL_OPEN", "true")

    def boom(path, payload, **kw):
        raise OSError("connection refused")

    rc, out = _run_hook(hooks.pre_tool_use, PRE_INPUT, boom)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_pre_tool_use_malformed_input_fails_closed():
    stdin = io.StringIO("not json{")
    stdout = io.StringIO()
    rc = hooks.pre_tool_use(stdin=stdin, stdout=stdout, post=None)
    assert rc == 0
    out = json.loads(stdout.getvalue())
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_user_prompt_submit_provisions_and_reports():
    seen = {}

    def fake_post(path, payload, **kw):
        seen["path"] = path
        seen["payload"] = payload
        return {"session_id": payload["session_id"], "grants_written": 3}

    rc, out = _run_hook(
        hooks.user_prompt_submit,
        {"session_id": "cc-1", "prompt": "Email bob@example.com the notes."},
        fake_post,
    )
    assert rc == 0
    assert seen["path"] == "/v1/sessions:parse"
    assert seen["payload"]["request_text"].startswith("Email bob@")
    assert "3" in out["hookSpecificOutput"]["additionalContext"]


def test_user_prompt_submit_never_blocks_on_failure():
    def boom(path, payload, **kw):
        raise OSError("engine down")

    rc, out = _run_hook(
        hooks.user_prompt_submit, {"session_id": "cc-1", "prompt": "hi"}, boom
    )
    assert rc == 0  # the user's prompt must go through regardless
    assert out is None


# ── integration: hook against the real in-process engine ────────────────────


def _engine_post(app):
    """A hooks-compatible post() that drives the real ASGI engine app."""

    def post(path, payload, timeout=None, token=None):
        async def _call():
            headers = {"content-type": "application/json"}
            if token:
                headers["authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                r = await client.post(path, json=payload, headers=headers)
                r.raise_for_status()
                return r.json()

        return asyncio.run(_call())

    return post


def test_hook_end_to_end_against_engine(monkeypatch):
    from engine.api.app import create_app
    from engine.config import EngineConfig
    from engine.schema import Mode

    monkeypatch.setenv("INTENTGUARD_CC_SUBJECT", "user:cc")
    registry = ToolRegistry.load(PROFILE)
    app = create_app(config=EngineConfig(mode=Mode.enforce, tool_registry=registry))
    post = _engine_post(app)

    # Provision: this session may write /tmp/notes.txt only.
    post(
        "/v1/sessions",
        {
            "session_id": "cc-e2e",
            "subject": "user:cc",
            "allowed_actions": [{"tool": "Write", "resource": "/tmp/notes.txt"}],
        },
    )

    ok_input = {
        "session_id": "cc-e2e",
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/notes.txt", "content": "x"},
    }
    _, out = _run_hook(hooks.pre_tool_use, ok_input, post)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    evil_input = {
        "session_id": "cc-e2e",
        "tool_name": "Write",
        "tool_input": {"file_path": "/etc/passwd", "content": "x"},
    }
    _, out = _run_hook(hooks.pre_tool_use, evil_input, post)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── registry profile ────────────────────────────────────────────────────────


def test_claude_code_registry_profile_loads_and_binds():
    reg = ToolRegistry.load(PROFILE)
    for tool in (
        "Bash",
        "Read",
        "Write",
        "Edit",
        "WebFetch",
        "WebSearch",
        "Glob",
        "Grep",
        "Task",
        "TodoWrite",
    ):
        assert reg.is_known(tool), tool
    assert reg.resource_args("Write") == ["file_path"]
    assert reg.resource_args("WebFetch") == ["url"]
    assert reg.resource_args("Bash") == []
    # Constraints hold: a javascript: URL violates WebFetch's pattern.
    assert (
        reg.validate_arguments("WebFetch", {"url": "javascript:alert(1)"})
        == "pattern_mismatch:url"
    )


# ── installer ───────────────────────────────────────────────────────────────


def _install(tmp_path, *extra):
    settings = tmp_path / "settings.json"
    rc = cli.main(["install", "--claude-code", "--settings", str(settings), *extra])
    assert rc == 0
    return settings, json.loads(settings.read_text())


def test_install_creates_pre_tool_use_hook(tmp_path):
    _, data = _install(tmp_path)
    entries = data["hooks"]["PreToolUse"]
    assert len(entries) == 1
    cmd = entries[0]["hooks"][0]["command"]
    assert "adapters.claude_code.hooks" in cmd and "pre-tool-use" in cmd


def test_install_is_idempotent(tmp_path):
    settings, _ = _install(tmp_path)
    cli.main(["install", "--claude-code", "--settings", str(settings)])
    data = json.loads(settings.read_text())
    assert len(data["hooks"]["PreToolUse"]) == 1


def test_install_preserves_existing_hooks_and_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo hi"}],
                        }
                    ]
                },
            }
        )
    )
    cli.main(["install", "--claude-code", "--settings", str(settings)])
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    commands = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert "echo hi" in commands
    assert any("adapters.claude_code.hooks" in c for c in commands)


def test_install_with_prompt_hook(tmp_path):
    _, data = _install(tmp_path, "--with-prompt-hook")
    cmd = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "user-prompt-submit" in cmd


def test_remove_deletes_only_ours(tmp_path):
    settings, _ = _install(tmp_path, "--with-prompt-hook")
    data = json.loads(settings.read_text())
    data["hooks"]["PreToolUse"].append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    )
    settings.write_text(json.dumps(data))
    rc = cli.main(["install", "--claude-code", "--settings", str(settings), "--remove"])
    assert rc == 0
    data = json.loads(settings.read_text())
    commands = [
        h["command"] for e in data["hooks"].get("PreToolUse", []) for h in e["hooks"]
    ]
    assert commands == ["echo hi"]
    assert "UserPromptSubmit" not in data["hooks"]
