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


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def test_demo_config_get_and_live_apply():
    app = _load_app()
    async with _client(app) as client:
        cfg = (await client.get("/demo/config")).json()
        assert cfg["mode"] == "observe"
        assert cfg["backend"] == "memory"
        assert cfg["provisioning_token_set"] is False

        # Apply live settings: the engine app is rebuilt and swapped in place.
        r = await client.post(
            "/demo/config",
            json={"mode": "enforce", "escalatable_tools": ["email.send"]},
        )
        assert r.status_code == 200
        assert r.json()["mode"] == "enforce"
        assert r.json()["escalatable_tools"] == ["email.send"]
        health = (await client.get("/healthz")).json()
        assert health["mode"] == "enforce"


async def test_demo_config_swap_preserves_state():
    app = _load_app()
    async with _client(app) as client:
        await client.post(
            "/v1/sessions",
            json={
                "session_id": "s-cfg",
                "subject": "user:a",
                "allowed_actions": [
                    {"tool": "email.send", "resource": "bob@example.com"}
                ],
            },
        )
        await client.post("/demo/config", json={"mode": "enforce"})
        # Grants written before the swap still authorize calls after it.
        r = await client.post(
            "/v1/decide",
            json={
                "session_id": "s-cfg",
                "subject": "user:a",
                "tool": "email.send",
                "arguments": {"to": "bob@example.com"},
            },
        )
        assert r.json()["decision"] == "allow"


async def test_demo_config_rejects_bad_values():
    app = _load_app()
    async with _client(app) as client:
        assert (
            await client.post("/demo/config", json={"mode": "yolo"})
        ).status_code == 400
        assert (
            await client.post("/demo/config", json={"pdp_timeout_seconds": -1})
        ).status_code == 400


async def test_demo_registry_apply_and_reject():
    app = _load_app()
    async with _client(app) as client:
        bad = {
            "tools": [{"name": "t", "arguments": [{"name": "x", "pattern": "[oops"}]}]
        }
        r = await client.post("/demo/config/registry", json=bad)
        assert r.status_code == 400
        assert "registry rejected" in r.json()["detail"]

        good = {"tools": [{"name": "slack.post", "resource_arg": "channel"}]}
        r = await client.post("/demo/config/registry", json=good)
        assert r.status_code == 200
        assert r.json()["tools"] == ["slack.post"]
        # The registry route now serves the live registry, not the bundled file.
        assert (await client.get("/demo/registry")).json() == good
        # The engine actually runs with it: email.send is now unknown.
        d = await client.post(
            "/v1/decide",
            json={
                "session_id": "s",
                "subject": "user:a",
                "tool": "email.send",
                "arguments": {"to": "b@x.com"},
                "mode_override": "enforce",
            },
        )
        assert d.json()["reason"] == "unknown_tool"


async def test_demo_provisioning_token_enforced_live():
    app = _load_app()
    async with _client(app) as client:
        await client.post("/demo/config", json={"provisioning_token": "sekrit"})
        body = {"session_id": "s2", "subject": "user:a", "allowed_actions": []}
        assert (await client.post("/v1/sessions", json=body)).status_code == 401
        ok = await client.post(
            "/v1/sessions", json=body, headers={"authorization": "Bearer sekrit"}
        )
        assert ok.status_code == 200


async def test_demo_eval_run_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = _load_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        r = await client.post("/demo/eval/run", json={"tag": "smoke"})
        assert r.status_code == 400
        assert "ANTHROPIC_API_KEY" in r.json()["detail"]
