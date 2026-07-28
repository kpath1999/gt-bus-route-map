# flashfusion / pipeline / operators.py

"""
What the guardrail prompt looks like:
return (
                "You are a data analyst working with a pandas DataFrame named `df`.\n"
                f"Columns: {col_descriptions}\n"
                f"Total rows: {len(df)}\n\n"
                "SCOPE CHECK: Before writing code, decide if the question is answerable "
                "using ONLY the columns above (aggregation, filtering, grouping, ranking, "
                "correlation, stats, or in-dataset train/predict on a specified split/sequence "
                "all count as in-scope).\n"
                "Reject ONLY if it needs external data, internet access, outside domain "
                "knowledge, personal info beyond the schema, or a prediction/forecast whose "
                "inputs cannot be derived from these columns.\n"
                "If rejecting, write no code. Respond exactly:\n"
                "Final Answer: This request is out-of-scope for the available data because "
                "<one-sentence reason>.\n"
            )
"""

"""
PROCESS (end-to-end):

1. Parse query into existing typed operators
2. Validate against column metadata/descriptions (guradrail prompt would do that)
3. Attempt answer with typed operator vocabulary
4. If feasible, use a small, cheap model only to select and fill typed operators when the deterministic parser is uncertain. Then execute
5. If infeasible, we would add the operator necessary to the script and then execute
"""

"""
ANOTHER POSSIBLE OPTION (to avoid arbitrary code from being generated):

1. Parse query into existing typed operators.
2. Validate against dataset metadata and supported operator grammar.
3. Execute if confidence is high.
4. If no valid operator graph exists:
   a. Route to a code model.
   b. Run code in the existing sandbox.
   c. Record the query, generated code, outcome, and operator gap.
5. Offline, review recurring successful gaps.
6. Add a tested operator/compiler rule and regression tests.
"""

## with Pydantic, you could define a closed operator schema, validate column names/types, reject malformed plans
# the current loader (flashfusion/pipeline/loader.py) already provides the full dataset schema, and the typed planner should treat it as a closed-loop vocabulary

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Aggregate(str, Enum):
    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    SUM = "sum"
    COUNT = "count"
    STD = "std"


class Predicate(BaseModel):
    column: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in"]
    value: Any


class Metric(BaseModel):
    column: str
    aggregate: Aggregate


class TypedOperation(BaseModel):
    kind: Literal["aggregate", "group_aggregate", "compare_groups"]
    metric: Metric
    filters: list[Predicate] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    derived_feature: str | None = None
    answer_style: Literal["value", "grouped_table", "comparison"] = "value"


## instead of the guardrail prompt, could there be a more efficient way of detecting out-of-scope queries?
# one idea:
def validate_columns(plan: TypedOperation, valid_columns: set[str]) -> None:
    referenced = {plan.metric.column, *plan.group_by}
    referenced |= {predicate.column for predicate in plan.filters}

    unknown = referenced - valid_columns
    if unknown:
        raise ValueError(f"Plan references unknown columns: {sorted(unknown)}")
"""
Pydantic/JSON-schema structured output could be the right mechanism here because it constrains the model to a predictable object that is easy to parse and validate downstream, rather than relying on brittle text parsing.
"""

## for direct operations, we could compile the validated plan without an LLM.
import pandas as pd

def compile_predicate(df: pd.DataFrame, p: Predicate) -> pd.Series:
    series = df[p.column]
    if p.op == "eq":
        return series.eq(p.value)
    elif p.op == "ne":
        return series.ne(p.value)
    elif p.op == "gt":
        return series.gt(p.value)
    elif p.op == "gte":
        return series.ge(p.value)
    elif p.op == "lt":
        return series.lt(p.value)
    elif p.op == "lte":
        return series.le(p.value)
    elif p.op == "in":
        return series.isin(p.value)
    raise ValueError(f"Unsupported predicate operator: {p.op}")

## for more difficult reasoning queries, like deciphering what "dynamic" and "static" refers to and tying that to specific instances in the activity label column, we would need to do a bit of transformations in the process.
def execute_typed_plan(df: pd.DataFrame, plan: TypedOperation):
    mask = pd.Series(True, index=df.index)
    for predicate in plan.filters:
        mask &= compile_predicate(df, predicate)

    needed = list(dict.fromkeys(
        [plan.metric.column, *plan.group_by, *[p.column for p in plan.filters]]
    ))
    frame = df.loc[mask, needed]

    if plan.kind == "aggregate":
        return getattr(frame[plan.metric.column], plan.metric.aggregate.value)()

    if plan.kind == "group_aggregate":
        return (
            frame.groupby(plan.group_by, dropna=False)[plan.metric.column]
            .agg(plan.metric.aggregate.value)
        )

    raise ValueError(f"Unsupported plan kind: {plan.kind}")
"""
an important optimization would be that the typed plan reveals filters and required columns before execution
an IoT-appropriate form of predicate pushdown, rather than sending all 20M rows and every column into a spawned code sandbox
"""

## if, for example, repeated queries require acceleration magnitude, we could add a reviewed vector_norm derived operator:
class DerivedFeature(BaseModel):
    kind: Literal["vector_norm"]
    inputs: tuple[str, str, str]
    output_name: str
"""
goals: expands the operator library based on observed IoT workloads, keeping the executable DSL versioned, testable, and safe
"""

# first implementation---
"""
we could start with five operator families drawn from the query suite (good news is we already made a foray into deterministic typed operators and now we need to keep it focused and latency-conscious)
_run_deterministic_plan in flash_fusion.py may provide some hints here

1. aggregate -- filter + min/max/mean/median/count
2. group_aggregate -- group-by + aggregate
3. compare_groups -- same metric across two filtered states, wit absolute/% difference
4. rank -- grouped aggregate plus argmin/argmax
5. derived_aggregate -- cached IoT-derived feature, then one of the above
"""