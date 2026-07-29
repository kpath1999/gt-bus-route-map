# flashfusion/pipeline/operators.py
"""
Closed, typed operator vocabulary for Flash-Fusion's default execution path.

Design contract
---------------
1. Every operator is a distinct Pydantic model with ``extra="forbid"``. The
   operators are joined into a discriminated union on the ``op`` field, so a
   malformed or unknown operator fails *structurally* — before any data is
   touched — instead of being silently coerced.
2. A plan passes **two gates** before execution:
     a. structural — ``DeterministicPlan.model_validate(...)`` (Pydantic)
     b. schema     — ``validate_plan_against_dataframe(plan, df)``
   Both are pure Python and cost microseconds relative to an LLM call, so
   neither is ever skipped for latency reasons. The guardrail answers "is this
   question answerable in principle"; schema validation answers "did this
   specific generated plan reference real columns correctly". They reason over
   different artifacts and are not redundant.
3. Execution dispatches with ``match`` over the union. There is no
   ``op.upper().strip()`` string dispatch and no ``**kwargs`` passthrough
   anywhere in this module.
4. The vocabulary is **closed**. When a query needs an operator that does not
   exist, ``log_operator_gap()`` records the gap and the caller falls back to
   ReAct for that query. New operators are designed, tested, and versioned
   offline — never invented inside a live request.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "PLAN_VERSION",
    "OPERATOR_VOCABULARY_SPEC",
    "Aggregate",
    "Comparator",
    "DeterministicPlan",
    "TypedPlan",
    "GuardrailAndPlan",
    "PlanExecution",
    "PlanSchemaError",
    "PlanExecutionError",
    "StructuralValidationError",
    "TypedOperator",
    "structural_validate",
    "parse_guardrail_and_plan",
    "validate_plan_against_dataframe",
    "execute_plan",
    "log_operator_gap",
]

PLAN_VERSION = "1"

# ---------------------------------------------------------------------------
# Scalar vocabularies
# ---------------------------------------------------------------------------

Aggregate = Literal[
    "min", "max", "mean", "median", "sum", "count", "std", "var", "nunique", "rms"
]
Comparator = Literal["eq", "ne", "gt", "gte", "lt", "lte"]
Direction = Literal["max", "min"]
BinaryOperation = Literal["add", "subtract", "multiply", "divide", "abs_difference"]
ThresholdStat = Literal["median", "mean", "min", "max"]
CompareMode = Literal["difference", "abs_difference", "ratio"]
PredictiveModel = Literal[
    "logistic_regression",
    "random_forest",
    "one_nearest_neighbor",
    "hist_gradient_boosting",
]
Scalar = Union[str, int, float, bool]

# Aggregates that are only meaningful on a numeric column.
_NUMERIC_ONLY_AGGREGATES: frozenset[str] = frozenset(
    {"mean", "median", "sum", "std", "var", "rms"}
)


# ---------------------------------------------------------------------------
# Operator models
# ---------------------------------------------------------------------------


class _Operator(BaseModel):
    """Base contract shared by every deterministic operator."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FilterCompare(_Operator):
    """Row filter: keep rows where ``column <comparator> value``."""

    op: Literal["FILTER_COMPARE"]
    column: str = Field(min_length=1)
    comparator: Comparator
    value: Scalar


class FilterIn(_Operator):
    """Row filter: keep rows where ``column`` is one of ``values``."""

    op: Literal["FILTER_IN"]
    column: str = Field(min_length=1)
    values: list[Scalar] = Field(min_length=1)


class FilterNotEmpty(_Operator):
    """Row filter: keep rows where ``column`` is non-null and non-blank."""

    op: Literal["FILTER_NOT_EMPTY"]
    column: str = Field(min_length=1)


class FilterEqAggregate(_Operator):
    """Row filter: keep rows where ``column`` equals an aggregate of itself.

    Covers "return every row where X reaches its maximum" without a two-pass
    plan or a placeholder referencing a prior observation.
    """

    op: Literal["FILTER_EQ_AGGREGATE"]
    column: str = Field(min_length=1)
    aggregate: Aggregate


class AggregateColumn(_Operator):
    """Reduce one column of the current frame to a scalar."""

    op: Literal["AGGREGATE_COLUMN"]
    column: str = Field(min_length=1)
    aggregate: Aggregate


class CountRows(_Operator):
    """Count rows remaining in the current frame."""

    op: Literal["COUNT_ROWS"]


class CountDistinct(_Operator):
    """Count distinct non-null values of a column in the current frame."""

    op: Literal["COUNT_DISTINCT"]
    column: str = Field(min_length=1)


class SelectColumn(_Operator):
    """Return the values of a column as a list."""

    op: Literal["SELECT_COLUMN"]
    column: str = Field(min_length=1)
    distinct: bool = False


class DeriveBinary(_Operator):
    """Row-wise arithmetic between two columns into a new named column."""

    op: Literal["DERIVE_BINARY"]
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)
    operation: BinaryOperation
    result: str = Field(min_length=1)


class DeriveVectorMagnitude(_Operator):
    """Row-wise ``sqrt(a^2 + b^2 + c^2)`` — the canonical IoT derived feature."""

    op: Literal["DERIVE_VECTOR_MAGNITUDE"]
    columns: tuple[str, str, str]
    result: str = Field(default="vector_magnitude", min_length=1)


class DeriveBin(_Operator):
    """Bucket a column into fixed-width bins: ``floor(col / width) * width``.

    ``column`` may be numeric or datetime. For a datetime column, ``width`` is
    interpreted in nanoseconds (e.g. 60_000_000_000 for a 1-minute bucket),
    matching ``pd.Timestamp`` resolution, and the bucketed result is itself a
    datetime — the start of each fixed-width time window.
    """

    op: Literal["DERIVE_BIN"]
    column: str = Field(min_length=1)
    width: float = Field(gt=0.0)
    result: str = Field(min_length=1)


class GroupAggregate(_Operator):
    """Group the current frame by one or more keys and aggregate one column.

    ``freq`` switches to time-bucketed grouping (``pd.Grouper``) and requires a
    single datetime ``group_by`` key. ``column`` may be omitted when
    ``aggregate`` is ``count``, which then yields group sizes.
    """

    op: Literal["GROUP_AGGREGATE"]
    group_by: list[str] = Field(min_length=1)
    aggregate: Aggregate
    column: str | None = None
    freq: str | None = None


class AggregateGroups(_Operator):
    """Reduce the previous GROUP_AGGREGATE result to a scalar."""

    op: Literal["AGGREGATE_GROUPS"]
    aggregate: Aggregate


class RankGroups(_Operator):
    """Return the highest/lowest group from the previous GROUP_AGGREGATE result."""

    op: Literal["RANK_GROUPS"]
    direction: Direction


class RankRows(_Operator):
    """Return the row that maximises/minimises a column, projected to columns."""

    op: Literal["RANK_ROWS"]
    column: str = Field(min_length=1)
    direction: Direction
    return_columns: list[str] = Field(min_length=1)


class SplitByThreshold(_Operator):
    """Name a partition of the ORIGINAL frame split at a statistic of ``column``."""

    op: Literal["SPLIT_BY_THRESHOLD"]
    column: str = Field(min_length=1)
    comparator: Literal["gt", "gte", "lt", "lte"]
    threshold: ThresholdStat = "median"
    label: str = Field(min_length=1)


class SplitByValues(_Operator):
    """Name a partition of the ORIGINAL frame by membership in a value set.

    Partitions are drawn from the original frame, not the possibly-narrowed
    working frame, so two disjoint category groups never collapse to zero rows.
    """

    op: Literal["SPLIT_BY_VALUES"]
    column: str = Field(min_length=1)
    values: list[Scalar] = Field(min_length=1)
    label: str = Field(min_length=1)


class AggregatePartitions(_Operator):
    """Aggregate the same metric across previously named partitions."""

    op: Literal["AGGREGATE_PARTITIONS"]
    partitions: list[str] = Field(min_length=2)
    aggregate: Aggregate
    column: str | None = None


class ComparePartitions(_Operator):
    """Compare the two values produced by AGGREGATE_PARTITIONS."""

    op: Literal["COMPARE_PARTITIONS"]
    mode: CompareMode = "difference"


class CompareValues(_Operator):
    """Compare the two most recent scalar aggregates, from ANY prior source.

    Generalizes COMPARE_PARTITIONS beyond SPLIT_BY_*: the two values being
    compared can come from AGGREGATE_COLUMN or AGGREGATE_GROUPS run in any
    context, including twice in a row after a PARALLEL_AGGREGATE merge (e.g.
    to reduce per-entity branch results down to two comparable scalars).
    Requires exactly two preceding AGGREGATE_COLUMN/AGGREGATE_GROUPS steps
    with no COMPARE_VALUES between them (enforced in schema validation).
    """

    op: Literal["COMPARE_VALUES"]
    mode: CompareMode = "difference"
    label_a: str = Field(default="value_a", min_length=1)
    label_b: str = Field(default="value_b", min_length=1)


class ParallelAggregateBranch(BaseModel):
    """A single branch of PARALLEL_AGGREGATE: filter → group → aggregate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Filter stage (optional - if None, uses all rows)
    filter_column: str | None = None
    filter_values: list[Scalar] | None = None

    # Group stage
    group_by: list[str] = Field(min_length=1)
    aggregate: Aggregate
    column: str | None = None  # None with aggregate="count" gives group sizes

    # Result naming
    result_column: str = Field(min_length=1)  # name for the aggregated values


class ParallelAggregate(_Operator):
    """Execute multiple independent filter→group→aggregate pipelines, then merge.

    Design rationale:
    - Solves the "compare resting vs. dynamic duration per user" pattern
    - Each branch filters the ORIGINAL dataframe independently
    - All branches must group by the SAME keys (enforced in validation)
    - Results are outer-merged on group keys, filling missing with 0
    - Working frame becomes the merged result with columns: [group_keys, result1, result2, ...]

    Example (WISDM query 6):
        {
          "op": "PARALLEL_AGGREGATE",
          "branches": [
            {
              "filter_column": "activity_label",
              "filter_values": ["Sitting", "Standing"],
              "group_by": ["subject_id"],
              "aggregate": "sum",
              "column": "timestamp",
              "result_column": "resting_duration"
            },
            {
              "filter_column": "activity_label",
              "filter_values": ["Walking", "Jogging", "Upstairs", "Downstairs"],
              "group_by": ["subject_id"],
              "aggregate": "sum",
              "column": "timestamp",
              "result_column": "dynamic_duration"
            }
          ]
        }
    """

    op: Literal["PARALLEL_AGGREGATE"]
    branches: list[ParallelAggregateBranch] = Field(min_length=2)

    @property
    def shared_group_keys(self) -> list[str]:
        """All branches must group by the same columns."""
        return self.branches[0].group_by


class PredictivePipeline(_Operator):
    """Deterministic chronological train/holdout classification.

    ``feature_columns`` is explicit and required: trained models must be
    reproducible and auditable across runs, so "all remaining numeric columns"
    is not an acceptable specification.
    """

    op: Literal["PREDICTIVE_PIPELINE"]
    model: PredictiveModel
    feature_columns: list[str] = Field(min_length=1)
    target_column: str = Field(min_length=1)
    sort_by: list[str] = Field(min_length=1)
    train_fraction: float = Field(default=0.8, gt=0.0, lt=1.0)
    holdout_row: Literal["first", "last"] = "first"
    filter_column: str | None = None
    filter_value: Scalar | None = None
    target_from_non_empty: bool = False
    target_label: str = Field(default="label", min_length=1)


TypedOperator = Annotated[
    Union[
        FilterCompare,
        FilterIn,
        FilterNotEmpty,
        FilterEqAggregate,
        AggregateColumn,
        CountRows,
        CountDistinct,
        SelectColumn,
        DeriveBinary,
        DeriveVectorMagnitude,
        DeriveBin,
        GroupAggregate,
        AggregateGroups,
        RankGroups,
        RankRows,
        SplitByThreshold,
        SplitByValues,
        AggregatePartitions,
        ComparePartitions,
        CompareValues,
        ParallelAggregate,
        PredictivePipeline,
    ],
    Field(discriminator="op"),
]


class DeterministicPlan(BaseModel):
    """A fully typed, pre-validatable Flash-Fusion execution plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1"] = PLAN_VERSION
    steps: list[TypedOperator] = Field(min_length=1)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeterministicPlan":
        return cls.model_validate(data)

    @property
    def operators_used(self) -> list[str]:
        return [step.op for step in self.steps]

    @property
    def kind(self) -> str:
        return self.steps[-1].op.lower()


#: Alias used by flashfusion/scripts/run_typed_operators.py.
TypedPlan = DeterministicPlan


class GuardrailAndPlan(BaseModel):
    """Single-round-trip guardrail verdict + candidate plan.

    ``extra="ignore"`` applies to this wrapper only — the plan and every
    operator inside it still forbid unknown fields, so nothing untyped can
    reach execution.
    """

    model_config = ConfigDict(extra="ignore")

    in_scope: bool
    rejection_reason: str | None = None
    ambiguous_concepts: list[str] = Field(default_factory=list)
    plan: DeterministicPlan | None = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PlanSchemaError(ValueError):
    """Raised when a structurally valid plan does not fit the DataFrame schema."""


class PlanExecutionError(RuntimeError):
    """Raised when a validated plan fails at execution time."""


#: Re-exported so callers can catch structural failures without importing pydantic.
StructuralValidationError = ValidationError


# ---------------------------------------------------------------------------
# Execution result
# ---------------------------------------------------------------------------


@dataclass
class PlanExecution:
    """Outcome of running one DeterministicPlan against a DataFrame."""

    value: Any = None
    ok: bool = False
    error: str | None = None
    plan_kind: str = ""
    rows_scanned: int = 0
    rows_after_filter: int | None = None
    columns_used: list[str] = field(default_factory=list)
    operators_used: list[str] = field(default_factory=list)
    code: str = ""
    trace: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Value coercion + shared helpers
# ---------------------------------------------------------------------------


def _coerce_value(series: pd.Series, value: Any) -> Any:
    """Coerce a JSON scalar into something comparable with ``series``' dtype."""
    if isinstance(value, str):
        value = value.strip()

    if pd.api.types.is_datetime64_any_dtype(series):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return pd.Timestamp(value)
        parsed = pd.to_datetime(value, errors="coerce")
        if parsed is pd.NaT:
            raise PlanSchemaError(f"Cannot compare datetime column against {value!r}")
        return parsed

    if pd.api.types.is_bool_dtype(series):
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    if pd.api.types.is_numeric_dtype(series):
        if isinstance(value, (bool, int, float)):
            return value
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise PlanSchemaError(
                f"Cannot compare numeric column against {value!r}"
            ) from exc
        return int(numeric) if numeric.is_integer() else numeric

    return str(value)


def _compare(series: pd.Series, comparator: str, value: Any) -> pd.Series:
    match comparator:
        case "eq":
            return series == value
        case "ne":
            return series != value
        case "gt":
            return series > value
        case "gte":
            return series >= value
        case "lt":
            return series < value
        case "lte":
            return series <= value
    raise PlanExecutionError(f"Unsupported comparator: {comparator!r}")


def _aggregate_series(series: pd.Series, aggregate: str) -> Any:
    match aggregate:
        case "rms":
            values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
            if values.size == 0:
                raise PlanExecutionError("RMS requires at least one numeric value")
            return float(np.sqrt(np.mean(np.square(values))))
        case "nunique":
            return int(series.nunique())
        case "count":
            return int(series.count())
        case _:
            return getattr(series, aggregate)()


def _to_python(value: Any) -> Any:
    """Normalize numpy/pandas scalars so results serialize cleanly."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _bin_series(series: pd.Series, width: float) -> pd.Series:
    """Bucket ``series`` into fixed-width bins: ``floor(value / width) * width``.

    Numeric columns are binned directly. Datetime columns are binned in the
    nanosecond domain (``width`` is nanoseconds, matching pandas' internal
    ``datetime64[ns]`` resolution) and the result is converted back to a
    datetime, so the bucket value is the start of each fixed-width window.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        nanos = series.astype("int64")
        width_ns = int(width)
        bucketed = (nanos // width_ns) * width_ns
        return pd.to_datetime(bucketed)
    return (series // width) * width


# ---------------------------------------------------------------------------
# Gate 2 — DataFrame schema validation
# ---------------------------------------------------------------------------


def _require_column(column: str, available: set[str], op: str) -> None:
    if column not in available:
        raise PlanSchemaError(f"{op} references unknown column {column!r}")


def _require_numeric(column: str, df: pd.DataFrame, op: str) -> None:
    if column in df.columns and not pd.api.types.is_numeric_dtype(df[column]):
        raise PlanSchemaError(
            f"{op} requires a numeric column; {column!r} has dtype {df[column].dtype}"
        )


def _require_numeric_or_datetime(column: str, df: pd.DataFrame, op: str) -> None:
    if column not in df.columns:
        return
    dtype = df[column].dtype
    if not (
        pd.api.types.is_numeric_dtype(dtype)
        or pd.api.types.is_datetime64_any_dtype(dtype)
    ):
        raise PlanSchemaError(
            f"{op} requires a numeric or datetime column; {column!r} has dtype {dtype}"
        )


def _validate_aggregate(
    column: str | None,
    aggregate: str,
    df: pd.DataFrame,
    available: set[str],
    op: str,
) -> None:
    if column is None:
        if aggregate != "count":
            raise PlanSchemaError(f"{op} requires a column for aggregate {aggregate!r}")
        return
    _require_column(column, available, op)
    if aggregate in _NUMERIC_ONLY_AGGREGATES:
        _require_numeric(column, df, op)


def validate_plan_against_dataframe(plan: DeterministicPlan, df: pd.DataFrame) -> None:
    """Gate 2: check a structurally valid plan against this DataFrame's schema.

    Raises:
        PlanSchemaError: unknown column, incompatible dtype, undefined partition,
            or a step that depends on a prior step the plan never emits.
    """
    available: set[str] = {str(c) for c in df.columns}
    partitions: set[str] = set()
    has_group_result = False
    has_partition_aggregate = False
    # Count of AGGREGATE_COLUMN/AGGREGATE_GROUPS steps since the last COMPARE_VALUES
    # (or since the start of the plan) — COMPARE_VALUES requires exactly two.
    pending_scalar_count = 0

    for step in plan.steps:
        match step:
            case FilterCompare():
                _require_column(step.column, available, step.op)
                if step.column in df.columns:
                    _coerce_value(df[step.column], step.value)

            case FilterIn() | FilterNotEmpty() | CountDistinct() | SelectColumn():
                _require_column(step.column, available, step.op)

            case FilterEqAggregate():
                _validate_aggregate(step.column, step.aggregate, df, available, step.op)

            case AggregateColumn():
                _validate_aggregate(step.column, step.aggregate, df, available, step.op)
                pending_scalar_count += 1

            case CountRows():
                pass

            case DeriveBinary():
                _require_column(step.left, available, step.op)
                _require_column(step.right, available, step.op)
                _require_numeric(step.left, df, step.op)
                _require_numeric(step.right, df, step.op)
                available.add(step.result)

            case DeriveVectorMagnitude():
                for column in step.columns:
                    _require_column(column, available, step.op)
                    _require_numeric(column, df, step.op)
                available.add(step.result)

            case DeriveBin():
                _require_column(step.column, available, step.op)
                _require_numeric_or_datetime(step.column, df, step.op)
                available.add(step.result)

            case GroupAggregate():
                for key in step.group_by:
                    _require_column(key, available, step.op)
                _validate_aggregate(step.column, step.aggregate, df, available, step.op)
                if step.freq is not None:
                    if len(step.group_by) != 1:
                        raise PlanSchemaError(
                            "GROUP_AGGREGATE with freq requires exactly one group key"
                        )
                    key = step.group_by[0]
                    if key in df.columns and not pd.api.types.is_datetime64_any_dtype(
                        df[key]
                    ):
                        raise PlanSchemaError(
                            f"GROUP_AGGREGATE freq={step.freq!r} requires a datetime "
                            f"column; {key!r} has dtype {df[key].dtype}"
                        )
                has_group_result = True

            case AggregateGroups():
                if not has_group_result:
                    raise PlanSchemaError(
                        "AGGREGATE_GROUPS requires a preceding GROUP_AGGREGATE step"
                    )
                pending_scalar_count += 1

            case RankGroups():
                if not has_group_result:
                    raise PlanSchemaError(
                        "RANK_GROUPS requires a preceding GROUP_AGGREGATE step"
                    )

            case RankRows():
                _require_column(step.column, available, step.op)
                _require_numeric(step.column, df, step.op)
                for column in step.return_columns:
                    _require_column(column, available, step.op)

            case SplitByThreshold():
                _require_column(step.column, available, step.op)
                _require_numeric(step.column, df, step.op)
                partitions.add(step.label)

            case SplitByValues():
                _require_column(step.column, available, step.op)
                partitions.add(step.label)

            case AggregatePartitions():
                missing = [p for p in step.partitions if p not in partitions]
                if missing:
                    raise PlanSchemaError(
                        f"AGGREGATE_PARTITIONS references undefined partition(s): {missing}"
                    )
                _validate_aggregate(step.column, step.aggregate, df, available, step.op)
                has_partition_aggregate = True

            case ComparePartitions():
                if not has_partition_aggregate:
                    raise PlanSchemaError(
                        "COMPARE_PARTITIONS requires a preceding AGGREGATE_PARTITIONS step"
                    )

            case CompareValues():
                if pending_scalar_count != 2:
                    raise PlanSchemaError(
                        "COMPARE_VALUES requires exactly two preceding AGGREGATE_COLUMN or "
                        "AGGREGATE_GROUPS steps (with no COMPARE_VALUES between them); "
                        f"found {pending_scalar_count}"
                    )
                pending_scalar_count = 0

            case ParallelAggregate():
                # All branches must use the same group_by keys
                reference_keys = step.branches[0].group_by
                for i, branch in enumerate(step.branches):
                    if branch.group_by != reference_keys:
                        raise PlanSchemaError(
                            f"PARALLEL_AGGREGATE: branch {i} has group_by={branch.group_by!r}, "
                            f"expected {reference_keys!r} (all branches must group by same keys)"
                        )

                    # Validate filter column if present
                    if branch.filter_column is not None:
                        _require_column(branch.filter_column, available, "PARALLEL_AGGREGATE")

                    # Validate group keys
                    for key in branch.group_by:
                        _require_column(key, available, "PARALLEL_AGGREGATE")

                    # Validate aggregation
                    _validate_aggregate(
                        branch.column,
                        branch.aggregate,
                        df,
                        available,
                        "PARALLEL_AGGREGATE"
                    )

                # After PARALLEL_AGGREGATE, working frame only has group keys + result columns
                available = {*reference_keys, *(b.result_column for b in step.branches)}

            case PredictivePipeline():
                for column in step.feature_columns:
                    _require_column(column, available, step.op)
                    _require_numeric(column, df, step.op)
                for column in step.sort_by:
                    _require_column(column, available, step.op)
                if step.filter_column is not None:
                    _require_column(step.filter_column, available, step.op)
                    if step.filter_value is None:
                        raise PlanSchemaError(
                            "PREDICTIVE_PIPELINE filter_column requires filter_value"
                        )
                _require_column(step.target_column, available, step.op)
                if step.target_column in step.feature_columns:
                    raise PlanSchemaError(
                        "PREDICTIVE_PIPELINE target_column must not also be a feature"
                    )


# ---------------------------------------------------------------------------
# Predictive model factory (mirrors eval/build_groundtruth/simple_pred.py)
# ---------------------------------------------------------------------------

PREDICTIVE_RANDOM_SEED = 42

_PREDICTIVE_MODEL_DISPLAY: dict[str, str] = {
    "logistic_regression": "Logistic regression",
    "random_forest": "Random forest",
    "one_nearest_neighbor": "1-nearest-neighbor",
    "hist_gradient_boosting": "Hist gradient boosting",
}


def _build_predictive_model(model: str):
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    match model:
        case "logistic_regression":
            return make_pipeline(
                StandardScaler(),
                LogisticRegression(random_state=PREDICTIVE_RANDOM_SEED, max_iter=2000),
            )
        case "random_forest":
            return RandomForestClassifier(
                n_estimators=300, random_state=PREDICTIVE_RANDOM_SEED, n_jobs=-1
            )
        case "hist_gradient_boosting":
            return HistGradientBoostingClassifier(
                max_iter=200,
                learning_rate=0.08,
                max_leaf_nodes=31,
                l2_regularization=1.0,
                random_state=PREDICTIVE_RANDOM_SEED,
            )
        case "one_nearest_neighbor":
            return make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=1))
    raise PlanExecutionError(f"Unsupported predictive model {model!r}")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class _State:
    original: pd.DataFrame
    working: pd.DataFrame
    observations: list[Any] = field(default_factory=list)
    partitions: dict[str, pd.DataFrame] = field(default_factory=dict)
    group_result: pd.Series | None = None
    group_label: str = "group"
    group_metric: str = "value"
    partition_result: dict[str, Any] = field(default_factory=dict)
    partition_metric: str = "value"
    columns_used: set[str] = field(default_factory=set)
    # (label, value) pairs from AGGREGATE_COLUMN/AGGREGATE_GROUPS, consumed by COMPARE_VALUES.
    scalar_trail: list[tuple[str, Any]] = field(default_factory=list)

    def use(self, *columns: str) -> None:
        self.columns_used.update(c for c in columns if c)


def _run_predictive(step: PredictivePipeline, state: _State) -> tuple[Any, str]:
    frame = state.original
    state.use(*step.feature_columns, *step.sort_by, step.target_column)

    if step.filter_column is not None:
        state.use(step.filter_column)
        value = _coerce_value(frame[step.filter_column], step.filter_value)
        frame = frame[frame[step.filter_column] == value]
        if frame.empty:
            raise PlanExecutionError(
                f"Predictive filter produced no rows: "
                f"{step.filter_column}=={step.filter_value!r}"
            )

    frame = frame.sort_values(list(step.sort_by)).reset_index(drop=True)

    if step.target_from_non_empty:
        target = (
            frame[step.target_column].astype(str).str.strip().ne("").astype(int).to_numpy()
        )
    else:
        target = frame[step.target_column].astype(str).to_numpy()

    features = frame[list(step.feature_columns)].to_numpy(dtype=float)

    n_rows = len(frame)
    split = max(1, min(int(math.floor(n_rows * step.train_fraction)), n_rows - 1))
    if split >= n_rows:
        raise PlanExecutionError("Predictive split leaves no holdout rows")

    x_train, y_train = features[:split], target[:split]
    holdout_index = split if step.holdout_row == "first" else n_rows - 1
    x_test = features[holdout_index].reshape(1, -1)

    train_classes = pd.unique(y_train)
    if len(train_classes) == 1:
        prediction = train_classes[0]
    else:
        model = _build_predictive_model(step.model)
        model.fit(x_train, y_train)
        prediction = model.predict(x_test)[0]

    display = _PREDICTIVE_MODEL_DISPLAY.get(step.model, step.model)
    answer = (
        f"{display} predicts {step.target_label} "
        f"'{_to_python(prediction)}' for the {step.holdout_row} holdout row."
    )
    code = (
        f"# sort_by={list(step.sort_by)!r} split={split}/{n_rows} "
        f"model={step.model!r} features={list(step.feature_columns)!r}\n"
        f"result = {answer!r}"
    )
    return answer, code


def _execute_step(step: TypedOperator, state: _State) -> tuple[Any, str]:
    """Run one typed operator. Returns ``(observation, equivalent_code)``."""
    match step:
        case FilterCompare():
            state.use(step.column)
            value = _coerce_value(state.working[step.column], step.value)
            state.working = state.working[
                _compare(state.working[step.column], step.comparator, value)
            ]
            return (
                f"rows={len(state.working)}",
                f"df = df[df[{step.column!r}] {step.comparator} {value!r}]",
            )

        case FilterIn():
            state.use(step.column)
            values = [_coerce_value(state.working[step.column], v) for v in step.values]
            state.working = state.working[state.working[step.column].isin(values)]
            return (
                f"rows={len(state.working)}",
                f"df = df[df[{step.column!r}].isin({values!r})]",
            )

        case FilterNotEmpty():
            state.use(step.column)
            series = state.working[step.column]
            mask = series.notna()
            if not pd.api.types.is_numeric_dtype(series):
                mask &= series.astype(str).str.strip().ne("")
            state.working = state.working[mask]
            return (
                f"rows={len(state.working)}",
                f"df = df[df[{step.column!r}].notna()]",
            )

        case FilterEqAggregate():
            state.use(step.column)
            target = _aggregate_series(state.working[step.column], step.aggregate)
            state.working = state.working[state.working[step.column] == target]
            return (
                f"rows={len(state.working)} ({step.column}=={_to_python(target)})",
                f"_v = df[{step.column!r}].{step.aggregate}(); "
                f"df = df[df[{step.column!r}] == _v]",
            )

        case AggregateColumn():
            state.use(step.column)
            value = _to_python(
                _aggregate_series(state.working[step.column], step.aggregate)
            )
            state.observations.append(value)
            state.scalar_trail.append((f"{step.column} {step.aggregate}", value))
            return value, f"result = df[{step.column!r}].{step.aggregate}()"

        case CountRows():
            value = int(len(state.working))
            state.observations.append(value)
            return value, "result = len(df)"

        case CountDistinct():
            state.use(step.column)
            value = int(state.working[step.column].nunique())
            state.observations.append(value)
            return value, f"result = df[{step.column!r}].nunique()"

        case SelectColumn():
            state.use(step.column)
            series = state.working[step.column]
            if step.distinct:
                series = series.drop_duplicates()
            value = [_to_python(v) for v in series.tolist()]
            state.observations.append(value)
            return value, f"result = df[{step.column!r}].tolist()"

        case DeriveBinary():
            state.use(step.left, step.right)
            left = state.working[step.left]
            right = state.working[step.right]
            match step.operation:
                case "add":
                    derived, symbol = left + right, "+"
                case "subtract":
                    derived, symbol = left - right, "-"
                case "multiply":
                    derived, symbol = left * right, "*"
                case "divide":
                    derived, symbol = left / right, "/"
                case "abs_difference":
                    derived, symbol = (left - right).abs(), "- (abs)"
            # Apply to both working and original so partitions can access derived columns
            state.working = state.working.assign(**{step.result: derived})
            # Only update original if source columns exist there (they may have been created by PARALLEL_AGGREGATE)
            if step.left in state.original.columns and step.right in state.original.columns:
                left_orig = state.original[step.left]
                right_orig = state.original[step.right]
                match step.operation:
                    case "add":
                        derived_orig = left_orig + right_orig
                    case "subtract":
                        derived_orig = left_orig - right_orig
                    case "multiply":
                        derived_orig = left_orig * right_orig
                    case "divide":
                        derived_orig = left_orig / right_orig
                    case "abs_difference":
                        derived_orig = (left_orig - right_orig).abs()
                state.original = state.original.assign(**{step.result: derived_orig})
            state.use(step.result)
            return (
                f"derived {step.result!r} (rows={len(state.working)})",
                f"df[{step.result!r}] = df[{step.left!r}] {symbol} df[{step.right!r}]",
            )

        case DeriveVectorMagnitude():
            a, b, c = step.columns
            state.use(a, b, c)
            derived = (
                state.working[a].pow(2)
                + state.working[b].pow(2)
                + state.working[c].pow(2)
            ).pow(0.5)
            # Apply to both working and original so partitions can access derived columns
            state.working = state.working.assign(**{step.result: derived})
            # Only update original if source columns exist there (they may have been created by PARALLEL_AGGREGATE)
            if all(col in state.original.columns for col in [a, b, c]):
                derived_orig = (
                    state.original[a].pow(2)
                    + state.original[b].pow(2)
                    + state.original[c].pow(2)
                ).pow(0.5)
                state.original = state.original.assign(**{step.result: derived_orig})
            state.use(step.result)
            return (
                f"derived {step.result!r} (rows={len(state.working)})",
                f"df[{step.result!r}] = (df[{a!r}]**2 + df[{b!r}]**2 + df[{c!r}]**2)**0.5",
            )

        case DeriveBin():
            state.use(step.column)
            derived = _bin_series(state.working[step.column], step.width)
            # Apply to both working and original so partitions can access derived columns
            state.working = state.working.assign(**{step.result: derived})
            # Only update original if source column exists there (it may have been created by PARALLEL_AGGREGATE)
            if step.column in state.original.columns:
                derived_orig = _bin_series(state.original[step.column], step.width)
                state.original = state.original.assign(**{step.result: derived_orig})
            state.use(step.result)
            return (
                f"derived {step.result!r} (width={step.width})",
                f"df[{step.result!r}] = (df[{step.column!r}] // {step.width}) * {step.width}",
            )

        case GroupAggregate():
            state.use(*step.group_by, step.column or "")
            if step.freq is not None:
                grouper = pd.Grouper(key=step.group_by[0], freq=step.freq)
                grouped = state.working.groupby(grouper)
                code_key = f"pd.Grouper(key={step.group_by[0]!r}, freq={step.freq!r})"
            else:
                keys = step.group_by[0] if len(step.group_by) == 1 else list(step.group_by)
                grouped = state.working.groupby(keys, dropna=False)
                code_key = repr(keys)

            if step.column is None:
                series = grouped.size()
                metric = "count"
                code = f"result = df.groupby({code_key}).size()"
            elif step.aggregate == "rms":
                series = grouped[step.column].apply(lambda s: _aggregate_series(s, "rms"))
                metric = step.column
                code = f"result = df.groupby({code_key})[{step.column!r}].apply(rms)"
            else:
                series = grouped[step.column].agg(step.aggregate)
                metric = step.column
                code = (
                    f"result = df.groupby({code_key})[{step.column!r}]"
                    f".{step.aggregate}()"
                )

            state.group_result = series
            state.group_label = (
                step.group_by[0] if len(step.group_by) == 1 else "+".join(step.group_by)
            )
            state.group_metric = metric
            value = {str(_to_python(k)): _to_python(v) for k, v in series.items()}
            state.observations.append(value)
            return value, code

        case AggregateGroups():
            if state.group_result is None:
                raise PlanExecutionError("AGGREGATE_GROUPS has no grouped result")
            value = _to_python(_aggregate_series(state.group_result, step.aggregate))
            state.observations.append(value)
            state.scalar_trail.append((f"{state.group_metric} {step.aggregate}", value))
            return value, f"result = grouped.{step.aggregate}()"

        case RankGroups():
            if state.group_result is None or state.group_result.empty:
                raise PlanExecutionError("RANK_GROUPS has no grouped result")
            key = (
                state.group_result.idxmax()
                if step.direction == "max"
                else state.group_result.idxmin()
            )
            value = {
                state.group_label: str(_to_python(key)),
                state.group_metric: _to_python(state.group_result.loc[key]),
            }
            state.observations.append(value)
            return value, f"result = grouped.idx{step.direction}()"

        case RankRows():
            state.use(step.column, *step.return_columns)
            if state.working.empty:
                raise PlanExecutionError("RANK_ROWS received no rows after filtering")
            index = (
                state.working[step.column].idxmax()
                if step.direction == "max"
                else state.working[step.column].idxmin()
            )
            value = {
                column: _to_python(state.working.loc[index, column])
                for column in step.return_columns
            }
            value.setdefault(
                step.column, _to_python(state.working.loc[index, step.column])
            )
            state.observations.append(value)
            return (
                value,
                f"idx = df[{step.column!r}].idx{step.direction}(); "
                f"result = df.loc[idx, {list(step.return_columns)!r}].to_dict()",
            )

        case SplitByThreshold():
            state.use(step.column)
            threshold = getattr(state.original[step.column], step.threshold)()
            subset = state.original[
                _compare(state.original[step.column], step.comparator, threshold)
            ]
            state.partitions[step.label] = subset
            return (
                f"{step.label}: rows={len(subset)} ({step.column} {step.comparator} "
                f"{step.threshold}={_to_python(threshold)})",
                f"{step.label} = df[df[{step.column!r}] {step.comparator} "
                f"df[{step.column!r}].{step.threshold}()]",
            )

        case SplitByValues():
            state.use(step.column)
            values = [_coerce_value(state.original[step.column], v) for v in step.values]
            subset = state.original[state.original[step.column].isin(values)]
            state.partitions[step.label] = subset
            return (
                f"{step.label}: rows={len(subset)}",
                f"{step.label} = df[df[{step.column!r}].isin({values!r})]",
            )

        case AggregatePartitions():
            state.use(step.column or "")
            result: dict[str, Any] = {}
            for label in step.partitions:
                subset = state.partitions.get(label)
                if subset is None:
                    raise PlanExecutionError(f"Unknown partition {label!r}")
                if step.column is None:
                    result[label] = int(len(subset))
                else:
                    result[label] = _to_python(
                        _aggregate_series(subset[step.column], step.aggregate)
                    )
            state.partition_result = result
            state.partition_metric = (
                f"{step.aggregate} {step.column}" if step.column else "row count"
            )
            state.observations.append(result)
            return result, "result = {label: agg(partition) for label in partitions}"

        case ComparePartitions():
            if len(state.partition_result) < 2:
                raise PlanExecutionError(
                    "COMPARE_PARTITIONS requires two aggregated partitions"
                )
            (label_a, value_a), (label_b, value_b) = list(
                state.partition_result.items()
            )[:2]
            match step.mode:
                case "difference":
                    delta: Any = value_a - value_b
                case "abs_difference":
                    delta = abs(value_a - value_b)
                case "ratio":
                    delta = value_a / value_b if value_b else float("nan")
            higher, lower = (
                (label_a, label_b) if value_a >= value_b else (label_b, label_a)
            )
            value = {
                "higher": higher,
                "lower": lower,
                "metric": state.partition_metric,
                label_a: value_a,
                label_b: value_b,
                step.mode: delta,
            }
            state.observations.append(value)
            return value, f"result = compare({label_a}, {label_b}, mode={step.mode!r})"

        case CompareValues():
            if len(state.scalar_trail) < 2:
                raise PlanExecutionError(
                    "COMPARE_VALUES requires two preceding AGGREGATE_COLUMN/"
                    "AGGREGATE_GROUPS steps"
                )
            (metric_a, value_a), (metric_b, value_b) = state.scalar_trail[-2:]
            match step.mode:
                case "difference":
                    delta: Any = value_a - value_b
                case "abs_difference":
                    delta = abs(value_a - value_b)
                case "ratio":
                    delta = value_a / value_b if value_b else float("nan")
            higher, lower = (
                (step.label_a, step.label_b)
                if value_a >= value_b
                else (step.label_b, step.label_a)
            )
            value = {
                "higher": higher,
                "lower": lower,
                step.label_a: value_a,
                step.label_b: value_b,
                f"{step.label_a}_metric": metric_a,
                f"{step.label_b}_metric": metric_b,
                step.mode: delta,
            }
            state.observations.append(value)
            return (
                value,
                f"result = compare({step.label_a}={value_a!r}, "
                f"{step.label_b}={value_b!r}, mode={step.mode!r})",
            )

        case ParallelAggregate():
            group_keys = step.shared_group_keys
            state.use(*group_keys)

            # Execute each branch independently on the ORIGINAL dataframe
            branch_results: list[pd.DataFrame] = []
            code_lines = ["# PARALLEL_AGGREGATE branches:"]

            for i, branch in enumerate(step.branches):
                # Start with original frame
                branch_df = state.original.copy()

                # Apply filter if specified
                if branch.filter_column is not None and branch.filter_values is not None:
                    state.use(branch.filter_column)
                    values = [
                        _coerce_value(state.original[branch.filter_column], v)
                        for v in branch.filter_values
                    ]
                    branch_df = branch_df[branch_df[branch.filter_column].isin(values)]
                    code_lines.append(
                        f"# Branch {i}: filter {branch.filter_column!r} in {values!r}"
                    )

                # Group and aggregate
                if branch.column is not None:
                    state.use(branch.column)

                grouped = branch_df.groupby(group_keys, dropna=False)

                if branch.column is None:
                    # Count group sizes
                    aggregated = grouped.size()
                elif branch.aggregate == "rms":
                    aggregated = grouped[branch.column].apply(
                        lambda s: _aggregate_series(s, "rms")
                    )
                else:
                    aggregated = grouped[branch.column].agg(branch.aggregate)

                # Convert to DataFrame with named result column
                result_df = aggregated.reset_index(name=branch.result_column)
                branch_results.append(result_df)

                agg_method = "size()" if branch.column is None else f"[{branch.column!r}].{branch.aggregate}()"
                code_lines.append(
                    f"branch_{i} = df.groupby({group_keys!r}){agg_method}"
                )

            # Merge all branch results on group keys (outer join, fill NaN with 0)
            merged = branch_results[0]
            for i, branch_df in enumerate(branch_results[1:], start=1):
                merged = merged.merge(branch_df, on=group_keys, how="outer")

            # Fill NaN with 0 for all result columns
            result_columns = [b.result_column for b in step.branches]
            merged[result_columns] = merged[result_columns].fillna(0)

            # Update working frame
            state.working = merged

            observation = {
                "groups": len(merged),
                "columns": [*group_keys, *result_columns]
            }
            state.observations.append(observation)

            code_lines.append(
                f"merged = branch_0.merge(branch_1, on={group_keys!r}, how='outer').fillna(0)"
            )

            return (observation, "\n".join(code_lines))

        case PredictivePipeline():
            value, code = _run_predictive(step, state)
            state.observations.append(value)
            return value, code

    raise PlanExecutionError(f"Unhandled operator: {type(step).__name__}")


def execute_plan(df: pd.DataFrame, plan: DeterministicPlan) -> PlanExecution:
    """Execute a validated plan in-process. No codegen, no sandbox, no LLM.

    The caller is expected to have already run both gates
    (``structural_validate`` and ``validate_plan_against_dataframe``). Any
    failure here is returned as ``ok=False`` with an ``error`` message rather
    than raised, so the caller can log a coverage gap and fall back to ReAct.
    """
    started = time.perf_counter()
    state = _State(original=df, working=df)
    steps: list[dict[str, Any]] = []
    code_lines: list[str] = []
    trace_lines: list[str] = []

    def _failure(message: str) -> PlanExecution:
        return PlanExecution(
            ok=False,
            error=message,
            plan_kind=plan.kind,
            rows_scanned=len(df),
            rows_after_filter=len(state.working),
            columns_used=sorted(state.columns_used),
            operators_used=plan.operators_used,
            code="\n".join(code_lines),
            trace="\n".join(trace_lines),
            steps=steps,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    for index, step in enumerate(plan.steps, start=1):
        try:
            observation, code = _execute_step(step, state)
        except (PlanSchemaError, PlanExecutionError) as exc:
            return _failure(f"{step.op}: {exc}")
        except Exception as exc:  # noqa: BLE001 — surfaced as a coverage gap
            return _failure(f"{step.op}: {type(exc).__name__}: {exc}")

        steps.append(
            {"step": index, "op": step.op, "ok": True, "output": str(observation)}
        )
        code_lines.append(code)
        trace_lines.extend(
            [
                f"Thought: typed operator step {index} ({step.op})",
                "Action: typed_operator_exec",
                f"Action Input: {code}",
                f"Observation: {observation}",
            ]
        )

    value = (
        state.observations[-1] if state.observations else f"rows={len(state.working)}"
    )
    trace_lines.append(f"Final Answer: {value}")
    return PlanExecution(
        value=value,
        ok=True,
        error=None,
        plan_kind=plan.kind,
        rows_scanned=len(df),
        rows_after_filter=len(state.working),
        columns_used=sorted(state.columns_used),
        operators_used=plan.operators_used,
        code="\n".join(code_lines),
        trace="\n".join(trace_lines),
        steps=steps,
        latency_ms=round((time.perf_counter() - started) * 1000, 3),
    )


# ---------------------------------------------------------------------------
# Plan parsing (Gate 1)
# ---------------------------------------------------------------------------


def structural_validate(raw: Any) -> DeterministicPlan:
    """Gate 1: structural validation of a raw plan payload.

    Raises:
        ValidationError: unknown op, missing field, or wrong type.
    """
    if isinstance(raw, DeterministicPlan):
        return raw
    return DeterministicPlan.model_validate(raw)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body.lower().startswith("json"):
        body = body[4:]
    closing = body.rfind("```")
    return (body[:closing] if closing != -1 else body).strip()


def parse_guardrail_and_plan(payload: str | dict[str, Any]) -> GuardrailAndPlan:
    """Parse the single-round-trip guardrail+plan response (Gate 1).

    Raises:
        ValidationError: the response is not a valid GuardrailAndPlan.
        ValueError: the response contains no JSON object at all.
    """
    if isinstance(payload, dict):
        return GuardrailAndPlan.model_validate(payload)

    text = _strip_code_fence(payload)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in guardrail+plan response")
    return GuardrailAndPlan.model_validate_json(text[start : end + 1])


# ---------------------------------------------------------------------------
# Offline vocabulary growth — coverage gap log
# ---------------------------------------------------------------------------


def log_operator_gap(
    *,
    query: str,
    stage: str,
    error: str,
    raw_plan: Any = None,
    dataset: str = "",
) -> None:
    """Append a coverage gap to the offline review log.

    Gaps are reviewed **between** runs: a missing operator is designed, tested,
    and versioned into the vocabulary offline. Nothing here mutates the
    vocabulary at request time.

    Args:
        stage: "structural", "schema", "execution", or "no_plan".
    """
    path = Path(
        os.getenv(
            "FF_OPERATOR_GAP_LOG",
            str(Path(__file__).resolve().parents[1] / "results" / "operator_gaps.jsonl"),
        )
    )
    record = {
        "logged_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": dataset,
        "query": query,
        "stage": stage,
        "error": error,
        "raw_plan": raw_plan,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError:
        # Gap logging is diagnostic only; never fail a query because of it.
        pass


# ---------------------------------------------------------------------------
# Prompt-facing vocabulary description
# ---------------------------------------------------------------------------

OPERATOR_VOCABULARY_SPEC: str = """\
EXECUTION MODEL:
- Plans are sequential by default: each step reads the current working frame.
- FILTER_* steps narrow the working frame (rows removed).
- DERIVE_* steps add columns to both working and original frames.
- GROUP_AGGREGATE produces an internal grouped result for RANK_GROUPS or AGGREGATE_GROUPS.
  It does NOT create DataFrame columns and does NOT accept result_column.
- PARALLEL_AGGREGATE is the ONLY operator that creates multiple independent branches from
  the ORIGINAL dataframe and produces a merged working frame with new columns.
- After GROUP_AGGREGATE, only RANK_GROUPS or AGGREGATE_GROUPS can consume the result.
- After PARALLEL_AGGREGATE, the working frame contains group keys + result_columns.
- COMPARE_VALUES reduces exactly two preceding AGGREGATE_COLUMN/AGGREGATE_GROUPS scalars into one
  comparison — it works no matter what produced those scalars (a plain filter, a PARALLEL_AGGREGATE
  merge, a GROUP_AGGREGATE). RANK_ROWS/RANK_GROUPS answer "which ROW/GROUP is extreme"; COMPARE_VALUES
  and COMPARE_PARTITIONS answer "what is the difference between these two numbers" — never use
  RANK_ROWS as a substitute for a two-value comparison.

OPERATORS:

FILTER_COMPARE      {"op":"FILTER_COMPARE","column":str,"comparator":"eq|ne|gt|gte|lt|lte","value":scalar}
FILTER_IN           {"op":"FILTER_IN","column":str,"values":[scalar,...]}
FILTER_NOT_EMPTY    {"op":"FILTER_NOT_EMPTY","column":str}

FILTER_EQ_AGGREGATE {"op":"FILTER_EQ_AGGREGATE","column":str,"aggregate":AGG}   rows where column == AGG(column)
                    USE FOR: rows where a column reaches its maximum or minimum;
                    this preserves every tied row. Follow with SELECT_COLUMN to return
                    another field from those rows. Never nest an operator object inside
                    FILTER_COMPARE.value; it must always be a literal scalar.

AGGREGATE_COLUMN    {"op":"AGGREGATE_COLUMN","column":str,"aggregate":AGG}
COUNT_ROWS          {"op":"COUNT_ROWS"}
COUNT_DISTINCT      {"op":"COUNT_DISTINCT","column":str}
SELECT_COLUMN       {"op":"SELECT_COLUMN","column":str,"distinct":bool}
DERIVE_BINARY       {"op":"DERIVE_BINARY","left":str,"right":str,"operation":"add|subtract|multiply|divide|abs_difference","result":str}
                    CRITICAL: left and right must be EXISTING COLUMN NAMES (strings), never nested operator objects,
                    and NEVER a numeric literal (e.g. 60000000000) or a time constant — DERIVE_BINARY is
                    column-vs-column arithmetic ONLY. If one side of the arithmetic is a constant/scalar number
                    rather than a real column name, DERIVE_BINARY is the WRONG operator — use DERIVE_BIN instead
                    (see below) when the constant is a bucket width, or set "plan" to null if no operator fits.
                    operation must be an ARITHMETIC operation, never a comparator like "gt" or "lt".
                    Use this ONLY after PARALLEL_AGGREGATE to combine result_column values, or to combine
                    existing columns. Cannot compute medians/aggregates inline — use SPLIT_BY_THRESHOLD for that.
DERIVE_VECTOR_MAGNITUDE {"op":"DERIVE_VECTOR_MAGNITUDE","columns":[str,str,str],"result":str}
DERIVE_BIN          {"op":"DERIVE_BIN","column":str,"width":number,"result":str}   floor(col/width)*width
                    USE FOR: bucketing/grouping a column into fixed-size intervals against a CONSTANT width,
                    e.g. "group timestamps into 1-minute windows" → column="timestamp", width=60000000000
                    (nanoseconds per minute) or the correct unit for this column's dtype. This is the ONLY
                    operator that divides a column by a literal scalar — DERIVE_BINARY must never be used
                    for this because its "right" operand must be a column name, not a constant.
                    After DERIVE_BIN, typically follow with GROUP_AGGREGATE(group_by=[result], ...) to
                    aggregate a metric within each bucket, then RANK_GROUPS to find the extreme bucket.

GROUP_AGGREGATE     {"op":"GROUP_AGGREGATE","group_by":[str,...],"aggregate":AGG,"column":str|null,"freq":str|null}
                    USE WHEN: One filtered subset needs one grouped metric and the answer is the highest/lowest
                    group or a scalar reduction of that grouped metric.
                    OUTPUT: Internal grouped result consumed ONLY by RANK_GROUPS or AGGREGATE_GROUPS.
                    DO NOT USE: When comparing two or more independently filtered subsets per group.

AGGREGATE_GROUPS    {"op":"AGGREGATE_GROUPS","aggregate":AGG}      reduce the previous GROUP_AGGREGATE result
RANK_GROUPS         {"op":"RANK_GROUPS","direction":"max|min"}     best group from the previous GROUP_AGGREGATE

RANK_ROWS           {"op":"RANK_ROWS","column":str,"direction":"max|min","return_columns":[str,...]}
                    USE WHEN: You need the row with max/min value from a DataFrame (after filters, derives, or
                    PARALLEL_AGGREGATE). REQUIRES: A working frame with actual rows and columns.

PARALLEL_AGGREGATE  {"op":"PARALLEL_AGGREGATE","branches":[{"filter_column":str|null,"filter_values":[scalar,...]|null,
                     "group_by":[str,...],"aggregate":AGG,"column":str|null,"result_column":str},...]}
                    USE WHEN: The question compares, combines, or ranks metrics from TWO OR MORE independently
                    filtered subsets PER SHARED GROUPING KEY (e.g., "compare resting vs dynamic activity per subject",
                    "which user has the largest difference between X and Y"). There MUST be a grouping dimension
                    (subject_id, user_id, etc.) that appears in every branch's group_by field.
                    DO NOT USE when comparing two halves of the entire dataset with no per-entity grouping — use
                    SPLIT_BY_THRESHOLD + AGGREGATE_PARTITIONS + COMPARE_PARTITIONS for that pattern instead.
                    EXECUTION: Every branch starts from ORIGINAL dataframe, filters its own rows, groups by the
                    SAME keys, aggregates, then all branch outputs are outer-merged into the working frame.
                    OUTPUT: A working frame with columns [group_by keys, result_column_1, result_column_2, ...].
                    NEXT STEPS depend on what the question asks for:
                    - "which entity/subject/user is highest/lowest" → RANK_ROWS(column=a result_column).
                    - "what is entity X's difference between the two metrics" → DERIVE_BINARY on the two
                      result_columns, then RANK_ROWS or SELECT_COLUMN.
                    - "what is the OVERALL/AGGREGATE difference between the two metrics across ALL entities"
                      (no single entity singled out) → AGGREGATE_COLUMN(column=result_column_1) then
                      AGGREGATE_COLUMN(column=result_column_2) then COMPARE_VALUES. Do NOT use RANK_ROWS for
                      this — RANK_ROWS picks one entity's row, which silently answers a different question.
                    DO NOT use GROUP_AGGREGATE before, after, or inside this pattern.
                    CRITICAL: group_by must contain at least one column name — NEVER use an empty list.
                    CRITICAL: "column" must be null ONLY when aggregate is "count". For every other aggregate
                    (mean, sum, median, std, var, rms, min, max, nunique) column is REQUIRED and must be a real
                    column name — never leave it null "by default".
                    CRITICAL: if the metric to aggregate is NOT a raw column (e.g. "acceleration magnitude",
                    "vector magnitude", "overall intensity"), it does not exist yet — emit a DERIVE_* step
                    (e.g. DERIVE_VECTOR_MAGNITUDE) BEFORE this operator to materialize it, then reference that
                    step's "result" name as every branch's "column". Derived columns are written to BOTH the
                    working and original frames, so a DERIVE_* step run before PARALLEL_AGGREGATE is visible to
                    every branch.
                    Example (raw column, group A vs group B duration, per entity):
                    {"op":"PARALLEL_AGGREGATE","branches":[
                      {"filter_column":"category_col","filter_values":["cat_1","cat_2"],
                       "group_by":["entity_id"],"aggregate":"sum","column":"duration_col","result_column":"a_duration"},
                      {"filter_column":"category_col","filter_values":["cat_3","cat_4"],
                       "group_by":["entity_id"],"aggregate":"sum","column":"duration_col","result_column":"b_duration"}
                    ]}
                    Example (derived metric — a DERIVE_* step must run first, group A vs group B metric
                    PER ENTITY, then find which entity differs most — "per entity"/"which X" is explicit):
                    [{"op":"DERIVE_VECTOR_MAGNITUDE","columns":["c1","c2","c3"],"result":"derived_metric"},
                     {"op":"PARALLEL_AGGREGATE","branches":[
                       {"filter_column":"category_col","filter_values":["cat_1","cat_2"],
                        "group_by":["entity_id"],"aggregate":"mean","column":"derived_metric","result_column":"a_mean_metric"},
                       {"filter_column":"category_col","filter_values":["cat_3","cat_4"],
                        "group_by":["entity_id"],"aggregate":"mean","column":"derived_metric","result_column":"b_mean_metric"}
                     ]},
                     {"op":"RANK_ROWS","column":"b_mean_metric","direction":"max",
                      "return_columns":["entity_id","a_mean_metric","b_mean_metric"]}]
                    Example (per-entity breakdown reduced to ONE overall comparison — "which entity" is NOT
                    asked; the question wants a single overall number, so RANK_ROWS would be wrong here):
                    [{"op":"DERIVE_VECTOR_MAGNITUDE","columns":["c1","c2","c3"],"result":"derived_metric"},
                     {"op":"PARALLEL_AGGREGATE","branches":[
                       {"filter_column":"category_col","filter_values":["cat_1","cat_2"],
                        "group_by":["entity_id"],"aggregate":"mean","column":"derived_metric","result_column":"a_mean_metric"},
                       {"filter_column":"category_col","filter_values":["cat_3","cat_4"],
                        "group_by":["entity_id"],"aggregate":"mean","column":"derived_metric","result_column":"b_mean_metric"}
                     ]},
                     {"op":"AGGREGATE_COLUMN","column":"a_mean_metric","aggregate":"mean"},
                     {"op":"AGGREGATE_COLUMN","column":"b_mean_metric","aggregate":"mean"},
                     {"op":"COMPARE_VALUES","mode":"difference","label_a":"group_a","label_b":"group_b"}]
                    NOTE: this two-level mean-of-per-entity-means is NOT the same number as pooling every row
                    directly. If the question has NO per-entity framing at all ("overall", "in general", no
                    mention of entity/user/per-X), prefer SPLIT_BY_VALUES + AGGREGATE_PARTITIONS +
                    COMPARE_PARTITIONS instead (see below) — it aggregates over rows directly, matching what
                    "overall average" normally means.

SPLIT_BY_THRESHOLD  {"op":"SPLIT_BY_THRESHOLD","column":str,"comparator":"gt|gte|lt|lte","threshold":"median|mean|min|max","label":str}
SPLIT_BY_VALUES     {"op":"SPLIT_BY_VALUES","column":str,"values":[scalar,...],"label":str}
AGGREGATE_PARTITIONS {"op":"AGGREGATE_PARTITIONS","partitions":[label,label],"aggregate":AGG,"column":str|null}
COMPARE_PARTITIONS  {"op":"COMPARE_PARTITIONS","mode":"difference|abs_difference|ratio"}
                    USE: SPLIT_BY_* + AGGREGATE_PARTITIONS + COMPARE_PARTITIONS for "compare A versus B" questions
                    when the comparison is based on static partitions, not per-group aggregates.
                    Example (comparing above-median vs below-median half by a numeric column):
                    [{"op":"SPLIT_BY_THRESHOLD","column":"numeric_col","comparator":"gt","threshold":"median","label":"above_median"},
                     {"op":"SPLIT_BY_THRESHOLD","column":"numeric_col","comparator":"lte","threshold":"median","label":"below_median"},
                     {"op":"AGGREGATE_PARTITIONS","partitions":["above_median","below_median"],"aggregate":"mean","column":"metric_col"},
                     {"op":"COMPARE_PARTITIONS","mode":"difference"}]
                    Example (overall group A vs group B derived metric, NO per-entity framing — "the overall X
                    between A and B" with no mention of entity/user/per-X means pool ALL matching rows directly):
                    [{"op":"DERIVE_VECTOR_MAGNITUDE","columns":["c1","c2","c3"],"result":"derived_metric"},
                     {"op":"SPLIT_BY_VALUES","column":"category_col","values":["cat_1","cat_2"],"label":"group_a"},
                     {"op":"SPLIT_BY_VALUES","column":"category_col",
                      "values":["cat_3","cat_4"],"label":"group_b"},
                     {"op":"AGGREGATE_PARTITIONS","partitions":["group_a","group_b"],"aggregate":"mean","column":"derived_metric"},
                     {"op":"COMPARE_PARTITIONS","mode":"difference"}]
                    This pattern compares TWO DATASET HALVES (pooled row-level statistic — one number per side).
                    If the question asks to compare metrics PER ENTITY (e.g., "per subject", "per user", "for
                    each X", "which user"), use PARALLEL_AGGREGATE instead.

COMPARE_VALUES      {"op":"COMPARE_VALUES","mode":"difference|abs_difference|ratio","label_a":str,"label_b":str}
                    USE: Generalized version of COMPARE_PARTITIONS — compares the two most recently computed
                    scalar aggregates, from ANY source (not just AGGREGATE_PARTITIONS). REQUIRES exactly two
                    immediately-preceding AGGREGATE_COLUMN or AGGREGATE_GROUPS steps, with nothing else of that
                    kind in between. Typical use: reducing a PARALLEL_AGGREGATE per-entity breakdown down to one
                    overall comparison (see the PARALLEL_AGGREGATE section above for the worked example).
                    DO NOT use after only ONE AGGREGATE_COLUMN/AGGREGATE_GROUPS step, and DO NOT use after
                    AGGREGATE_PARTITIONS (use COMPARE_PARTITIONS for that).

PREDICTIVE_PIPELINE {"op":"PREDICTIVE_PIPELINE","model":"logistic_regression|random_forest|one_nearest_neighbor|hist_gradient_boosting",
                     "feature_columns":[str,...],"target_column":str,"sort_by":[str,...],"train_fraction":number,
                     "holdout_row":"first|last","filter_column":str|null,"filter_value":scalar|null,
                     "target_from_non_empty":bool,"target_label":str}
                    feature_columns must be listed explicitly.

AGG is one of: min, max, mean, median, sum, count, std, var, nunique, rms.

INVALID PATTERN—never emit:
[{"op":"FILTER_IN",...}, {"op":"GROUP_AGGREGATE",...,"result_column":"X"}, {"op":"FILTER_IN",...}, {"op":"GROUP_AGGREGATE",...,"result_column":"Y"}]
2. NEVER nest an operator object inside another operator's field — all column references must be strings.
3. NEVER use a comparator (gt, lt, eq, etc.) as a DERIVE_BINARY operation — only arithmetic operations allowed.
4. NEVER emit an empty group_by array — it must contain at least one column name.
5. Use PARALLEL_AGGREGATE ONLY when comparing metrics PER ENTITY with a shared grouping key. Use
   SPLIT_BY_THRESHOLD + AGGREGATE_PARTITIONS + COMPARE_PARTITIONS when comparing two halves of the
   entire dataset with no per-entity dimension.
6. Use RANK_GROUPS only after GROUP_AGGREGATE; use RANK_ROWS after DataFrame-producing operations.
7. Use ONLY the operators above and ONLY real column names from the schema.
8. Use PARALLEL_AGGREGATE when two or more independently filtered aggregates must become columns on the same table.
9. NEVER put a numeric literal or time constant (e.g. 60000000000, 3600, 1000) into DERIVE_BINARY's
   left/right fields — those fields are column names only. A constant-vs-column operation (e.g.
   bucketing a timestamp into fixed-width windows) is DERIVE_BIN(column, width, result), not DERIVE_BINARY.
10. NEVER leave PARALLEL_AGGREGATE branch "column" null unless aggregate is "count". If the metric is
    derived (e.g. "magnitude", "vector magnitude", "intensity"), emit DERIVE_VECTOR_MAGNITUDE or another
    DERIVE_* step first, then set "column" to that step's "result" name in every branch.
11. After PARALLEL_AGGREGATE, only group keys + branch result_column names are available to DERIVE_BINARY and RANK_ROWS.
12. Use RANK_GROUPS only after GROUP_AGGREGATE; use RANK_ROWS after DataFrame-producing operations.
13. Use ONLY the operators above and ONLY real column names from the schema.
14. If the question cannot be expressed with these operators, set "plan" to null.
15. NEVER use RANK_ROWS/RANK_GROUPS as a substitute for a two-value comparison. RANK_ROWS/RANK_GROUPS
    answer "which entity/group is extreme" (one row picked out); COMPARE_VALUES/COMPARE_PARTITIONS answer
    "what is the difference between these two numbers" (no entity picked out). If the question has no
    per-entity framing ("overall", "in general", no "per subject"/"per user"/"which X"), the answer is a
    single overall comparison — use COMPARE_VALUES (after two AGGREGATE_COLUMN/AGGREGATE_GROUPS steps) or
    COMPARE_PARTITIONS (after SPLIT_BY_* + AGGREGATE_PARTITIONS), never RANK_ROWS.\
"""
