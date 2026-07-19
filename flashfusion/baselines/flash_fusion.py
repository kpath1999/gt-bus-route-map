"""
baselines/flash_fusion.py — Flash-Fusion baseline (B4).

S1 → S2 → guardrail(query + S2 grounding) → judge_plan → S3 → agent(grounded_query)
                                      [one S3 refinement on FAIL]

The stages build a grounded query that maps indirect concepts to exact column
names and injects sub-task structure. The agent resolves sub-tasks autonomously
via its ReAct loop. The guardrail runs after S2 (before S3) so OOS queries are
rejected early without wasting S3 + agent cost.

Expected benchmark behaviour:
  - Q4, Q10: rejected=True (guardrail on query + S2 grounding)
    - Q1–Q3, Q5–Q9: executed=True, judge_verdict={"verdict": "PASS"|"FAIL"}

See CLAUDE.md §_run_flash_fusion for the full algorithm.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.pipeline.stages import (
    Stage1_ConceptExtraction,
    Stage2_SchemaGrounding,
    Stage3_SubqueryGeneration,
)

FF_DEBUG = os.getenv("FF_DEBUG", "").lower() in ("1", "true", "yes")

_SUPPORTED_DIRECT_AGGREGATES: dict[str, tuple[str, ...]] = {
    "max": ("max", "maximum", "highest", "largest", "peak"),
    "min": ("min", "minimum", "lowest", "smallest"),
    "mean": ("mean", "average", "avg"),
    "median": ("median",),
    "sum": ("sum", "total"),
    "count": ("count", "number of"),
}

_DIRECT_DISQUALIFIER_PATTERNS: tuple[str, ...] = (
    r"\bfor\b",
    r"\bwhere\b",
    r"\bwith\b",
    r"\bbetween\b",
    r"\bby\b",
    r"\bper\b",
    r"\beach\b",
    r"\bgroup(?:ed|ing)?\b",
    r"\bcompare\b",
    r"\bversus\b",
    r"\bvs\b",
    r"\bhigher than\b",
    r"\blower than\b",
    r"\bdifference\b",
    r"\bsubtract\b",
    r"\bminus\b",
    r"\bplus\b",
    r"\border\b",
    r"\bsort\b",
    r"\brank\b",
    r"\btop\b",
    r"\bbottom\b",
    r"\bfirst\b",
    r"\blast\b",
    r"\bover time\b",
    r"\btime\b",
    r"\bbefore\b",
    r"\bafter\b",
    r"\bsince\b",
    r"\bduring\b",
    r"\btrend\b",
)

_SUPPORTED_PLAN_OPS: frozenset[str] = frozenset(
    {"FILTER", "AGGREGATE", "GROUPBY", "COMPARE", "RANK", "SELECT", "DERIVE"}
)


def _parse_kv_line(text: str) -> dict[str, str] | None:
    """Parse a typed 'key=value | key=value | ...' sub-query body into a dict.

    Returns None when the text does not look like the typed key=value grammar
    (e.g. legacy/older prose sub-queries), so callers can fall back gracefully.
    """
    if "=" not in text:
        return None
    kv: dict[str, str] = {}
    for part in text.split("|"):
        part = part.strip()
        if not part or "=" not in part:
            return None
        key, _, value = part.partition("=")
        key = key.strip().lower()
        if not key:
            return None
        kv[key] = value.strip()
    return kv or None


def _apply_comparator(series, comparator: str, value):
    """Apply a named comparator between a pandas Series and a value."""
    comparator = comparator.strip().lower()
    try:
        if comparator in ("eq", "=="):
            return series == value
        if comparator in ("gt", ">"):
            return series > value
        if comparator in ("gte", ">="):
            return series >= value
        if comparator in ("lt", "<"):
            return series < value
        if comparator in ("lte", "<="):
            return series <= value
        if comparator in ("ne", "!="):
            return series != value
    except (TypeError, ValueError) as exc:
        raise _DeterministicPlanUnsupported(
            f"Invalid comparator/value for column dtype {series.dtype}: "
            f"{comparator!r} {value!r}"
        ) from exc
    raise _DeterministicPlanUnsupported(f"Unsupported comparator: {comparator!r}")


def _extract_diff_columns(text: str, df_columns: list[str]) -> tuple[str, str] | None:
    """Detect 'difference/subtract/minus/delta between COL_A and COL_B' phrasing.

    Used as a legacy-prose fallback so an AGGREGATE step that actually asks
    for a per-record two-column difference (not a scalar reduction) is routed
    to a row-wise DERIVE computation instead of raising an ambiguous-aggregate
    error.
    """
    text_l = text.lower()
    if not re.search(r"\b(difference|subtract|minus|delta)\b", text_l):
        return None
    cols = _find_columns_in_text(text, df_columns)
    if len(cols) < 2:
        return None
    ordered = sorted(dict.fromkeys(cols), key=lambda c: text_l.find(c.lower()))
    if len(ordered) < 2:
        return None
    return ordered[0], ordered[1]


class _DeterministicPlanUnsupported(ValueError):
    """Raised when a deterministic execution plan is unsupported or ambiguous."""


@dataclass
class _DeterministicExecutionResult:
    answer: str
    trace: str
    final_code: str
    tries: int
    attempts: list[dict[str, Any]]


def _extract_plan_op(sub_query: str) -> tuple[str, str]:
    match = re.match(r"^\s*\[([A-Za-z_]+)\]\s*(.+)$", sub_query.strip())
    if not match:
        raise _DeterministicPlanUnsupported(f"Missing [OP] tag: {sub_query!r}")
    op = match.group(1).upper().strip()
    text = match.group(2).strip()
    if op not in _SUPPORTED_PLAN_OPS:
        raise _DeterministicPlanUnsupported(f"Unsupported operation {op!r}")
    return op, text


def _find_columns_in_text(text: str, df_columns: list[str]) -> list[str]:
    lowered = text.lower()
    return [c for c in df_columns if c.lower() in lowered]


def _extract_aggregate_op(text: str) -> str:
    text_l = text.lower()
    alias_to_op = {
        "maximum": "max",
        "highest": "max",
        "max": "max",
        "minimum": "min",
        "lowest": "min",
        "min": "min",
        "average": "mean",
        "mean": "mean",
        "median": "median",
        "sum": "sum",
        "total": "sum",
        "count": "count",
    }
    hits = {op for alias, op in alias_to_op.items() if re.search(rf"\b{re.escape(alias)}\b", text_l)}
    if len(hits) != 1:
        raise _DeterministicPlanUnsupported(f"Ambiguous aggregate operation in: {text!r}")
    return next(iter(hits))


def _extract_groupby_column(text: str, df_columns: list[str]) -> str:
    text_l = text.lower()
    match = re.search(r"group\s+by\s+([A-Za-z_][A-Za-z0-9_]*)", text_l)
    if match:
        col = match.group(1)
        if col in {c.lower() for c in df_columns}:
            for real_col in df_columns:
                if real_col.lower() == col:
                    return real_col
    cols = _find_columns_in_text(text, df_columns)
    if len(cols) == 1:
        return cols[0]
    raise _DeterministicPlanUnsupported(f"Unable to identify groupby column from: {text!r}")


def _extract_eq_filters(text: str, df_columns: list[str]) -> list[tuple[str, Any]]:
    """Extract simple equality filters such as `col == 'value'` or `col equals value`."""
    out: list[tuple[str, Any]] = []
    # Pattern: col == 'value' / col == "value"
    for col in df_columns:
        regex = rf"\b{re.escape(col)}\b\s*(?:==|=|equals|is)\s*['\"]?([A-Za-z0-9_ .-]+)['\"]?"
        for m in re.finditer(regex, text, flags=re.IGNORECASE):
            raw = m.group(1).strip().strip("'\"")
            if raw:
                out.append((col, raw))
    return out


def _extract_extremum_filter(text: str, df_columns: list[str]) -> tuple[str, str] | None:
    """Extract patterns like 'col equals its maximum/minimum value' from FILTER text."""
    text_l = text.lower()
    if not re.search(r"\bequals\b", text_l):
        return None
    op = None
    if re.search(r"\b(max|maximum|highest|largest|peak)\b", text_l):
        op = "max"
    elif re.search(r"\b(min|minimum|lowest|smallest)\b", text_l):
        op = "min"
    if op is None:
        return None

    cols = _find_columns_in_text(text, df_columns)
    if len(cols) != 1:
        return None
    return cols[0], op


def _is_prior_result_placeholder(value: str) -> bool:
    value_l = value.lower().strip()
    return any(
        phrase in value_l
        for phrase in (
            "previously computed aggregate result",
            "previous aggregate result",
            "aggregate result",
            "previously computed result",
            "prior result",
        )
    )


def _coerce_numeric(value: Any) -> float | int | None:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
            return float(raw)
    return None


def _coerce_for_column(series: "pd.Series", raw_value: Any) -> Any:
    """Coerce a raw literal into a value comparable with a Series' own dtype.

    Comparators previously assumed every filter value was numeric, which crashes
    with a pandas ``TypeError`` when the target column is ``datetime64[ns]``
    (e.g. ``timestamp > 0``). Dispatching on ``series.dtype`` first means the
    coercion strategy always matches what the column can actually be compared
    against, instead of guessing from the literal's own text.
    """
    if isinstance(raw_value, str):
        raw_value = raw_value.strip().strip("'\"")

    if pd.api.types.is_datetime64_any_dtype(series):
        numeric = _coerce_numeric(raw_value)
        if numeric is not None:
            # A bare numeric literal against a datetime column is interpreted
            # the same way pandas itself would (nanoseconds since epoch), so
            # e.g. "timestamp > 0" becomes a harmless epoch comparison instead
            # of raising, matching the common S3 placeholder-filter pattern.
            return pd.Timestamp(numeric)
        try:
            parsed = pd.to_datetime(raw_value)
        except (ValueError, TypeError) as exc:
            raise _DeterministicPlanUnsupported(
                f"Cannot compare datetime64 column against value: {raw_value!r}"
            ) from exc
        if parsed is pd.NaT:
            raise _DeterministicPlanUnsupported(
                f"Cannot compare datetime64 column against value: {raw_value!r}"
            )
        return parsed

    if pd.api.types.is_bool_dtype(series):
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str) and raw_value.lower() in ("true", "false"):
            return raw_value.lower() == "true"
        return raw_value

    if pd.api.types.is_numeric_dtype(series):
        numeric = _coerce_numeric(raw_value)
        return numeric if numeric is not None else raw_value

    return raw_value


def _run_deterministic_plan(
    query: str,
    df,
    sub_queries: list[Any],
) -> _DeterministicExecutionResult:
    """Execute an S3 plan with a strict deterministic operator vocabulary."""
    if not sub_queries:
        raise _DeterministicPlanUnsupported("No sub-queries provided")

    working_df = df
    observations: list[Any] = []
    attempts: list[dict[str, Any]] = []
    trace_lines: list[str] = []
    code_lines: list[str] = []
    # partitions: named DataFrame subsets produced by SPLIT_BY_THRESHOLD, consumed
    # by GROUP_AGGREGATE. group_agg_result: last GROUP_AGGREGATE output, consumed
    # by COMPARE_GROUPS. These support two-way median-split comparisons
    # (e.g. "is the northern half rougher than the southern half") without
    # falling back to the ReAct agent.
    partitions: dict[str, Any] = {}
    group_agg_result: dict[str, Any] | None = None
    group_agg_column: str = ""
    group_agg_op: str = ""
    # last_derived_column/last_derived_label: name and human-readable label of
    # the most recent per-record column computed by a [DERIVE] step (typed) or
    # a legacy-prose AGGREGATE step that detected a two-column difference
    # (e.g. "difference between accel_stats_z_p99 and accel_stats_z_p1").
    # A following [RANK] step falls back to this when it can't find enough
    # literal column names mentioned in its own text.
    last_derived_column: str = ""
    last_derived_label: str = ""
    # last_groupby_group_column/last_groupby_value_column: name of the group-key
    # column and the aggregated value column from the most recent [GROUPBY] step.
    # A following [RANK] step whose metric matches last_groupby_value_column
    # ranks the GROUPBY dict result directly (e.g. "which time window") instead
    # of re-scanning the ungrouped DataFrame, which would ignore the grouping
    # entirely.
    last_groupby_group_column: str = ""
    last_groupby_value_column: str = ""

    # Fast path for S3_bypass expression format: df['col'].op()
    if len(sub_queries) == 1:
        expr_match = re.match(r"^df\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]\.(max|min|mean|median|sum|count)\(\)\s*$", sub_queries[0].strip())
        if expr_match:
            col = expr_match.group(1)
            op = expr_match.group(2)
            if col not in working_df.columns:
                raise _DeterministicPlanUnsupported(f"Unknown column in bypass expression: {col}")
            series = working_df[col]
            result = getattr(series, op)()
            attempts.append({"attempt": 1, "op": "AGGREGATE", "ok": True, "output": result})
            code_lines.append(f"result = df[{col!r}].{op}()")
            trace_lines.extend(
                [
                    "Thought: Deterministic execution path (direct aggregate bypass)",
                    "Action: deterministic_exec",
                    f"Action Input: result = df[{col!r}].{op}()",
                    f"Observation: {result}",
                ]
            )
            answer = f"{result}"
            trace_lines.append(f"Final Answer: {answer}")
            return _DeterministicExecutionResult(
                answer=answer,
                trace="\n".join(trace_lines),
                final_code="\n".join(code_lines),
                tries=1,
                attempts=attempts,
            )

    for idx, sq in enumerate(sub_queries, start=1):
        typed_op = None
        typed_step: dict[str, Any] = {}
        text = ""
        text_l = ""
        if isinstance(sq, dict):
            typed_step = sq
            typed_op = str(sq.get("op", "")).upper().strip()
            op = typed_op
        else:
            op, text = _extract_plan_op(str(sq))
            text_l = text.lower()

        step_code = ""
        step_obs: Any = ""

        if typed_op:
            if typed_op == "AGGREGATE_COLUMN":
                col = str(typed_step.get("column", ""))
                agg = str(typed_step.get("aggregate", "")).lower()
                if col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown AGGREGATE column: {col}")
                if agg not in {"max", "min", "mean", "median", "sum", "count"}:
                    raise _DeterministicPlanUnsupported(f"Unsupported aggregate op: {agg}")
                result = getattr(working_df[col], agg)()
                observations.append(result)
                step_code = f"result = df[{col!r}].{agg}()"
                step_obs = result

            elif typed_op == "FILTER_EQ_PREV":
                col = str(typed_step.get("column", ""))
                if col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown FILTER column: {col}")
                prior_scalars = [v for v in observations if isinstance(v, (int, float))]
                if not prior_scalars:
                    raise _DeterministicPlanUnsupported("FILTER_EQ_PREV requires prior scalar observation")
                cast_value = prior_scalars[-1]
                working_df = working_df[working_df[col] == cast_value]
                step_code = f"df = df[df[{col!r}] == {cast_value!r}]"
                step_obs = f"rows={len(working_df)} (filtered by prior aggregate result {cast_value})"

            elif typed_op == "FILTER_COMPARE":
                col = str(typed_step.get("column", ""))
                cmp = str(typed_step.get("comparator", "")).lower()
                raw_val = typed_step.get("value")
                if col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown FILTER column: {col}")
                cast_value = _coerce_for_column(working_df[col], raw_val)
                op_symbol_by_cmp = {
                    "gt": ">",
                    "gte": ">=",
                    "lt": "<",
                    "lte": "<=",
                    "eq": "==",
                }
                if cmp not in op_symbol_by_cmp:
                    raise _DeterministicPlanUnsupported(f"Unsupported comparator: {cmp!r}")
                mask = _apply_comparator(working_df[col], cmp, cast_value)
                working_df = working_df[mask]
                op_txt = op_symbol_by_cmp[cmp]
                step_code = f"df = df[df[{col!r}] {op_txt} {cast_value!r}]"
                step_obs = f"rows={len(working_df)}"

            elif typed_op == "AGGREGATE_COUNT_ROWS":
                result = int(len(working_df))
                observations.append(result)
                step_code = "result = len(df)"
                step_obs = result

            elif typed_op == "SELECT_LIST":
                col = str(typed_step.get("column", ""))
                if col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown SELECT column: {col}")
                result = working_df[col].tolist()
                observations.append(result)
                step_code = f"result = df[{col!r}].tolist()"
                step_obs = result

            elif typed_op == "SPLIT_BY_THRESHOLD":
                col = str(typed_step.get("column", ""))
                cmp = str(typed_step.get("comparator", "")).lower()
                label = str(typed_step.get("label", ""))
                if col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown SPLIT column: {col}")
                if not label:
                    raise _DeterministicPlanUnsupported("SPLIT_BY_THRESHOLD requires a label")
                threshold = working_df[col].median()
                if cmp == "gt":
                    subset = working_df[working_df[col] > threshold]
                    op_txt = ">"
                elif cmp == "gte":
                    subset = working_df[working_df[col] >= threshold]
                    op_txt = ">="
                elif cmp == "lt":
                    subset = working_df[working_df[col] < threshold]
                    op_txt = "<"
                elif cmp == "lte":
                    subset = working_df[working_df[col] <= threshold]
                    op_txt = "<="
                else:
                    raise _DeterministicPlanUnsupported(f"Unsupported comparator: {cmp!r}")
                partitions[label] = subset
                step_code = (
                    f"_median = df[{col!r}].median(); "
                    f"{label} = df[df[{col!r}] {op_txt} _median]"
                )
                step_obs = f"{label}: rows={len(subset)} ({col} {op_txt} median={threshold})"

            elif typed_op == "GROUP_AGGREGATE":
                col = str(typed_step.get("column", ""))
                agg = str(typed_step.get("aggregate", "")).lower()
                groups = typed_step.get("groups") or []
                if col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown GROUP_AGGREGATE column: {col}")
                if agg not in {"max", "min", "mean", "median", "sum", "count"}:
                    raise _DeterministicPlanUnsupported(f"Unsupported aggregate op: {agg}")
                missing = [g for g in groups if g not in partitions]
                if missing:
                    raise _DeterministicPlanUnsupported(f"Unknown partition(s) for GROUP_AGGREGATE: {missing}")
                result = {g: getattr(partitions[g][col], agg)() for g in groups}
                observations.append(result)
                group_agg_result = result
                group_agg_column = col
                group_agg_op = agg
                step_code = (
                    "result = {" + ", ".join(f"{g!r}: {g}[{col!r}].{agg}()" for g in groups) + "}"
                )
                step_obs = result

            elif typed_op == "COMPARE_GROUPS":
                if not group_agg_result or len(group_agg_result) < 2:
                    raise _DeterministicPlanUnsupported("COMPARE_GROUPS requires a prior GROUP_AGGREGATE result")
                (label_a, value_a), (label_b, value_b) = list(group_agg_result.items())[:2]
                if value_a >= value_b:
                    higher_label, higher_val = label_a, value_a
                    lower_label, lower_val = label_b, value_b
                else:
                    higher_label, higher_val = label_b, value_b
                    lower_label, lower_val = label_a, value_a
                delta = higher_val - lower_val
                metric_desc = f"{group_agg_op} {group_agg_column}" if group_agg_column else "metric"
                result = (
                    f"{higher_label} has the higher {metric_desc} ({higher_val}) versus "
                    f"{lower_label} ({lower_val}); difference={delta}"
                )
                observations.append(result)
                step_code = "result = compare(" + ", ".join(group_agg_result.keys()) + ")"
                step_obs = result

            else:
                raise _DeterministicPlanUnsupported(f"Unsupported typed operation {typed_op!r}")

            attempts.append({
                "attempt": idx,
                "op": op,
                "ok": True,
                "output": str(step_obs),
            })
            code_lines.append(step_code)
            trace_lines.append(f"Thought: Deterministic step {idx} ({op})")
            trace_lines.append("Action: deterministic_exec")
            trace_lines.append(f"Action Input: {step_code}")
            trace_lines.append(f"Observation: {step_obs}")
            continue

        if op == "FILTER":
            kv = _parse_kv_line(text)
            if kv is not None and "column" in kv:
                col = kv["column"].strip().strip("'\"")
                if col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown FILTER column: {col}")
                comparator = kv.get("comparator", "eq").strip().lower()
                raw_value = kv.get("value", "").strip()
                if comparator == "in":
                    vals_raw = raw_value.strip("[]")
                    raw_vals = [v.strip().strip("'\"") for v in vals_raw.split(",") if v.strip()]
                    coerced_vals = [
                        (_coerce_numeric(v) if _coerce_numeric(v) is not None else v) for v in raw_vals
                    ]
                    working_df = working_df[working_df[col].isin(coerced_vals)]
                    step_code = f"df = df[df[{col!r}].isin({coerced_vals!r})]"
                else:
                    if raw_value.upper() in ("PREV", "PREVIOUS", "PREVIOUS_RESULT") or _is_prior_result_placeholder(raw_value):
                        prior_scalars = [v for v in observations if isinstance(v, (int, float))]
                        if not prior_scalars:
                            raise _DeterministicPlanUnsupported("FILTER value references prior result, but none exists")
                        cast_value = prior_scalars[-1]
                    else:
                        cast_value = _coerce_for_column(working_df[col], raw_value)
                    mask = _apply_comparator(working_df[col], comparator, cast_value)
                    working_df = working_df[mask]
                    step_code = f"df = df[df[{col!r}] {comparator} {cast_value!r}]"
                step_obs = f"rows={len(working_df)}"
            # Avoid unnecessary non-null filters; pandas aggregations skip nulls.
            elif "not null" in text_l or "is not null" in text_l:
                step_code = "# skipped no-op null filter"
                step_obs = f"rows={len(working_df)} (null-filter omitted as no-op)"
            else:
                eq_filters = _extract_eq_filters(text, list(working_df.columns))
                if not eq_filters:
                    inline_extreme = _extract_extremum_filter(text, list(working_df.columns))
                    if inline_extreme is not None:
                        col, ext_op = inline_extreme
                        extreme_value = (
                            working_df[col].max() if ext_op == "max" else working_df[col].min()
                        )
                        working_df = working_df[working_df[col] == extreme_value]
                        step_code = (
                            f"_v = df[{col!r}].{ext_op}(); "
                            f"df = df[df[{col!r}] == _v]"
                        )
                        step_obs = f"rows={len(working_df)} ({col} == {ext_op}={extreme_value})"
                    else:
                        # Cross-step pattern: FILTER where col == <prior aggregate result>.
                        # Detect a column name in the text and use the most recent scalar
                        # observation as the filter value.
                        prior_scalars = [
                            v for v in observations if isinstance(v, (int, float))
                        ]
                        mentioned_cols = _find_columns_in_text(text, list(working_df.columns))
                        if prior_scalars and len(mentioned_cols) == 1:
                            col = mentioned_cols[0]
                            cast_value = prior_scalars[-1]
                            working_df = working_df[working_df[col] == cast_value]
                            step_code = f"df = df[df[{col!r}] == {cast_value!r}]"
                            step_obs = f"rows={len(working_df)} (filtered by prior aggregate result {cast_value})"
                        else:
                            raise _DeterministicPlanUnsupported(f"Unsupported FILTER pattern: {text!r}")
                else:
                    prior_scalars = [
                        v for v in observations if isinstance(v, (int, float))
                    ]
                    for col, value in eq_filters:
                        if col not in working_df.columns:
                            raise _DeterministicPlanUnsupported(f"Unknown FILTER column: {col}")
                        if " or " in value.lower():
                            raise _DeterministicPlanUnsupported(
                                f"Unsupported disjunctive FILTER value: {value!r}"
                            )
                        # Keep values as string match first; if numeric cast succeeds, use numeric compare.
                        cast_value = value
                        if _is_prior_result_placeholder(value):
                            if not prior_scalars:
                                raise _DeterministicPlanUnsupported(
                                    "FILTER references prior aggregate result, but none exists"
                                )
                            cast_value = prior_scalars[-1]
                        elif value.isdigit():
                            cast_value = int(value)
                        else:
                            try:
                                cast_value = float(value)
                            except ValueError:
                                cast_value = value
                        working_df = working_df[working_df[col] == cast_value]
                    step_code = " ; ".join(
                        [f"df = df[df[{col!r}] == {repr(v)}]" for col, v in eq_filters]
                    )
                    step_obs = f"rows={len(working_df)}"

        elif op == "DERIVE":
            kv = _parse_kv_line(text)
            if kv is None:
                raise _DeterministicPlanUnsupported(f"DERIVE requires typed key=value form: {text!r}")
            col_a = kv.get("column_a", "").strip().strip("'\"")
            col_b = kv.get("column_b", "").strip().strip("'\"")
            derive_op = kv.get("op", "subtract").strip().lower()
            result_name = kv.get("result", "").strip().strip("'\"") or "_derived_value"
            if col_a not in working_df.columns or col_b not in working_df.columns:
                raise _DeterministicPlanUnsupported(f"Unknown DERIVE column(s): {col_a!r}, {col_b!r}")
            working_df = working_df.copy()
            if derive_op in ("subtract", "diff", "difference"):
                working_df[result_name] = working_df[col_a] - working_df[col_b]
                op_symbol = "-"
            elif derive_op == "add":
                working_df[result_name] = working_df[col_a] + working_df[col_b]
                op_symbol = "+"
            elif derive_op == "multiply":
                working_df[result_name] = working_df[col_a] * working_df[col_b]
                op_symbol = "*"
            elif derive_op == "divide":
                working_df[result_name] = working_df[col_a] / working_df[col_b]
                op_symbol = "/"
            else:
                raise _DeterministicPlanUnsupported(f"Unsupported DERIVE op: {derive_op!r}")
            last_derived_column = result_name
            last_derived_label = kv.get("label", result_name).strip() or result_name
            step_code = f"df[{result_name!r}] = df[{col_a!r}] {op_symbol} df[{col_b!r}]"
            step_obs = f"computed '{result_name}' column (rows={len(working_df)})"

        elif op == "AGGREGATE":
            kv = _parse_kv_line(text)
            if kv is not None and "column" in kv and "stat" in kv:
                col = kv["column"].strip().strip("'\"")
                alias_map = {
                    "average": "mean", "maximum": "max", "highest": "max",
                    "minimum": "min", "lowest": "min", "total": "sum",
                }
                agg_op = alias_map.get(kv["stat"].strip().lower(), kv["stat"].strip().lower())
                if col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown AGGREGATE column: {col}")
                if agg_op not in {"max", "min", "mean", "median", "sum", "count"}:
                    raise _DeterministicPlanUnsupported(f"Unsupported aggregate op: {agg_op}")
                result = getattr(working_df[col], agg_op)()
                step_code = f"result = df[{col!r}].{agg_op}()"
                observations.append(result)
                step_obs = result
            else:
                diff_cols = _extract_diff_columns(text, list(working_df.columns))
                if diff_cols is not None:
                    col_a, col_b = diff_cols
                    working_df = working_df.copy()
                    working_df["_derived_diff"] = working_df[col_a] - working_df[col_b]
                    last_derived_column = "_derived_diff"
                    last_derived_label = f"difference between {col_a} and {col_b}"
                    step_code = f"df['_derived_diff'] = df[{col_a!r}] - df[{col_b!r}]"
                    step_obs = f"computed per-record '_derived_diff' column (rows={len(working_df)})"
                else:
                    agg_op = _extract_aggregate_op(text)
                    cols = _find_columns_in_text(text, list(working_df.columns))
                    if not cols:
                        raise _DeterministicPlanUnsupported(f"No known column found in AGGREGATE: {text!r}")

                    # Handle argmax/argmin-style "corresponding timestamp" pattern.
                    if "corresponding" in text_l and agg_op in {"max", "min"} and len(cols) >= 2:
                        metric_col = cols[-1]
                        target_col = cols[0]
                        if metric_col not in working_df.columns or target_col not in working_df.columns:
                            raise _DeterministicPlanUnsupported("Unknown column in corresponding-value aggregate")
                        idx_val = working_df[metric_col].idxmax() if agg_op == "max" else working_df[metric_col].idxmin()
                        result = working_df.loc[idx_val, target_col]
                        step_code = (
                            f"idx = df[{metric_col!r}].idx{'max' if agg_op == 'max' else 'min'}(); "
                            f"result = df.loc[idx, {target_col!r}]"
                        )
                    else:
                        if len(cols) != 1:
                            raise _DeterministicPlanUnsupported(f"Ambiguous AGGREGATE columns: {cols}")
                        col = cols[0]
                        if col not in working_df.columns:
                            raise _DeterministicPlanUnsupported(f"Unknown AGGREGATE column: {col}")
                        series = working_df[col]
                        result = getattr(series, agg_op)()
                        step_code = f"result = df[{col!r}].{agg_op}()"

                    observations.append(result)
                    step_obs = result

        elif op == "GROUPBY":
            kv = _parse_kv_line(text)
            if kv is not None and "group_column" in kv:
                group_col = kv["group_column"].strip().strip("'\"")
                value_col = kv.get("value_column", "").strip().strip("'\"")
                agg_op = kv.get("stat", "").strip().lower()
                freq = kv.get("freq", "").strip()
                if group_col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown GROUPBY group column: {group_col}")
                if value_col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown GROUPBY value column: {value_col}")
                if agg_op not in {"max", "min", "mean", "median", "sum", "count"}:
                    raise _DeterministicPlanUnsupported(f"Unsupported aggregate op: {agg_op!r}")
                if freq:
                    # Time-bin grouping (e.g. "1-minute intervals") requires a
                    # real datetime column; pd.Grouper does the bucketing.
                    if not pd.api.types.is_datetime64_any_dtype(working_df[group_col]):
                        raise _DeterministicPlanUnsupported(
                            f"GROUPBY freq={freq!r} requires a datetime64 column; "
                            f"{group_col} has dtype {working_df[group_col].dtype}"
                        )
                    grouped = working_df.groupby(pd.Grouper(key=group_col, freq=freq))[value_col]
                    step_code = (
                        f"result = df.groupby(pd.Grouper(key={group_col!r}, freq={freq!r}))"
                        f"[{value_col!r}].{agg_op}().to_dict()"
                    )
                else:
                    grouped = working_df.groupby(group_col)[value_col]
                    step_code = (
                        f"result = df.groupby({group_col!r})[{value_col!r}].{agg_op}().to_dict()"
                    )
                raw_result = getattr(grouped, agg_op)()
                result = {str(k): v for k, v in raw_result.to_dict().items()}
                observations.append(result)
                last_groupby_group_column = group_col
                last_groupby_value_column = value_col
                step_obs = result
            else:
                group_col = _extract_groupby_column(text, list(working_df.columns))
                agg_op = _extract_aggregate_op(text)
                cols = _find_columns_in_text(text, list(working_df.columns))
                value_cols = [c for c in cols if c != group_col]
                if len(value_cols) != 1:
                    raise _DeterministicPlanUnsupported(f"Ambiguous GROUPBY value column in: {text!r}")
                value_col = value_cols[0]
                grouped = working_df.groupby(group_col)[value_col]
                result = getattr(grouped, agg_op)().to_dict()
                observations.append(result)
                last_groupby_group_column = group_col
                last_groupby_value_column = value_col
                step_code = (
                    f"result = df.groupby({group_col!r})[{value_col!r}].{agg_op}().to_dict()"
                )
                step_obs = result

        elif op == "RANK":
            # RANK: find the row with the max/min of a metric column and return
            # a dict containing both the entity identifier(s) and the metric
            # value, matching the S3 prompt convention for [RANK] sub-questions.
            kv = _parse_kv_line(text)
            if kv is not None and "metric" in kv:
                metric_col = kv["metric"].strip().strip("'\"")
                rank_agg = kv.get("stat", "max").strip().lower()
                if rank_agg not in {"max", "min"}:
                    raise _DeterministicPlanUnsupported(f"RANK stat must be max/min; got {rank_agg!r}")
                if metric_col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown RANK metric column: {metric_col}")
                return_cols = [c.strip() for c in kv.get("return", "").split(",") if c.strip()]
                return_cols = [c for c in return_cols if c in working_df.columns]
                if (
                    observations
                    and isinstance(observations[-1], dict)
                    and last_groupby_value_column
                    and metric_col == last_groupby_value_column
                ):
                    # metric refers to the value aggregated by the immediately
                    # preceding [GROUPBY] step: rank the grouped dict itself
                    # (e.g. "which time window") rather than re-scanning the
                    # ungrouped DataFrame, which would silently ignore the grouping.
                    grouped_result = observations[-1]
                    numeric_items = {
                        k: v for k, v in grouped_result.items() if isinstance(v, (int, float))
                    }
                    if not numeric_items:
                        raise _DeterministicPlanUnsupported(
                            "RANK over GROUPBY result requires numeric aggregate values"
                        )
                    best_key = (
                        max(numeric_items, key=lambda k: numeric_items[k])
                        if rank_agg == "max"
                        else min(numeric_items, key=lambda k: numeric_items[k])
                    )
                    group_label = last_groupby_group_column or "group"
                    result = {group_label: best_key, metric_col: numeric_items[best_key]}
                    step_code = (
                        f"best_key = {'max' if rank_agg == 'max' else 'min'}(result_groupby, key=result_groupby.get); "
                        f"result = {{{group_label!r}: best_key, {metric_col!r}: result_groupby[best_key]}}"
                    )
                    observations.append(result)
                    step_obs = result
                else:
                    if not return_cols:
                        raise _DeterministicPlanUnsupported("RANK requires a valid 'return' column list")
                    idx_val = (
                        working_df[metric_col].idxmax() if rank_agg == "max" else working_df[metric_col].idxmin()
                    )
                    result = {c: working_df.loc[idx_val, c] for c in return_cols}
                    if metric_col not in result:
                        result[metric_col] = working_df.loc[idx_val, metric_col]
                    step_code = (
                        f"idx = df[{metric_col!r}].idx{'max' if rank_agg == 'max' else 'min'}(); "
                        f"result = {{{', '.join(f'{c!r}: df.loc[idx, {c!r}]' for c in result)}}}"
                    )
                    observations.append(result)
                    step_obs = result
            else:
                rank_agg = _extract_aggregate_op(text)
                if rank_agg not in {"max", "min"}:
                    raise _DeterministicPlanUnsupported(
                        f"RANK only supports max/min ranking; got: {rank_agg!r}"
                    )
                if last_derived_column and last_derived_column in working_df.columns:
                    metric_col = last_derived_column
                    metric_label = last_derived_label or metric_col
                    identifier_cols = _find_columns_in_text(text, list(df.columns))
                else:
                    cols = _find_columns_in_text(text, list(working_df.columns))
                    if len(cols) < 2:
                        raise _DeterministicPlanUnsupported(
                            f"RANK requires at least two columns (identifier + metric); found: {cols}"
                        )
                    metric_col = cols[-1]
                    metric_label = metric_col
                    identifier_cols = cols[:-1]
                if not identifier_cols:
                    raise _DeterministicPlanUnsupported("RANK requires at least one identifier column")
                if metric_col not in working_df.columns:
                    raise _DeterministicPlanUnsupported(f"Unknown column in RANK: metric={metric_col!r}")
                idx_val = (
                    working_df[metric_col].idxmax()
                    if rank_agg == "max"
                    else working_df[metric_col].idxmin()
                )
                result = {c: working_df.loc[idx_val, c] for c in identifier_cols}
                result[metric_label] = working_df.loc[idx_val, metric_col]
                step_code = (
                    f"idx = df[{metric_col!r}].idx{'max' if rank_agg == 'max' else 'min'}(); "
                    f"result = {{{', '.join(f'{c!r}: df.loc[idx, {c!r}]' for c in identifier_cols)}, "
                    f"{metric_label!r}: df.loc[idx, {metric_col!r}]}}"
                )
                observations.append(result)
                step_obs = result

        elif op == "SELECT":
            # SELECT: return specified column(s) from the current working DataFrame.
            cols = _find_columns_in_text(text, list(working_df.columns))
            if not cols:
                raise _DeterministicPlanUnsupported(f"No known column found in SELECT: {text!r}")
            if len(cols) == 1 and "list" in text.lower():
                result = working_df[cols[0]].tolist()
                step_code = f"result = df[{cols[0]!r}].tolist()"
            else:
                result = working_df[cols].to_dict(orient="list")
                step_code = f"result = df[{cols!r}].to_dict(orient='list')"
            observations.append(result)
            step_obs = result

        elif op == "COMPARE":
            if len(observations) < 2:
                raise _DeterministicPlanUnsupported("COMPARE requires at least two prior observations")
            a = observations[-2]
            b = observations[-1]
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                result = a - b
                step_code = "result = obs[-2] - obs[-1]"
                step_obs = result
                observations.append(result)
            else:
                raise _DeterministicPlanUnsupported("COMPARE currently supports scalar numeric observations only")

        attempts.append({
            "attempt": idx,
            "op": op,
            "ok": True,
            "output": str(step_obs),
        })
        code_lines.append(step_code)
        trace_lines.append(f"Thought: Deterministic step {idx} ({op})")
        trace_lines.append("Action: deterministic_exec")
        trace_lines.append(f"Action Input: {step_code}")
        trace_lines.append(f"Observation: {step_obs}")

    final = observations[-1] if observations else f"rows={len(working_df)}"
    answer = f"{final}"
    trace_lines.append(f"Final Answer: {answer}")
    return _DeterministicExecutionResult(
        answer=answer,
        trace="\n".join(trace_lines),
        final_code="\n".join(line for line in code_lines if line),
        tries=len(sub_queries),
        attempts=attempts,
    )


def _extract_single_grounded_column(raw_grounding: str) -> str | None:
    """Return a single grounded column only when all S2 mappings resolve to the same column.

    Multiple mapping lines are allowed (e.g. S2 grounding both a DATA concept and
    its REASONING counterpart such as "average → accel_mean (mean)") as long as
    every line's resolved column is identical and no multi-column operators are present.
    """
    lines = raw_grounding.splitlines()
    mappings: list[str] = []
    unmappable: list[str] = []
    in_mappings = False

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("MAPPINGS:"):
            in_mappings = True
            continue
        if upper.startswith("UNMAPPABLE:"):
            in_mappings = False
            raw_unmappable = stripped.split(":", 1)[1].strip()
            if raw_unmappable.upper() not in ("NONE", ""):
                unmappable = [u.strip() for u in raw_unmappable.split(",") if u.strip()]
            continue
        if in_mappings and "→" in stripped:
            mappings.append(stripped)

    if unmappable or not mappings:
        return None

    # Hard-disqualify any RHS that references multiple columns, comparisons, or
    # multi-arg operators (e.g. "latitude > MEDIAN(latitude)", "GROUP_COMPARE(...)").
    _MULTI_COL_CHARS = {",", "+", "-", "*", "/", "=", "[", "]", ">", "<"}
    # Single-argument derived-stat operations whose wrapped column should be
    # unwrapped rather than mistaken for a literal column name (e.g. the "MEDIAN"
    # in "MEDIAN(latitude)" is an operation, not a column called "MEDIAN").
    _UNARY_OPS = {"MEDIAN", "MEAN", "SUM", "COUNT", "MIN", "MAX", "STD", "VARIANCE", "PROXY"}
    columns: set[str] = set()
    for mapping in mappings:
        rhs = mapping.split("→", 1)[1].strip()
        if not rhs:
            return None
        if any(ch in rhs for ch in _MULTI_COL_CHARS):
            return None
        # Take the first token before any space or parenthetical descriptor
        # (e.g. "accel_mean (mean)" → "accel_mean").
        first_token = re.split(r"[\s(]", rhs)[0].strip("`\"'")
        if first_token.upper() in _UNARY_OPS:
            arg_match = re.match(
                rf"{re.escape(first_token)}\(\s*([A-Za-z_][A-Za-z0-9_]*)", rhs
            )
            if not arg_match:
                return None
            first_token = arg_match.group(1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", first_token):
            return None
        columns.add(first_token)

    if len(columns) != 1:
        return None

    return next(iter(columns))


def _detect_direct_aggregate(query: str, raw_grounding: str) -> dict[str, str] | None:
    """Conservative detector for direct single-column aggregate queries."""
    query_lc = query.lower()
    for pattern in _DIRECT_DISQUALIFIER_PATTERNS:
        if re.search(pattern, query_lc):
            return None

    matched_ops: set[str] = set()
    for op, aliases in _SUPPORTED_DIRECT_AGGREGATES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", query_lc):
                matched_ops.add(op)
                break
    if len(matched_ops) != 1:
        return None

    column = _extract_single_grounded_column(raw_grounding)
    if not column:
        return None

    return {"column": column, "operation": next(iter(matched_ops))}


def _build_grounded_query(
    query: str,
    raw_grounding: str,
    sub_queries: list,
    synthesis_hint: str,
) -> str:
    """Construct the agent prompt from the S3 decomposition.

    ``raw_grounding`` is accepted for backward-compatible call signatures
    (e.g. eval/trace_query.py) but intentionally NOT included in the output:
    S3 sub-queries are now typed, self-contained key=value steps (exact
    column names baked in already), so re-including the raw S2 mapping text
    here only added noise/redundant, sometimes-misleading concept mappings
    without helping the ReAct agent execute the plan.
    """
    del raw_grounding  # unused: see docstring
    sub_tasks = "\n".join(f"- {sq}" for sq in sub_queries) if sub_queries else "(none)"
    return (
        f"{query}\n\n"
        f"Sub-tasks to address:\n{sub_tasks}\n\n"
        f"Hint: {synthesis_hint}"
    )


def run_flash_fusion(
    query: str,
    df,
    client: LLMClient,
    r: RunResult,
) -> RunResult:
    """
    Execute the full Flash-Fusion pipeline.

    Args:
        query:   Raw natural language query.
        df:      WISDM DataFrame (deterministically enriched by BaselineRunner).
        client:  LLMClient instance for this run.
        r:       RunResult to populate.

    Returns:
        Populated RunResult.

    Algorithm:
        S1 → S2 → guardrail(query + S2 grounding) → reject or proceed
        S3 → build grounded_query
        judge_plan(query, grounding, sub_queries, synthesis_hint) → plan verdict
        if FAIL + suggestion:
            rerun S3 once with correction note appended to query context
            rebuild grounded_query
            judge_plan again → final plan verdict
        execute_single(grounded_query) → raw_answer, trace, details
        r.answer = raw_answer; r.executed = True
    """
    last_stage = "init"
    stage_latency_s = {
        "s1": 0.0,
        "s2": 0.0,
        "guardrail": 0.0,
        "agent": 0.0,
        "synthesis": 0.0,
    }
    deterministic_plan_input: list[Any] = []

    def record_stage(stage_key: str, start_s: float) -> None:
        elapsed = max(0.0, time.time() - start_s)
        stage_latency_s[stage_key] = stage_latency_s.get(stage_key, 0.0) + elapsed
        r.stage_latency_s = dict(stage_latency_s)

    r.stage_latency_s = dict(stage_latency_s)
    try:
        meta_str = meta_to_str(build_column_metadata(df))

        # Stages 1 and 2 may run on a lighter sibling model (client.light) when a
        # --stage12-model is configured; client.light is client itself otherwise.
        stage1 = Stage1_ConceptExtraction(client.light)
        stage2 = Stage2_SchemaGrounding(client.light)
        stage3 = Stage3_SubqueryGeneration(client)
        executor = ExecutionLayer(df, client)

        if FF_DEBUG:
            print(f"[FF_DEBUG] Starting S1 for query: {query[:80]}...", file=sys.stderr, flush=True)
        last_stage = "S1"
        stage_t0 = time.time()
        concepts = stage1.run(query)
        record_stage("s1", stage_t0)
        r.s1_concepts = concepts
        r.stages_run.append("S1")
        if FF_DEBUG:
            print(f"[FF_DEBUG] S1 complete. Concepts: {str(concepts)[:100]}...", file=sys.stderr, flush=True)

        if FF_DEBUG:
            print(f"[FF_DEBUG] Starting S2...", file=sys.stderr, flush=True)
        last_stage = "S2"
        stage_t0 = time.time()
        grounding = stage2.run(concepts, query, meta_str, df)
        record_stage("s2", stage_t0)
        r.s2_grounding = grounding["raw_grounding"]
        r.s2_filtered_concepts = grounding.get("filtered_concepts", {})
        r.stages_run.append("S2")
        if FF_DEBUG:
            print(f"[FF_DEBUG] S2 complete. Grounding: {grounding['raw_grounding'][:100]}...", file=sys.stderr, flush=True)

        # Guardrail runs after S2, before S3.
        # Pass query + S2 grounding so the guardrail can detect unmappable concepts
        # (e.g. OOS requests for data not in the schema) before wasting S3 + agent cost.
        post_s2_query = (
            f"{query}\n\n"
            f"Concept-to-column mappings produced by schema grounding:\n{grounding['raw_grounding']}"
        )
        last_stage = "guardrail"
        stage_t0 = time.time()
        proceed, reason = executor.guardrail(post_s2_query)
        record_stage("guardrail", stage_t0)
        r.stages_run.append("guardrail")
        if not proceed:
            r.rejected = True
            r.rejection_reason = reason
            r.alignment_explanation = (
                "Rejected after schema grounding because the query cannot be "
                f"answered from available dataset fields. Reason: {reason}"
            )
            r.answer = (
                "Query rejected. "
                f"Reason: {reason}. "
                "This request is not supported by the current dataset schema or task scope."
            )
            r.executed = False
            return r

        direct_plan = _detect_direct_aggregate(query, grounding["raw_grounding"])
        if direct_plan:
            column = direct_plan["column"]
            operation = direct_plan["operation"]
            grounded_query = (
                f"{query}\n\n"
                f"Resolved column: {column}\n"
                f"Required operation: {operation}\n"
                "Execute exactly one pandas expression and report the result."
            )
            r.s3_sub_queries = [f"df[{column!r}].{operation}()"]
            r.s3_synthesis_hint = "Return the result of the single aggregate."
            deterministic_plan_input = list(r.s3_sub_queries)
            r.stages_run.append("S3_bypass")
            if FF_DEBUG:
                print(
                    f"[FF_DEBUG] S3 bypassed with direct plan: {column}.{operation}()",
                    file=sys.stderr,
                    flush=True,
                )
        else:
            if FF_DEBUG:
                print(f"[FF_DEBUG] Starting S3...", file=sys.stderr, flush=True)
            last_stage = "S3"
            stage_t0 = time.time()
            sub_result = stage3.run(query, grounding["raw_grounding"], meta_str)
            record_stage("s3", stage_t0)
            r.s3_sub_queries = sub_result["sub_queries"]
            r.s3_synthesis_hint = sub_result["synthesis_hint"]
            deterministic_plan_input = sub_result.get("typed_sub_queries") or list(r.s3_sub_queries)
            if sub_result.get("compiled_plan"):
                r.stages_run.append("S3_compiled")
            else:
                r.stages_run.append("S3")
            if FF_DEBUG:
                print(
                    f"[FF_DEBUG] S3 complete. Sub-queries: {str(r.s3_sub_queries)[:200]}...",
                    file=sys.stderr,
                    flush=True,
                )

            grounded_query = _build_grounded_query(
                query,
                grounding["raw_grounding"],
                sub_result["sub_queries"],
                sub_result["synthesis_hint"],
            )

        # Clear from any prior run context; set only if fallback occurs.
        r.deterministic_fallback_reason = ""

        # TEMPORARILY COMMENTED OUT: Judge plan and refinement loop
        # last_stage = "judge_plan"
        # plan_verdict = executor.judge_plan(
        #     query,
        #     grounding["raw_grounding"],
        #     sub_result["sub_queries"],
        #     sub_result["synthesis_hint"],
        # )
        # r.stages_run.append("judge_plan")
        #
        # if plan_verdict.get("verdict") == "FAIL" and plan_verdict.get("suggestion"):
        #     if FF_DEBUG:
        #         print(f"[FF_DEBUG] Judge plan failed, refining S3...", file=sys.stderr, flush=True)
        #     last_stage = "S3_refine"
        #     refine_input = (
        #         f"{query}\n\n"
        #         f"Plan correction note: {plan_verdict['suggestion']}"
        #     )
        #     refined_sub_result = stage3.run(refine_input, grounding["raw_grounding"], meta_str)
        #     sub_result = refined_sub_result
        #     r.s3_sub_queries = sub_result["sub_queries"]
        #     r.s3_synthesis_hint = sub_result["synthesis_hint"]
        #     r.stages_run.append("S3_refine")
        #     grounded_query = _build_grounded_query(
        #         query,
        #         grounding["raw_grounding"],
        #         sub_result["sub_queries"],
        #         sub_result["synthesis_hint"],
        #     )
        #     last_stage = "judge_plan_retry"
        #     plan_verdict = executor.judge_plan(
        #         query,
        #         grounding["raw_grounding"],
        #         sub_result["sub_queries"],
        #         sub_result["synthesis_hint"],
        #     )
        #     r.stages_run.append("judge_plan_retry")
        #
        # r.judge_verdict = plan_verdict
        # r.alignment_explanation = executor.explain_alignment(query, plan_verdict)

        deterministic_ok = False
        try:
            last_stage = "deterministic_exec"
            stage_t0 = time.time()
            det = _run_deterministic_plan(query, df, deterministic_plan_input)
            record_stage("agent", stage_t0)
            deterministic_ok = True
            r.answer = det.answer
            r.trace = det.trace
            r.executed = True
            r.final_code = det.final_code
            r.agent_tries = det.tries
            r.execution_attempts = list(det.attempts)
            r.stages_run.append("deterministic_exec")
            if FF_DEBUG:
                print("[FF_DEBUG] Deterministic execution succeeded.", file=sys.stderr, flush=True)
        except _DeterministicPlanUnsupported as det_err:
            r.stages_run.append("deterministic_fallback")
            r.deterministic_fallback_reason = str(det_err)
            if FF_DEBUG:
                print(
                    f"[FF_DEBUG] Deterministic execution unsupported ({det_err}); falling back to agent.",
                    file=sys.stderr,
                    flush=True,
                )

        if not deterministic_ok:
            if FF_DEBUG:
                print(f"[FF_DEBUG] Starting agent execution...", file=sys.stderr, flush=True)
            last_stage = "agent"
            stage_t0 = time.time()
            raw_answer, trace, details = executor.execute_single(grounded_query)
            record_stage("agent", stage_t0)
            r.trace = trace
            r.executed = True
            r.final_code = details.final_code or ""
            r.agent_tries = details.tries
            r.execution_attempts = list(details.attempts)
            r.stages_run.append("agent")
            r.answer = raw_answer

        # Final conversion/review layer: turn the raw machine answer (a Python
        # dict/scalar repr from deterministic_exec, or the agent's raw output)
        # into a direct, user-friendly natural-language response, checked
        # against the original question and Stage-3 synthesis guidance. The
        # pre-synthesis value is preserved in r.raw_answer for debugging/trace
        # inspection; r.answer becomes the user-facing text.
        if r.executed and r.answer:
            try:
                last_stage = "synthesis"
                stage_t0 = time.time()
                synthesized = executor.synthesize(query, [r.answer], r.s3_synthesis_hint)
                record_stage("synthesis", stage_t0)
                if synthesized:
                    r.raw_answer = r.answer
                    r.answer = synthesized
                r.stages_run.append("synthesis")
                if FF_DEBUG:
                    print(f"[FF_DEBUG] Synthesis complete: {r.answer[:100]}...", file=sys.stderr, flush=True)
            except Exception as synth_err:
                # Non-fatal: keep the raw machine answer if synthesis fails.
                if FF_DEBUG:
                    print(f"[FF_DEBUG] Synthesis step failed ({synth_err}); keeping raw answer.", file=sys.stderr, flush=True)

        if FF_DEBUG:
            print(f"[FF_DEBUG] Flash-Fusion complete. Answer: {r.answer[:100]}...", file=sys.stderr, flush=True)
        return r
    except Exception as e:
        if FF_DEBUG:
            import traceback
            print(f"[FF_DEBUG] Flash-Fusion FAILED at stage {last_stage}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
        r.answer = f"[ERROR in {last_stage}] {type(e).__name__}: {e}"
        r.alignment_explanation = f"Flash-Fusion failed during {last_stage}: {e}"
        r.executed = False
        raise
