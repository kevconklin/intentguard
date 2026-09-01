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
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

# Bundled default registry, shipped with the package.
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("tools.json")

# Argument value types an ``ArgSpec`` can require, and how to check each. bool is
# excluded from integer/number because ``True == 1`` in Python.
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, (list, tuple)),
}


class ArgSpec(BaseModel):
    """Optional per-argument constraint used for argument-level validation.

    A tool declares zero or more of these. A call is denied (``invalid_arguments``,
    fail closed) if a declared argument that is present violates its constraint,
    or a ``required`` argument is missing. Arguments NOT declared here are left
    untouched (e.g. an email ``body``), so validation never rejects extra fields.
    Resource-carrying arguments should be declared in ``resource_args`` (their
    presence is enforced via ``missing_resource``); use ``ArgSpec`` for shape.
    """

    name: str = Field(..., min_length=1)
    type: Optional[str] = None
    required: bool = False
    enum: Optional[list[Any]] = None
    pattern: Optional[str] = None
    max_length: Optional[int] = Field(default=None, ge=0)

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def _check_constraints(self) -> "ArgSpec":
        if self.type is not None and self.type not in _TYPE_CHECKS:
            raise ValueError(
                f"unknown argument type {self.type!r}; allowed: {sorted(_TYPE_CHECKS)}"
            )
        if self.pattern is not None:
            # A bad regex must fail at config-load time; raised at decision
            # time it would escape the fail-closed error handling.
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(
                    f"invalid regex pattern for argument {self.name!r}: {exc}"
                ) from exc
        return self


class ToolSpec(BaseModel):
    """Definition of a single known tool.

    ``resource_args`` lists the argument key(s) whose values, combined in order,
    form the security-relevant resource. Empty means the tool binds to any
    resource (e.g. ``calendar.read``). When non-empty, all listed arguments are
    required: a call missing one is denied (fail closed).

    ``arguments`` optionally declares per-argument shape constraints (type, enum,
    pattern, max length, required) validated before the store is consulted.

    For ergonomics and backward compatibility, a singular ``resource_arg``
    (string or null) is accepted in configs and coerced into ``resource_args``.
    """

    name: str = Field(..., min_length=1)
    description: str = ""
    resource_args: list[str] = Field(default_factory=list)
    arguments: list[ArgSpec] = Field(default_factory=list)

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _coerce_singular_resource_arg(cls, data: object) -> object:
        if isinstance(data, dict) and "resource_arg" in data:
            data = dict(data)
            val = data.pop("resource_arg")
            data.setdefault("resource_args", [val] if val else [])
        return data


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


# Hard upper bound on the length of any string fed to a declared pattern,
# regardless of ``max_length``: bounds regex cost on the decision hot path even
# against a pathological operator-supplied pattern (fail closed).
PATTERN_INPUT_CAP = 65536


def _pattern_violation(name: str, pattern: str, value: Any) -> Optional[str]:
    """Apply ``pattern`` to a string or a list of strings (fail closed).

    A declared pattern implies a string-valued argument: any other type is a
    ``wrong_type`` violation rather than a silent pass, so non-string values
    cannot bypass the shape check. List values (e.g. multiple recipients) must
    match element-wise.
    """
    if not isinstance(value, (str, list, tuple)):
        return f"wrong_type:{name}"
    items = [value] if isinstance(value, str) else value
    for item in items:
        if not isinstance(item, str):
            return f"pattern_mismatch:{name}"
        if len(item) > PATTERN_INPUT_CAP:
            return f"too_long:{name}"
        if re.fullmatch(pattern, item) is None:
            return f"pattern_mismatch:{name}"
    return None


def validate_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> Optional[str]:
    """Check a call's arguments against a tool's ``ArgSpec`` list.

    Returns ``None`` if every declared constraint holds, or a short,
    machine-friendly reason describing the first violation (fail closed). Pure
    and deterministic; a tool with no declared arguments always passes.
    ``max_length`` is checked before ``pattern`` so a declared length limit
    also bounds regex cost.
    """
    for arg in spec.arguments:
        present = arg.name in arguments and not _is_blank(arguments.get(arg.name))
        if not present:
            if arg.required:
                return f"missing_required:{arg.name}"
            continue
        value = arguments[arg.name]
        if arg.type is not None and not _TYPE_CHECKS[arg.type](value):
            return f"wrong_type:{arg.name}"
        if arg.enum is not None and value not in arg.enum:
            return f"not_in_enum:{arg.name}"
        if arg.max_length is not None and hasattr(value, "__len__"):
            if len(value) > arg.max_length:
                return f"too_long:{arg.name}"
        if arg.pattern is not None:
            violation = _pattern_violation(arg.name, arg.pattern, value)
            if violation is not None:
                return violation
    return None


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

    def resource_args(self, tool: str) -> list[str]:
        """The argument key(s) forming ``tool``'s resource (empty = any/unknown)."""
        spec = self._by_name.get(tool)
        return list(spec.resource_args) if spec else []

    def validate_arguments(self, tool: str, arguments: dict[str, Any]) -> Optional[str]:
        """Validate ``arguments`` against ``tool``'s declared ArgSpecs.

        Returns ``None`` when the tool is unknown (the allowlist gate owns that)
        or every constraint holds, else a short reason for the first violation.
        """
        spec = self._by_name.get(tool)
        return validate_arguments(spec, arguments) if spec else None

    def tool_names(self) -> list[str]:
        return list(self._by_name)

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
