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

# Sentinel resource for tools that carry no security-relevant resource (or when
# a grant is intentionally scoped to "any resource").
ANY_RESOURCE = "*"

# Maps a tool to the argument key holding its security-relevant resource.
# Milestone 1 ships a small static registry; Milestone 2 should derive this
# from tool schemas / the intent parser. A tool absent here binds to ANY_RESOURCE.
TOOL_RESOURCE_ARG: dict[str, Optional[str]] = {
    "email.send": "to",
    "calendar.read": None,
    "calendar.create": "calendar",
    "http.get": "url",
    "file.read": "path",
    "file.write": "path",
}


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
) -> str:
    """Determine the resource a tool call targets.

    An explicit resource on the request wins. Otherwise the registry decides
    which argument is security-relevant. Unknown tools / missing arguments bind
    to ANY_RESOURCE.
    """
    if explicit_resource is not None and explicit_resource != "":
        return normalize_resource(explicit_resource)
    arg_key = TOOL_RESOURCE_ARG.get(tool, None)
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
