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
            "relations": {"principal": {"this": {}}},
            "metadata": {
                "relations": {
                    "principal": {
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
                "session": {"this": {}},
                "grantee": {"this": {}},
                # can_invoke is computed from the direct grantee relation.
                "can_invoke": {"computedUserset": {"relation": "grantee"}},
            },
            "metadata": {
                "relations": {
                    "session": {
                        "directly_related_user_types": [{"type": "session"}]
                    },
                    "grantee": {
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


def _client(api_url: str, store_id: str | None, model_id: str | None):
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
    from openfga_sdk import (
        ClientConfiguration,
        CreateStoreRequest,
        OpenFgaClient,
        WriteAuthorizationModelRequest,
    )

    async with OpenFgaClient(ClientConfiguration(api_url=api_url)) as client:
        store = await client.create_store(CreateStoreRequest(name=store_name))
        client.set_store_id(store.id)
        model = await client.write_authorization_model(
            WriteAuthorizationModelRequest(**AUTHORIZATION_MODEL)
        )
        return store.id, model.authorization_model_id


class OpenFgaPolicyStore:
    """Read-only OpenFGA queries (decision path)."""

    def __init__(self, api_url: str, store_id: str, model_id: str | None) -> None:
        self._api_url = api_url
        self._store_id = store_id
        self._model_id = model_id

    async def session_exists(self, session_id: str, subject: str) -> bool:
        from engine.pdp.model import session_object

        return await self._check(subject, "principal", session_object(session_id))

    async def check_grant(self, subject: str, grant_object_id: str) -> bool:
        return await self._check(subject, "can_invoke", grant_object_id)

    async def _check(self, user: str, relation: str, obj: str) -> bool:
        from openfga_sdk.client.models import ClientCheckRequest

        async with _client(self._api_url, self._store_id, self._model_id) as client:
            resp = await client.check(
                ClientCheckRequest(user=user, relation=relation, object=obj)
            )
            return bool(resp.allowed)


class OpenFgaPolicyWriter:
    """Write-only OpenFGA mutations (trusted provisioning path)."""

    def __init__(self, api_url: str, store_id: str, model_id: str | None) -> None:
        self._api_url = api_url
        self._store_id = store_id
        self._model_id = model_id

    async def write_grants(
        self, session_id: str, subject: str, grants: list[tuple[str, str]]
    ) -> None:
        from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

        writes = [
            ClientTuple(user=u, relation=r, object=o)
            for (u, r, o) in grant_tuples(session_id, subject, grants)
        ]
        async with _client(self._api_url, self._store_id, self._model_id) as client:
            await self._write_idempotent(client, ClientWriteRequest(writes=writes))

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
