"""Flash-Fusion baseline with exact-match typed-plan skeleton caching.

A cache hit never returns a stored answer.  It retrieves only a previously
validated operator sequence, asks ``client.light`` to ground a new typed plan
with the SAME sequence against the live DataFrame schema, validates that plan
with the normal Flash-Fusion gates, and executes it deterministically.

If any cache, light-model, structural-validation, schema-validation, or
execution check fails, this module falls back to ``run_flash_fusion``.

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

An out-of-scope query may be cached with an empty skeleton:

    {
      "dataset": "bus",
      "query_text": "How does passenger occupancy correlate with road roughness?",
      "status": "reusable",
      "operator_skeleton": []
    }

The cache hit still requires a single light-model call to infer a guardrail-style
rejection reason from the query and the live DataFrame schema; it does not invoke
the full planner or guardrail.

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
import importlib
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
DEFAULT_HYBRID_CONFIG_PATH = Path(__file__).resolve().parents[1] / "eval" / "cache" / "hybrid_match_config.json"

#: Execution path recorded on a successful cache-grounded typed run. Distinct
#: from ``typed_operator`` so cache wins are never confused with planner wins.
PATH_TYPED_OPERATOR_CACHE = "typed_operator_cache"

#: Registry dataset keys. Mirrors
#: ``build_operator_skeleton_cache.canonical_dataset_dir_name`` so a benchmark
#: ``--dataset mit_ecg`` run matches the ``ecg`` entries the builder wrote.
_DATASET_ALIASES = {"mit_ecg": "ecg", "ecg": "ecg", "bus": "bus", "wisdm": "wisdm"}


@dataclass
class _HybridMatcherRuntime:
    matcher: Any
    cache_key: str
    entry_fingerprint: str
    warmup: dict[str, float]


_HYBRID_RUNTIME: dict[str, _HybridMatcherRuntime] = {}

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

Semantic grounding rules (must follow):
- Keep operator sequence fixed, but choose semantically correct field values.
- Bare entity/id mentions imply equality filters on that key:
    - Example: "record_id 106", "for record_id 106", "user 20" -> comparator must be
        "eq" with that value for the corresponding key column.
    - Do NOT weaken bare key mentions to ranges like gt/ge/lt/le.
- Explicit relational language must map to the matching comparator:
    - ">", "strictly greater", "greater than", "above" -> "gt"
    - ">=", "at least", "no less than" -> "gte"
    - "<", "strictly less", "below" -> "lt"
    - "<=", "at most", "no more than" -> "lte"
- FILTER_COMPARE.comparator accepts ONLY "eq","ne","gt","gte","lt","lte".
    Never emit "max", "min", "top", or a null value for comparator or value.
- To select the row(s) with the largest/smallest value of a derived column
    (e.g. "by the largest margin", "highest", "greatest"), use RANK_ROWS alone.
    Do NOT precede RANK_ROWS with a FILTER_COMPARE step whose purpose is
    extremum selection - that logic belongs to RANK_ROWS's direction field only.
- Any step or branch object that sets non-empty filter_values MUST also set
    filter_column to the categorical column those values belong to (grounded
    from the LIVE DATASET SCHEMA, e.g. "activity_label"). Never leave
    filter_column null when filter_values is non-empty.
- "how many", "number of samples/rows" means row counting semantics:
    - Use COUNT_ROWS / COUNT / group size when asking for sample counts.
    - Use nunique only when the question asks for unique entities.
- "highest/lowest total number of ... samples" means per-entity sample COUNT then rank.
    - Do NOT substitute sum of a sensor channel for sample counts.
- "average X" / "mean X" means mean aggregation of X.
    - Do NOT use variance unless the question explicitly asks for variance.
- Comparative roughness phrased on average variance must preserve average semantics.
    - If asked "rougher" with average/mean variance, compare mean variance values and use
        a comparison mode aligned to the question (difference/which higher), not ratio by default.
- "A exceeds/is greater than B by the largest margin" implies a SIGNED
    difference in the stated order (A - B), not abs_difference. Compute
    DERIVE_BINARY with operation="subtract" in that order, optionally gate
    with FILTER_COMPARE(comparator="gt", value=0) to enforce the stated
    direction, then RANK_ROWS on that signed column. Use abs_difference only
    when the query says "difference" or "contrast" with no stated direction.
- When comparing two or more named groups of a categorical column (e.g.
    "compare X between label A and label B"), emit one SPLIT_BY_VALUES step
    PER GROUP, each with its own distinct label and its own values list.
    Never reuse the same label across steps and never merge multiple
    comparison groups' values into a single SPLIT_BY_VALUES call.
    AGGREGATE_PARTITIONS.partitions must then list every distinct label
    produced this way (minimum two), never a single repeated label.
- Train split and holdout row are independent fields in predictive plans:
    - The query may mention both a training split (e.g., 'first 80%') and a holdout row
        position (e.g., 'first row in the holdout set'). These are independent.
        `train_fraction` controls the split; `holdout_row` must reflect only the phrase
        that describes which holdout row to predict. If the query says 'first row in the
        holdout set', `holdout_row` must be 'first'.
"""

OUT_OF_SCOPE_SYSTEM_PROMPT = """You are a dataset guardrail. The user's query
has already been classified as out-of-scope for the available dataset. Given
the query and the live dataset schema, produce a concise, factual rejection
reason explaining why the query cannot be answered from the available fields.

Use the same style as the original Flash-Fusion guardrail:
- "The dataset does not contain columns for X or Y."
- "The dataset does not contain any information about Z."
- "The dataset does not contain any column indicating W."

Return exactly one JSON object and no markdown, prose, or code fences:
{"rejection_reason": "..."}

Strict requirements:
- Return a single valid JSON object only; no trailing commas.
- The top-level keys are exactly: {"rejection_reason":"..."}.
- Do not include comments, markdown fences, or any text before/after the JSON.
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
    semantic_match_evidence: dict[str, Any] | None = None


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
    elif isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
        candidates = raw["entries"].values()
    elif isinstance(raw, dict):
        candidates = raw.values()
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise ValueError("cache registry must be a list, a key->record mapping, or {'entries': [...]}")
    return [x for x in candidates if isinstance(x, dict)]


def _entry_fingerprint(entries: Iterable[dict[str, Any]]) -> str:
    payload: list[tuple[Any, ...]] = []
    for entry in entries:
        payload.append(
            (
                canonical_dataset(entry.get("dataset")),
                str(entry.get("query_text") or "").strip(),
                entry.get("status"),
                tuple(entry.get("operator_skeleton") or ()),
                entry.get("operator_contract_hash"),
                entry.get("schema_fingerprint"),
            )
        )
    payload.sort()
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def _invalidate_hybrid_runtime(*, cache_path: str | Path, dataset: str | None) -> None:
    prefix = f"{Path(cache_path)}::{canonical_dataset(dataset)}::"
    doomed = [key for key in _HYBRID_RUNTIME if key.startswith(prefix)]
    for key in doomed:
        _HYBRID_RUNTIME.pop(key, None)


def _query_contract_to_signature(contract: Any) -> dict[str, Any]:
    predictive = {k: v for k, v in getattr(contract, "predictive", ())}
    return {
        "admissibility": getattr(contract, "admissibility", "unknown"),
        "confidence": float(getattr(contract, "confidence", 0.0) or 0.0),
        "aggregate": getattr(contract, "aggregate", None),
        "fields": sorted(list(getattr(contract, "fields", ()) or ())),
        "predicate_ops": {k: v for k, v in getattr(contract, "predicate_ops", ())},
        "filter_values": {k: v for k, v in getattr(contract, "filter_values", ())},
        "output_shape": getattr(contract, "output_shape", None),
        "predictive": {
            "model": predictive.get("model"),
            "target_column": predictive.get("target_column"),
            "holdout_row": predictive.get("holdout_row"),
            "train_fraction": predictive.get("train_fraction"),
        },
        "operator_skeleton_hint": list(getattr(contract, "operator_skeleton_hint", ()) or ()) or None,
        "applicable_evidence_count": int(getattr(contract, "applicable_evidence_count", 0) or 0),
        "matched_evidence_count": int(getattr(contract, "matched_evidence_count", 0) or 0),
    }


def _merge_semantic_overlays(
    *,
    entries: list[dict[str, Any]],
    semantic_entries: list[dict[str, Any]] | None,
    df: pd.DataFrame,
) -> list[dict[str, Any]]:
    overlay_by_key: dict[tuple[str | None, str], dict[str, Any]] = {}
    if semantic_entries:
        for item in semantic_entries:
            if item.get("status") != "reusable":
                continue
            query_text = str(item.get("query_text") or "").strip()
            if not query_text:
                continue
            key = (canonical_dataset(item.get("dataset")), query_text)
            overlay_by_key[key] = item

    out: list[dict[str, Any]] = []
    for entry in entries:
        merged = dict(entry)
        if not str(merged.get("query_text") or "").strip():
            qid = merged.get("query_id")
            if qid is not None:
                try:
                    from flashfusion.eval.trace_hybrid_cache import resolve_query

                    merged["query_text"] = resolve_query(
                        query_id=str(qid),
                        version="v1",
                        dataset=str(canonical_dataset(merged.get("dataset")) or ""),
                        override=None,
                    )
                except Exception:
                    merged["query_text"] = str(qid)
        query_text = str(merged.get("query_text") or "").strip()
        key = (canonical_dataset(merged.get("dataset")), query_text)
        overlay = overlay_by_key.get(key)
        if overlay is not None:
            if merged.get("semantic_signature") is None and isinstance(overlay.get("semantic_signature"), dict):
                merged["semantic_signature"] = overlay["semantic_signature"]
            if merged.get("retrieval_contract") is None and isinstance(overlay.get("retrieval_contract"), dict):
                merged["retrieval_contract"] = overlay["retrieval_contract"]
        if merged.get("semantic_signature") is None and query_text:
            merged["semantic_signature"] = _extract_semantic_signature(query_text, df)
        out.append(merged)
    return out


def _is_out_of_scope_entry(entry: dict[str, Any]) -> bool:
    """Return whether a reusable entry represents a guardrail rejection."""
    return entry.get("operator_skeleton") == []


def _build_hybrid_runtime(
    *,
    entries: list[dict[str, Any]],
    dataset: str | None,
    df: pd.DataFrame,
    cache_path: str | Path,
    semantic_cache_path: str | Path | None,
    out_of_scope_only: bool = False,
) -> _HybridMatcherRuntime:
    _require_sentence_transformers()
    from flashfusion.eval.trace_hybrid_cache import HybridMatcher, load_config

    usable = [
        entry
        for entry in entries
        if entry.get("status") == "reusable"
        and isinstance(entry.get("operator_skeleton"), list)
        and (not out_of_scope_only or _is_out_of_scope_entry(entry))
    ]
    semantic_entries: list[dict[str, Any]] | None = None
    if semantic_cache_path is not None and Path(semantic_cache_path).exists():
        semantic_entries = _load_entries(semantic_cache_path)

    hybrid_entries = _merge_semantic_overlays(
        entries=usable,
        semantic_entries=semantic_entries,
        df=df,
    )
    fingerprint = _entry_fingerprint(hybrid_entries)
    ds = canonical_dataset(dataset)
    schema_fp = _schema_fingerprint(df)
    cols_fp = hashlib.sha256(
        json.dumps([str(c) for c in df.columns], separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    runtime_key = (
        f"{Path(cache_path)}::{ds}::{schema_fp}::{cols_fp}::"
        f"{Path(semantic_cache_path) if semantic_cache_path else ''}::oos={out_of_scope_only}"
    )
    cached = _HYBRID_RUNTIME.get(runtime_key)
    if cached is not None and cached.entry_fingerprint == fingerprint:
        return cached

    matcher = HybridMatcher(
        entries=hybrid_entries,
        config=load_config(DEFAULT_HYBRID_CONFIG_PATH),
        dataset=dataset,
        schema_columns=[str(column) for column in df.columns],
        schema_fingerprint=schema_fp,
        device="cpu",
        no_warmup=False,
        mode="hybrid",
        dense_top_k_override=None,
        lexical_top_k_override=None,
    )
    warm = matcher.warm_up()
    runtime = _HybridMatcherRuntime(
        matcher=matcher,
        cache_key=runtime_key,
        entry_fingerprint=fingerprint,
        warmup={k: float(v) for k, v in warm.items()},
    )
    _HYBRID_RUNTIME[runtime_key] = runtime
    return runtime


def _require_sentence_transformers() -> None:
    try:
        importlib.import_module("sentence_transformers")
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Hybrid cache matching requires sentence_transformers. "
            "Install it in the active environment before running FLASH_FUSION_CACHE."
        ) from exc


def prewarm_hybrid_cache_runtime(
    *,
    df: pd.DataFrame,
    dataset: str | None,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    semantic_cache_path: str | Path | None = None,
) -> dict[str, float]:
    """Preload and warm the hybrid matcher runtime for this dataset/schema.

    This is intended for benchmark setup so dense model load and warm-up are
    completed outside per-query timing.
    """
    entries = _load_entries(cache_path)
    runtime = _build_hybrid_runtime(
        entries=entries,
        dataset=dataset,
        df=df,
        cache_path=cache_path,
        semantic_cache_path=semantic_cache_path,
        out_of_scope_only=False,
    )
    warm = dict(runtime.warmup)
    warm.setdefault("model_load_ms", 0.0)
    warm.setdefault("warm_up_ms", 0.0)
    warm.setdefault("dense_index_build_ms", 0.0)
    return {
        "model_load_ms": float(warm.get("model_load_ms", 0.0) or 0.0),
        "warm_up_ms": float(warm.get("warm_up_ms", 0.0) or 0.0),
        "dense_index_build_ms": float(warm.get("dense_index_build_ms", 0.0) or 0.0),
    }


def _hybrid_decision_to_status(decision: str) -> str:
    mapping = {
        "exact_hit": "semantic_cache_hit",
        "hybrid_hit": "semantic_cache_hit",
        "out_of_scope_hit": "admissibility_out_of_scope",
        "ambiguous_multi_candidate": "semantic_ambiguous_candidates",
        "low_confidence_candidate": "semantic_low_confidence_winner",
        "incompatible_candidate": "semantic_gate_reject_all",
        "complete_miss": "semantic_no_candidates",
    }
    return mapping.get(decision, f"semantic_{decision}")


def _hybrid_evidence(
    *,
    matcher: Any,
    query: str,
    result: Any,
    decision_status: str,
) -> dict[str, Any]:
    query_contract = _query_contract_to_signature(matcher.extractor.extract(query))
    candidate_scores = {
        c.candidate_id: {
            "score": float(c.final_score),
            "details": dict(c.component_scores),
            "dense_score": float(c.dense_score),
            "lexical_score": float(c.lexical_score),
            "contract_score": float(c.contract_score),
            "retrieval_score": float(c.retrieval_score),
        }
        for c in result.candidates
    }
    hard_gate_results = {
        c.candidate_id: {
            "ok": bool(c.compatibility),
            "gates": {},
            "reason": ",".join(c.compatibility_failures),
        }
        for c in result.candidates
    }
    elapsed_ms = {k: float(v) for k, v in (result.elapsed_ms or {}).items()}
    elapsed_ms["model_load_ms"] = 0.0
    elapsed_ms["warm_up_ms"] = 0.0
    return {
        "decision": result.decision,
        "status": decision_status,
        "extracted_signature": query_contract,
        "candidate_ids_considered": [c.candidate_id for c in result.candidates],
        "hard_gate_results": hard_gate_results,
        "candidate_scores": candidate_scores,
        "extraction_confidence": query_contract.get("confidence"),
        "abstention_reason": "" if decision_status == "semantic_cache_hit" else decision_status,
        "score_threshold": float(matcher.thresholds.get("acceptance_floor", 0.75)),
        "score_margin": float(matcher.thresholds.get("ambiguity_margin", 0.08)),
        "winner": None
        if result.winner is None
        else {
            "candidate_id": result.winner.candidate_id,
            "query_id": result.winner.query_id,
            "score": float(result.winner.final_score),
        },
        "runner_up": None
        if result.runner_up is None
        else {
            "candidate_id": result.runner_up.candidate_id,
            "query_id": result.runner_up.query_id,
            "score": float(result.runner_up.final_score),
        },
        "elapsed_ms": elapsed_ms,
    }


def _extract_cached_skeleton_from_result(result: RunResult) -> list[str]:
    steps = result.typed_plan.get("steps") if isinstance(result.typed_plan, dict) else None
    if not isinstance(steps, list):
        return []
    skeleton: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            return []
        op = step.get("op")
        if not isinstance(op, str) or not op.strip():
            return []
        skeleton.append(op)
    return skeleton


def _upsert_reusable_cache_entry(
    *,
    cache_path: str | Path,
    dataset: str | None,
    query: str,
    skeleton: list[str],
    df: pd.DataFrame,
    expected_operator_contract_hash: str | None,
    query_id: int | None,
) -> None:
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any]
    if path.exists():
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = {"entries": []}

    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        entries = [x for x in raw["entries"] if isinstance(x, dict)]
    elif isinstance(raw, dict):
        entries = [x for x in raw.values() if isinstance(x, dict)]
    elif isinstance(raw, list):
        entries = [x for x in raw if isinstance(x, dict)]
    else:
        entries = []

    wanted_ds = canonical_dataset(dataset)
    wanted_q = _normalise_query(query)
    now_sig = _extract_semantic_signature(query, df)
    base = {
        "dataset": wanted_ds,
        "query_text": query,
        "status": "reusable",
        "operator_skeleton": list(skeleton),
        "schema_fingerprint": _schema_fingerprint(df),
        "semantic_signature": now_sig,
    }
    if expected_operator_contract_hash:
        base["operator_contract_hash"] = expected_operator_contract_hash
    if query_id and query_id > 0:
        base["query_id"] = str(query_id)

    replaced = False
    for idx, entry in enumerate(entries):
        if (
            canonical_dataset(entry.get("dataset")) == wanted_ds
            and isinstance(entry.get("query_text"), str)
            and entry["query_text"].strip() == wanted_q
        ):
            merged = dict(entry)
            merged.update(base)
            entries[idx] = merged
            replaced = True
            break
    if not replaced:
        entries.append(base)

    payload = {"entries": entries}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    if isinstance(skeleton, list) and len(skeleton) == 0:
        # Out-of-scope queries are cached with an empty skeleton. We will
        # reject via a single cheap light-model call rather than the full
        # planner/guardrail pipeline.
        return matches[0], "exact_cache_hit_out_of_scope"
    if not isinstance(skeleton, list) or not skeleton or not all(isinstance(x, str) for x in skeleton):
        return None, "invalid_or_empty_operator_skeleton"
    return matches[0], "exact_cache_hit"


def _detect_aggregate(query_lc: str) -> str | None:
    if re.search(r"\b(median)\b", query_lc):
        return "median"
    if re.search(r"\b(min|minimum|smallest|lowest|least)\b", query_lc):
        return "min"
    if re.search(r"\b(max|maximum|largest|highest|greatest|peak)\b", query_lc):
        return "max"#
    if re.search(r"\b(mean|average)\b", query_lc):
        return "mean"
    if re.search(r"\b(sum|total)\b", query_lc):
        return "sum"
    if re.search(r"\b(how many|count|number of)\b", query_lc):
        return "count"
    return None


def _detect_predictive_model(query_lc: str) -> str | None:
    model_patterns = {
        "logistic_regression": (
            r"\blogistic[- ]regression\b",
            r"\blogistic[- ]classifier\b",
        ),
        "random_forest": (
            r"\brandom[- ]forest(?:[- ]classifier)?\b",
            r"\brf[- ]classifier\b",
        ),
        "one_nearest_neighbor": (
            r"\b1\s*[- ]?nearest[- ]?neighbo?r(?:[- ]classifier)?\b",
            r"\b1[- ]?nn\b",
            r"\bk\s*=\s*1\s+nearest[- ]neighbo?r\b",
        ),
        "hist_gradient_boosting": (
            r"\bhist(?:ogram)?[- ]gradient[- ]boosting(?:[- ]classifier)?\b",
            r"\bhistgradientboosting(?:classifier)?\b",
        ),
    }
    matches = {
        model
        for model, patterns in model_patterns.items()
        if any(re.search(pattern, query_lc) for pattern in patterns)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _detect_train_fraction(query_lc: str) -> float | None:
    m = re.search(
        r"\bfirst\s+(\d{1,3})%\s+of\s+(?:the\s+)?rows?"
        r"\s+(?:for|in|as)\s+train(?:ing)?\b",
        query_lc,
    )
    if not m:
        return None
    pct = int(m.group(1))
    if not 0 < pct < 100:
        return None
    return round(pct / 100.0, 4)


def _detect_holdout_position(query_lc: str) -> str | None:
    if "first row in the holdout" in query_lc:
        return "first"
    if "last row in the holdout" in query_lc:
        return "last"
    return None


def _extract_filter_values(query_lc: str, df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    key_mentions = _extract_key_mentions(query_lc)
    for key, val in key_mentions.items():
        out[key] = val

    if re.search(r"\bactivity[_ ]labels?\b", query_lc):
        activity_columns = [
            column
            for column in df.columns
            if str(column).lower() in {"activity", "activity_label"}
        ]
        activity_values = {
            str(value).strip().lower()
            for column in activity_columns
            for value in df[column].dropna().unique()
            if str(value).strip()
        }
        mentioned_values = {
            value
            for value in activity_values
            if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", query_lc)
        }
        if len(mentioned_values) == 1:
            out["activity"] = next(iter(mentioned_values))

    # Parse explicit numeric column filters like "mlii > 0".
    for m in re.finditer(r"\b([a-z_][a-z0-9_]*)\s*(>=|<=|>|<|=)\s*(-?\d+(?:\.\d+)?)", query_lc):
        field = m.group(1)
        raw = float(m.group(3))
        value: Any = int(raw) if raw.is_integer() else raw
        out[f"{field}__value"] = value
    return out


def _extract_intent_flags(query_lc: str) -> dict[str, bool]:
    return {
        "rank": bool(re.search(r"\b(highest|lowest|largest|smallest|most|least)\b", query_lc)),
        "group_by": bool(re.search(r"\b(per|each|group|grouped|by)\b", query_lc)),
        "difference": bool(re.search(r"\b(difference|gap|exceeds|absolute difference)\b", query_lc)),
        "derived_magnitude": bool(re.search(r"\b(magnitude|root mean square|rms)\b", query_lc)),
        "time_bin": bool(re.search(r"\b(minute|1-minute|window|interval|bin|bucket)\b", query_lc)),
        "correlation": bool(re.search(r"\b(correlate|correlation)\b", query_lc)),
        "last_event": bool(re.search(r"\b(last annotated|last)\b", query_lc)),
        "predictive": bool(re.search(r"\b(train|training|fit|holdout|predict|prediction|forecast|estimate|classifier|regressor|model)\b", query_lc)),
    }


def _extract_target_column(query_lc: str, df: pd.DataFrame) -> str | None:
    columns_lc = {str(column).lower(): str(column) for column in df.columns}
    m = re.search(r"\blabel\s+in\s+the\s+([a-z_][a-z0-9_]*)\s+column\b", query_lc)
    if m and m.group(1) in columns_lc:
        return columns_lc[m.group(1)]
    if re.search(r"\bactivity[_ ]label\b", query_lc) and "activity_label" in columns_lc:
        return columns_lc["activity_label"]
    if re.search(r"\bbehavio(?:u)?r\s+label\b", query_lc) and "behavior" in columns_lc:
        return columns_lc["behavior"]
    if re.search(r"\bannotation\s+is\s+present\b", query_lc) and "annotation_present" in columns_lc:
        return columns_lc["annotation_present"]
    return None


def _extract_operator_skeleton_hint(query_lc: str) -> list[str] | None:
    # Conservative hints only: emit only when intent is explicit and unambiguous.
    if re.search(r"\b(train|training|fit|holdout|predict|prediction|forecast|estimate|classifier|regressor|model)\b", query_lc):
        return ["PREDICTIVE_PIPELINE"]
    has_count = bool(re.search(r"\b(how many|count|number of)\b", query_lc))
    has_numeric_cmp = bool(re.search(r"(>=|<=|>|<)", query_lc))
    if has_count and has_numeric_cmp:
        return ["FILTER_COMPARE", "COUNT_ROWS"]
    return None


def _extract_semantic_signature(query: str, df: pd.DataFrame) -> dict[str, Any]:
    """Extract a conservative schema-aware semantic signature for pre-grounding gates.

    This is intentionally minimal and abstain-first. It only emits fields when
    they are directly supported by query text and live schema headers.
    """
    query_lc = query.lower()
    out_of_scope_markers = (
        "geographic location",
        "location where",
        "family history",
        "who recommended",
        "weekly moderate-to-vigorous",
        "weather",
        "occupancy",
        "operating schedule",
    )
    if any(marker in query_lc for marker in out_of_scope_markers):
        return {
            "admissibility": "out_of_scope",
            "confidence": 0.95,
            "aggregate": None,
            "fields": [],
            "predicate_ops": {},
            "filter_values": {},
            "intent_flags": _extract_intent_flags(query_lc),
            "predictive": {
                "model": _detect_predictive_model(query_lc),
                "target_column": _extract_target_column(query_lc, df),
                "train_fraction": _detect_train_fraction(query_lc),
                "holdout_row": _detect_holdout_position(query_lc),
            },
            "operator_skeleton_hint": _extract_operator_skeleton_hint(query_lc),
            "output_shape": "unknown",
        }

    columns = [str(c) for c in df.columns]
    columns_lc = [c.lower() for c in columns]
    fields = [c for c in columns if re.search(rf"\b{re.escape(c.lower())}\b", query_lc)]
    predicate_ops: dict[str, str] = {}
    for m in re.finditer(r"\b([a-z_][a-z0-9_]*)\s*(>=|<=|>|<|=)\s*(-?\d+(?:\.\d+)?)", query_lc):
        col = m.group(1)
        if col not in columns_lc:
            continue
        op = m.group(2)
        predicate_ops[col] = {
            ">": "gt",
            ">=": "gte",
            "<": "lt",
            "<=": "lte",
            "=": "eq",
        }[op]

    for key_col in ("record_id", "user_id", "subject_id"):
        if key_col in columns_lc and re.search(rf"\b{key_col}\s*(?:=|is)?\s*\d+\b", query_lc):
            predicate_ops.setdefault(key_col, "eq")

    aggregate = _detect_aggregate(query_lc)
    filter_values = _extract_filter_values(query_lc, df)
    intent_flags = _extract_intent_flags(query_lc)
    predictive = {
        "model": _detect_predictive_model(query_lc),
        "target_column": _extract_target_column(query_lc, df),
        "train_fraction": _detect_train_fraction(query_lc),
        "holdout_row": _detect_holdout_position(query_lc),
    }
    operator_skeleton_hint = _extract_operator_skeleton_hint(query_lc)

    output_shape = "unknown"
    if predictive["model"] is not None:
        output_shape = "predictive"
    elif re.search(r"\blist\b", query_lc):
        output_shape = "list"
    elif aggregate is not None or re.search(r"\b(how many|count|number of)\b", query_lc):
        output_shape = "scalar"

    admissibility = "unknown"
    if intent_flags["predictive"] or aggregate or fields or predicate_ops or filter_values:
        admissibility = "in_scope"
    if re.search(
        r"\b(?:predict|forecast)\s+next\s+week(?:'s)?\b|\b(?:fatal cardiac event|bmi|cadence)\b",
        query_lc,
    ):
        admissibility = "out_of_scope"

    confidence = 0.95 if admissibility == "out_of_scope" else 0.85 if admissibility == "in_scope" else 0.4
    return {
        "admissibility": admissibility,
        "confidence": confidence,
        "aggregate": aggregate,
        "fields": sorted(set(fields)),
        "predicate_ops": predicate_ops,
        "filter_values": filter_values,
        "intent_flags": intent_flags,
        "predictive": predictive,
        "operator_skeleton_hint": operator_skeleton_hint,
        "output_shape": output_shape,
    }


def extract_semantic_signature(query: str, df: pd.DataFrame) -> dict[str, Any]:
    """Public wrapper used by offline tooling to mirror runtime extraction."""
    return _extract_semantic_signature(query, df)


def _candidate_id(entry: dict[str, Any], index: int) -> str:
    raw = entry.get("sig_id") or entry.get("template_id") or entry.get("signature")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"candidate_{index}"


def _find_semantic_entry(
    entries: Iterable[dict[str, Any]],
    query: str,
    dataset: str | None,
    df: pd.DataFrame,
    expected_operator_contract_hash: str | None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    _require_sentence_transformers()
    live_signature = _extract_semantic_signature(query, df)
    if live_signature.get("admissibility") == "out_of_scope":
        evidence = {
            "decision": "out_of_scope_hit",
            "status": "admissibility_out_of_scope",
            "extracted_signature": live_signature,
            "candidate_ids_considered": [],
            "hard_gate_results": {},
            "candidate_scores": {},
            "extraction_confidence": float(live_signature.get("confidence", 0.0) or 0.0),
            "abstention_reason": "admissibility_out_of_scope",
            "score_threshold": 0.75,
            "score_margin": 0.08,
            "winner": None,
            "runner_up": None,
            "elapsed_ms": {"model_load_ms": 0.0, "warm_up_ms": 0.0},
        }
        return None, "admissibility_out_of_scope", evidence

    candidates = [x for x in entries if isinstance(x, dict)]
    overlayed = _merge_semantic_overlays(entries=candidates, semantic_entries=None, df=df)
    if not overlayed:
        evidence = {
            "decision": "complete_miss",
            "status": "semantic_no_candidates",
            "extracted_signature": live_signature,
            "candidate_ids_considered": [],
            "hard_gate_results": {},
            "candidate_scores": {},
            "extraction_confidence": 0.0,
            "abstention_reason": "semantic_no_candidates",
            "score_threshold": 0.75,
            "score_margin": 0.08,
            "winner": None,
            "runner_up": None,
            "elapsed_ms": {"model_load_ms": 0.0, "warm_up_ms": 0.0},
        }
        return None, "semantic_no_candidates", evidence

    from flashfusion.eval.trace_hybrid_cache import HybridMatcher, load_config

    matcher = HybridMatcher(
        entries=overlayed,
        config=load_config(DEFAULT_HYBRID_CONFIG_PATH),
        dataset=dataset,
        schema_columns=[str(column) for column in df.columns],
        schema_fingerprint=_schema_fingerprint(df),
        device="cpu",
        no_warmup=False,
        mode="hybrid",
        dense_top_k_override=None,
        lexical_top_k_override=None,
    )
    matcher.warm_up()
    result = matcher.match(query, expected_contract_hash=expected_operator_contract_hash)
    status = _hybrid_decision_to_status(result.decision)
    evidence = _hybrid_evidence(matcher=matcher, query=query, result=result, decision_status=status)
    evidence["extracted_signature"] = _query_contract_to_signature(matcher.extractor.extract(query))

    if result.entry is None:
        return None, status, evidence
    return result.entry, "semantic_cache_hit", evidence


def _execute_grounded_cache_entry(
    *,
    query: str,
    df: pd.DataFrame,
    client: LLMClient,
    result: RunResult,
    trace: CacheGroundingTrace | None,
    entry: dict[str, Any],
    stage_hit: str,
    plan_source: str,
    stage_latency: dict[str, float],
    started: float,
) -> RunResult:
    _record(trace, operator_skeleton=list(entry["operator_skeleton"]))
    _append_stage(result, stage_hit)
    _append_stage(result, "cache_light_grounding")
    grounding_started = time.perf_counter()
    try:
        prompt = _grounding_prompt(query, entry, df)
        _record(trace, prompt=prompt)
        raw_plan = _invoke_light_for_plan(client, prompt, trace)
    finally:
        stage_latency["cache_grounding"] += time.perf_counter() - grounding_started
    validation_started = time.perf_counter()
    try:
        raw_plan = _apply_grounding_semantic_guards(raw_plan, query)
        plan = _parse_and_validate_cached_plan(raw_plan, entry["operator_skeleton"], df)
    finally:
        stage_latency["cache_validation"] += time.perf_counter() - validation_started
    _append_stage(result, "cache_plan_validated")
    _record(trace, validated_plan=plan.model_dump(mode="json"))

    execution_started = time.perf_counter()
    execution = execute_plan(df, plan)
    stage_latency["typed_exec"] += time.perf_counter() - execution_started
    if not execution.ok:
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
    _set_if_present(result, "plan_source", plan_source)
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
            "code": execution.code,
        },
    )
    _set_if_present(
        result,
        "trace",
        "Cache hit: light model grounded cached skeleton; validated typed execution.\n"
        + (execution.trace or ""),
    )
    _set_if_present(result, "final_code", execution.code)
    _set_if_present(result, "latency_s", time.perf_counter() - started)
    return result


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


def _rejection_reason_prompt(query: str, df: pd.DataFrame) -> str:
    return "\n".join(
        [
            f"QUESTION: {query}",
            "LIVE DATASET SCHEMA:",
            _schema_context(df),
        ]
    )


def _invoke_light_for_rejection_reason(
    client: LLMClient, query: str, df: pd.DataFrame, trace: CacheGroundingTrace | None = None
) -> str:
    """Ask the light model to infer a guardrail-style rejection reason."""
    light = getattr(client, "light", None) or client
    if getattr(light, "llm", None) is None or not hasattr(light, "invoke_messages"):
        raise RuntimeError("cache rejection reasoning requires client.light.llm")
    messages = [
        SystemMessage(content=OUT_OF_SCOPE_SYSTEM_PROMPT),
        HumanMessage(content=_rejection_reason_prompt(query, df)),
    ]
    started = time.perf_counter()
    raw = light.invoke_messages(messages, stage="cache_rejection_reason")
    _record(
        trace,
        raw_light_output=raw,
        grounding_latency_s=time.perf_counter() - started,
    )
    repaired = _repair_light_json(raw)
    parsed = json.loads(repaired)
    if not isinstance(parsed, dict):
        raise ValueError("light model rejection output must be a JSON object")
    reason = parsed.get("rejection_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("light model did not return a non-empty rejection_reason")
    return reason.strip()


_RELATIONAL_TERMS = {
    "gt": ("strictly greater", "greater than", "above", "over"),
    "gte": ("at least", "no less than", "greater than or equal"),
    "lt": ("strictly less", "less than", "below", "under"),
    "lte": ("at most", "no more than", "less than or equal"),
}


def _query_mentions_relational_for_column(query_lc: str, column: str) -> bool:
    escaped = re.escape(column.lower())
    symbolic = rf"\b{escaped}\b\s*(>=|<=|>|<)"
    if re.search(symbolic, query_lc):
        return True
    for terms in _RELATIONAL_TERMS.values():
        for term in terms:
            if re.search(rf"\b{escaped}\b[^.\n]{{0,48}}\b{re.escape(term)}\b", query_lc):
                return True
            if re.search(rf"\b{re.escape(term)}\b[^.\n]{{0,48}}\b{escaped}\b", query_lc):
                return True
    return False


def _extract_key_mentions(query_lc: str) -> dict[str, int]:
    mentions: dict[str, int] = {}
    patterns = {
        "record_id": [
            r"\bfor\s+record_id\s*(?:=|is)?\s*(\d+)\b",
            r"\brecord_id\s*(?:=|is)?\s*(\d+)\b",
        ],
        "subject_id": [
            r"\bfor\s+subject_id\s*(?:=|is)?\s*(\d+)\b",
            r"\bsubject_id\s*(?:=|is)?\s*(\d+)\b",
            r"\buser\s+(\d+)\b",
        ],
    }
    for column, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, query_lc)
            if m:
                mentions[column] = int(m.group(1))
                break
    return mentions


def _query_requests_ratio(query_lc: str) -> bool:
    return bool(
        re.search(r"\b(ratio|times|x\s+as\s+much|percent|percentage|per\s+cent)\b", query_lc)
    )


def _apply_grounding_semantic_guards(raw_plan: dict[str, Any], query: str) -> dict[str, Any]:
    """Apply conservative post-grounding fixes for high-confidence intent cues.

    These guards do not add/remove/reorder operators. They only patch field values
    in-place when the query intent is explicit and a mismatch is a known failure mode.
    """
    if not isinstance(raw_plan, dict):
        return raw_plan
    steps = raw_plan.get("steps")
    if not isinstance(steps, list):
        return raw_plan

    query_lc = query.lower()
    key_mentions = _extract_key_mentions(query_lc)
    wants_count_extrema = bool(
        re.search(r"\b(highest|lowest)\b", query_lc)
        and re.search(r"\b(number|count)\b", query_lc)
        and re.search(r"\b(sample|samples|row|rows)\b", query_lc)
    )
    wants_average = bool(re.search(r"\b(mean|average)\b", query_lc))
    wants_rough_compare = bool(re.search(r"\brougher\b", query_lc))
    wants_first_holdout = "first row in the holdout" in query_lc
    wants_last_holdout = "last row in the holdout" in query_lc
    asks_difference = bool(re.search(r"\b(difference|gap)\b", query_lc))

    for step in steps:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op", ""))

        if op == "FILTER_COMPARE":
            column = str(step.get("column", ""))
            mention_value = key_mentions.get(column)
            if mention_value is not None and not _query_mentions_relational_for_column(query_lc, column):
                step["comparator"] = "eq"
                step["value"] = mention_value

        elif op == "GROUP_AGGREGATE":
            if isinstance(step.get("aggregate"), dict):
                agg_dict = step["aggregate"]
                step["aggregate"] = agg_dict.get("op") or agg_dict.get("aggregate")
                if not step.get("column") and "column" in agg_dict:
                    step["column"] = agg_dict["column"]
            if wants_count_extrema:
                step["aggregate"] = "count"
                step["column"] = None

        elif op == "AGGREGATE_PARTITIONS" and wants_average and str(step.get("aggregate")) == "var":
            step["aggregate"] = "mean"

        elif op == "COMPARE_PARTITIONS":
            mode = str(step.get("mode", ""))
            if mode == "ratio" and (wants_rough_compare or wants_average) and not _query_requests_ratio(query_lc):
                step["mode"] = "difference" if asks_difference else "difference"

        elif op == "PREDICTIVE_PIPELINE":
            if wants_first_holdout:
                step["holdout_row"] = "first"
            elif wants_last_holdout:
                step["holdout_row"] = "last"

    return raw_plan


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
    semantic_cache_path: str | Path | None = None,
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
    stage_latency = (
        dict(result.stage_latency_s)
        if isinstance(result.stage_latency_s, dict)
        else {}
    )
    stage_latency.setdefault("cache_grounding", 0.0)
    stage_latency.setdefault("cache_lookup", 0.0)
    stage_latency.setdefault("cache_validation", 0.0)
    stage_latency.setdefault("typed_exec", 0.0)
    stage_latency.setdefault("cache_rejection", 0.0)
    result.stage_latency_s = stage_latency
    started = time.perf_counter()
    _record(
        trace,
        cache_path=str(cache_path),
        requested_dataset=canonical_dataset(dataset),
    )

    try:
        lookup_started = time.perf_counter()
        try:
            entries = _load_entries(cache_path)
            entry, lookup_status = _find_exact_entry(entries, query, dataset)
            _record(trace, lookup_status=lookup_status, entry=entry)

            if entry is None:
                semantic_signature = _extract_semantic_signature(query, df)
                admissibility_hint = (
                    "out_of_scope"
                    if semantic_signature.get("admissibility") == "out_of_scope"
                    and float(semantic_signature.get("confidence", 0.0) or 0.0) >= 0.9
                    else None
                )
                runtime = _build_hybrid_runtime(
                    entries=entries,
                    dataset=dataset,
                    df=df,
                    cache_path=cache_path,
                    semantic_cache_path=semantic_cache_path,
                    out_of_scope_only=admissibility_hint == "out_of_scope",
                )
                hybrid_result = runtime.matcher.match(
                    query,
                    expected_contract_hash=expected_operator_contract_hash,
                    admissibility_hint=admissibility_hint,
                )
                hybrid_status = _hybrid_decision_to_status(hybrid_result.decision)
                hybrid_evidence = _hybrid_evidence(
                    matcher=runtime.matcher,
                    query=query,
                    result=hybrid_result,
                    decision_status=hybrid_status,
                )
                elapsed_ms = hybrid_result.elapsed_ms if isinstance(hybrid_result.elapsed_ms, dict) else {}
                stage_latency["cache_validation"] += float(elapsed_ms.get("verify_ms", 0.0) or 0.0) / 1000.0
                _record(trace, semantic_match_evidence=hybrid_evidence)

                if hybrid_result.entry is not None and hybrid_status == "semantic_cache_hit":
                    entry = hybrid_result.entry
                    lookup_status = "semantic_cache_hit"
                    _record(trace, lookup_status=lookup_status, entry=entry)
                elif hybrid_result.entry is not None and hybrid_status == "admissibility_out_of_scope":
                    entry = hybrid_result.entry
                    lookup_status = "semantic_cache_hit_out_of_scope"
                    _record(trace, lookup_status=lookup_status, entry=entry)
                else:
                    _append_stage(result, f"hybrid_{hybrid_status}")
                    raise LookupError(f"semantic: {hybrid_status}")

            if entry is None:
                raise LookupError(lookup_status)
        finally:
            stage_latency["cache_lookup"] += time.perf_counter() - lookup_started

        # Cheap out-of-scope short-circuit: the registry records an empty
        # skeleton, so we ask the light model for the guardrail reason instead
        # of invoking the full planner/guardrail pipeline.
        if lookup_status in {"exact_cache_hit_out_of_scope", "semantic_cache_hit_out_of_scope"}:
            _append_stage(result, lookup_status)
            _append_stage(result, "cache_light_rejection_reason")
            grounding_started = time.perf_counter()
            try:
                reason = _invoke_light_for_rejection_reason(client, query, df, trace)
            finally:
                stage_latency["cache_rejection"] += (
                    time.perf_counter() - grounding_started
                )
            _append_stage(result, "cache_rejection_reason_ready")
            _set_if_present(result, "rejected", True)
            _set_if_present(result, "executed", False)
            _set_if_present(result, "execution_path", "guardrail_reject")
            _set_if_present(
                result,
                "plan_source",
                "exact_query_cache_out_of_scope"
                if lookup_status == "exact_cache_hit_out_of_scope"
                else "semantic_query_cache_out_of_scope",
            )
            _set_if_present(result, "answer", f"Query rejected. Reason: {reason}")
            _set_if_present(
                result,
                "trace",
                "Rejected by the guardrail because the query cannot be answered "
                f"from available dataset fields. Reason: {reason}",
            )
            _set_if_present(result, "latency_s", time.perf_counter() - started)
            return result

        if lookup_status == "exact_cache_hit":
            validation_started = time.perf_counter()
            try:
                cached_contract_hash = entry.get("operator_contract_hash")
                if expected_operator_contract_hash and cached_contract_hash != expected_operator_contract_hash:
                    raise LookupError("operator_contract_hash_mismatch")
                cached_schema_fingerprint = entry.get("schema_fingerprint")
                if cached_schema_fingerprint and cached_schema_fingerprint != _schema_fingerprint(df):
                    raise LookupError("schema_fingerprint_mismatch")
            finally:
                stage_latency["cache_validation"] += time.perf_counter() - validation_started
            return _execute_grounded_cache_entry(
                query=query,
                df=df,
                client=client,
                result=result,
                trace=trace,
                entry=entry,
                stage_hit="exact_cache_hit",
                plan_source="exact_query_cache_light_grounded",
                stage_latency=stage_latency,
                started=started,
            )

        if lookup_status == "semantic_cache_hit":
            return _execute_grounded_cache_entry(
                query=query,
                df=df,
                client=client,
                result=result,
                trace=trace,
                entry=entry,
                stage_hit="hybrid_cache_hit",
                plan_source="semantic_cache_light_grounded",
                stage_latency=stage_latency,
                started=started,
            )

        raise LookupError(f"unsupported_lookup_status:{lookup_status}")

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
        fallback_result = _run_normal_flash_fusion(query, df, client, result, **flash_fusion_kwargs)
        skeleton = _extract_cached_skeleton_from_result(fallback_result)
        if (
            fallback_result.executed
            and not fallback_result.rejected
            and fallback_result.execution_path == "typed_operator"
            and skeleton
        ):
            try:
                _upsert_reusable_cache_entry(
                    cache_path=cache_path,
                    dataset=dataset,
                    query=query,
                    skeleton=skeleton,
                    df=df,
                    expected_operator_contract_hash=expected_operator_contract_hash,
                    query_id=getattr(fallback_result, "query_id", 0),
                )
                _invalidate_hybrid_runtime(cache_path=cache_path, dataset=dataset)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # Cache write failures are non-fatal: the baseline answer remains valid.
                pass
        return fallback_result
