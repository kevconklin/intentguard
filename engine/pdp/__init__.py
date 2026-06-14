"""Policy Decision Point.

Two strictly separated surfaces:

* ``PolicyStore`` — read-only. The per-call decision path depends ONLY on this.
  It has no write methods, so no decision-path code can mutate authorization.
* ``PolicyWriter`` — write-only. Reachable only from the trusted provisioning
  path (``engine.intent.provision``), which runs at session setup time, before
  any tool executes and before any untrusted content is processed.

This module deliberately does NOT import the writer, so that importing the
decision-path surface cannot pull in a write path.
"""

from engine.pdp.store import CheckOutcome, PolicyStore

__all__ = ["PolicyStore", "CheckOutcome"]
