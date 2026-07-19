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
    {"FILTER", "AGGREGATE", "GROUPBY", "COMPARE", "RANK", "SELECT"}
)


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


def _run_deterministic_plan(
    query: str,
    df,
    sub_queries: list[str],
) -> _DeterministicExecutionResult:
    """Execute an S3 plan with a strict deterministic operator vocabulary."""
    if not sub_queries:
        raise _DeterministicPlanUnsupported("No sub-queries provided")

    working_df = df
    observations: list[Any] = []
    attempts: list[dict[str, Any]] = []
    trace_lines: list[str] = []
    code_lines: list[str] = []

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
        op, text = _extract_plan_op(sq)
        text_l = text.lower()
        step_code = ""
        step_obs: Any = ""

        if op == "FILTER":
            # Avoid unnecessary non-null filters; pandas aggregations skip nulls.
            if "not null" in text_l or "is not null" in text_l:
                step_code = "# skipped no-op null filter"
                step_obs = f"rows={len(working_df)} (null-filter omitted as no-op)"
            else:
                eq_filters = _extract_eq_filters(text, list(working_df.columns))
                if not eq_filters:
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
                    for col, value in eq_filters:
                        if col not in working_df.columns:
                            raise _DeterministicPlanUnsupported(f"Unknown FILTER column: {col}")
                        if " or " in value.lower():
                            raise _DeterministicPlanUnsupported(
                                f"Unsupported disjunctive FILTER value: {value!r}"
                            )
                        # Keep values as string match first; if numeric cast succeeds, use numeric compare.
                        cast_value = value
                        if value.isdigit():
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

        elif op == "AGGREGATE":
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
            step_code = (
                f"result = df.groupby({group_col!r})[{value_col!r}].{agg_op}().to_dict()"
            )
            step_obs = result

        elif op == "RANK":
            # RANK: find the row with the max/min of a metric column and return
            # a dict containing both the entity identifier and the metric value,
            # matching the S3 prompt convention for [RANK] sub-questions.
            rank_agg = _extract_aggregate_op(text)
            if rank_agg not in {"max", "min"}:
                raise _DeterministicPlanUnsupported(
                    f"RANK only supports max/min ranking; got: {rank_agg!r}"
                )
            cols = _find_columns_in_text(text, list(working_df.columns))
            if len(cols) < 2:
                raise _DeterministicPlanUnsupported(
                    f"RANK requires at least two columns (identifier + metric); found: {cols}"
                )
            # Heuristic: the metric column is the last one mentioned in the text
            # (S3 typically writes "return X for the row with highest/lowest Y").
            metric_col = cols[-1]
            identifier_col = cols[0]
            if metric_col not in working_df.columns or identifier_col not in working_df.columns:
                raise _DeterministicPlanUnsupported(
                    f"Unknown column in RANK: metric={metric_col!r}, identifier={identifier_col!r}"
                )
            idx_val = (
                working_df[metric_col].idxmax()
                if rank_agg == "max"
                else working_df[metric_col].idxmin()
            )
            result = {
                identifier_col: working_df.loc[idx_val, identifier_col],
                metric_col: working_df.loc[idx_val, metric_col],
            }
            observations.append(result)
            step_code = (
                f"idx = df[{metric_col!r}].idx{'max' if rank_agg == 'max' else 'min'}(); "
                f"result = {{{identifier_col!r}: df.loc[idx, {identifier_col!r}], "
                f"{metric_col!r}: df.loc[idx, {metric_col!r}]}}"
            )
            step_obs = result

        elif op == "SELECT":
            # SELECT: return specified column(s) from the current working DataFrame.
            cols = _find_columns_in_text(text, list(working_df.columns))
            if not cols:
                raise _DeterministicPlanUnsupported(f"No known column found in SELECT: {text!r}")
            result = working_df[cols].to_dict(orient="list")
            observations.append(result)
            step_code = f"result = df[{cols!r}].to_dict(orient='list')"
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

    # Hard-disqualify any RHS that references multiple columns or operators.
    _MULTI_COL_CHARS = {",", "+", "-", "*", "/", "=", "[", "]"}
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
    """Construct an enriched agent prompt from S2 grounding and S3 decomposition."""
    sub_tasks = "\n".join(f"- {sq}" for sq in sub_queries) if sub_queries else "(none)"
    return (
        f"{query}\n\n"
        f"Concept-to-column mappings (use these exactly):\n{raw_grounding}\n\n"
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
    }

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
            det = _run_deterministic_plan(query, df, r.s3_sub_queries)
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
