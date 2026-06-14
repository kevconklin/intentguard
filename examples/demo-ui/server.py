"""Tiny launcher that serves the IntentGuard demo UI.

Serves the single static page SAME-ORIGIN with the engine's HTTP API (so the
browser's fetch() calls hit /v1/decide etc. with no CORS setup). The engine runs
in-process with the in-memory backend — no OpenFGA, no API key, no LLM.

    python examples/demo-ui/server.py            # http://localhost:8000
    PORT=9000 python examples/demo-ui/server.py

This is an OPT-IN example. It does not modify the core engine; it just composes
the existing FastAPI app and adds one route to serve index.html.
"""

from __future__ import annotations

import os
import sys

import uvicorn
from fastapi.responses import FileResponse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from engine.api.app import create_app  # noqa: E402
from engine.config import EngineConfig  # noqa: E402
from engine.schema import Mode  # noqa: E402

HERE = os.path.dirname(__file__)

# Server default is observe (safe); the UI sends mode_override per call so the
# observe/enforce toggle works live without a restart.
app = create_app(config=EngineConfig(mode=Mode.observe, backend="memory"))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(HERE, "index.html"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"IntentGuard demo UI -> http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
