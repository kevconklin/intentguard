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
        # The three folders and the argument-validation explainer are present.
        assert "Parser lab" in page.text
        assert "invalid_arguments" in page.text

        for path in ("/demo/static/styles.css", "/demo/static/app.js"):
            asset = await client.get(path)
            assert asset.status_code == 200, path
        # The decision-reason explainer lives in the behavior file.
        js = await client.get("/demo/static/app.js")
        assert "invalid_arguments" in js.text

        reg = await client.get("/demo/registry")
        assert reg.status_code == 200
        tools = reg.json()["tools"]
        assert {"email.send", "http.get"} <= {t["name"] for t in tools}
        # Constraints are exposed so the UI can display them.
        assert any(t.get("arguments") for t in tools)


async def test_demo_eval_cases_route():
    app = _load_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        r = await client.get("/demo/eval/cases")
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert len(cases) >= 30
        assert {"id", "request", "tags", "expected"} <= set(cases[0])
        # The smoke subset the UI's fast button runs must exist.
        assert any("smoke" in c["tags"] for c in cases)


async def test_demo_eval_run_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = _load_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        r = await client.post("/demo/eval/run", json={"tag": "smoke"})
        assert r.status_code == 400
        assert "ANTHROPIC_API_KEY" in r.json()["detail"]
