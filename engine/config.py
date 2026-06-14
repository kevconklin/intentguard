"""Engine configuration.

A single ``mode`` flag flips the whole engine between ``observe`` (always allow,
log the would-be decision) and ``enforce`` (real decisions, fail closed).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from engine.pdp.registry import ToolRegistry, default_registry
from engine.schema import Mode


@dataclass(frozen=True)
class EngineConfig:
    """Immutable engine configuration.

    Attributes:
        mode: Default enforcement mode. Secure deployments use ``enforce``;
            the safe rollout default is ``observe``.
        pdp_timeout_seconds: Per-call budget for a policy-store read. On
            timeout in enforce mode the decision is deny (fail closed).
        backend: ``memory`` (default, no network) or ``openfga``.
        openfga_api_url / openfga_store_id / openfga_model_id: OpenFGA conn.
        escalatable_tools: Tools whose out-of-intent calls escalate to a human
            instead of being hard-denied.
        audit_path: Append-only JSONL audit log path. ``None`` keeps audit
            entries in memory only (used by tests).
        tool_registry: The known-tools allowlist + per-tool resource binding.
        enforce_tool_allowlist: When True (the secure default), a tool not in
            the registry is denied (reason ``unknown_tool``) before any store
            lookup. Set False to permit unregistered tool names.
    """

    mode: Mode = Mode.observe
    pdp_timeout_seconds: float = 2.0
    backend: str = "memory"
    openfga_api_url: str = "http://localhost:8080"
    openfga_store_id: str | None = None
    openfga_model_id: str | None = None
    escalatable_tools: frozenset[str] = field(default_factory=frozenset)
    audit_path: str | None = None
    tool_registry: ToolRegistry = field(default_factory=default_registry)
    enforce_tool_allowlist: bool = True

    @staticmethod
    def from_env() -> "EngineConfig":
        """Build config from environment variables.

        Defaults are intentionally safe: mode=observe, fail-closed timeout,
        in-memory backend.
        """
        mode = Mode(os.environ.get("INTENTGUARD_MODE", Mode.observe.value))
        escalatable = os.environ.get("INTENTGUARD_ESCALATABLE_TOOLS", "")
        registry_path = os.environ.get("INTENTGUARD_TOOL_REGISTRY_PATH") or None
        registry = (
            ToolRegistry.load(registry_path) if registry_path else default_registry()
        )
        return EngineConfig(
            mode=mode,
            pdp_timeout_seconds=float(
                os.environ.get("INTENTGUARD_PDP_TIMEOUT_SECONDS", "2.0")
            ),
            backend=os.environ.get("INTENTGUARD_BACKEND", "memory"),
            openfga_api_url=os.environ.get(
                "INTENTGUARD_OPENFGA_API_URL", "http://localhost:8080"
            ),
            openfga_store_id=os.environ.get("INTENTGUARD_OPENFGA_STORE_ID") or None,
            openfga_model_id=os.environ.get("INTENTGUARD_OPENFGA_MODEL_ID") or None,
            escalatable_tools=frozenset(
                t.strip() for t in escalatable.split(",") if t.strip()
            ),
            audit_path=os.environ.get("INTENTGUARD_AUDIT_PATH") or None,
            tool_registry=registry,
            enforce_tool_allowlist=os.environ.get(
                "INTENTGUARD_ENFORCE_TOOL_ALLOWLIST", "true"
            ).lower()
            not in {"0", "false", "no"},
        )
