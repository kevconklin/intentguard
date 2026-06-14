"""ContextForge plugin framework imports, with an isolated shim fallback.

The real interface lives in the ``cpex`` package (``cpex.framework``), which the
gateway pins as ``cpex>=0.1.1`` (the framework was extracted out of
``mcpgateway.plugins.framework`` into ``cpex``). We import the real types when
available so the adapter matches the live interface exactly.

When ``cpex`` is not installed (e.g. in CI running only the engine tests), we
fall back to minimal local stand-ins with the SAME field/method names, so the
adapter's verdict-mapping logic stays importable and unit-testable.

TODO: validate field names against the installed cpex version before deploying
behind a real gateway. Confirmed against cpex 0.1.1:
  * Plugin base class, async tool_pre_invoke(payload, context) -> result
  * payload.name, payload.args
  * PluginResult.continue_processing (True=allow, False=block),
    .violation, .modified_payload, .metadata
  * PluginViolation(reason, description, code, details, ...)
"""

from __future__ import annotations

try:  # pragma: no cover - exercised only when cpex is installed
    from cpex.framework import (  # type: ignore
        Plugin,
        PluginConfig,
        PluginContext,
        ToolPreInvokePayload,
        ToolPreInvokeResult,
    )
    from cpex.framework.models import PluginViolation  # type: ignore

    CPEX_AVAILABLE = True
except Exception:  # ImportError or any cpex import-time error -> use the shim.
    CPEX_AVAILABLE = False

    from dataclasses import dataclass, field
    from typing import Any, Optional

    @dataclass
    class PluginViolation:  # type: ignore[no-redef]
        reason: str
        description: str
        code: str
        details: Optional[dict] = None

    @dataclass
    class ToolPreInvokePayload:  # type: ignore[no-redef]
        name: str
        args: dict[str, Any] = field(default_factory=dict)

        def model_copy(self, update: dict | None = None) -> "ToolPreInvokePayload":
            data = {"name": self.name, "args": dict(self.args)}
            data.update(update or {})
            return ToolPreInvokePayload(**data)

    @dataclass
    class ToolPreInvokeResult:  # type: ignore[no-redef]
        continue_processing: bool = True
        modified_payload: Optional[ToolPreInvokePayload] = None
        violation: Optional[PluginViolation] = None
        metadata: dict[str, Any] = field(default_factory=dict)

    class _GlobalContext:
        def __init__(self, request_id: str = "unknown", user: Optional[str] = None):
            self.request_id = request_id
            self.user = user

    @dataclass
    class PluginContext:  # type: ignore[no-redef]
        global_context: _GlobalContext = field(default_factory=_GlobalContext)
        state: dict[str, Any] = field(default_factory=dict)
        metadata: dict[str, Any] = field(default_factory=dict)

        @property
        def user_email(self) -> Optional[str]:
            return self.global_context.user

    @dataclass
    class PluginConfig:  # type: ignore[no-redef]
        name: str = "intentguard"
        kind: str = "external"
        config: dict[str, Any] = field(default_factory=dict)

    class Plugin:  # type: ignore[no-redef]
        """Minimal stand-in for cpex.framework.Plugin."""

        def __init__(self, config: "PluginConfig | None" = None, *args, **kwargs):
            self._config = config or PluginConfig()

        @property
        def config(self) -> "PluginConfig":
            return self._config


__all__ = [
    "CPEX_AVAILABLE",
    "Plugin",
    "PluginConfig",
    "PluginContext",
    "PluginViolation",
    "ToolPreInvokePayload",
    "ToolPreInvokeResult",
]
