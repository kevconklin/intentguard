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
        self._lock = threading.Lock()
        self._entries: list[AuditEntry] = []
        self._fh = None
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            # Append only: opened once in 'a'; the logger never seeks back or
            # truncates. Kept open for the logger's lifetime so each record
            # costs one write, not an open/close pair.
            self._fh = open(path, "a", encoding="utf-8")  # noqa: SIM115

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def record(self, **fields: Any) -> AuditEntry:
        """Append one entry. Returns the stored, immutable record."""
        entry = AuditEntry(timestamp=self._now(), **fields)
        with self._lock:
            self._entries.append(entry)
            if self._fh:
                self._fh.write(entry.to_json() + "\n")
                self._fh.flush()
        return entry

    def entries(self, limit: Optional[int] = None) -> list[AuditEntry]:
        """Return a copy of the in-memory mirror (newest last).

        ``limit`` returns only the newest N entries without copying the
        full history.
        """
        with self._lock:
            if limit is not None:
                return list(self._entries[-limit:])
            return list(self._entries)

    def close(self) -> None:
        """Release the on-disk handle (entries already written are untouched)."""
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None
