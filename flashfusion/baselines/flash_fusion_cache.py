"""Flash-Fusion baseline with exact-match typed-plan skeleton caching.

A cache hit never returns a stored answer.  It retrieves only a previously
validated operator sequence, asks ``client.light`` to ground a new typed plan
with the SAME sequence against the live DataFrame schema, validates that plan
with the normal Flash-Fusion gates, and executes it deterministically.

If any cache, light-model, structural-validation, schema-validation, or
execution check fails, this module falls back to ``run_flash_fusion``.  It
never uses embedding/fuzzy matching and never executes unvalidated LLM code.

Registry contract
-----------------
``flashfusion/eval/cache/cache_registry.json`` may be either a mapping from
arbitrary cache keys to records or ``{"entries": [...]}``.  A reusable record
must contain at least:

    {
      "dataset": "ecg",
      "query_text": "<literal query text>",
      "status": "reusable",
      "operator_skeleton": ["FILTER_COMPARE", "COUNT_ROWS"],
      "operator_contract_hash": "<optional; recommended>",
      "schema_fingerprint": "<optional; recommended>"
    }

The cache lookup uses literal equality on ``dataset`` and ``query_text``.
Dataset names are canonicalised the same way the cache builder does it
(``mit_ecg`` -> ``ecg``) so benchmark dataset keys match registry keys.
Set ``dataset`` at the call site; if it is omitted, a hit is permitted only
when the same literal query text appears under exactly one dataset.  This
prevents cross-dataset accidental reuse.

The registry intentionally does NOT need to retain an answer or bound values.
The light model returns an entire candidate ``DeterministicPlan`` JSON object,
but it is constrained to the cached operator sequence.  Pydantic plus
``validate_plan_against_dataframe`` remain the authoritative validators.

Typical benchmark integration:

    result = run_flash_fusion_cache(
        query, df, client, r,
        dataset="mit_ecg",
        cache_path="flashfusion/eval/cache/cache_registry.json",
    )

The function has the same leading arguments as ``run_flash_fusion``.  On a
cache miss/failure it delegates to the normal baseline.  The small adapter in
``_run_normal_flash_fusion`` tolerates either ``r`` or ``result`` as the
existing baseline's optional RunResult parameter.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from flashfusion.baselines.flash_fusion import (
    _format_typed_execution_value,
    run_flash_fusion,
    typed_plan_digest,
)
from flashfusion.pipeline.operators import (
    DeterministicPlan,
    PlanExecutionError,
    PlanSchemaError,
    build_compact_operator_spec,
    execute_plan,
    validate_plan_against_dataframe,
)
from flashfusion.pipeline.runner import LLMClient, RunResult

BASELINE_NAME = "FLASH_FUSION_CACHE"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "eval" / "cache" / "cache_registry.json"

#: Execution path recorded on a successful cache-grounded typed run. Distinct
#: from ``typed_operator`` so cache wins are never confused with planner wins.
PATH_TYPED_OPERATOR_CACHE = "typed_operator_cache"

#: Registry dataset keys. Mirrors
#: ``build_operator_skeleton_cache.canonical_dataset_dir_name`` so a benchmark
#: ``--dataset mit_ecg`` run matches the ``ecg`` entries the builder wrote.
_DATASET_ALIASES = {"mit_ecg": "ecg", "ecg": "ecg", "bus": "bus", "wisdm": "wisdm"}

GROUNDING_SYSTEM_PROMPT = """You ground values into a fixed Flash-Fusion typed
operator skeleton. Return exactly one JSON object and no markdown, prose, or
code fences.

You MUST preserve the supplied operator sequence exactly: same number of
steps, same operator names, and same order as the REQUIRED OUTPUT checklist
given below. Never emit fewer or more steps than that checklist lists. Each
step object MUST use ONLY the exact field names given for its operator in
OPERATOR FIELD SPEC below — no renamed, added, or extra fields (e.g. never
"filter_column"/"filter_op"/"filter_value"/"input" — those are not real
fields). Fill those fields using the QUESTION and the LIVE DATASET SCHEMA.
Do not invent columns. Do not add, remove, reorder, or rename operators. Do
not return an answer and do not write Python.

Strict JSON requirements:
- Return a single valid JSON object only; no trailing commas.
- The top-level keys are exactly: {"version":"1","steps":[...]}.
- Every item in `steps` must be a valid object with an `op` field.
- Do not include comments, markdown fences, or any text before/after the JSON.
- If a step cannot be grounded, emit exactly:
  {"cache_grounding_failed": true, "reason": "..."}

The returned plan will be parsed by Pydantic (extra fields are rejected),
checked against the live DataFrame schema, and executed deterministically.
If the requested structure cannot be grounded from the schema, return:
{"cache_grounding_failed": true, "reason": "..."}
"""


# ---------------------------------------------------------------------------
# Trace record (debug aid for eval/trace_query.py --cache)
# ---------------------------------------------------------------------------


@dataclass
class CacheGroundingTrace:
    """Everything the cache path did, for human inspection.

    Populated even on failure paths so a trace shows exactly which gate sent
    the query back to the full planner.
    """

    cache_path: str = ""
    requested_dataset: str | None = None
    lookup_status: str = ""
    entry: dict[str, Any] | None = None
    operator_skeleton: list[str] = field(default_factory=list)
    prompt: str = ""
    raw_light_output: str = ""
    grounding_latency_s: float = 0.0
    parsed_plan: dict[str, Any] | None = None
    validated_plan: dict[str, Any] | None = None
    executed_value: Any = None
    hit: bool = False
    failure_reason: str = ""
    fell_back: bool = False


def _record(trace: CacheGroundingTrace | None, **fields: Any) -> None:
    if trace is None:
        return
    for name, value in fields.items():
        setattr(trace, name, value)


# ---------------------------------------------------------------------------
# Registry loading and lookup
# ---------------------------------------------------------------------------


def canonical_dataset(name: str | None) -> str | None:
    """Map a benchmark dataset key onto the registry's dataset key."""
    if name is None:
        return None
    key = str(name).strip().lower()
    return _DATASET_ALIASES.get(key, key)


def _load_entries(cache_path: str | Path) -> list[dict[str, Any]]:
    """Load both supported registry shapes and reject malformed records."""
    with Path(cache_path).open(encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        candidates: Iterable[Any] = raw["entries"]
    elif isinstance(raw, dict):
        candidates = raw.values()
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise ValueError("cache registry must be a list, a key->record mapping, or {'entries': [...]}")
    return [x for x in candidates if isinstance(x, dict)]


def _normalise_query(query: str) -> str:
    # Preserve exact-match semantics.  Surrounding whitespace is transport
    # noise; all internal whitespace, punctuation, and case remain meaningful.
    return query.strip()


def _find_exact_entry(
    entries: Iterable[dict[str, Any]], query: str, dataset: str | None
) -> tuple[dict[str, Any] | None, str]:
    q = _normalise_query(query)
    wanted = canonical_dataset(dataset)
    matches = [
        entry
        for entry in entries
        if entry.get("status") == "reusable"
        and isinstance(entry.get("query_text"), str)
        and entry["query_text"].strip() == q
        and (wanted is None or canonical_dataset(entry.get("dataset")) == wanted)
    ]
    if not matches:
        return None, "exact_query_miss"
    if wanted is None:
        datasets = {canonical_dataset(entry.get("dataset")) for entry in matches}
        if len(datasets) != 1:
            return None, "ambiguous_cross_dataset_exact_match"
    if len(matches) != 1:
        return None, "duplicate_registry_entries"
    skeleton = matches[0].get("operator_skeleton")
    if not isinstance(skeleton, list) or not skeleton or not all(isinstance(x, str) for x in skeleton):
        # Out-of-scope queries are cached with an empty skeleton; there is no
        # operator sequence to reuse, so the full planner must decide again.
        return None, "invalid_or_empty_operator_skeleton"
    return matches[0], "exact_cache_hit"


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


def _schema_fingerprint(df: pd.DataFrame) -> str:
    """Stable local fingerprint for optional cache invalidation.

    This deliberately fingerprints columns and dtypes only. The normal live
    schema validator remains responsible for domain/type requirements; do not
    cache data values or answers.
    """
    payload = [(str(column), str(dtype)) for column, dtype in df.dtypes.items()]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _schema_context(df: pd.DataFrame, max_values: int = 8) -> str:
    """Provide the light model compact schema grounding context, not raw rows."""
    lines = []
    for column in df.columns:
        series = df[column]
        line = f"- {column}: dtype={series.dtype}; nulls={int(series.isna().sum())}"
        if not series.empty and (
            pd.api.types.is_object_dtype(series) or isinstance(series.dtype, pd.CategoricalDtype)
        ):
            values = [str(v) for v in series.dropna().drop_duplicates().head(max_values).tolist()]
            line += f"; sample_values={values}"
        lines.append(line)
    return "\n".join(lines)


def _grounding_prompt(query: str, entry: dict[str, Any], df: pd.DataFrame) -> str:
    skeleton = entry["operator_skeleton"]
    n = len(skeleton)
    checklist = "\n".join(f"  step {i + 1}: {op}" for i, op in enumerate(skeleton))
    return "\n".join(
        [
            f"QUESTION (literal exact cache key): {query}",
            f"DATASET: {entry.get('dataset', '(unspecified)')}",
            f"REQUIRED OUTPUT: exactly {n} steps, in this exact order — do not omit, merge, add, or reorder any:",
            checklist,
            "OPERATOR FIELD SPEC (use these exact field names, nothing else):",
            build_compact_operator_spec(skeleton),
            "LIVE DATASET SCHEMA:",
            _schema_context(df),
            f"\nBefore answering, count your steps array: it MUST have length {n}.",
        ]
    )


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].rstrip()
    return raw.strip()


def _repair_light_json(raw: str) -> str:
    """Repair the most common LLM JSON mistakes without inventing semantics.

    The lightweight repair is intentionally small: strip markdown fences, remove
    trailing commas before closing brackets/braces, and trim to the first JSON
    object in the response. Anything still malformed is rejected explicitly so
    the cache path can fall back to the full planner instead of crashing.
    """
    cleaned = _strip_code_fence(raw)
    if not cleaned:
        raise ValueError("light model returned empty content")

    candidates = [cleaned]
    candidates.append(re.sub(r",(\s*[}\]])", r"\1", cleaned))

    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if 0 <= first < last:
        candidates.append(cleaned[first : last + 1])

    last_err: ValueError | None = None
    for candidate in candidates:
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError as exc:
            last_err = ValueError(f"light model JSON repair failed: {exc.msg}")
    if last_err is not None:
        raise last_err
    raise ValueError("light model output was not valid JSON")


def _invoke_light_for_plan(
    client: LLMClient, prompt: str, trace: CacheGroundingTrace | None = None
) -> dict[str, Any]:
    """Call only the configured light model and parse a single JSON object.

    Messages are passed directly rather than through a ``ChatPromptTemplate``:
    the system prompt contains a literal JSON output contract, whose braces a
    template would try to interpolate. Going through ``invoke_messages`` also
    records tokens, latency, and cost in the client's call_log — otherwise the
    cache baseline would report a free LLM call in the benchmark.
    """
    light = getattr(client, "light", None) or client
    if getattr(light, "llm", None) is None or not hasattr(light, "invoke_messages"):
        raise RuntimeError("cache grounding requires client.light.llm")
    messages = [
        SystemMessage(content=GROUNDING_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]
    started = time.perf_counter()
    raw = light.invoke_messages(messages, stage="cache_grounding")
    _record(
        trace,
        raw_light_output=raw,
        grounding_latency_s=time.perf_counter() - started,
    )
    repaired = _repair_light_json(raw)
    parsed = json.loads(repaired)
    if not isinstance(parsed, dict):
        raise ValueError("light model output must be a JSON object")
    if parsed.get("cache_grounding_failed") is True:
        raise ValueError(f"light model declined grounding: {parsed.get('reason', '')}")
    _record(trace, parsed_plan=parsed)
    return parsed


def _parse_and_validate_cached_plan(
    raw_plan: dict[str, Any], expected_skeleton: list[str], df: pd.DataFrame
) -> DeterministicPlan:
    """Apply both standard Flash-Fusion gates plus skeleton equality."""
    plan = DeterministicPlan.model_validate(raw_plan)  # structural/Pydantic gate
    actual_skeleton = [step.op for step in plan.steps]
    if actual_skeleton != expected_skeleton:
        raise PlanSchemaError(
            "cache grounding changed the cached operator skeleton: "
            f"expected={expected_skeleton!r}, actual={actual_skeleton!r}"
        )
    validate_plan_against_dataframe(plan, df)  # live schema/semantic gate
    return plan


# ---------------------------------------------------------------------------
# RunResult plumbing
# ---------------------------------------------------------------------------


def _set_if_present(result: RunResult, name: str, value: Any) -> None:
    if hasattr(result, name):
        setattr(result, name, value)


def _append_stage(result: RunResult, stage: str) -> None:
    stages = getattr(result, "stages_run", None)
    if isinstance(stages, list):
        stages.append(stage)


def _new_result(query: str, client: LLMClient) -> RunResult:
    model = getattr(client, "model_name", None) or getattr(client, "model", None) or ""
    return RunResult(baseline=BASELINE_NAME, model=str(model), query=query)


def _record_cache_failure(result: RunResult, reason: str) -> None:
    _set_if_present(result, "deterministic_fallback_reason", f"cache: {reason}")
    _append_stage(result, "cache_miss_or_validation_failure")


def _run_normal_flash_fusion(
    query: str, df: pd.DataFrame, client: LLMClient, result: RunResult, **kwargs: Any
) -> RunResult:
    """Delegate safely despite small signature differences across revisions."""
    signature = inspect.signature(run_flash_fusion)
    call_kwargs = {name: value for name, value in kwargs.items() if name in signature.parameters}
    if "r" in signature.parameters:
        call_kwargs["r"] = result
    elif "result" in signature.parameters:
        call_kwargs["result"] = result
    returned = run_flash_fusion(query, df, client, **call_kwargs)
    return returned if isinstance(returned, RunResult) else result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_flash_fusion_cache(
    query: str,
    df: pd.DataFrame,
    client: LLMClient,
    r: RunResult | None = None,
    *,
    dataset: str | None = None,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    expected_operator_contract_hash: str | None = None,
    trace: CacheGroundingTrace | None = None,
    **flash_fusion_kwargs: Any,
) -> RunResult:
    """Run the cache-first Flash-Fusion baseline.

    On a valid cache hit this uses one light-model call for value grounding,
    then normal typed validation/execution. On every non-successful cache
    path it falls back to the existing full Flash-Fusion planner.
    """
    result = r if r is not None else _new_result(query, client)
    started = time.perf_counter()
    _record(
        trace,
        cache_path=str(cache_path),
        requested_dataset=canonical_dataset(dataset),
    )

    try:
        entries = _load_entries(cache_path)
        entry, lookup_status = _find_exact_entry(entries, query, dataset)
        _record(trace, lookup_status=lookup_status, entry=entry)
        if entry is None:
            raise LookupError(lookup_status)
        _record(trace, operator_skeleton=list(entry["operator_skeleton"]))

        cached_contract_hash = entry.get("operator_contract_hash")
        if expected_operator_contract_hash and cached_contract_hash != expected_operator_contract_hash:
            raise LookupError("operator_contract_hash_mismatch")
        cached_schema_fingerprint = entry.get("schema_fingerprint")
        if cached_schema_fingerprint and cached_schema_fingerprint != _schema_fingerprint(df):
            raise LookupError("schema_fingerprint_mismatch")

        _append_stage(result, "exact_cache_hit")
        _append_stage(result, "cache_light_grounding")
        prompt = _grounding_prompt(query, entry, df)
        _record(trace, prompt=prompt)
        raw_plan = _invoke_light_for_plan(client, prompt, trace)
        plan = _parse_and_validate_cached_plan(raw_plan, entry["operator_skeleton"], df)
        _append_stage(result, "cache_plan_validated")
        _record(trace, validated_plan=plan.model_dump(mode="json"))

        execution = execute_plan(df, plan)
        if not execution.ok:
            # A validated plan that still fails at execution is a real coverage
            # gap, not a cache answer — hand it to the full planner.
            raise PlanExecutionError(execution.error or "typed execution failed")
        _append_stage(result, "typed_exec")
        _record(trace, executed_value=execution.value, hit=True)

        answer = _format_typed_execution_value(execution.value)
        _set_if_present(result, "answer", answer)
        _set_if_present(result, "raw_answer", str(execution.value))
        _set_if_present(result, "executed_value", execution.value)
        _set_if_present(result, "executed", True)
        _set_if_present(result, "rejected", False)
        _set_if_present(result, "execution_path", PATH_TYPED_OPERATOR_CACHE)
        _set_if_present(result, "plan_validation_stage_failed", "")
        _set_if_present(result, "plan_source", "exact_query_cache_light_grounded")
        _set_if_present(result, "typed_plan", plan.model_dump(mode="json"))
        _set_if_present(result, "typed_plan_sha256", typed_plan_digest(plan))
        _set_if_present(result, "operators_used", list(plan.operators_used))
        _set_if_present(result, "agent_tries", len(execution.steps))
        _set_if_present(result, "execution_attempts", list(execution.steps))
        _set_if_present(
            result,
            "typed_execution_certificate",
            {
                "certificate_status": "ok",
                "execution_path": PATH_TYPED_OPERATOR_CACHE,
                "typed_plan_sha256": typed_plan_digest(plan),
                "operators_used": list(execution.operators_used),
                "rows_scanned": execution.rows_scanned,
                "rows_after_filter": execution.rows_after_filter,
                "cache_query_text": entry.get("query_text", ""),
                "cache_dataset": entry.get("dataset", ""),
                "result": execution.value,
            },
        )
        _set_if_present(
            result,
            "trace",
            "Cache hit: exact query text; light model grounded cached skeleton; "
            "validated typed execution.\n" + (execution.trace or ""),
        )
        # Leave final_code empty. The canonical provenance is typed_plan; do
        # not generate a second, lossy pseudo-Python artifact in this baseline.
        _set_if_present(result, "final_code", "")
        elapsed = time.perf_counter() - started
        _set_if_present(result, "latency_s", elapsed)
        stage_latency = getattr(result, "stage_latency_s", None)
        if isinstance(stage_latency, dict):
            stage_latency["cache_grounding"] = (
                trace.grounding_latency_s if trace is not None else 0.0
            )
            stage_latency["typed_exec"] = execution.latency_ms / 1000.0
        return result

    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        ValidationError,
        PlanSchemaError,
        PlanExecutionError,
        LookupError,
        RuntimeError,
    ) as exc:
        _record(trace, failure_reason=f"{type(exc).__name__}: {exc}", fell_back=True)
        _record_cache_failure(result, str(exc))
        return _run_normal_flash_fusion(query, df, client, result, **flash_fusion_kwargs)
