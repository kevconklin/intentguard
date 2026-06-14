"""IntentGuard engine.

The engine core is completely gateway-agnostic: nothing here imports anything
from the ``adapters`` package or from any MCP / ContextForge library. Gateway
integration lives entirely in ``adapters``. This separation is enforced by an
automated test (see ``tests/test_architecture_invariants.py``).
"""
