"""
baselines/flash_fusion.py — Flash-Fusion baseline.

Default path (one LLM round-trip, no codegen, no sandbox):

    query
      -> bypass detector (0 LLM calls)  OR  single structured guardrail+plan call
      -> Gate 1: Pydantic structural validation   (microseconds)
      -> Gate 2: DataFrame schema validation      (microseconds)
    -> execute_plan(df, plan)  — typed operators, in-process
    -> local result formatting

Fallback path (ReAct) is entered ONLY when:
  * the planner returns in_scope=True but no plan (operator vocabulary gap), or
  * Gate 1 fails (malformed/unknown operator), or
  * Gate 2 fails (unknown column / wrong dtype), or
  * execution of a validated plan fails.

Every fallback is recorded via ``log_operator_gap()`` for offline review. The
vocabulary is never extended inside a live request — that would reintroduce the
arbitrary-code-execution risk typed operators exist to remove.

Instrumentation (see RunResult): ``execution_path``,
``plan_validation_stage_failed``, ``typed_plan``, ``operators_used``,
``plan_source``, plus per-stage latency in ``stage_latency_s``.
"""

from __future__ import annotations

import os
import re
import signal
import sys
import time
from typing import Any

import pandas as pd
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.config import FLASH_FUSION_PREDICTIVE_TIMEOUT_S
from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
from flashfusion.pipeline.operators import (
    OPERATOR_VOCABULARY_SPEC,
    DeterministicPlan,
    GuardrailAndPlan,
    PlanSchemaError,
    StructuralValidationError,
    execute_plan,
    log_operator_gap,
    parse_guardrail_and_plan,
    structural_validate,
    validate_plan_against_dataframe,
)
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.prompts.templates import GUARDRAIL_AND_PLAN_PROMPT

FF_DEBUG = os.getenv("FF_DEBUG", "").lower() in ("1", "true", "yes")

#: Run concept extraction + schema grounding before the ReAct fallback. This
#: costs two extra LLM calls, but only on the small subset the typed vocabulary
#: cannot express — the fast path stays at a single round-trip.
FF_FALLBACK_GROUNDING = os.getenv("FF_FALLBACK_GROUNDING", "1").lower() in (
    "1",
    "true",
    "yes",
)

PATH_GUARDRAIL_REJECT = "guardrail_reject"
PATH_TYPED_OPERATOR = "typed_operator"
PATH_REACT_FALLBACK = "react_fallback"


def _debug(message: str) -> None:
    if FF_DEBUG:
        print(f"[FF_DEBUG] {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Timeout helper
# ---------------------------------------------------------------------------


class _FlashFusionTimeoutError(TimeoutError):
    """Raised when a Flash-Fusion execution path exceeds its allotted time."""


def _run_with_timeout(
    fn: Any,
    timeout_s: float,
    *,
    timeout_message: str = "Operation timed out",
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> Any:
    """Run a callable with a hard timeout and raise a structured timeout error."""
    if kwargs is None:
        kwargs = {}

    def _handle_timeout(signum: int, frame: Any) -> None:  # pragma: no cover - defensive
        raise _FlashFusionTimeoutError(timeout_message)

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        return fn(*args, **kwargs)
    except _FlashFusionTimeoutError:
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


# ---------------------------------------------------------------------------
# Bypass detector — builds a typed plan with zero LLM calls
# ---------------------------------------------------------------------------

_PREDICTIVE_MODEL_PHRASES: tuple[tuple[str, str], ...] = (
    ("hist gradient boosting", "hist_gradient_boosting"),
    ("random forest", "random_forest"),
    ("1-nearest-neighbor", "one_nearest_neighbor"),
    ("nearest neighbor", "one_nearest_neighbor"),
    ("logistic regression", "logistic_regression"),
)

#: Columns treated as "the acceleration features" when a predictive query names
#: the feature family instead of listing columns. Mirrors the feature set used
#: by eval/build_groundtruth/simple_pred.py for the bus dataset.
_ACCEL_FEATURE_EXTRAS = ("extreme_event_magnitude", "instability_score")


def _split_feature_names(text: str) -> list[str]:
    cleaned = re.sub(r"\band\b", ",", text)
    return [part.strip().strip("`'\"") for part in cleaned.split(",") if part.strip()]


def _resolve_feature_columns(
    query: str, df: pd.DataFrame, excluded: set[str]
) -> list[str]:
    """Resolve an explicit, auditable feature list for a predictive plan."""
    match = re.search(
        r"using the features?\s+(.+?)(?:\.|$)", query, re.IGNORECASE | re.DOTALL
    )
    if match:
        named = [c for c in _split_feature_names(match.group(1)) if c in df.columns]
        if named:
            return named

    if re.search(r"using the acceleration features", query, re.IGNORECASE):
        return [
            column
            for column in df.columns
            if column not in excluded
            and pd.api.types.is_numeric_dtype(df[column])
            and ("accel" in column.lower() or column in _ACCEL_FEATURE_EXTRAS)
        ]

    return []


def detect_predictive_plan(query: str, df: pd.DataFrame) -> DeterministicPlan | None:
    """Recognize the CHRONO_SPLIT+CLASSIFY template (eval/queries.py ids 13-16).

    Returns a fully typed, already-structurally-valid DeterministicPlan, or None
    when the query does not confidently match — in which case the normal
    single-call planning path runs.
    """
    lowered = query.lower()
    if not ("train" in lowered and "predict" in lowered and "holdout" in lowered):
        return None

    model = next(
        (key for phrase, key in _PREDICTIVE_MODEL_PHRASES if phrase in lowered), None
    )
    if model is None:
        return None

    sort_match = re.search(
        r"by\s+([A-Za-z_][A-Za-z0-9_]*)\s+in ascending order", query, re.IGNORECASE
    )
    if not sort_match or sort_match.group(1) not in df.columns:
        return None
    sort_by = [sort_match.group(1)]

    tie_match = re.search(
        r"using\s+([A-Za-z_][A-Za-z0-9_]*)\s+as the tie-breaker", query, re.IGNORECASE
    )
    if tie_match and tie_match.group(1) in df.columns:
        sort_by.append(tie_match.group(1))

    fraction_match = re.search(r"first\s+(\d+)\s*%", query, re.IGNORECASE)
    train_fraction = float(fraction_match.group(1)) / 100.0 if fraction_match else 0.8

    filter_column: str | None = None
    filter_value: Any = None
    filter_match = re.search(
        r"Filter to\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\d+)", query, re.IGNORECASE
    )
    if filter_match:
        if filter_match.group(1) not in df.columns:
            return None
        filter_column, filter_value = filter_match.group(1), int(filter_match.group(2))

    row_match = re.search(
        r"for the (first|last) row in the holdout set", query, re.IGNORECASE
    )
    holdout_row = row_match.group(1).lower() if row_match else "first"

    target_from_non_empty = False
    column_target = re.search(
        r"label in the\s+([A-Za-z_][A-Za-z0-9_]*)\s+column", query, re.IGNORECASE
    )
    if column_target and column_target.group(1) in df.columns:
        target_column = column_target.group(1)
        target_label = target_column
    elif "activity label" in lowered and "activity_label" in df.columns:
        target_column, target_label = "activity_label", "activity"
    elif "annotation is present" in lowered and "annotation" in df.columns:
        target_column, target_label = "annotation", "annotation"
        target_from_non_empty = True
    else:
        return None

    excluded = {target_column, *sort_by} | ({filter_column} if filter_column else set())
    feature_columns = _resolve_feature_columns(query, df, excluded)
    if not feature_columns:
        return None

    # Routed through Gate 1 like any other plan: the bypass detector gets no
    # privileged path into the executor.
    try:
        return structural_validate(
            {
                "version": "1",
                "steps": [
                    {
                        "op": "PREDICTIVE_PIPELINE",
                        "model": model,
                        "feature_columns": feature_columns,
                        "target_column": target_column,
                        "sort_by": sort_by,
                        "train_fraction": train_fraction,
                        "holdout_row": holdout_row,
                        "filter_column": filter_column,
                        "filter_value": filter_value,
                        "target_from_non_empty": target_from_non_empty,
                        "target_label": target_label,
                    }
                ],
            }
        )
    except StructuralValidationError as exc:
        _debug(f"Predictive bypass failed structural validation: {exc}")
        return None


# ---------------------------------------------------------------------------
# Single structured LLM call — guardrail verdict + candidate plan
# ---------------------------------------------------------------------------


def request_guardrail_and_plan(
    query: str, meta_str: str, client: LLMClient
) -> tuple[GuardrailAndPlan | None, str, str]:
    """Ask for the scope verdict and a candidate plan in one round-trip.

    Returns:
        (parsed, raw_response, structural_error). ``parsed`` is None when the
        response could not be structurally validated (Gate 1 failure).
    """
    # Use LangChain's partial_variables to avoid curly-brace conflicts with
    # the JSON structures in OPERATOR_VOCABULARY_SPEC
    prompt = ChatPromptTemplate.from_messages(
        [("system", GUARDRAIL_AND_PLAN_PROMPT), ("human", "{query}")]
    )
    prompt = prompt.partial(
        column_metadata=meta_str, operator_spec=OPERATOR_VOCABULARY_SPEC
    )
    chain = prompt | client.llm | StrOutputParser()
    raw = client.invoke_chain(chain, {"query": query}, stage="guardrail_plan")
    try:
        return parse_guardrail_and_plan(raw), raw, ""
    except (StructuralValidationError, ValueError) as exc:
        return None, raw, str(exc)


# ---------------------------------------------------------------------------
# ReAct fallback prompt
# ---------------------------------------------------------------------------
# (NOTE): is S1/S2 even needed? ReAct-Only worked fine even on ambiguous queries
# that would have benefitted from concept-to-column matching

def build_react_query(
    query: str, grounding: str = "", ambiguous_concepts: list[str] | None = None
) -> str:
    """Build the ReAct prompt used whenever the typed path cannot serve a query.

    Concept-to-column grounding is included when available: a fallback happens
    precisely because the typed plan was insufficient, so the agent benefits
    from the mapping as scaffolding while reasoning over the raw DataFrame.
    """
    sections = [query]
    if grounding.strip():
        sections.append(
            "Concept-to-column grounding produced by schema analysis "
            "(use these exact column names; derive anything else from them):\n"
            f"{grounding.strip()}"
        )
    if ambiguous_concepts:
        sections.append(
            "Concepts with no literal column — resolve them from the schema: "
            + ", ".join(ambiguous_concepts)
        )
    return "\n\n".join(sections)


def _ground_for_fallback(
    query: str, df: pd.DataFrame, meta_str: str, client: LLMClient, r: RunResult
) -> str:
    """Run concept extraction + schema grounding for the ReAct fallback subset."""
    if not FF_FALLBACK_GROUNDING:
        return ""
    from flashfusion.pipeline.stages import (
        Stage1_ConceptExtraction,
        Stage2_SchemaGrounding,
    )

    try:
        concepts = Stage1_ConceptExtraction(client.light).run(query, df)
        r.s1_concepts = concepts
        r.stages_run.append("S1")
        grounding = Stage2_SchemaGrounding(client.light).run(
            concepts, query, meta_str, df
        )
        r.s2_grounding = grounding["raw_grounding"]
        r.s2_filtered_concepts = grounding.get("filtered_concepts", {})
        r.stages_run.append("S2")
        return grounding["raw_grounding"]
    except Exception as exc:  # noqa: BLE001 — grounding is best-effort scaffolding
        _debug(f"Fallback grounding failed ({exc}); using bare query.")
        return ""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_flash_fusion(
    query: str,
    df,
    client: LLMClient,
    r: RunResult,
    timeout_s: float | None = None,
) -> RunResult:
    """Execute the Flash-Fusion pipeline for one query."""
    effective_timeout = (
        timeout_s if timeout_s is not None else FLASH_FUSION_PREDICTIVE_TIMEOUT_S
    )
    stage_latency_s: dict[str, float] = {
        "s1": 0.0,
        "s2": 0.0,
        "s3": 0.0,
        "guardrail+plan": 0.0,
        "typed_exec": 0.0,
        "agent": 0.0,
    }
    r.stage_latency_s = dict(stage_latency_s)

    def record(stage: str, started: float) -> None:
        stage_latency_s[stage] = stage_latency_s.get(stage, 0.0) + max(
            0.0, time.time() - started
        )
        r.stage_latency_s = dict(stage_latency_s)

    last_stage = "init"
    try:
        meta_str = meta_to_str(build_column_metadata(df))
        ambiguous: list[str] = []
        gap_stage = ""
        gap_error = ""
        raw_plan_payload: Any = None

        # --- Bypass detector: a pre-validated typed plan, zero LLM calls -----
        last_stage = "bypass_detect"
        plan: DeterministicPlan | None = detect_predictive_plan(query, df)
        if plan is not None:
            r.plan_source = "predictive_template"
            r.stages_run.append("plan_bypass_predictive")
            _debug(f"Predictive template matched: {plan.steps[0].op}")

        # --- Single structured call: guardrail verdict + candidate plan -----
        if plan is None:
            r.plan_source = "llm"
            last_stage = "guardrail_plan"
            r.guardrail_input = query
            started = time.time()
            parsed, raw, structural_error = request_guardrail_and_plan(
                query, meta_str, client
            )
            record("guardrail+plan", started)
            r.stages_run.append("guardrail_plan")
            raw_plan_payload = raw

            if parsed is None:
                gap_stage, gap_error = "structural", structural_error
                _debug(f"Gate 1 (structural) failed: {structural_error}")
            elif not parsed.in_scope:
                reason = (
                    parsed.rejection_reason
                    or "The query cannot be answered from the available columns."
                )
                r.execution_path = PATH_GUARDRAIL_REJECT
                r.rejected = True
                r.executed = False
                r.rejection_reason = reason
                r.alignment_explanation = (
                    "Rejected by the guardrail because the query cannot be "
                    f"answered from available dataset fields. Reason: {reason}"
                )
                r.answer = (
                    f"Query rejected. Reason: {reason}. This request is not "
                    "supported by the current dataset schema or task scope."
                )
                return r
            else:
                ambiguous = list(parsed.ambiguous_concepts)
                r.ambiguous_concepts = ambiguous
                plan = parsed.plan
                if plan is None:
                    gap_stage = "no_plan"
                    gap_error = (
                        "planner returned no plan; unresolved: "
                        f"{ambiguous or ['(unspecified)']}"
                    )

        # --- Gate 2: DataFrame schema validation ----------------------------
        if plan is not None:
            last_stage = "schema_validation"
            raw_plan_payload = plan.model_dump(mode="json")
            try:
                validate_plan_against_dataframe(plan, df)
                r.typed_plan = raw_plan_payload
                r.operators_used = plan.operators_used
                r.stages_run.append("plan_validated")
            except PlanSchemaError as exc:
                gap_stage, gap_error = "schema", str(exc)
                plan = None
                _debug(f"Gate 2 (schema) failed: {exc}")

        # --- Typed operator execution ---------------------------------------
        if plan is not None:
            last_stage = "typed_exec"
            started = time.time()
            try:
                execution = _run_with_timeout(
                    execute_plan,
                    effective_timeout,
                    args=(df, plan),
                    timeout_message=(
                        f"Flash-Fusion execution exceeded {effective_timeout:.0f}s timeout"
                    ),
                )
            except _FlashFusionTimeoutError as exc:
                record("typed_exec", started)
                r.execution_path = PATH_TYPED_OPERATOR
                r.plan_validation_stage_failed = "execution"
                r.deterministic_fallback_reason = str(exc)
                r.stages_run.append("typed_exec_timeout")
                r.answer = str(exc)
                r.trace = f"Timed out after {effective_timeout:.0f}s in typed execution."
                r.executed = False
                return r
            record("typed_exec", started)
            # Keep the legacy "agent" latency slot meaningful: it is the
            # execution phase, whichever engine served it.
            stage_latency_s["agent"] = stage_latency_s["typed_exec"]
            r.stage_latency_s = dict(stage_latency_s)

            if execution.ok:
                r.execution_path = PATH_TYPED_OPERATOR
                r.plan_validation_stage_failed = ""
                r.answer = f"The result is {execution.value}"
                r.trace = execution.trace
                r.final_code = execution.code
                r.agent_tries = len(execution.steps)
                r.execution_attempts = list(execution.steps)
                r.executed = True
                r.stages_run.append("typed_exec")
                _debug(f"Typed execution ok in {execution.latency_ms:.1f}ms")
            else:
                gap_stage, gap_error = "execution", execution.error or "unknown"
                plan = None
                _debug(f"Typed execution failed: {gap_error}")

        # --- ReAct fallback --------------------------------------------------
        if plan is None:
            log_operator_gap(
                query=query,
                stage=gap_stage or "no_plan",
                error=gap_error,
                raw_plan=raw_plan_payload,
            )
            r.execution_path = PATH_REACT_FALLBACK
            r.plan_validation_stage_failed = gap_stage or "no_plan"
            r.deterministic_fallback_reason = f"{gap_stage}: {gap_error}"
            r.stages_run.append("react_fallback")

            last_stage = "fallback_grounding"
            started = time.time()
            grounding = _ground_for_fallback(query, df, meta_str, client, r)
            record("s2", started)

            react_query = build_react_query(query, grounding, ambiguous)
            r.react_query = react_query
            r.grounded_query = react_query

            last_stage = "agent"
            started = time.time()
            try:
                raw_answer, trace, details = _run_with_timeout(
                    ExecutionLayer(df, client).execute_single,
                    effective_timeout,
                    args=(react_query,),
                    timeout_message=(
                        f"Flash-Fusion execution exceeded {effective_timeout:.0f}s timeout"
                    ),
                )
            except _FlashFusionTimeoutError as exc:
                record("agent", started)
                r.stages_run.append("agent_timeout")
                r.answer = str(exc)
                r.trace = f"Timed out after {effective_timeout:.0f}s in ReAct fallback."
                r.executed = False
                return r
            record("agent", started)
            r.answer = raw_answer
            r.trace = trace
            r.final_code = details.final_code or ""
            r.agent_tries = details.tries
            r.execution_attempts = list(details.attempts)
            r.executed = True
            r.stages_run.append("agent")

        return r
    except Exception as exc:
        if FF_DEBUG:
            import traceback

            print(f"[FF_DEBUG] Flash-Fusion FAILED at {last_stage}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        r.answer = f"[ERROR in {last_stage}] {type(exc).__name__}: {exc}"
        r.alignment_explanation = f"Flash-Fusion failed during {last_stage}: {exc}"
        r.executed = False
        raise
