"""A small, copy-pasteable IntentGuard client for your own agent.

This is the ONLY integration code you need. Drop this file into your project
(it depends on `httpx` only) and use it to:

  * provision()  — TOUCHPOINT A: fix the user's intent on the trusted request
  * decide()     — TOUCHPOINT B: authorize one tool call before it executes
  * audit()      — read the append-only decision log (observe-mode rollout)

The `connect()` helper lets every example in this folder run with NO server and
NO API key by spinning the engine up in-process. In your real project you'd talk
to a deployed engine instead: pass `engine_url=` or set INTENTGUARD_ENGINE_URL.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import httpx


class IntentDenied(Exception):
    """Raised by ``enforce()`` when a tool call is not within the user's intent."""

    def __init__(self, verdict: dict) -> None:
        self.verdict = verdict
        super().__init__(f"{verdict['decision']}: {verdict['reason']}")


class IntentGuardClient:
    """Thin async wrapper over the engine's HTTP contract."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    # ── TOUCHPOINT A ────────────────────────────────────────────────────────
    async def provision(
        self, session_id: str, subject: str, allowed_actions: list[dict]
    ) -> int:
        """Fix the permissions for a session from the user's trusted request.

        Call this ONCE, up front, BEFORE the agent processes any untrusted
        content. ``allowed_actions`` is a list of {"tool", "resource"} (resource
        may be null = "any resource for this tool"). Returns the grant count.
        """
        r = await self._http.post(
            "/v1/sessions",
            json={
                "session_id": session_id,
                "subject": subject,
                "allowed_actions": allowed_actions,
            },
        )
        r.raise_for_status()
        return r.json()["grants_written"]

    # ── TOUCHPOINT B ────────────────────────────────────────────────────────
    async def decide(
        self,
        session_id: str,
        subject: str,
        tool: str,
        arguments: dict,
        *,
        resource: Optional[str] = None,
        mode_override: Optional[str] = None,
    ) -> dict:
        """Authorize a single tool call. Returns the full DecideResponse dict."""
        body: dict[str, Any] = {
            "schema_version": "1",
            "session_id": session_id,
            "subject": subject,
            "tool": tool,
            "arguments": arguments,
        }
        if resource is not None:
            body["resource"] = resource
        if mode_override is not None:
            body["mode_override"] = mode_override
        r = await self._http.post("/v1/decide", json=body)
        r.raise_for_status()
        return r.json()

    async def enforce(self, *args: Any, **kwargs: Any) -> dict:
        """Like ``decide`` but raises ``IntentDenied`` unless the decision is allow."""
        verdict = await self.decide(*args, **kwargs)
        if verdict["decision"] != "allow":
            raise IntentDenied(verdict)
        return verdict

    async def audit(self, limit: int = 100) -> list[dict]:
        """Return recent append-only audit entries (newest last)."""
        r = await self._http.get("/v1/audit", params={"limit": limit})
        r.raise_for_status()
        return r.json()["entries"]


@asynccontextmanager
async def connect(
    engine_url: Optional[str] = None, *, mode: str = "enforce"
) -> AsyncIterator[IntentGuardClient]:
    """Yield an IntentGuardClient.

    * If ``engine_url`` (or env INTENTGUARD_ENGINE_URL) is set, talk to that
      deployed engine over HTTP — this is what you do in production.
    * Otherwise run the engine in-process (in-memory store, no network) so the
      examples are self-contained. Requires the ``engine`` package to be
      importable (``pip install -e .`` in this repo).
    """
    url = engine_url or os.environ.get("INTENTGUARD_ENGINE_URL")
    if url:
        async with httpx.AsyncClient(base_url=url) as http:
            yield IntentGuardClient(http)
        return

    try:
        from engine.api.app import create_app
        from engine.config import EngineConfig
        from engine.schema import Mode
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "In-process mode needs the IntentGuard engine installed "
            "(`pip install -e .`), or set INTENTGUARD_ENGINE_URL to a running engine."
        ) from exc

    app = create_app(config=EngineConfig(mode=Mode(mode), backend="memory"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://intentguard") as http:
        yield IntentGuardClient(http)
