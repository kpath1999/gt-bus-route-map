"""CORRELATE_COLUMNS: the only way this vocabulary answers "does X relate to Y".

Before this operator existed, a correlation question had two bad outcomes. It
fell through to ReAct, which would happily write `df['a'].corr(df['b'])` for
columns that did not exist, or the planner squeezed the question into
COMPARE_VALUES / RANK_ROWS and returned a confident number answering something
else. Making correlation expressible means the *unanswerable* correlation
questions now fail at Gate 2 with a named column, which is a verdict rather than
a guess.
"""

from __future__ import annotations

import pandas as pd
import pytest

from flashfusion.pipeline.operators import (
    PlanSchemaError,
    execute_plan,
    structural_validate,
    validate_plan_against_dataframe,
)


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "x": [1.0, 2.0, 3.0, 4.0],
            "y": [2.0, 4.0, 6.0, 8.0],
            "z": [4.0, 3.0, 2.0, 1.0],
            "flat": [1.0, 1.0, 1.0, 1.0],
            "label": ["a", "b", "a", "b"],
        }
    )


def _plan(*steps: dict):
    return structural_validate({"version": "1", "steps": list(steps)})


def test_perfect_positive_correlation_is_computed_in_process() -> None:
    plan = _plan({"op": "CORRELATE_COLUMNS", "left": "x", "right": "y"})
    validate_plan_against_dataframe(plan, _df())
    execution = execute_plan(_df(), plan)

    assert execution.ok
    assert execution.value == pytest.approx(1.0)
    assert plan.operators_used == ["CORRELATE_COLUMNS"]


def test_negative_correlation_keeps_its_sign() -> None:
    plan = _plan({"op": "CORRELATE_COLUMNS", "left": "x", "right": "z"})
    assert execute_plan(_df(), plan).value == pytest.approx(-1.0)


def test_method_is_honoured() -> None:
    plan = _plan(
        {"op": "CORRELATE_COLUMNS", "left": "x", "right": "y", "method": "spearman"}
    )
    assert execute_plan(_df(), plan).value == pytest.approx(1.0)


def test_default_method_is_pearson() -> None:
    plan = _plan({"op": "CORRELATE_COLUMNS", "left": "x", "right": "y"})
    assert plan.steps[0].method == "pearson"


def test_missing_column_is_a_named_scope_verdict_not_a_vague_failure() -> None:
    """The caller routes on ``missing_columns``: an absent field terminates the
    query instead of handing it to ReAct to hallucinate."""
    plan = _plan({"op": "CORRELATE_COLUMNS", "left": "bmi", "right": "x"})
    with pytest.raises(PlanSchemaError) as excinfo:
        validate_plan_against_dataframe(plan, _df())
    assert excinfo.value.missing_columns == {"bmi"}


def test_non_numeric_operand_is_rejected() -> None:
    """Correlating a nominal label column requires inventing an ordinal encoding
    of it, which would answer a question nobody asked."""
    plan = _plan({"op": "CORRELATE_COLUMNS", "left": "label", "right": "x"})
    with pytest.raises(PlanSchemaError):
        validate_plan_against_dataframe(plan, _df())


def test_self_correlation_is_rejected_as_vacuous() -> None:
    plan = _plan({"op": "CORRELATE_COLUMNS", "left": "x", "right": "x"})
    with pytest.raises(PlanSchemaError):
        validate_plan_against_dataframe(plan, _df())


def test_zero_variance_operand_raises_instead_of_returning_nan() -> None:
    """pandas returns NaN here; surfacing that as an answer would report a
    correlation that is undefined, not absent."""
    plan = _plan({"op": "CORRELATE_COLUMNS", "left": "x", "right": "flat"})
    validate_plan_against_dataframe(plan, _df())
    execution = execute_plan(_df(), plan)

    assert execution.ok is False
    assert execution.value is None
    assert "undefined" in execution.error


def test_correlation_runs_on_the_filtered_working_frame() -> None:
    plan = _plan(
        {"op": "FILTER_IN", "column": "label", "values": ["a"]},
        {"op": "CORRELATE_COLUMNS", "left": "x", "right": "y"},
    )
    validate_plan_against_dataframe(plan, _df())
    assert execute_plan(_df(), plan).value == pytest.approx(1.0)


def test_vocabulary_spec_advertises_the_operator() -> None:
    from flashfusion.pipeline.operators import OPERATOR_VOCABULARY_SPEC

    assert "CORRELATE_COLUMNS" in OPERATOR_VOCABULARY_SPEC
