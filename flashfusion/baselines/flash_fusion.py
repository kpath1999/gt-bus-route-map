"""
baselines/flash_fusion.py — Flash-Fusion baseline.

Default path (one LLM round-trip, no codegen, no sandbox):

    query
    -> single structured guardrail+plan call
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

import hashlib
import json
import os
import signal
import sys
import time
from typing import Any

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage

from flashfusion.config import FLASH_FUSION_PREDICTIVE_TIMEOUT_S
from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
from flashfusion.pipeline.operators import (
    FLASH_FUSION_PLANNER_PREFIX,
    NORMALIZATION_VERSION,
    PLANNER_PREFIX_SHA256,
    PLANNER_PREFIX_VERSION,
    DeterministicPlan,
    ParsedGuardrail,
    PlanSchemaError,
    StructuralValidationError,
    _extract_json_object,
    _strip_code_fence,
    execute_plan,
    log_operator_gap,
    parse_guardrail_response,
    structural_validate,
    validate_plan_against_dataframe,
)
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.prompts.templates import (
    FAST_PATH_PLANNER_TEMPLATE,
    PLANNER_DYNAMIC_SUFFIX_TEMPLATE,
)

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
#: Gate 2 found the plan referencing a field this dataset does not have. That is a
#: verdict about the question, not a gap in the vocabulary, so it is terminal — the
#: ReAct fallback cannot conjure a column either, it can only hallucinate one.
PATH_SCOPE_REJECT = "scope_reject"


# These caches only prepare immutable, byte-identical message components. They
# intentionally do not issue an LLM request: warming a provider prompt cache
# would itself be a billable planning call and distort benchmark comparisons.
_PLANNER_PREFIX_CACHE: dict[str, SystemMessage] = {}
_PLANNER_SUFFIX_PREFIX_CACHE: dict[tuple[str, str], str] = {}


def _debug(message: str) -> None:
    if FF_DEBUG:
        print(f"[FF_DEBUG] {message}", file=sys.stderr, flush=True)


def _planner_prefix_message(client: LLMClient) -> SystemMessage:
    """Return the stable planner prefix message for one provider session."""
    session_key = getattr(client, "session_key", "")
    message = _PLANNER_PREFIX_CACHE.get(session_key)
    if message is None:
        message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": FLASH_FUSION_PLANNER_PREFIX,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )
        _PLANNER_PREFIX_CACHE[session_key] = message
    return message


def _planner_suffix_prefix(meta_str: str, client: LLMClient, dataset: str = "") -> str:
    """Return the cached dynamic prompt through its final QUESTION field."""
    fingerprint = schema_fingerprint(meta_str)
    key = (getattr(client, "session_key", ""), f"{dataset or '(unnamed)'}:{fingerprint}")
    suffix_prefix = _PLANNER_SUFFIX_PREFIX_CACHE.get(key)
    if suffix_prefix is None:
        suffix_prefix = PLANNER_DYNAMIC_SUFFIX_TEMPLATE.format(
            dataset=dataset or "(unnamed)",
            schema_fingerprint=fingerprint,
            column_metadata=meta_str,
            query="",
        )
        _PLANNER_SUFFIX_PREFIX_CACHE[key] = suffix_prefix
    return suffix_prefix


def warm_flash_fusion_prefix(df: pd.DataFrame, client: LLMClient) -> None:
    """Prepare planner message components for a DataFrame without an LLM call."""
    meta_str = meta_to_str(build_column_metadata(df))
    _planner_prefix_message(client)
    _planner_suffix_prefix(meta_str, client)


def _call_log_length(client: LLMClient) -> int:
    call_log = getattr(client, "call_log", None)
    return len(call_log) if isinstance(call_log, list) else 0


def _record_call_usage(r: RunResult, client: LLMClient, start_index: int, prefix: str) -> None:
    """Store aggregate usage from calls appended after ``start_index``."""
    call_log = getattr(client, "call_log", None)
    calls = call_log[start_index:] if isinstance(call_log, list) else []
    setattr(r, f"{prefix}_input_tokens", sum(call.input_tokens for call in calls))
    setattr(r, f"{prefix}_output_tokens", sum(call.output_tokens for call in calls))
    setattr(r, f"{prefix}_cost_usd", sum(call.cost_usd for call in calls))


def schema_fingerprint(meta_str: str) -> str:
    """Short stable digest of the schema block, logged so two runs can be compared."""
    return hashlib.sha256(meta_str.encode("utf-8")).hexdigest()[:16]


def typed_plan_digest(plan: DeterministicPlan) -> str:
    """Digest of the *validated* plan — the determinism harness compares these."""
    payload = json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _format_typed_execution_value(value: Any) -> str:
    """Render typed execution values into a stable answer string."""
    if isinstance(value, dict) and len(value) == 2:
        window_key = next((k for k in value if "window" in k.lower()), None)
        if window_key is not None:
            metric_key = next(k for k in value if k != window_key)
            metric_value = value[metric_key]
            if isinstance(metric_value, float):
                metric_text = f"{metric_value:.4f}"
            else:
                metric_text = str(metric_value)
            return (
                f"The {window_key.replace('_', ' ')} starting at "
                f"{value[window_key]} has {metric_key.replace('_', ' ')} "
                f"{metric_text}."
            )
    return f"The result is {value}"


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
# Single structured LLM call — guardrail verdict + candidate plan
# ---------------------------------------------------------------------------


def attempt_fast_path_plan(
    query: str, meta_str: str, client: LLMClient
) -> dict | None:
    """Zero-shot a plan from the six strict fast-path skeletons, or decline.

    A single lightweight LLM call maps the query onto one of the allowed
    skeletons. It returns the raw plan dict only when the router is confident;
    it returns ``None`` when the router emits ``{"fallback": true}``, when the
    response cannot be parsed as a JSON object, or when the call itself fails.
    The function never raises — declining just means the full planner runs.
    """
    prompt = FAST_PATH_PLANNER_TEMPLATE.format(meta_str=meta_str, query=query)
    try:
        # The lightweight model is configured by the caller as ``client.light``.
        raw_text = client.light.invoke_messages(
            [HumanMessage(content=prompt)], stage="fast_path_plan"
        )
    except Exception as exc:  # noqa: BLE001 — fail open to the full planner
        _debug(f"Fast-path call failed ({type(exc).__name__}: {exc}).")
        return None
    try:
        payload = json.loads(_extract_json_object(_strip_code_fence(raw_text)))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("fallback") is True:
        return None
    return payload


def request_guardrail_and_plan(
    query: str, meta_str: str, client: LLMClient, dataset: str = ""
) -> tuple[ParsedGuardrail | None, str, str]:
    """Ask for the scope verdict and a candidate plan in one round-trip.

    The static planner contract goes in the system message and never varies; the
    dataset schema and the question go in the human message. Providers with
    prefix caching can therefore reuse the (large) vocabulary across every query.

    Returns:
        (parsed, dynamic_suffix, structural_error). ``parsed`` is None when the
        response could not be structurally validated (Gate 1 failure).
    """
    suffix = _planner_suffix_prefix(meta_str, client, dataset) + query
    # Messages are passed as objects, not through a template: the prefix is full
    # of literal braces from the operator JSON spec, and any interpolation pass
    # over it would both fail and break byte-stability.
    #
    # The prefix is sent as a single content block with an explicit `cache_control`
    # breakpoint. Providers with *automatic* caching (OpenAI, DeepSeek, Gemini,
    # Grok) ignore the marker and cache the prefix anyway; providers that require
    # an *explicit* breakpoint (Anthropic, Alibaba Qwen — see
    # OPERATOR_VOCABULARY_SPEC's caching note) only get cache hits with this
    # marker present. OpenRouter translates/drops the field per-provider, so it
    # is safe to always send it.
    messages = [_planner_prefix_message(client), HumanMessage(content=suffix)]
    raw = client.invoke_messages(messages, stage="guardrail_plan")
    try:
        return parse_guardrail_response(raw), suffix, ""
    except (StructuralValidationError, ValueError) as exc:
        return None, suffix, str(exc)


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
    # if grounding.strip():
    #     sections.append(
    #         "Concept-to-column grounding produced by schema analysis "
    #         "(use these exact column names; derive anything else from them):\n"
    #         f"{grounding.strip()}"
    #     )
    # if ambiguous_concepts:
    #     sections.append(
    #         "Concepts with no literal column — resolve them from the schema: "
    #         + ", ".join(ambiguous_concepts)
    #     )
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
        "fast_path": 0.0,
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

        # --- Static fast-path semantic router (fail-open) -------------------
        # A single cheap LLM call attempts one of five strict skeletons. Any
        # structural, schema, or execution failure silently delegates to the
        # full guardrail+planner path below. The fast path can only shortcut the
        # common case; it never rejects a query or alters the fallback logic.
        last_stage = "fast_path"
        started = time.time()
        fast_path_call_start = _call_log_length(client)
        fast_raw_plan = attempt_fast_path_plan(query, meta_str, client)
        r.ff_fast_path_latency_s = max(0.0, time.time() - started)
        _record_call_usage(r, client, fast_path_call_start, "ff_fast_path")
        record("fast_path", started)
        if fast_raw_plan is not None:
            try:
                fast_plan = structural_validate(fast_raw_plan)
                validate_plan_against_dataframe(fast_plan, df)
                fast_started = time.time()
                fast_exec = execute_plan(df, fast_plan)
                record("typed_exec", fast_started)
                if not fast_exec.ok:
                    raise RuntimeError(fast_exec.error or "fast-path execution failed")
                stage_latency_s["agent"] = stage_latency_s["typed_exec"]
                r.stage_latency_s = dict(stage_latency_s)
                r.plan_source = "fast_path"
                r.ff_fast_path_used = True
                r.execution_path = PATH_TYPED_OPERATOR
                r.plan_validation_stage_failed = ""
                r.raw_plan = fast_raw_plan
                r.typed_plan = fast_plan.model_dump(mode="json")
                r.typed_plan_sha256 = typed_plan_digest(fast_plan)
                r.operators_used = fast_plan.operators_used
                r.schema_fingerprint = schema_fingerprint(meta_str)
                r.answer = _format_typed_execution_value(fast_exec.value)
                r.trace = fast_exec.trace
                r.final_code = fast_exec.code
                r.agent_tries = len(fast_exec.steps)
                r.execution_attempts = list(fast_exec.steps)
                r.executed = True
                r.stages_run.append("fast_path")
                _debug(f"Fast-path execution ok in {fast_exec.latency_ms:.1f}ms")
                return r
            except Exception as exc:  # noqa: BLE001 — fail open to full planner
                _debug(
                    "Fast-path declined "
                    f"({type(exc).__name__}: {exc}); using full planner."
                )

        # --- Single structured call: guardrail verdict + candidate plan -----
        r.plan_source = "llm"
        r.ff_planner_used = True
        last_stage = "guardrail_plan"
        r.guardrail_input = query
        started = time.time()
        planner_call_start = _call_log_length(client)
        parsed_result, dynamic_suffix, structural_error = request_guardrail_and_plan(
            query, meta_str, client
        )
        r.ff_planner_latency_s = max(0.0, time.time() - started)
        _record_call_usage(r, client, planner_call_start, "ff_planner")
        record("guardrail+plan", started)
        r.stages_run.append("guardrail_plan")
        r.guardrail_input = dynamic_suffix
        r.planner_prefix_version = PLANNER_PREFIX_VERSION
        r.planner_prefix_sha256 = PLANNER_PREFIX_SHA256
        r.planner_cache_key = client.session_key
        r.schema_fingerprint = schema_fingerprint(meta_str)
        r.normalization_version = NORMALIZATION_VERSION

        parsed = parsed_result.parsed if parsed_result is not None else None
        if parsed_result is not None:
            raw_plan_payload = parsed_result.raw
            r.raw_plan = parsed_result.raw
            r.normalization_actions = list(parsed_result.normalization_actions)

        if parsed is None:
            gap_stage, gap_error = "structural", structural_error
            _debug(f"Gate 1 (structural) failed: {structural_error}")
            plan: DeterministicPlan | None = None
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
                r.typed_plan_sha256 = typed_plan_digest(plan)
                r.operators_used = plan.operators_used
                r.stages_run.append("plan_validated")
            except PlanSchemaError as exc:
                gap_stage, gap_error = "schema", str(exc)
                plan = None
                _debug(f"Gate 2 (schema) failed: {exc}")
                if exc.missing_columns:
                    # The plan asked for a field the dataset does not measure.
                    # No amount of agentic retrying creates that column, so
                    # stop here rather than letting ReAct invent a number.
                    missing = sorted(exc.missing_columns)
                    reason = (
                        "The dataset has no field(s) "
                        + ", ".join(repr(c) for c in missing)
                        + " required to answer this question."
                    )
                    r.execution_path = PATH_SCOPE_REJECT
                    r.plan_validation_stage_failed = "scope"
                    r.missing_columns = missing
                    r.rejected = True
                    r.executed = False
                    r.rejection_reason = reason
                    r.alignment_explanation = (
                        "Rejected at schema validation: the planner referenced "
                        f"non-existent column(s) {missing}."
                    )
                    r.answer = (
                        f"Query rejected. Reason: {reason} This request is not "
                        "supported by the current dataset schema."
                    )
                    log_operator_gap(
                        query=query,
                        stage="scope",
                        error=str(exc),
                        raw_plan=raw_plan_payload,
                    )
                    return r

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
                r.answer = _format_typed_execution_value(execution.value)
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
            # grounding = _ground_for_fallback(query, df, meta_str, client, r)
            # record("s2", started)

            react_query = build_react_query(query)
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
