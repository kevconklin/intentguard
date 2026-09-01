"""Tiny launcher that serves the IntentGuard demo UI.

Serves the single static page SAME-ORIGIN with the engine's HTTP API (so the
browser's fetch() calls hit /v1/decide etc. with no CORS setup). The engine runs
in-process with the in-memory backend — no OpenFGA, no API key, no LLM.

    python examples/demo-ui/server.py            # http://127.0.0.1:5050
    PORT=9000 python examples/demo-ui/server.py

The default port avoids 5000/7000, which macOS AirPlay Receiver occupies (it
answers 403 on IPv6 localhost, shadowing anything bound to IPv4 only).

This is an OPT-IN example. It does not modify the core engine; it just composes
the existing FastAPI app and adds one route to serve index.html.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from engine.api.app import create_app  # noqa: E402
from engine.config import EngineConfig  # noqa: E402
from engine.pdp.registry import DEFAULT_REGISTRY_PATH  # noqa: E402
from engine.schema import Mode  # noqa: E402
from evals.harness import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    check_thresholds,
    load_cases,
    run_eval,
)

HERE = os.path.dirname(__file__)
REPO_ROOT = Path(HERE).resolve().parents[1]


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


# Server default is observe (safe); the UI sends mode_override per call so the
# observe/enforce toggle works live without a restart.
app = create_app(config=EngineConfig(mode=Mode.observe, backend="memory"))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/demo/registry")
def registry() -> dict:
    """The bundled tool registry, so the UI can display each tool's declared
    resource binding and argument constraints. Demo-only route."""
    return json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))


@app.get("/demo/eval/cases")
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


@app.post("/demo/eval/run")
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
