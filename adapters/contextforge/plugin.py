"""IntentGuard ContextForge plugin.

A native ``cpex.framework.Plugin`` subclass that, on ``tool_pre_invoke``, builds
an engine decide request, calls the IntentGuard engine over HTTP, and acts on
the verdict: allow lets the call proceed, deny blocks it, escalate raises a
human-approval prompt.

Deployment: run this inside the cpex external-plugin MCP runtime, then register
it in the gateway's plugins config with ``kind: external`` and an ``mcp`` block
pointing at that runtime (see config.yaml). The engine itself is a separate
service the plugin reaches over HTTP.
"""

from __future__ import annotations

from typing import Any

import httpx

from adapters.contextforge._cpex import (
    Plugin,
    PluginContext,
    ToolPreInvokePayload,
    ToolPreInvokeResult,
)
from adapters.contextforge.verdict import EngineVerdict, verdict_to_result

DEFAULT_ENGINE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 5.0


def _plugin_setting(plugin_config: Any, key: str, default: Any) -> Any:
    """Read a plugin-specific setting from the cpex PluginConfig.config dict."""
    cfg = getattr(plugin_config, "config", None) or {}
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return default


def _resolve_session_and_subject(
    context: PluginContext, session_state_key: str, subject_prefix: str
) -> tuple[str, str]:
    """Derive the IntentGuard session id and subject from the gateway context.

    The session id should be the stable conversation/parsed-intent id. We look
    in plugin state first, then fall back to the request id. The subject is the
    authenticated user, prefixed to the engine's subject convention.
    """
    state = getattr(context, "state", {}) or {}
    gctx = getattr(context, "global_context", None)
    session_id = state.get(session_state_key) or getattr(gctx, "request_id", "unknown")

    user = getattr(context, "user_email", None) or getattr(gctx, "user", None) or "unknown"
    subject = user if ":" in str(user) else f"{subject_prefix}:{user}"
    return str(session_id), subject


class IntentGuardPlugin(Plugin):
    """Authorize each tool call against the user's parsed intent."""

    def __init__(self, config: Any = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(config, *args, **kwargs)
        pc = getattr(self, "config", config)
        self._engine_url = _plugin_setting(pc, "engine_url", DEFAULT_ENGINE_URL).rstrip("/")
        self._timeout = float(_plugin_setting(pc, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self._session_state_key = _plugin_setting(pc, "session_state_key", "intentguard_session_id")
        self._subject_prefix = _plugin_setting(pc, "subject_prefix", "user")
        # Fail closed by default: if the engine is unreachable, block the call.
        self._fail_open = bool(_plugin_setting(pc, "fail_open", False))

    async def tool_pre_invoke(
        self, payload: ToolPreInvokePayload, context: PluginContext, *args: Any
    ) -> ToolPreInvokeResult:
        session_id, subject = _resolve_session_and_subject(
            context, self._session_state_key, self._subject_prefix
        )
        decide_request = {
            "schema_version": "1",
            "session_id": session_id,
            "subject": subject,
            "tool": payload.name,
            "arguments": dict(getattr(payload, "args", {}) or {}),
        }

        try:
            verdict = await self._call_engine(decide_request)
        except Exception as exc:  # noqa: BLE001 - transport failure
            return self._on_engine_error(payload, exc)

        return verdict_to_result(verdict, payload)

    async def _call_engine(self, decide_request: dict) -> EngineVerdict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._engine_url}/v1/decide", json=decide_request
            )
            resp.raise_for_status()
            data = resp.json()
        return EngineVerdict(
            decision=data["decision"],
            reason=data["reason"],
            decision_id=data["decision_id"],
            escalation_prompt=data.get("escalation_prompt"),
        )

    def _on_engine_error(
        self, payload: ToolPreInvokeResult, exc: Exception
    ) -> ToolPreInvokeResult:
        """Engine unreachable. Fail closed (block) unless fail_open is set."""
        if self._fail_open:
            return ToolPreInvokeResult(
                continue_processing=True,
                metadata={"intentguard_error": str(exc), "intentguard_fail_open": True},
            )
        verdict = EngineVerdict(
            decision="deny",
            reason="pdp_error_failclosed",
            decision_id="engine_unreachable",
        )
        return verdict_to_result(verdict, payload)
