"""Claude Code hook implementations for IntentGuard.

Two hooks, both stdlib-only so they run in any Python without the engine's
dependencies installed:

* ``pre-tool-use`` — called by Claude Code before every tool call. Posts the
  call to the engine's ``/v1/decide`` and maps the verdict onto Claude Code's
  permission decisions: allow → ``allow``, deny → ``deny`` (the reason is shown
  to the model so it can adapt), escalate → ``ask`` (Claude Code's native
  permission prompt becomes the human-approval step). If the engine is
  unreachable the hook fails closed and denies, unless
  ``INTENTGUARD_CC_FAIL_OPEN=true``.

* ``user-prompt-submit`` — called when the user submits a prompt, which is
  exactly IntentGuard's trusted moment: the prompt is parsed and the session
  provisioned via ``/v1/sessions:parse`` before any untrusted tool output
  exists. A provisioning failure never blocks the prompt (the decide path
  still fails closed later).

Configuration (environment):
    INTENTGUARD_URL                 engine base URL (default http://127.0.0.1:8000)
    INTENTGUARD_CC_SUBJECT          decide subject (default user:<os user>)
    INTENTGUARD_CC_FAIL_OPEN        allow when the engine is unreachable (default false)
    INTENTGUARD_PROVISIONING_TOKEN  bearer token for the provisioning write path
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Callable, Optional

DEFAULT_ENGINE_URL = "http://127.0.0.1:8000"
DECIDE_TIMEOUT_SECONDS = 5.0
PROVISION_TIMEOUT_SECONDS = 30.0

Post = Callable[..., dict]


def _engine_url() -> str:
    return os.environ.get("INTENTGUARD_URL", DEFAULT_ENGINE_URL).rstrip("/")


def _subject() -> str:
    return os.environ.get("INTENTGUARD_CC_SUBJECT") or f"user:{getpass.getuser()}"


def _fail_open() -> bool:
    raw = os.environ.get("INTENTGUARD_CC_FAIL_OPEN", "")
    return raw.strip().lower() in {"1", "true", "yes"}


def _post(
    path: str,
    payload: dict,
    timeout: float = DECIDE_TIMEOUT_SECONDS,
    token: Optional[str] = None,
) -> dict:
    url = _engine_url() + path
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"INTENTGUARD_URL must be http(s), got {url!r}")
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    # Scheme validated above; the URL is operator-configured, not user input.
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def build_decide_request(hook_input: dict) -> dict:
    return {
        "session_id": hook_input.get("session_id") or "claude-code-unknown",
        "subject": _subject(),
        "tool": hook_input.get("tool_name") or "",
        "arguments": hook_input.get("tool_input") or {},
    }


def verdict_to_hook_output(verdict: dict) -> dict:
    decision = verdict.get("decision")
    reason = verdict.get("reason", "")
    if decision == "allow":
        permission, message = "allow", f"IntentGuard: {reason}"
    elif decision == "escalate":
        permission = "ask"
        message = verdict.get("escalation_prompt") or (
            f"IntentGuard escalated this call for approval ({reason})."
        )
    else:
        permission = "deny"
        message = (
            f"IntentGuard denied this call ({reason}): it is outside the "
            "intent granted for this session."
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
            "permissionDecisionReason": message,
        }
    }


def _unreachable_output(error: Exception) -> dict:
    if _fail_open():
        permission = "allow"
        message = f"IntentGuard unreachable ({error}); INTENTGUARD_CC_FAIL_OPEN is set."
    else:
        permission = "deny"
        message = (
            f"IntentGuard engine unreachable ({error}); failing closed. "
            "Start the engine or set INTENTGUARD_URL."
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
            "permissionDecisionReason": message,
        }
    }


def pre_tool_use(stdin=None, stdout=None, post: Optional[Post] = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    post = post or _post
    try:
        hook_input = json.load(stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        json.dump(_unreachable_output(exc), stdout)
        return 0
    try:
        verdict = post("/v1/decide", build_decide_request(hook_input))
        output = verdict_to_hook_output(verdict)
    except Exception as exc:  # noqa: BLE001 - any transport failure fails closed
        output = _unreachable_output(exc)
    json.dump(output, stdout)
    return 0


def user_prompt_submit(stdin=None, stdout=None, post: Optional[Post] = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    post = post or _post
    try:
        hook_input = json.load(stdin)
        prompt = (hook_input.get("prompt") or "").strip()
        if not prompt:
            return 0
        payload = {
            "session_id": hook_input.get("session_id") or "claude-code-unknown",
            "subject": _subject(),
            "request_text": prompt,
        }
        resp = post(
            "/v1/sessions:parse",
            payload,
            timeout=PROVISION_TIMEOUT_SECONDS,
            token=os.environ.get("INTENTGUARD_PROVISIONING_TOKEN"),
        )
        grants = resp.get("grants_written")
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        f"IntentGuard provisioned {grants} grant(s) for this "
                        "session from your request; tool calls outside them "
                        "will be denied or escalated."
                    ),
                }
            },
            stdout,
        )
    except Exception as exc:  # noqa: BLE001 - never block the user's prompt
        print(f"intentguard: provisioning failed: {exc}", file=sys.stderr)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    event = args[0] if args else ""
    if event == "pre-tool-use":
        return pre_tool_use()
    if event == "user-prompt-submit":
        return user_prompt_submit()
    print(
        "usage: python -m adapters.claude_code.hooks {pre-tool-use|user-prompt-submit}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
