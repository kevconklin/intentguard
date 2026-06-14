"""Append-only audit logger.

Every decision produces exactly one immutable entry. On-disk entries are written
as one JSON object per line (JSONL) in append mode; the logger never rewrites or
deletes prior lines. An in-memory mirror supports the demo and tests.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class AuditEntry:
    """One immutable audit record."""

    decision_id: str
    timestamp: str
    session_id: str
    subject: str
    tool: str
    resource: str
    grant_key: str
    decision: str
    reason: str
    effective_mode: str
    would_have_decided: Optional[str]
    owasp_threats: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


class AuditLogger:
    """Thread-safe append-only logger with an in-memory mirror."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._entries: list[AuditEntry] = []
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def record(self, **fields: Any) -> AuditEntry:
        """Append one entry. Returns the stored, immutable record."""
        entry = AuditEntry(timestamp=self._now(), **fields)
        with self._lock:
            self._entries.append(entry)
            if self._path:
                # Append only: open in 'a', write one line, flush. Never seek
                # back or truncate.
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(entry.to_json() + "\n")
        return entry

    def entries(self) -> list[AuditEntry]:
        """Return a copy of the in-memory mirror (newest last)."""
        with self._lock:
            return list(self._entries)
