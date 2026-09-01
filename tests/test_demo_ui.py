"""The demo-UI server composes the engine app and serves the page + registry."""

from __future__ import annotations

import importlib.util
import pathlib

import httpx

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "examples" / "demo-ui" / "server.py"


def _load_app():
    spec = importlib.util.spec_from_file_location("demo_ui_server", SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


async def test_demo_ui_serves_page_and_registry():
    app = _load_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        page = await client.get("/")
        assert page.status_code == 200
        # The page documents the argument-validation gate for users.
        assert "invalid_arguments" in page.text

        reg = await client.get("/demo/registry")
        assert reg.status_code == 200
        tools = reg.json()["tools"]
        assert {"email.send", "http.get"} <= {t["name"] for t in tools}
        # Constraints are exposed so the UI can display them.
        assert any(t.get("arguments") for t in tools)
