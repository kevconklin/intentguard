"""Argument-level validation: ArgSpec constraints and the invalid_arguments gate."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from engine.config import EngineConfig
from engine.core import decide
from engine.pdp.registry import (
    ArgSpec,
    ToolRegistry,
    ToolSpec,
    default_registry,
    validate_arguments,
)
from engine.schema import Decision, Mode, Reason
from tests.conftest import make_request as _req


def _spec(*args: ArgSpec) -> ToolSpec:
    return ToolSpec(name="t", arguments=list(args))


# ── ArgSpec model validation ────────────────────────────────────────────────


def test_unknown_type_name_rejected():
    with pytest.raises(ValidationError):
        ArgSpec(name="x", type="float")  # not in the allowed type set


def test_invalid_regex_pattern_rejected_at_construction():
    # A bad pattern must fail at config-load time, never at decision time
    # (a decision-time re.error would escape the fail-closed path as a 500).
    with pytest.raises(ValidationError):
        ArgSpec(name="x", pattern="[unclosed")


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        ArgSpec(name="x", bogus=1)


# ── validate_arguments (pure) ───────────────────────────────────────────────


def test_no_declared_arguments_always_passes():
    assert validate_arguments(_spec(), {"anything": object()}) is None


def test_undeclared_arguments_ignored():
    spec = _spec(ArgSpec(name="mode", enum=["a", "b"]))
    # Free-form fields like an email body are never rejected.
    assert validate_arguments(spec, {"mode": "a", "body": "x" * 10_000}) is None


def test_wrong_type_detected():
    spec = _spec(ArgSpec(name="n", type="integer"))
    assert validate_arguments(spec, {"n": "5"}) == "wrong_type:n"
    assert validate_arguments(spec, {"n": 5}) is None


def test_bool_is_not_integer_or_number():
    # True == 1 in Python; a boolean must not satisfy integer/number.
    for type_name in ("integer", "number"):
        spec = _spec(ArgSpec(name="n", type=type_name))
        assert validate_arguments(spec, {"n": True}) == "wrong_type:n"


def test_number_accepts_int_and_float():
    spec = _spec(ArgSpec(name="n", type="number"))
    assert validate_arguments(spec, {"n": 1}) is None
    assert validate_arguments(spec, {"n": 1.5}) is None


def test_enum_membership():
    spec = _spec(ArgSpec(name="mode", enum=["low", "high"]))
    assert validate_arguments(spec, {"mode": "urgent"}) == "not_in_enum:mode"
    assert validate_arguments(spec, {"mode": "low"}) is None


def test_pattern_fullmatch():
    spec = _spec(ArgSpec(name="to", pattern=r"[^@\s]+@[^@\s]+"))
    assert validate_arguments(spec, {"to": "not-an-email"}) == "pattern_mismatch:to"
    assert validate_arguments(spec, {"to": "a@b.com"}) is None
    # fullmatch, not search: a valid fragment inside junk does not pass.
    assert validate_arguments(spec, {"to": "a@b.com and more"}) == "pattern_mismatch:to"


def test_pattern_applies_to_every_list_element():
    # A pattern-constrained argument accepts a list of matching strings
    # (e.g. multiple email recipients); any non-matching element fails.
    spec = _spec(ArgSpec(name="to", pattern=r"[^@\s]+@[^@\s]+"))
    assert validate_arguments(spec, {"to": ["a@b.com", "c@d.com"]}) is None
    assert (
        validate_arguments(spec, {"to": ["a@b.com", "evil"]}) == "pattern_mismatch:to"
    )
    assert validate_arguments(spec, {"to": ["a@b.com", 42]}) == "pattern_mismatch:to"


def test_pattern_rejects_non_string_non_list_values():
    # A declared pattern implies a string-valued (or list-of-strings)
    # argument: other types cannot silently bypass the shape check.
    spec = _spec(ArgSpec(name="to", pattern=r"[^@\s]+@[^@\s]+"))
    assert validate_arguments(spec, {"to": {"x": "y"}}) == "wrong_type:to"
    assert validate_arguments(spec, {"to": 12345}) == "wrong_type:to"


def test_max_length_checked_before_pattern():
    # max_length must bound regex cost, so it runs first: an over-long value
    # is rejected without ever reaching the regex engine.
    spec = _spec(ArgSpec(name="v", pattern=r"x+", max_length=5))
    assert validate_arguments(spec, {"v": "y" * 10}) == "too_long:v"


def test_pattern_input_hard_cap():
    # Even without a declared max_length, an absurdly long string is denied
    # before any regex runs (fail closed, bounds decision-path cost).
    from engine.pdp.registry import PATTERN_INPUT_CAP

    spec = _spec(ArgSpec(name="v", pattern=r".*"))
    assert (
        validate_arguments(spec, {"v": "a" * (PATTERN_INPUT_CAP + 1)}) == "too_long:v"
    )
    assert validate_arguments(spec, {"v": "a" * 10}) is None


def test_max_length_on_string_and_array():
    spec = _spec(ArgSpec(name="v", max_length=3))
    assert validate_arguments(spec, {"v": "abcd"}) == "too_long:v"
    assert validate_arguments(spec, {"v": [1, 2, 3, 4]}) == "too_long:v"
    assert validate_arguments(spec, {"v": "abc"}) is None


def test_required_missing_and_blank():
    spec = _spec(ArgSpec(name="to", required=True))
    assert validate_arguments(spec, {}) == "missing_required:to"
    # A present-but-blank string counts as absent (fail closed).
    assert validate_arguments(spec, {"to": "   "}) == "missing_required:to"
    assert validate_arguments(spec, {"to": None}) == "missing_required:to"


def test_optional_blank_skips_constraints():
    # A blank optional argument is treated as absent: its shape constraints
    # do not run. Pinned deliberately (documented in the ArgSpec docstring).
    spec = _spec(ArgSpec(name="to", pattern=r"[^@\s]+@[^@\s]+"))
    assert validate_arguments(spec, {"to": "  "}) is None


def test_first_violation_in_declaration_order():
    spec = _spec(
        ArgSpec(name="a", type="string"),
        ArgSpec(name="b", type="integer"),
    )
    assert validate_arguments(spec, {"a": 1, "b": "x"}) == "wrong_type:a"


def test_registry_method_unknown_tool_returns_none():
    reg = ToolRegistry([_spec(ArgSpec(name="x", required=True))])
    # The allowlist gate owns unknown tools; validation stays out of its way.
    assert reg.validate_arguments("nope.unknown", {}) is None


def test_arguments_loaded_from_json_file(tmp_path):
    path = tmp_path / "tools.json"
    path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "slack.post",
                        "resource_arg": "channel",
                        "arguments": [
                            {"name": "channel", "pattern": "#[a-z0-9-]+"},
                            {"name": "thread_ts", "type": "string"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    reg = ToolRegistry.load(path)
    assert reg.validate_arguments("slack.post", {"channel": "general"}) == (
        "pattern_mismatch:channel"
    )
    assert reg.validate_arguments("slack.post", {"channel": "#general"}) is None


def test_bad_pattern_in_config_file_rejected_at_load(tmp_path):
    path = tmp_path / "tools.json"
    path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "t",
                        "arguments": [{"name": "x", "pattern": "[unclosed"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        ToolRegistry.load(path)


# ── bundled default registry constraints ────────────────────────────────────


def test_default_registry_constrains_url_shape():
    reg = default_registry()
    assert reg.validate_arguments("http.get", {"url": "https://example.com/x"}) is None
    assert (
        reg.validate_arguments("http.get", {"url": "javascript:alert(1)"})
        == "pattern_mismatch:url"
    )


def test_default_registry_constrains_email_recipient():
    # The recipient is the security-relevant resource argument; non-string
    # values must not bypass its shape check (they'd flow into the grant
    # identity unvalidated).
    reg = default_registry()
    assert reg.validate_arguments("email.send", {"to": "bob@example.com"}) is None
    assert reg.validate_arguments("email.send", {"to": ["a@b.com", "c@d.com"]}) is None
    assert (
        reg.validate_arguments("email.send", {"to": "not-an-email"})
        == "pattern_mismatch:to"
    )
    assert reg.validate_arguments("email.send", {"to": {"x": "y"}}) == "wrong_type:to"
    assert reg.validate_arguments("email.send", {"to": 12345}) == "wrong_type:to"


def test_default_registry_constrains_file_path_type():
    reg = default_registry()
    assert reg.validate_arguments("file.read", {"path": "/tmp/x"}) is None
    assert reg.validate_arguments("file.read", {"path": 42}) == "wrong_type:path"


# ── decision path ───────────────────────────────────────────────────────────

REG = ToolRegistry(
    [
        ToolSpec(
            name="email.send",
            resource_args=["to"],
            arguments=[
                ArgSpec(name="subject", type="string", max_length=10),
                ArgSpec(name="priority", enum=["low", "high"]),
            ],
        ),
        ToolSpec(name="calendar.read"),
    ]
)


def _cfg(mode: Mode = Mode.enforce) -> EngineConfig:
    return EngineConfig(mode=mode, tool_registry=REG)


async def test_violating_call_denied_in_enforce(store, audit, seeded):
    await seeded()
    resp = await decide(
        _req("email.send", {"to": "bob@example.com", "priority": "urgent"}),
        store,
        _cfg(),
        audit,
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.invalid_arguments.value
    entry = audit.entries()[-1]
    assert entry.error == "not_in_enum:priority"
    assert "T2:tool_misuse" in entry.owasp_threats


async def test_valid_arguments_flow_to_allow(store, audit, seeded):
    await seeded()
    resp = await decide(
        _req("email.send", {"to": "bob@example.com", "priority": "low"}),
        store,
        _cfg(),
        audit,
    )
    assert resp.decision == Decision.allow.value
    assert resp.reason == Reason.in_intent.value


async def test_violation_in_observe_logs_would_deny(store, audit, seeded):
    await seeded()
    resp = await decide(
        _req("email.send", {"to": "bob@example.com", "subject": "x" * 11}),
        store,
        _cfg(Mode.observe),
        audit,
    )
    assert resp.decision == Decision.allow.value
    assert resp.would_have_decided == Decision.deny.value
    assert resp.reason == Reason.invalid_arguments.value


async def test_validation_runs_before_store_no_session_needed(store, audit):
    # Nothing provisioned: shape validation is deterministic and pre-store,
    # so the reason is invalid_arguments, not no_session.
    resp = await decide(
        _req("email.send", {"to": "bob@example.com", "priority": "urgent"}),
        store,
        _cfg(),
        audit,
    )
    assert resp.reason == Reason.invalid_arguments.value


async def test_unknown_tool_takes_precedence(store, audit, seeded):
    await seeded()
    resp = await decide(_req("file.delete", {"path": 42}), store, _cfg(), audit)
    assert resp.reason == Reason.unknown_tool.value


async def test_invalid_arguments_beats_missing_resource(store, audit, seeded):
    # `to` is missing AND `priority` violates its enum: shape validation is
    # the earlier, more specific gate.
    await seeded()
    resp = await decide(
        _req("email.send", {"priority": "urgent"}), store, _cfg(), audit
    )
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.invalid_arguments.value


async def test_missing_resource_still_owns_bare_absence(store, audit, seeded):
    # No ArgSpec violated (to is not ArgSpec-required); the resource gate
    # still fails closed on the missing resource argument.
    await seeded()
    resp = await decide(_req("email.send", {}), store, _cfg(), audit)
    assert resp.decision == Decision.deny.value
    assert resp.reason == Reason.missing_resource.value
