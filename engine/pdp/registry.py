"""Known-tools registry: the allowlist + per-tool resource binding.

A single source of truth describing which tools exist, what each does, and which
argument carries the security-relevant resource (e.g. the email recipient). Two
things depend on it:

* the decision path uses it as an *allowlist* — a tool not in the registry is
  unknown and is denied by default (no silent pass-through), and to find which
  argument to bind into the grant identity;
* the intent parser (Milestone 2) will use it to validate extracted actions.

This module is pure data: it loads/validates a config file (JSON, or YAML when
PyYAML is available) with pydantic. It imports nothing from the writer, the LLM,
or any gateway.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator

# Bundled default registry, shipped with the package.
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("tools.json")


class ToolSpec(BaseModel):
    """Definition of a single known tool.

    ``resource_args`` lists the argument key(s) whose values, combined in order,
    form the security-relevant resource. Empty means the tool binds to any
    resource (e.g. ``calendar.read``). When non-empty, all listed arguments are
    required: a call missing one is denied (fail closed).

    For ergonomics and backward compatibility, a singular ``resource_arg``
    (string or null) is accepted in configs and coerced into ``resource_args``.
    """

    name: str = Field(..., min_length=1)
    description: str = ""
    resource_args: list[str] = Field(default_factory=list)

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _coerce_singular_resource_arg(cls, data: object) -> object:
        if isinstance(data, dict) and "resource_arg" in data:
            data = dict(data)
            val = data.pop("resource_arg")
            data.setdefault("resource_args", [val] if val else [])
        return data


class _RegistryFile(BaseModel):
    """On-disk shape of a registry config file."""

    tools: list[ToolSpec] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ToolRegistry:
    """An immutable collection of known tools, indexed by name."""

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._by_name: dict[str, ToolSpec] = {}
        for spec in specs:
            if spec.name in self._by_name:
                raise ValueError(f"duplicate tool in registry: {spec.name!r}")
            self._by_name[spec.name] = spec

    def is_known(self, tool: str) -> bool:
        """True if ``tool`` is in the allowlist."""
        return tool in self._by_name

    def spec(self, tool: str) -> Optional[ToolSpec]:
        return self._by_name.get(tool)

    def resource_args(self, tool: str) -> list[str]:
        """The argument key(s) forming ``tool``'s resource (empty = any/unknown)."""
        spec = self._by_name.get(tool)
        return list(spec.resource_args) if spec else []

    def resource_arg(self, tool: str) -> Optional[str]:
        """The first resource argument for ``tool``, or None (no resource/unknown)."""
        args = self.resource_args(tool)
        return args[0] if args else None

    def tool_names(self) -> list[str]:
        return list(self._by_name)

    # ── construction ────────────────────────────────────────────────────────
    @classmethod
    def from_specs(cls, specs: list[ToolSpec]) -> "ToolRegistry":
        return cls(specs)

    @classmethod
    def load(cls, path: str | Path) -> "ToolRegistry":
        """Load and validate a registry config file (JSON, or YAML if available)."""
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        data = _parse(text, path)
        parsed = _RegistryFile.model_validate(data)
        return cls(parsed.tools)


def _parse(text: str, path: Path) -> dict:
    """Parse JSON, falling back to YAML for .yaml/.yml when PyYAML is present."""
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"{path} is YAML but PyYAML is not installed; use JSON or "
                "`pip install pyyaml`."
            ) from exc
        return yaml.safe_load(text)
    return json.loads(text)


@functools.lru_cache(maxsize=1)
def default_registry() -> ToolRegistry:
    """The bundled default registry (cached; effectively immutable)."""
    return ToolRegistry.load(DEFAULT_REGISTRY_PATH)
