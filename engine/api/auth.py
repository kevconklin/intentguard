"""Authentication for the provisioning write path.

The provisioning endpoints (`POST /v1/sessions`, `POST /v1/sessions:parse`) are
the ONLY way to grant permissions. Only the trusted orchestrator should reach
them — never the agent, the LLM, or anything downstream of untrusted content.

This guards them with a shared-secret Bearer token, compared in constant time.
The read path (`/v1/decide`, `/v1/audit`, `/healthz`) is intentionally NOT
guarded by this and never gains a write capability.

Posture:
* token configured  -> writes require ``Authorization: Bearer <token>`` (else 401)
* no token, strict   -> writes refused (503) — fail closed for strict deployments
* no token, default  -> writes open, with a loud startup warning (dev convenience)
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException

from engine.config import EngineConfig

logger = logging.getLogger("intentguard.api")


def parse_bearer(authorization: Optional[str]) -> Optional[str]:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def warn_if_unauthenticated(config: EngineConfig) -> None:
    """Emit a startup warning when the write path is left unauthenticated."""
    if config.provisioning_token is None and not config.require_provisioning_auth:
        logger.warning(
            "provisioning write path is UNAUTHENTICATED. Set "
            "INTENTGUARD_PROVISIONING_TOKEN to require a token, or "
            "INTENTGUARD_REQUIRE_PROVISIONING_AUTH=true to fail closed."
        )


def make_provisioning_guard(config: EngineConfig):
    """Build a FastAPI dependency that authenticates provisioning requests."""

    async def guard(authorization: Optional[str] = Header(default=None)) -> None:
        token = config.provisioning_token
        if token is None:
            if config.require_provisioning_auth:
                # Strict mode but misconfigured: refuse rather than allow.
                raise HTTPException(
                    status_code=503,
                    detail="provisioning auth required but no token configured",
                )
            return  # dev default: open (a startup warning was logged)

        provided = parse_bearer(authorization)
        if provided is None or not hmac.compare_digest(provided, token):
            raise HTTPException(
                status_code=401,
                detail="invalid or missing provisioning token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return guard
