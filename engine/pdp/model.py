"""Authorization model: grant identity and argument binding.

A permission is keyed on the combination of (session, tool, resource). The
security-relevant argument (e.g. an email recipient) is extracted from the raw
tool arguments and bound into the grant's identity, so "email.send to bob" is a
genuinely different grant from "email.send to anyone".

This module is pure and deterministic. It is shared by both the read path
(building the object id to check) and the write path (building the object id to
grant), guaranteeing they agree on identity.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from engine.pdp.registry import ToolRegistry, default_registry

# Sentinel resource for tools that carry no security-relevant resource (or when
# a grant is intentionally scoped to "any resource").
ANY_RESOURCE = "*"


def normalize_resource(value: Any) -> str:
    """Normalize an extracted argument value into a stable resource string."""
    if value is None:
        return ANY_RESOURCE
    if isinstance(value, (list, tuple, set)):
        parts = sorted(str(v).strip().lower() for v in value)
        return ",".join(parts) if parts else ANY_RESOURCE
    return str(value).strip().lower()


def extract_resource(
    tool: str,
    arguments: dict[str, Any],
    explicit_resource: Optional[str] = None,
    registry: Optional[ToolRegistry] = None,
) -> str:
    """Determine the resource a tool call targets.

    An explicit resource on the request wins. Otherwise the tool registry decides
    which argument is security-relevant. Unknown tools / missing arguments bind
    to ANY_RESOURCE (allowlist enforcement of unknown tools is a separate concern
    handled on the decision path).
    """
    if explicit_resource is not None and explicit_resource != "":
        return normalize_resource(explicit_resource)
    reg = registry or default_registry()
    arg_key = reg.resource_arg(tool)
    if arg_key is None:
        return ANY_RESOURCE
    return normalize_resource(arguments.get(arg_key))


def grant_key(session_id: str, tool: str, resource: str) -> str:
    """Stable, human-debuggable composite key for a grant."""
    return f"{session_id}|{tool}|{resource}"


def grant_object(session_id: str, tool: str, resource: str) -> str:
    """OpenFGA object id for a (session, tool, resource) grant.

    Hashed so the id is safe regardless of characters in the inputs (OpenFGA
    object ids must not contain whitespace and the ``type:id`` form is colon
    delimited). The readable key is preserved in the audit log.
    """
    digest = hashlib.sha256(
        grant_key(session_id, tool, resource).encode("utf-8")
    ).hexdigest()
    return f"grant:{digest}"


def session_object(session_id: str) -> str:
    return f"session:{session_id}"
