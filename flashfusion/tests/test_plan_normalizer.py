"""Deterministic repair of near-miss plans, and refusal to repair the rest.

The planner is a sampler; the same question can come back with `"MEAN"` instead
of `"mean"`, a null field that should have been omitted, or a one-branch
PARALLEL_AGGREGATE that is just a GROUP_AGGREGATE wearing a costume. Normalizing
those in code — rather than spending a second LLM call on a repair prompt —
keeps the fast path at exactly one round-trip and makes the plan digest stable.

The normalizer is deliberately narrow. It rewrites shape, never intent: an
invented aggregate or an invented column is surfaced, not silently patched.
"""

from __future__ import annotations

import pytest

from flashfusion.pipeline.operators import (
    PlanSchemaError,
    normalize_guardrail_payload,
    normalize_raw_plan,
    parse_guardrail_response,
    structural_validate,
)


def _plan(*steps: dict) -> dict:
    return {"version": "1", "steps": list(steps)}


# ---------------------------------------------------------------------------
# Cosmetic variance
# ---------------------------------------------------------------------------


def test_enum_case_is_folded_so_two_samplings_hash_alike() -> None:
    plan, actions = normalize_raw_plan(
        _plan({"op": "aggregate_column", "column": "x", "aggregate": "MEAN"})
    )
    step = plan["steps"][0]
    assert step["op"] == "AGGREGATE_COLUMN"
    assert step["aggregate"] == "mean"
    assert actions


def test_explicit_nulls_are_dropped_rather_than_failing_extra_forbid() -> None:
    """A model that fills every optional field with null is expressing the same
    plan as one that omits them; only the former trips validation."""
    plan, _ = normalize_raw_plan(
        _plan(
            {
                "op": "AGGREGATE_COLUMN",
                "column": "x",
                "aggregate": "mean",
                "group_by": None,
            }
        )
    )
    assert "group_by" not in plan["steps"][0]


def test_order_free_value_lists_are_sorted_stably() -> None:
    """FILTER_IN is a set operation, so ['b','a'] and ['a','b'] are one plan."""
    first, _ = normalize_raw_plan(
        _plan({"op": "FILTER_IN", "column": "c", "values": ["b", "a", "c"]})
    )
    second, _ = normalize_raw_plan(
        _plan({"op": "FILTER_IN", "column": "c", "values": ["c", "a", "b"]})
    )
    assert first == second


def test_normalization_is_idempotent() -> None:
    once, _ = normalize_raw_plan(
        _plan({"op": "aggregate_column", "column": "x", "aggregate": "MAX", "group_by": None})
    )
    twice, actions = normalize_raw_plan(once)
    assert twice == once
    assert actions == []


# ---------------------------------------------------------------------------
# Structural near-misses
# ---------------------------------------------------------------------------


def test_single_branch_parallel_aggregate_becomes_a_group_aggregate() -> None:
    """PARALLEL_AGGREGATE requires two branches. One branch is not an error of
    intent — it is a GROUP_AGGREGATE the planner over-decorated."""
    plan, actions = normalize_raw_plan(
        _plan(
            {
                "op": "PARALLEL_AGGREGATE",
                "branches": [
                    {
                        "group_by": ["record_id"],
                        "column": "annotation",
                        "aggregate": "count",
                        "result_column": "beats",
                    }
                ],
            },
            {
                "op": "RANK_ROWS",
                "column": "beats",
                "direction": "max",
                "return_columns": ["record_id", "beats"],
            },
        )
    )
    ops = [s["op"] for s in plan["steps"]]
    assert ops == ["GROUP_AGGREGATE", "RANK_GROUPS"]
    assert plan["steps"][1]["direction"] == "max"
    assert any("PARALLEL_AGGREGATE" in a for a in actions)


def test_single_branch_filter_is_preserved_as_an_explicit_filter_step() -> None:
    plan, _ = normalize_raw_plan(
        _plan(
            {
                "op": "PARALLEL_AGGREGATE",
                "branches": [
                    {
                        "filter_column": "record_id",
                        "filter_values": [101],
                        "group_by": ["window"],
                        "column": "annotation",
                        "aggregate": "count",
                        "result_column": "n",
                    }
                ],
            },
            {"op": "AGGREGATE_COLUMN", "column": "n", "aggregate": "max"},
        )
    )
    assert [s["op"] for s in plan["steps"]] == [
        "FILTER_IN",
        "GROUP_AGGREGATE",
        "AGGREGATE_GROUPS",
    ]
    assert plan["steps"][0]["values"] == [101]


def test_ambiguous_consumer_leaves_the_plan_untouched() -> None:
    """If the step after the one-branch aggregate does not consume its result
    column, the rewrite would change meaning. Better to fail Gate 1 loudly."""
    original = _plan(
        {
            "op": "PARALLEL_AGGREGATE",
            "branches": [
                {"group_by": ["g"], "column": "x", "aggregate": "mean", "result_column": "m"}
            ],
        },
        {"op": "AGGREGATE_COLUMN", "column": "x", "aggregate": "max"},
    )
    plan, actions = normalize_raw_plan(original)
    assert plan["steps"][0]["op"] == "PARALLEL_AGGREGATE"
    assert not any("PARALLEL_AGGREGATE" in a for a in actions)


# ---------------------------------------------------------------------------
# Refusals — the normalizer must not invent semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "aggregate", ["percentile_99", "p99", "quantile_95", "percentile_1"]
)
def test_percentile_shaped_aggregates_are_rejected_with_a_pointer(aggregate: str) -> None:
    """The AGG list is closed. A percentile is a pre-computed *column* in these
    datasets, so silently mapping it to an aggregate would fabricate a statistic
    the frame never contained."""
    with pytest.raises(PlanSchemaError) as excinfo:
        normalize_raw_plan(
            _plan({"op": "AGGREGATE_COLUMN", "column": "x", "aggregate": aggregate})
        )
    assert "DERIVE_BINARY" in str(excinfo.value)


def test_guardrail_payload_normalizes_the_nested_plan() -> None:
    payload = {
        "in_scope": True,
        "plan": _plan({"op": "aggregate_column", "column": "x", "aggregate": "MEAN"}),
    }
    normalized, actions = normalize_guardrail_payload(payload)
    assert normalized["plan"]["steps"][0]["aggregate"] == "mean"
    assert actions


# ---------------------------------------------------------------------------
# Response extraction
# ---------------------------------------------------------------------------


def test_prose_and_fences_around_the_json_are_tolerated() -> None:
    raw = (
        "Sure! Here is the plan:\n```json\n"
        '{"in_scope": true, "plan": {"version": "1", "steps": ['
        '{"op": "AGGREGATE_COLUMN", "column": "x", "aggregate": "mean"}]}}'
        "\n```\nLet me know if you need anything else."
    )
    result = parse_guardrail_response(raw)
    assert result.parsed.in_scope is True
    assert result.parsed.plan is not None


def test_braces_inside_string_literals_do_not_truncate_the_object() -> None:
    """A naive rfind('}') scan breaks on any brace appearing in a value."""
    raw = (
        '{"in_scope": false, "rejection_reason": "no column named {bmi} exists", '
        '"plan": null} trailing prose }'
    )
    result = parse_guardrail_response(raw)
    assert result.parsed.in_scope is False
    assert "{bmi}" in (result.parsed.rejection_reason or "")


def test_unbalanced_response_raises_rather_than_guessing() -> None:
    with pytest.raises(ValueError):
        parse_guardrail_response('{"in_scope": true, "plan": ')


def test_normalized_plan_still_satisfies_structural_validation() -> None:
    plan, _ = normalize_raw_plan(
        _plan({"op": "aggregate_column", "column": "x", "aggregate": "MEAN", "group_by": None})
    )
    validated = structural_validate(plan)
    assert validated.operators_used == ["AGGREGATE_COLUMN"]
