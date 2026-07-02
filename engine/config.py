"""Engine configuration.

A single ``mode`` flag flips the whole engine between ``observe`` (always allow,
log the would-be decision) and ``enforce`` (real decisions, fail closed).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from engine.pdp.registry import ToolRegistry, default_registry
from engine.schema import Mode

# Defaults shared by the dataclass fields and the env fallbacks below, so the
# two construction paths cannot drift.
DEFAULT_PDP_TIMEOUT_SECONDS = 2.0
DEFAULT_BACKEND = "memory"
DEFAULT_OPENFGA_API_URL = "http://localhost:8080"
DEFAULT_INTENT_PARSER = "mock"

_FALSY = {"0", "false", "no"}
_TRUTHY = {"1", "true", "yes"}


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var, preserving the flag's default when unset.

    A default-True flag stays True unless explicitly disabled (falsy value);
    a default-False flag stays False unless explicitly enabled (truthy value).
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    return value not in _FALSY if default else value in _TRUTHY


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
    pdp_timeout_seconds: float = DEFAULT_PDP_TIMEOUT_SECONDS
    backend: str = DEFAULT_BACKEND
    openfga_api_url: str = DEFAULT_OPENFGA_API_URL
    openfga_store_id: str | None = None
    openfga_model_id: str | None = None
    escalatable_tools: frozenset[str] = field(default_factory=frozenset)
    audit_path: str | None = None
    tool_registry: ToolRegistry = field(default_factory=default_registry)
    enforce_tool_allowlist: bool = True
    intent_parser: str = DEFAULT_INTENT_PARSER
    provisioning_token: str | None = None
    require_provisioning_auth: bool = False

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
                os.environ.get(
                    "INTENTGUARD_PDP_TIMEOUT_SECONDS",
                    str(DEFAULT_PDP_TIMEOUT_SECONDS),
                )
            ),
            backend=os.environ.get("INTENTGUARD_BACKEND", DEFAULT_BACKEND),
            openfga_api_url=os.environ.get(
                "INTENTGUARD_OPENFGA_API_URL", DEFAULT_OPENFGA_API_URL
            ),
            openfga_store_id=os.environ.get("INTENTGUARD_OPENFGA_STORE_ID") or None,
            openfga_model_id=os.environ.get("INTENTGUARD_OPENFGA_MODEL_ID") or None,
            escalatable_tools=frozenset(
                t.strip() for t in escalatable.split(",") if t.strip()
            ),
            audit_path=os.environ.get("INTENTGUARD_AUDIT_PATH") or None,
            tool_registry=registry,
            enforce_tool_allowlist=_env_bool(
                "INTENTGUARD_ENFORCE_TOOL_ALLOWLIST", default=True
            ),
            intent_parser=os.environ.get(
                "INTENTGUARD_INTENT_PARSER", DEFAULT_INTENT_PARSER
            ),
            provisioning_token=os.environ.get("INTENTGUARD_PROVISIONING_TOKEN") or None,
            require_provisioning_auth=_env_bool(
                "INTENTGUARD_REQUIRE_PROVISIONING_AUTH", default=False
            ),
        )
