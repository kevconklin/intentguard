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
from dataclasses import dataclass
from typing import Any, Optional

from engine.pdp.registry import ToolRegistry, default_registry

# Sentinel resource for tools that carry no security-relevant resource (or when
# a grant is intentionally scoped to "any resource").
ANY_RESOURCE = "*"

# Separator used to combine a compound resource's parts (in declared order).
# Provisioning a compound grant supplies the parts pre-joined with this.
RESOURCE_SEPARATOR = "&"


@dataclass(frozen=True)
class ResourceBinding:
    """The resource a call targets, plus whether it bound completely.

    ``complete`` is False when the tool declares required resource argument(s)
    but the call omitted one — a fail-closed condition the decision path denies.
    """

    resource: str
    complete: bool


def normalize_resource(value: Any) -> str:
    """Normalize an extracted argument value into a stable resource string."""
    if value is None:
        return ANY_RESOURCE
    if isinstance(value, (list, tuple, set)):
        parts = sorted(str(v).strip().lower() for v in value)
        return ",".join(parts) if parts else ANY_RESOURCE
    return str(value).strip().lower()


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def bind_resource(
    tool: str,
    arguments: dict[str, Any],
    explicit_resource: Optional[str] = None,
    registry: Optional[ToolRegistry] = None,
) -> ResourceBinding:
    """Resolve the resource a tool call targets, per the tool registry.

    * An explicit resource on the request always wins (complete).
    * A tool with no declared resource arguments binds to ANY_RESOURCE.
    * A tool with declared resource arguments binds the combination of their
      values (in declared order). If any declared argument is missing/blank the
      binding is incomplete — a fail-closed condition the decision path denies.
    """
    if explicit_resource is not None and explicit_resource != "":
        return ResourceBinding(normalize_resource(explicit_resource), True)

    reg = registry or default_registry()
    keys = reg.resource_args(tool)
    if not keys:
        return ResourceBinding(ANY_RESOURCE, True)

    parts: list[str] = []
    for key in keys:
        value = arguments.get(key)
        if _is_blank(value):
            return ResourceBinding(ANY_RESOURCE, False)
        parts.append(normalize_resource(value))
    resource = parts[0] if len(parts) == 1 else RESOURCE_SEPARATOR.join(parts)
    return ResourceBinding(resource, True)


def extract_resource(
    tool: str,
    arguments: dict[str, Any],
    explicit_resource: Optional[str] = None,
    registry: Optional[ToolRegistry] = None,
) -> str:
    """The resource string for a call (thin wrapper over ``bind_resource``)."""
    return bind_resource(tool, arguments, explicit_resource, registry).resource


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
