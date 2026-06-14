"""FastAPI application: the engine's HTTP surface and composition root.

Endpoints:
* POST /v1/decide   - the per-call decision path (read-only, no LLM, fail closed)
* POST /v1/sessions - trusted provisioning: write a session's permission tuples
* GET  /v1/audit    - recent audit entries (for the demo / debugging)
* GET  /healthz     - liveness

The decision path and the provisioning path are wired here from separate
components: a read-only ``PolicyStore`` for decisions and a write-only
``PolicyWriter`` for provisioning. They never share a code path.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from engine.audit import AuditLogger
from engine.config import EngineConfig
from engine.core import decide
from engine.intent.base import AllowedAction, ParsedIntent
from engine.intent.provision import provision_session
from engine.schema import DecideRequest, DecideResponse


class ProvisionRequest(BaseModel):
    """Trusted session-seed request: the allowed actions for a session."""

    session_id: str
    subject: str
    allowed_actions: list[AllowedAction] = Field(default_factory=list)


class ProvisionResponse(BaseModel):
    session_id: str
    grants_written: int


def _build_default_backend(config: EngineConfig):
    """Construct (store, writer) for the configured backend.

    OpenFGA is imported lazily so the default in-memory path needs no SDK.
    """
    if config.backend == "openfga":
        if not config.openfga_store_id:
            raise RuntimeError(
                "backend=openfga requires INTENTGUARD_OPENFGA_STORE_ID "
                "(run engine.pdp.openfga.bootstrap first)."
            )
        from engine.pdp.openfga import OpenFgaPolicyStore, OpenFgaPolicyWriter

        store = OpenFgaPolicyStore(
            config.openfga_api_url, config.openfga_store_id, config.openfga_model_id
        )
        writer = OpenFgaPolicyWriter(
            config.openfga_api_url, config.openfga_store_id, config.openfga_model_id
        )
        return store, writer

    from engine.pdp.memory import make_memory_backend

    return make_memory_backend()


def create_app(
    config: Optional[EngineConfig] = None,
    store=None,
    writer=None,
    audit: Optional[AuditLogger] = None,
) -> FastAPI:
    """Create the engine app. Components are injectable for tests."""
    config = config or EngineConfig.from_env()
    audit = audit or AuditLogger(config.audit_path)
    if store is None or writer is None:
        default_store, default_writer = _build_default_backend(config)
        store = store or default_store
        writer = writer or default_writer

    app = FastAPI(title="IntentGuard", version="0.1.0")
    app.state.config = config
    app.state.audit = audit

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "mode": config.mode.value, "backend": config.backend}

    @app.post("/v1/decide", response_model=DecideResponse, response_model_exclude_none=True)
    async def decide_endpoint(request: DecideRequest) -> DecideResponse:
        return await decide(request, store, config, audit)

    @app.post("/v1/sessions", response_model=ProvisionResponse)
    async def provision_endpoint(request: ProvisionRequest) -> ProvisionResponse:
        # Trusted path: seed the session's permissions. In Milestone 1 these
        # come from config / the mock parser. The LLM/untrusted content never
        # reaches this endpoint.
        intent = ParsedIntent(
            session_id=request.session_id,
            subject=request.subject,
            allowed_actions=request.allowed_actions,
        )
        count = await provision_session(intent, writer)
        return ProvisionResponse(session_id=request.session_id, grants_written=count)

    @app.get("/v1/audit")
    async def audit_endpoint(limit: int = 100) -> dict:
        entries = [asdict(e) for e in audit.entries()[-limit:]]
        return {"count": len(entries), "entries": entries}

    return app
