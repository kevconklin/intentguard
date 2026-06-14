"""Architecture-invariant tests.

These encode the non-negotiable security invariants as executable checks:

  1. The engine package is gateway-agnostic: it imports nothing from the
     adapters package and no MCP/ContextForge library.
  2. The per-call decide() path consults no LLM and cannot reach a write path.
  3. The LLM / untrusted input has no write path to the policy store: among the
     intent parsers, only the trusted provisioning module imports the writer.
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engine"

# Top-level modules the engine must never import.
FORBIDDEN_ROOTS = {"adapters", "cpex", "mcpgateway", "mcp", "openfga_sdk", "anthropic"}


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: pathlib.Path) -> list[str]:
    """Return the fully-qualified module names imported by a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                mods.append(node.module)
    return mods


def _engine_files() -> list[pathlib.Path]:
    return sorted(ENGINE_DIR.rglob("*.py"))


def _lazy_imported_in_function(path: pathlib.Path, root: str) -> bool:
    """True if every import of ``root`` in the file is inside a function body.

    The OpenFGA backend imports openfga_sdk lazily (inside methods) so the
    dependency is optional; that is allowed. A module-level import is not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_level = set()
    for node in tree.body:  # only top-level statements
        if isinstance(node, ast.Import):
            module_level.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level.add(node.module.split(".")[0])
    return root not in module_level


def test_engine_does_not_import_adapters_or_mcp():
    """Invariant 1: engine core is gateway-agnostic."""
    violations: list[str] = []
    for path in _engine_files():
        for mod in _imports(path):
            root = mod.split(".")[0]
            if root not in FORBIDDEN_ROOTS:
                continue
            # Heavy optional dependencies (the policy store and the LLM) are
            # permitted ONLY as lazy, in-function imports in their dedicated
            # modules. The decision path's inability to reach the parser/LLM is
            # enforced separately by test_decide_path_cannot_reach_writer_or_llm.
            if root == "openfga_sdk" and path.name in {"openfga.py", "bootstrap.py"}:
                if _lazy_imported_in_function(path, "openfga_sdk"):
                    continue
            if root == "anthropic" and path.name == "anthropic.py":
                if _lazy_imported_in_function(path, "anthropic"):
                    continue
            violations.append(f"{_module_name(path)} imports forbidden '{mod}'")
    assert not violations, "Engine must not import adapters/MCP libs:\n" + "\n".join(violations)


# --- Static import graph over the engine package ---------------------------

def _engine_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in _engine_files():
        name = _module_name(path)
        deps = {m for m in _imports(path) if m.split(".")[0] == "engine"}
        graph[name] = deps
    return graph


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph.get(cur, set()))
    return seen


def test_decide_path_cannot_reach_writer_or_llm():
    """Invariant 2 & 3: nothing reachable from decide() can write or call an LLM."""
    graph = _engine_import_graph()
    reachable = _reachable(graph, "engine.core")

    forbidden = {"engine.pdp.writer", "engine.pdp.openfga", "engine.intent.provision"}
    leaked = reachable & forbidden
    assert not leaked, f"decide() path must not reach a write path; reached: {leaked}"

    # No intent parser (LLM) module is reachable from the decision path.
    intent_modules = {m for m in reachable if m.startswith("engine.intent")}
    assert not intent_modules, f"decide() path must not reach the parser: {intent_modules}"


def test_only_provisioning_imports_the_writer():
    """Invariant 3: among intent modules, only provisioning touches the writer."""
    intent_dir = ENGINE_DIR / "intent"
    for path in sorted(intent_dir.rglob("*.py")):
        imports_writer = any("engine.pdp.writer" in m for m in _imports(path))
        if path.name == "provision.py":
            assert imports_writer, "provision.py is the trusted write path; it should import the writer"
        else:
            assert not imports_writer, f"{path.name} must not import the policy writer"


def test_read_only_store_has_no_write_methods():
    """Invariant 2: the decision-path store interface exposes no writes."""
    from engine.pdp.memory import InMemoryPolicyStore
    from engine.pdp.store import PolicyStore

    write_like = {"write", "write_grants", "create", "update", "delete", "set"}
    for cls in (InMemoryPolicyStore, PolicyStore):
        names = {n for n in dir(cls) if not n.startswith("_")}
        assert not (names & write_like), f"{cls.__name__} exposes write methods: {names & write_like}"


def test_parser_output_is_inert_data():
    """Invariant 3: the parser returns plain data with no capability to write."""
    from engine.intent.base import AllowedAction, ParsedIntent

    intent = ParsedIntent(
        session_id="s", subject="user:a",
        allowed_actions=[AllowedAction(tool="email.send", resource="bob@example.com")],
    )
    public_callables = {
        n for n in dir(intent)
        if not n.startswith("_") and callable(getattr(intent, n))
    }
    # Only pydantic's serialization/validation helpers; nothing store-related.
    assert not any("write" in n or "provision" in n or "grant" in n for n in public_callables)
