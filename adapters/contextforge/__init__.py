"""ContextForge (IBM mcp-context-forge) adapter for IntentGuard."""

from adapters.contextforge.verdict import EngineVerdict, verdict_to_result

__all__ = ["EngineVerdict", "verdict_to_result"]
