"""The parse-quality eval harness: scoring logic, case hygiene, live smoke.

The harness itself is deterministic and fully tested without a network or key;
the live evaluation against the Anthropic parser runs only when
ANTHROPIC_API_KEY is set (same pattern as the OpenFGA integration tests).
"""

from __future__ import annotations

import os

import pytest

from engine.intent.anthropic import AnthropicIntentParser
from engine.intent.base import AllowedAction
from engine.pdp.registry import default_registry
from evals.harness import (
    EvalCase,
    aggregate,
    check_thresholds,
    load_cases,
    run_eval,
    score_case,
)


def _case(expected=(), forbidden=(), id="c1"):
    return EvalCase(
        id=id,
        request="r",
        expected=frozenset(expected),
        forbidden=frozenset(forbidden),
    )


def _actions(*grants):
    return [AllowedAction(tool=t, resource=r) for t, r in grants]


# ── scoring ─────────────────────────────────────────────────────────────────


def test_exact_match_scores_clean():
    case = _case(expected=[("email.send", "bob@example.com")])
    r = score_case(case, _actions(("email.send", "bob@example.com")))
    assert (r.tp, r.fp, r.fn) == (1, 0, 0)
    assert not r.overbroad and not r.leaks


def test_resource_comparison_is_normalized():
    case = _case(expected=[("email.send", "bob@example.com")])
    r = score_case(case, _actions(("email.send", "  Bob@Example.COM ")))
    assert (r.tp, r.fp, r.fn) == (1, 0, 0)


def test_wrong_resource_is_fp_and_fn():
    case = _case(expected=[("email.send", "bob@example.com")])
    r = score_case(case, _actions(("email.send", "carol@example.com")))
    assert (r.tp, r.fp, r.fn) == (0, 1, 1)


def test_missing_grant_is_fn_extra_grant_is_fp():
    case = _case(expected=[("calendar.read", None)])
    r = score_case(case, _actions(("calendar.read", None), ("file.read", "/tmp/x")))
    assert (r.tp, r.fp, r.fn) == (1, 1, 0)
    r2 = score_case(case, _actions())
    assert (r2.tp, r2.fp, r2.fn) == (0, 0, 1)


def test_overbroad_grant_detected():
    # Expected a concrete recipient; parser granted "any recipient".
    case = _case(expected=[("email.send", "bob@example.com")])
    r = score_case(case, _actions(("email.send", None)))
    assert r.overbroad == frozenset({("email.send", None)})


def test_expected_null_resource_is_not_overbroad():
    # "Email the team" with no addresses: null is the CORRECT extraction.
    case = _case(expected=[("email.send", None)])
    r = score_case(case, _actions(("email.send", None)))
    assert (r.tp, r.fp, r.fn) == (1, 0, 0)
    assert not r.overbroad


def test_leak_specific_resource():
    case = _case(
        expected=[("email.send", "bob@example.com")],
        forbidden=[("email.send", "attacker@evil.com")],
    )
    r = score_case(
        case,
        _actions(
            ("email.send", "bob@example.com"), ("email.send", "attacker@evil.com")
        ),
    )
    assert r.leaks == frozenset({("email.send", "attacker@evil.com")})


def test_leak_any_resource_for_tool():
    # forbidden resource None = ANY grant of that tool is a leak.
    case = _case(expected=[("calendar.read", None)], forbidden=[("email.send", None)])
    r = score_case(case, _actions(("calendar.read", None), ("email.send", "x@y.com")))
    assert r.leaks == frozenset({("email.send", "x@y.com")})


def test_aggregate_micro_averages():
    c1 = _case(expected=[("email.send", "a@b.com")], id="a")
    c2 = _case(expected=[("calendar.read", None)], id="b")
    results = [
        score_case(c1, _actions(("email.send", "a@b.com"))),  # tp=1
        score_case(
            c2, _actions(("calendar.read", None), ("file.read", "/x"))
        ),  # tp=1 fp=1
    ]
    m = aggregate(results)
    assert m.tp == 2 and m.fp == 1 and m.fn == 0
    assert m.precision == pytest.approx(2 / 3)
    assert m.recall == 1.0
    assert m.leaks == 0


def test_thresholds_flag_failures():
    c = _case(
        expected=[("email.send", "a@b.com")],
        forbidden=[("email.send", "wrong@x.com")],
    )
    bad = aggregate([score_case(c, _actions(("email.send", "wrong@x.com")))])
    failures = check_thresholds(bad)
    assert any("precision" in f for f in failures)
    assert any("leak" in f for f in failures)
    good = aggregate([score_case(c, _actions(("email.send", "a@b.com")))])
    assert check_thresholds(good) == []


# ── case-file hygiene ───────────────────────────────────────────────────────


def test_cases_load_and_are_wellformed():
    cases = load_cases()
    assert len(cases) >= 30
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    reg = default_registry()
    for c in cases:
        assert c.request.strip()
        for tool, _ in c.expected | c.forbidden:
            assert reg.is_known(tool), f"{c.id}: unknown tool {tool!r}"
    tags = {t for c in cases for t in c.tags}
    assert "injection" in tags, "eval set must include injection cases"
    assert "ambiguous" in tags, "eval set must include ambiguous cases"


# ── harness end-to-end with a perfect deterministic extractor ───────────────


async def test_perfect_extractor_scores_perfectly():
    cases = load_cases()
    by_request = {
        c.request: [
            {"tool": t, "resource": r}
            for t, r in sorted(c.expected, key=lambda g: (g[0], g[1] or ""))
        ]
        for c in cases
    }

    async def perfect(request_text: str, tool_names: list[str]) -> list[dict]:
        return by_request[request_text]

    parser = AnthropicIntentParser(extractor=perfect)
    results, metrics = await run_eval(parser, cases)
    assert metrics.precision == 1.0 and metrics.recall == 1.0
    assert metrics.leaks == 0 and metrics.overbroad == 0
    assert check_thresholds(metrics) == []
    assert len(results) == len(cases)


# ── live smoke against the real parser (requires ANTHROPIC_API_KEY) ─────────

live = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; live parser eval skipped",
)


@live
async def test_live_parser_smoke():
    pytest.importorskip("anthropic")
    cases = [c for c in load_cases() if "smoke" in c.tags]
    assert cases, "case file must tag a small smoke subset"
    parser = AnthropicIntentParser()
    results, metrics = await run_eval(parser, cases)
    # The smoke test asserts the harness runs and nothing forbidden leaks;
    # full thresholds are enforced by `python -m evals.harness`.
    assert len(results) == len(cases)
    assert metrics.leaks == 0, f"forbidden grants produced: {metrics}"
