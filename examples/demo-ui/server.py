"""Tiny launcher that serves the IntentGuard demo UI.

Serves the single-page app SAME-ORIGIN with the engine's HTTP API (so the
browser's fetch() calls hit /v1/decide etc. with no CORS setup). The engine runs
in-process with the in-memory backend — no OpenFGA required.

    python examples/demo-ui/server.py            # http://127.0.0.1:5050
    PORT=9000 python examples/demo-ui/server.py

The default port avoids 5000/7000, which macOS AirPlay Receiver occupies (it
answers 403 on IPv6 localhost, shadowing anything bound to IPv4 only).

This is an OPT-IN example. It does not modify the core engine; it composes the
existing FastAPI app and adds demo-only routes. The Setup folder edits engine
configuration live by rebuilding the engine app with a new (still immutable)
EngineConfig and swapping it in — the same effect as a restart, minus the
process — while the in-memory policy store and audit log carry over.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from engine.api.app import create_app  # noqa: E402
from engine.audit import AuditLogger  # noqa: E402
from engine.config import EngineConfig  # noqa: E402
from engine.pdp.memory import make_memory_backend  # noqa: E402
from engine.pdp.registry import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    ToolRegistry,
    ToolSpec,
)
from engine.schema import Mode  # noqa: E402
from evals.harness import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    check_thresholds,
    load_cases,
    run_eval,
)

HERE = os.path.dirname(__file__)
REPO_ROOT = Path(HERE).resolve().parents[1]

# Long-lived components shared across engine rebuilds, so editing settings in
# the Setup folder never loses grants or audit history.
_STORE, _WRITER = make_memory_backend()
_AUDIT = AuditLogger()

# Mutable demo state: the current (immutable) config and the registry JSON the
# engine was built from.
STATE: dict = {
    "config": EngineConfig(mode=Mode.observe, backend="memory"),
    "registry_json": json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8")),
}


class SwappableApp:
    """ASGI wrapper delegating to the current engine app; swap() replaces it."""

    def __init__(self) -> None:
        self.inner: Optional[FastAPI] = None

    async def __call__(self, scope, receive, send):
        await self.inner(scope, receive, send)


app = SwappableApp()


def _load_dotenv() -> None:
    """Load repo-root ``.env`` (KEY=VALUE lines) into the environment.

    Lets the parser-eval panel find ANTHROPIC_API_KEY / ANTHROPIC_WORKSPACE_ID
    without shell exports. Existing environment variables win; the file is
    gitignored and never read outside this opt-in demo launcher.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _fmt(grant: tuple[str, Optional[str]]) -> str:
    return f"{grant[0]}→{grant[1] if grant[1] is not None else '*'}"


def _fmt_all(grants) -> list[str]:
    return [_fmt(g) for g in sorted(grants, key=lambda g: (g[0], g[1] or ""))]


def _config_view() -> dict:
    """The Setup folder's view of the engine config (no secret values)."""
    c: EngineConfig = STATE["config"]
    return {
        # Live-editable (applied by rebuilding the engine app in place):
        "mode": c.mode.value,
        "pdp_timeout_seconds": c.pdp_timeout_seconds,
        "enforce_tool_allowlist": c.enforce_tool_allowlist,
        "trust_explicit_resource": c.trust_explicit_resource,
        "escalatable_tools": sorted(c.escalatable_tools),
        "intent_parser": c.intent_parser,
        "require_provisioning_auth": c.require_provisioning_auth,
        "provisioning_token_set": c.provisioning_token is not None,
        # Fixed for the life of this demo process (env-only in a deployment):
        "backend": c.backend,
        "audit_path": c.audit_path,
        "anthropic_key_loaded": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


def _apply_config(new_config: EngineConfig) -> None:
    """Build a fresh engine app from ``new_config`` and swap it in.

    Store, writer, and audit are injected so state survives the swap; the
    provisioning guard and the intent parser are rebuilt from the new config.
    """
    STATE["config"] = new_config
    inner = create_app(config=new_config, store=_STORE, writer=_WRITER, audit=_AUDIT)
    _add_demo_routes(inner)
    app.inner = inner


def _add_demo_routes(engine_app: FastAPI) -> None:
    @engine_app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(HERE, "index.html"))

    @engine_app.get("/demo/registry")
    def registry() -> dict:
        """The registry the engine is currently running with."""
        return STATE["registry_json"]

    @engine_app.get("/demo/config")
    def get_config() -> dict:
        return _config_view()

    @engine_app.post("/demo/config")
    def set_config(body: Optional[dict] = None) -> dict:
        """Apply live engine settings (demo-only; env vars in a deployment)."""
        b = body or {}
        c: EngineConfig = STATE["config"]
        changes: dict = {}
        if "mode" in b:
            try:
                changes["mode"] = Mode(b["mode"])
            except ValueError:
                raise HTTPException(400, "mode must be one of: observe, enforce")
        if "pdp_timeout_seconds" in b:
            try:
                timeout = float(b["pdp_timeout_seconds"])
            except (TypeError, ValueError):
                raise HTTPException(400, "pdp_timeout_seconds must be a number")
            if not 0 < timeout <= 60:
                raise HTTPException(400, "pdp_timeout_seconds must be in (0, 60]")
            changes["pdp_timeout_seconds"] = timeout
        for flag in (
            "enforce_tool_allowlist",
            "trust_explicit_resource",
            "require_provisioning_auth",
        ):
            if flag in b:
                if not isinstance(b[flag], bool):
                    raise HTTPException(400, f"{flag} must be true or false")
                changes[flag] = b[flag]
        if "escalatable_tools" in b:
            tools = b["escalatable_tools"]
            if not isinstance(tools, list) or not all(
                isinstance(t, str) for t in tools
            ):
                raise HTTPException(400, "escalatable_tools must be a list of names")
            changes["escalatable_tools"] = frozenset(
                t.strip() for t in tools if t.strip()
            )
        if "intent_parser" in b:
            if b["intent_parser"] not in ("mock", "anthropic"):
                raise HTTPException(400, "intent_parser must be 'mock' or 'anthropic'")
            changes["intent_parser"] = b["intent_parser"]
        if "provisioning_token" in b:
            token = b["provisioning_token"]
            if token is not None and not isinstance(token, str):
                raise HTTPException(400, "provisioning_token must be a string or null")
            changes["provisioning_token"] = (token or "").strip() or None
        _apply_config(dataclasses.replace(c, **changes))
        return _config_view()

    @engine_app.post("/demo/config/registry")
    def set_registry(body: dict) -> dict:
        """Validate and apply a new tool registry live."""
        tools = body.get("tools")
        if not isinstance(tools, list):
            raise HTTPException(400, 'registry rejected: expected {"tools": [...]}')
        try:
            new_registry = ToolRegistry([ToolSpec.model_validate(t) for t in tools])
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, f"registry rejected: {exc}")
        STATE["registry_json"] = body
        _apply_config(dataclasses.replace(STATE["config"], tool_registry=new_registry))
        return {"tools": new_registry.tool_names(), "applied": True}

    @engine_app.get("/demo/eval/cases")
    def eval_cases() -> dict:
        """The labeled parse-quality cases, so the UI can list and count them."""
        return {
            "cases": [
                {
                    "id": c.id,
                    "request": c.request,
                    "tags": list(c.tags),
                    "expected": _fmt_all(c.expected),
                    "forbidden": _fmt_all(c.forbidden),
                }
                for c in load_cases()
            ]
        }

    @engine_app.post("/demo/eval/run")
    async def eval_run(body: Optional[dict] = None) -> dict:
        """Run the parse-quality eval live against the Anthropic parser.

        Same cases, scoring, and thresholds as `python -m evals.harness`.
        Demo-only route; requires ANTHROPIC_API_KEY on the server.
        """
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(
                status_code=400,
                detail="ANTHROPIC_API_KEY is not set on the server. Add it to .env "
                "in the repo root (with ANTHROPIC_WORKSPACE_ID for identity-linked "
                "keys) and restart the demo.",
            )
        from engine.intent.anthropic import AnthropicIntentParser

        tag = (body or {}).get("tag")
        cases = load_cases()
        if tag:
            cases = [c for c in cases if tag in c.tags]
        results, metrics = await run_eval(AnthropicIntentParser(), cases)
        return {
            "cases": [
                {
                    "id": r.case.id,
                    "tags": list(r.case.tags),
                    "ok": not (r.fp or r.fn or r.leaks),
                    "tp": r.tp,
                    "fp": r.fp,
                    "fn": r.fn,
                    "missed": _fmt_all(r.case.expected - r.produced),
                    "extra": _fmt_all(r.produced - r.case.expected),
                    "leaks": _fmt_all(r.leaks),
                    "overbroad": _fmt_all(r.overbroad),
                }
                for r in sorted(results, key=lambda r: r.case.id)
            ],
            "metrics": dataclasses.asdict(metrics),
            "thresholds": DEFAULT_THRESHOLDS,
            "failures": check_thresholds(metrics),
        }

    engine_app.mount(
        "/demo/static",
        StaticFiles(directory=os.path.join(HERE, "static")),
        name="demo_static",
    )


# Build the initial engine app.
_apply_config(STATE["config"])


if __name__ == "__main__":
    _load_dotenv()
    port = int(os.environ.get("PORT", "5050"))
    eval_ready = bool(os.environ.get("ANTHROPIC_API_KEY"))
    # Print the numeric address: on macOS, "localhost" can resolve to ::1 and
    # hit another service (e.g. AirPlay on 5000/7000) instead of this server.
    print(f"IntentGuard demo UI -> http://127.0.0.1:{port}")
    print(
        "parser-eval panel: live (key loaded)"
        if eval_ready
        else "parser-eval panel: disabled (no ANTHROPIC_API_KEY in env or .env)"
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
