"""Argument-binding tests: the security-relevant argument is bound into identity."""

from __future__ import annotations

from engine.pdp.model import (
    ANY_RESOURCE,
    extract_resource,
    grant_object,
    normalize_resource,
)


def test_email_recipient_is_the_bound_resource():
    assert (
        extract_resource("email.send", {"to": "Bob@Example.com", "body": "x"})
        == "bob@example.com"
    )


def test_explicit_resource_overrides_argument():
    assert (
        extract_resource(
            "email.send", {"to": "bob@example.com"}, explicit_resource="carol@x.com"
        )
        == "carol@x.com"
    )


def test_tool_without_resource_binds_to_any():
    assert extract_resource("calendar.read", {}) == ANY_RESOURCE


def test_unknown_tool_binds_to_any():
    assert extract_resource("some.unknown.tool", {"x": 1}) == ANY_RESOURCE


def test_list_resource_is_normalized_stably():
    a = normalize_resource(["b@x.com", "a@x.com"])
    b = normalize_resource(["a@x.com", "b@x.com"])
    assert a == b == "a@x.com,b@x.com"


def test_grant_object_distinguishes_resource():
    bob = grant_object("s", "email.send", "bob@example.com")
    carol = grant_object("s", "email.send", "carol@example.com")
    anyone = grant_object("s", "email.send", ANY_RESOURCE)
    assert bob != carol != anyone
    assert bob.startswith("grant:")


def test_grant_object_distinguishes_session():
    assert grant_object("s1", "email.send", "bob@x") != grant_object(
        "s2", "email.send", "bob@x"
    )
