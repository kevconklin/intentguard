"""The ``intentguard`` CLI: install the Claude Code integration.

    intentguard install --claude-code                  # edit ~/.claude/settings.json
    intentguard install --claude-code --project        # edit ./.claude/settings.json
    intentguard install --claude-code --with-prompt-hook
    intentguard install --claude-code --remove

The installer edits the settings file's ``hooks`` section idempotently: it adds
a PreToolUse entry (and optionally a UserPromptSubmit entry) that runs this
package's hook module with the *current* interpreter, never duplicates its own
entries, preserves everything else in the file, and writes a one-time ``.bak``
backup before the first change.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

# How we recognize our own hook entries in a settings file.
MARKER = "adapters.claude_code.hooks"

PRE_TOOL_USE_TIMEOUT = 10
PROMPT_SUBMIT_TIMEOUT = 60


def _hook_command(event: str) -> str:
    return f"{sys.executable} -m {MARKER} {event}"


def _is_ours(entry: dict) -> bool:
    return any(MARKER in h.get("command", "") for h in entry.get("hooks", []))


def _pre_tool_use_entry() -> dict:
    return {
        "matcher": ".*",
        "hooks": [
            {
                "type": "command",
                "command": _hook_command("pre-tool-use"),
                "timeout": PRE_TOOL_USE_TIMEOUT,
            }
        ],
    }


def _prompt_submit_entry() -> dict:
    return {
        "hooks": [
            {
                "type": "command",
                "command": _hook_command("user-prompt-submit"),
                "timeout": PROMPT_SUBMIT_TIMEOUT,
            }
        ]
    }


def _settings_path(args) -> Path:
    if args.settings:
        return Path(args.settings).expanduser()
    if args.project:
        return Path.cwd() / ".claude" / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"error: {path} is not valid JSON ({exc}); fix or remove it first."
        )


def _write_settings(path: Path, settings: dict, had_file: bool) -> None:
    if had_file:
        backup = path.with_suffix(".json.intentguard.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
            print(f"  backup written: {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _install(args) -> int:
    path = _settings_path(args)
    had_file = path.exists()
    settings = _load_settings(path)
    hooks = settings.setdefault("hooks", {})
    changed: list[str] = []

    pre = hooks.setdefault("PreToolUse", [])
    if not any(_is_ours(e) for e in pre):
        pre.append(_pre_tool_use_entry())
        changed.append("PreToolUse")

    if args.with_prompt_hook:
        prompts = hooks.setdefault("UserPromptSubmit", [])
        if not any(_is_ours(e) for e in prompts):
            prompts.append(_prompt_submit_entry())
            changed.append("UserPromptSubmit")

    if not changed:
        print(f"Already installed in {path} — nothing to do.")
        return 0

    _write_settings(path, settings, had_file)
    print(f"Installed IntentGuard hooks ({', '.join(changed)}) into {path}")
    print()
    print("Next steps:")
    print("  1. Start the engine (per-call decisions fail closed without it):")
    print(f"     export INTENTGUARD_TOOL_REGISTRY_PATH={_profile_path()}")
    print("     uvicorn engine.api.server:app        # observe mode by default")
    print("  2. Provision sessions before use — or rerun with --with-prompt-hook")
    print("     to parse each user prompt into grants automatically.")
    print("  3. Watch the ledger: GET /v1/audit. Flip INTENTGUARD_MODE=enforce")
    print("     once observe-mode telemetry looks right.")
    return 0


def _remove(args) -> int:
    path = _settings_path(args)
    if not path.exists():
        print(f"{path} does not exist — nothing to remove.")
        return 0
    settings = _load_settings(path)
    hooks = settings.get("hooks", {})
    removed = False
    for event in ("PreToolUse", "UserPromptSubmit"):
        entries = hooks.get(event)
        if not entries:
            continue
        kept = [e for e in entries if not _is_ours(e)]
        if len(kept) != len(entries):
            removed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if not removed:
        print(f"No IntentGuard hooks found in {path}.")
        return 0
    if not hooks:
        settings.pop("hooks", None)
    _write_settings(path, settings, had_file=True)
    print(f"Removed IntentGuard hooks from {path}")
    return 0


def _profile_path() -> Path:
    return Path(__file__).with_name("tools-claude-code.json")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intentguard", description="IntentGuard command-line tools."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="wire IntentGuard into an agent runtime")
    install.add_argument(
        "--claude-code",
        action="store_true",
        help="install the Claude Code PreToolUse hook",
    )
    install.add_argument(
        "--project",
        action="store_true",
        help="edit ./.claude/settings.json instead of ~/.claude/settings.json",
    )
    install.add_argument(
        "--settings", metavar="PATH", help="explicit settings.json path"
    )
    install.add_argument(
        "--with-prompt-hook",
        action="store_true",
        help="also provision sessions from each user prompt (UserPromptSubmit)",
    )
    install.add_argument(
        "--remove", action="store_true", help="remove IntentGuard's hook entries"
    )
    args = parser.parse_args(argv)

    if not args.claude_code:
        parser.error("specify an install target: --claude-code")
    return _remove(args) if args.remove else _install(args)


if __name__ == "__main__":
    raise SystemExit(main())
