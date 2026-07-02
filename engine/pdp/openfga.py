"""OpenFGA-backed policy store + writer (the production backend).

Uses the official ``openfga-sdk`` async client. ``openfga_sdk`` is imported
lazily inside functions so the rest of the engine (and the whole test suite)
runs without the dependency installed.

Verified against openfga-sdk 0.10.x:
  * check:  ClientCheckRequest(user, relation, object) -> .allowed
  * write:  ClientWriteRequest(writes=[ClientTuple(user, relation, object)])
  * model:  WriteAuthorizationModelRequest(schema_version, type_definitions)
See https://github.com/openfga/python-sdk and https://openfga.dev/docs.
"""

from __future__ import annotations

from typing import Any

from engine.pdp.model import (
    REL_CAN_INVOKE,
    REL_GRANTEE,
    REL_PRINCIPAL,
    REL_SESSION,
    session_object,
)
from engine.pdp.writer import grant_tuples

# The authorization model, mirrored from engine/pdp/model.fga (DSL). OpenFGA's
# API accepts only JSON, so the JSON form is authoritative for the SDK call.
# Permissions are keyed on (session, tool, resource) via the grant object id;
# this model expresses "subject can_invoke grant" and "grant belongs to session".
AUTHORIZATION_MODEL: dict[str, Any] = {
    "schema_version": "1.1",
    "type_definitions": [
        {"type": "user"},
        {"type": "agent"},
        {
            "type": "session",
            "relations": {REL_PRINCIPAL: {"this": {}}},
            "metadata": {
                "relations": {
                    REL_PRINCIPAL: {
                        "directly_related_user_types": [
                            {"type": "user"},
                            {"type": "agent"},
                        ]
                    }
                }
            },
        },
        {
            "type": "grant",
            "relations": {
                REL_SESSION: {"this": {}},
                REL_GRANTEE: {"this": {}},
                # can_invoke is computed from the direct grantee relation.
                REL_CAN_INVOKE: {"computedUserset": {"relation": REL_GRANTEE}},
            },
            "metadata": {
                "relations": {
                    REL_SESSION: {"directly_related_user_types": [{"type": "session"}]},
                    REL_GRANTEE: {
                        "directly_related_user_types": [
                            {"type": "user"},
                            {"type": "agent"},
                        ]
                    },
                }
            },
        },
    ],
}


def _client(api_url: str, store_id: str | None = None, model_id: str | None = None):
    from openfga_sdk import ClientConfiguration, OpenFgaClient

    config = ClientConfiguration(
        api_url=api_url, store_id=store_id, authorization_model_id=model_id
    )
    return OpenFgaClient(config)


async def bootstrap(api_url: str, store_name: str = "intentguard") -> tuple[str, str]:
    """Create a store and write the authorization model.

    Returns ``(store_id, authorization_model_id)``. Run once; persist the ids
    into the engine config (env vars) for subsequent runs.
    """
    from openfga_sdk import CreateStoreRequest, WriteAuthorizationModelRequest

    async with _client(api_url) as client:
        store = await client.create_store(CreateStoreRequest(name=store_name))
        client.set_store_id(store.id)
        model = await client.write_authorization_model(
            WriteAuthorizationModelRequest(**AUTHORIZATION_MODEL)
        )
        return store.id, model.authorization_model_id


class _OpenFgaBase:
    """Shared connection handling: one long-lived SDK client per instance.

    The client (and its connection pool) is created lazily on first use and
    reused for every call — constructing one per request would pay a fresh
    TCP handshake on the hot decision path. ``close()`` releases it; the API
    layer calls it on shutdown.
    """

    def __init__(self, api_url: str, store_id: str, model_id: str | None) -> None:
        self._api_url = api_url
        self._store_id = store_id
        self._model_id = model_id
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = _client(self._api_url, self._store_id, self._model_id)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class OpenFgaPolicyStore(_OpenFgaBase):
    """Read-only OpenFGA queries (decision path)."""

    async def session_exists(self, session_id: str, subject: str) -> bool:
        return await self._check(subject, REL_PRINCIPAL, session_object(session_id))

    async def check_grant(self, subject: str, grant_object_id: str) -> bool:
        return await self._check(subject, REL_CAN_INVOKE, grant_object_id)

    async def _check(self, user: str, relation: str, obj: str) -> bool:
        from openfga_sdk.client.models import ClientCheckRequest

        resp = await self._get_client().check(
            ClientCheckRequest(user=user, relation=relation, object=obj)
        )
        return bool(resp.allowed)


class OpenFgaPolicyWriter(_OpenFgaBase):
    """Write-only OpenFGA mutations (trusted provisioning path)."""

    async def write_grants(
        self, session_id: str, subject: str, grants: list[tuple[str, str]]
    ) -> None:
        from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

        writes = [
            ClientTuple(user=u, relation=r, object=o)
            for (u, r, o) in grant_tuples(session_id, subject, grants)
        ]
        await self._write_idempotent(
            self._get_client(), ClientWriteRequest(writes=writes)
        )

    @staticmethod
    async def _write_idempotent(client, request) -> None:
        """Write, tolerating already-existing tuples.

        OpenFGA writes are transactional and reject duplicate tuples on older
        servers (error code ``write_failed_due_to_invalid_input``). We retry
        each tuple individually and ignore duplicates so provisioning is
        idempotent across servers that lack ``on_duplicate: ignore``.
        """
        from openfga_sdk.client.models import ClientWriteRequest
        from openfga_sdk.exceptions import ApiException

        try:
            await client.write(request)
        except ApiException:
            for t in request.writes:
                try:
                    await client.write(ClientWriteRequest(writes=[t]))
                except ApiException:
                    # Tuple already exists -> idempotent no-op.
                    continue
