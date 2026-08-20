"""
I) Sketch the hybrid matching design:
- Exact string match [first try this]
* [then try a combination of the following...]
- Fuzzy string match
- Keyword/semantic signature gates
- Embedding similarity (all-MiniLM-L6-v2)
^ combined via a weighted average

II) Add an explicit miss categorization to the logging:
- Ambiguous multi-candidates (several near-tied candidates)
- Complete miss (nothing cleared any gate)
^ this distinction should surface in every run's output

III) There should be a --fuzzy argument where:
- Only fuzzy matching is used, with no other gating
- Concretely demonstrate its false positive rate

Goal: to modify this script in a similar fashion as trace_query.py, where
I can take a reworded query, compare it against the original one, and
see what the similarities across key dimensions look like.
We already have the query id's so it's as simple as passing in the id and version,
with the ground truth already ready, and checking to see if it was able to isolate the
correct query.
Stretch: we can vary the query wordings of v2 and v3 even more once the hit framework is
stronger and general.
"""

# (i) embedding example script

import time
import torch
from sentence_transformers import SentenceTransformer
import re
from typing import Any, Iterable

MODEL_NAME = "all-MiniLM-L6-v2"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

"""
FYI here's the terminal output:
Loading weights: 100%; 103/103 [00:00<00:00, 9374.88it/s]
Loaded all-MiniLM-L6-v2 on cpu in 1.658s
Warm-up completed in 0.099s
Embedded 2 sentence(s) in 0.005s (0.0023s per sentence)
Embedding shape: (2, 384)
"""

# Load once at application startup.
t0 = time.perf_counter()
model = SentenceTransformer(MODEL_NAME, device=DEVICE)
load_s = time.perf_counter() - t0
print(f"Loaded {MODEL_NAME} on {DEVICE} in {load_s:.3f}s")

def _sync_device():
    """Ensure CUDA work completes before recording elapsed time."""
    if DEVICE.startswith("cuda"):
        torch.cuda.synchronize()

def warm_up():
    """Run once after loading to initialize runtime and GPU kernels."""
    _sync_device()
    t0 = time.perf_counter()

    _ = model.encode(
        ["warm-up request"],
        batch_size=1,
        show_progress_bar=False,
    )

    _sync_device()
    print(f"Warm-up completed in {time.perf_counter() - t0:.3f}s")

def embed_with_timer(sentences: list[str]):
    """Embed a query/batch and return embeddings plus latency metadata."""
    _sync_device()
    t0 = time.perf_counter()

    embeddings = model.encode(
        sentences,
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
    )

    _sync_device()
    elapsed_s = time.perf_counter() - t0

    print(
        f"Embedded {len(sentences)} sentence(s) in {elapsed_s:.3f}s "
        f"({elapsed_s / len(sentences):.4f}s per sentence)"
    )
    return embeddings, elapsed_s


# Do this once at startup—not per request.
warm_up()

sentences = [
    "This is an example sentence",
    "Each sentence is converted",
]

embeddings, latency_s = embed_with_timer(sentences)
print("Embedding shape:", embeddings.shape)

# (ii) what the first-pass at keyword and semantic matching looks like
# top issue is that it feels quite hard-coded at this stage
## indexing patterns should be "as general as possible," testing across all datasets,
## and avoiding overfit thresholds means the cache design needs to be workload-aware,
## not hand-tuned to any one particular dataset.

"""
# --------------------------------------------------
  Some examples to reuse from flash_fusion_cache.py
# --------------------------------------------------

details on fuzzy matching can be found here:
https://cloud.google.com/discover/what-is-fuzzy-search
"""

#: Registry dataset keys. Mirrors
#: ``build_operator_skeleton_cache.canonical_dataset_dir_name`` so a benchmark
#: ``--dataset mit_ecg`` run matches the ``ecg`` entries the builder wrote.
_DATASET_ALIASES = {"mit_ecg": "ecg", "ecg": "ecg", "bus": "bus", "wisdm": "wisdm"}


def canonical_dataset(name: str | None) -> str | None:
    """Map a benchmark dataset key onto the registry's dataset key."""
    if name is None:
        return None
    key = str(name).strip().lower()
    return _DATASET_ALIASES.get(key, key)


def _normalise_query(query: str) -> str:
    # Preserve exact-match semantics.  Surrounding whitespace is transport
    # noise; all internal whitespace, punctuation, and case remain meaningful.
    return query.strip()

# find the exact query match
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

# detect specific keywords, two examples:
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

# some info on how semantic candidates and weighted averaging is currently structured
def _semantic_entry_matches(
    extracted: dict[str, Any],
    entry: dict[str, Any],
    expected_operator_contract_hash: str | None,
    live_schema_fingerprint: str,
) -> tuple[bool, dict[str, Any], str | None]:
    """Run hard compatibility gates before invoking the light model."""
    gates: dict[str, Any] = {}

    skeleton = entry.get("operator_skeleton")
    gates["has_operator_skeleton"] = isinstance(skeleton, list) and bool(skeleton)
    if not gates["has_operator_skeleton"]:
        return False, gates, "invalid_or_empty_operator_skeleton"

    if expected_operator_contract_hash:
        gates["operator_contract_hash"] = entry.get("operator_contract_hash") == expected_operator_contract_hash
        if not gates["operator_contract_hash"]:
            return False, gates, "operator_contract_hash_mismatch"

    cached_schema_fingerprint = entry.get("schema_fingerprint")
    gates["schema_fingerprint"] = (
        True
        if not cached_schema_fingerprint
        else cached_schema_fingerprint == live_schema_fingerprint
    )
    if not gates["schema_fingerprint"]:
        return False, gates, "schema_fingerprint_mismatch"

    sig = entry.get("semantic_signature") or {}
    if not isinstance(sig, dict):
        return False, gates, "missing_semantic_signature"

    skeleton_hint = extracted.get("operator_skeleton_hint")
    if isinstance(skeleton_hint, list) and skeleton_hint:
        gates["operator_skeleton"] = skeleton == skeleton_hint
        if not gates["operator_skeleton"]:
            return False, gates, (
                f"operator_skeleton_mismatch:template={skeleton},live_hint={skeleton_hint}"
            )

    cand_agg = sig.get("aggregate")
    if cand_agg is not None:
        gates["aggregate"] = extracted.get("aggregate") == cand_agg
        if not gates["aggregate"]:
            return False, gates, f"aggregate_mismatch: template={cand_agg}, live={extracted.get('aggregate')}"

    cand_output = sig.get("output_shape")
    if cand_output is not None:
        gates["output_shape"] = extracted.get("output_shape") == cand_output
        if not gates["output_shape"]:
            return False, gates, f"output_shape_mismatch: template={cand_output}, live={extracted.get('output_shape')}"

    cand_fields = sig.get("fields")
    if isinstance(cand_fields, list) and cand_fields:
        extracted_fields = {str(x).lower() for x in extracted.get("fields") or []}
        template_fields = {str(x).lower() for x in cand_fields}
        gates["fields"] = template_fields.issubset(extracted_fields)
        if not gates["fields"]:
            return False, gates, "field_mismatch"

    cand_predicate_ops = sig.get("predicate_ops")
    if isinstance(cand_predicate_ops, dict) and cand_predicate_ops:
        live_ops = {str(k).lower(): str(v) for k, v in (extracted.get("predicate_ops") or {}).items()}
        for field_name, expected_op in cand_predicate_ops.items():
            key = str(field_name).lower()
            gates[f"predicate_op:{key}"] = live_ops.get(key) == expected_op
            if not gates[f"predicate_op:{key}"]:
                return False, gates, (
                    f"predicate_op_mismatch:{key}:template={expected_op},live={live_ops.get(key)}"
                )

    cand_filter_values = sig.get("filter_values")
    if isinstance(cand_filter_values, dict) and cand_filter_values:
        live_values = extracted.get("filter_values") or {}
        if not isinstance(live_values, dict):
            live_values = {}
        for key, expected_value in cand_filter_values.items():
            gates[f"filter_value:{key}"] = live_values.get(key) == expected_value
            if not gates[f"filter_value:{key}"]:
                return False, gates, (
                    f"filter_value_mismatch:{key}:template={expected_value},live={live_values.get(key)}"
                )

    cand_predictive = sig.get("predictive")
    if isinstance(cand_predictive, dict):
        live_predictive = extracted.get("predictive") or {}
        if not isinstance(live_predictive, dict):
            live_predictive = {}
        for key in ("model", "target_column", "holdout_row", "train_fraction"):
            expected_val = cand_predictive.get(key)
            if expected_val is None:
                continue
            gates[f"predictive:{key}"] = live_predictive.get(key) == expected_val
            if not gates[f"predictive:{key}"]:
                return False, gates, (
                    f"predictive_mismatch:{key}:template={expected_val},live={live_predictive.get(key)}"
                )

    return True, gates, None


def _jaccard_score(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _score_semantic_candidate(extracted: dict[str, Any], sig: dict[str, Any]) -> tuple[float, dict[str, float]]:
    details: dict[str, float] = {}
    score = 0.0

    agg_match = 1.0 if extracted.get("aggregate") == sig.get("aggregate") else 0.0
    details["aggregate"] = agg_match
    score += 0.15 * agg_match

    fields_score = _jaccard_score(
        {str(x).lower() for x in extracted.get("fields") or []},
        {str(x).lower() for x in sig.get("fields") or []},
    )
    details["fields"] = fields_score
    score += 0.10 * fields_score

    pred_ops_left = {f"{k}:{v}" for k, v in (extracted.get("predicate_ops") or {}).items()}
    pred_ops_right = {f"{k}:{v}" for k, v in (sig.get("predicate_ops") or {}).items()}
    pred_ops_score = _jaccard_score(pred_ops_left, pred_ops_right)
    details["predicate_ops"] = pred_ops_score
    score += 0.10 * pred_ops_score

    filter_vals_left = {f"{k}:{v}" for k, v in (extracted.get("filter_values") or {}).items()}
    filter_vals_right = {f"{k}:{v}" for k, v in (sig.get("filter_values") or {}).items()}
    filter_vals_score = _jaccard_score(filter_vals_left, filter_vals_right)
    details["filter_values"] = filter_vals_score
    score += 0.15 * filter_vals_score

    out_match = 1.0 if extracted.get("output_shape") == sig.get("output_shape") else 0.0
    details["output_shape"] = out_match
    score += 0.05 * out_match

    intent_l = extracted.get("intent_flags") or {}
    intent_r = sig.get("intent_flags") or {}
    shared = [k for k in set(intent_l) | set(intent_r)]
    if shared:
        intent_score = sum(1.0 for k in shared if bool(intent_l.get(k)) == bool(intent_r.get(k))) / len(shared)
    else:
        intent_score = 1.0
    details["intent_flags"] = intent_score
    score += 0.10 * intent_score

    pred_l = extracted.get("predictive") or {}
    pred_r = sig.get("predictive") or {}
    model_match = 1.0 if pred_l.get("model") == pred_r.get("model") else 0.0
    details["predictive_model"] = model_match
    score += 0.25 * model_match
    target_match = 1.0 if pred_l.get("target_column") == pred_r.get("target_column") else 0.0
    details["predictive_target"] = target_match
    score += 0.10 * target_match

    return score, details

# if multiple cache candidates bubble up, the following hack is used.
def _find_semantic_entry(
    entries: Iterable[dict[str, Any]],
    query: str,
    dataset: str | None,
    df: pd.DataFrame,
    expected_operator_contract_hash: str | None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    wanted = canonical_dataset(dataset)
    extracted = _extract_semantic_signature(query, df)
    evidence: dict[str, Any] = {
        "extracted_signature": extracted,
        "candidate_ids_considered": [],
        "hard_gate_results": {},
        "candidate_scores": {},
        "extraction_confidence": extracted.get("confidence"),
        "abstention_reason": "",
        "score_threshold": 0.85,
        "score_margin": 0.20,
        "winner": None,
        "runner_up": None,
    }

    if extracted.get("admissibility") in {"out_of_scope", "ambiguous", "unknown"}:
        reason = f"admissibility_{extracted.get('admissibility')}"
        evidence["abstention_reason"] = reason
        return None, reason, evidence

    live_schema_fp = _schema_fingerprint(df)
    candidates = [
        entry
        for entry in entries
        if entry.get("status") == "reusable"
        and (wanted is None or canonical_dataset(entry.get("dataset")) == wanted)
        and isinstance(entry.get("operator_skeleton"), list)
        and entry.get("semantic_signature") is not None
    ]
    if not candidates:
        evidence["abstention_reason"] = "semantic_no_candidates"
        return None, "semantic_no_candidates", evidence

    passing: list[tuple[str, dict[str, Any], float]] = []
    for index, entry in enumerate(candidates, start=1):
        cid = _candidate_id(entry, index)
        evidence["candidate_ids_considered"].append(cid)
        ok, gates, reason = _semantic_entry_matches(
            extracted,
            entry,
            expected_operator_contract_hash,
            live_schema_fp,
        )
        evidence["hard_gate_results"][cid] = {"ok": ok, "gates": gates, "reason": reason}
        if ok:
            score, details = _score_semantic_candidate(extracted, entry.get("semantic_signature") or {})
            evidence["candidate_scores"][cid] = {
                "score": round(score, 4),
                "details": details,
            }
            passing.append((cid, entry, score))

    if not passing:
        evidence["abstention_reason"] = "semantic_gate_reject_all"
        return None, "semantic_gate_reject_all", evidence

    passing.sort(key=lambda item: item[2], reverse=True)
    winner_id, winner_entry, winner_score = passing[0]
    runner_up_score = passing[1][2] if len(passing) > 1 else None
    evidence["winner"] = {"candidate_id": winner_id, "score": round(winner_score, 4)}
    if len(passing) > 1:
        evidence["runner_up"] = {
            "candidate_id": passing[1][0],
            "score": round(passing[1][2], 4),
        }

    threshold = float(evidence["score_threshold"])
    if winner_score < threshold:
        evidence["abstention_reason"] = "semantic_low_confidence_winner"
        return None, "semantic_low_confidence_winner", evidence

    # WISDM reasoning queries often produce multiple close semantic candidates
    # that differ only by wording. The cache should prefer the best-scoring
    # candidate as long as it clears the confidence threshold instead of
    # rejecting the whole lookup as "ambiguous".
    evidence["abstention_reason"] = ""
    return winner_entry, "semantic_cache_hit", evidence