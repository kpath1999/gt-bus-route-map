# Plan: Build `benchmark_hybrid_cache.py`

## Objective

Create `flashfusion/eval/benchmark_hybrid_cache.py`, an end-to-end benchmark for the verified hybrid cache matcher. The benchmark must measure retrieval quality, compatibility filtering, authorization quality, false-positive reuse, abstention, and ambiguity across every registered query ID, all query wording versions (`v1`, `v2`, `v3`), and all supported datasets:

- `bus`
- `wisdm`
- `mit_ecg`

This is intentionally different from `audit_hybrid_contracts.py`.

- `audit_hybrid_contracts.py` tests contract extraction and compatibility pairwise.
- `benchmark_hybrid_cache.py` must run the actual `HybridMatcher` in `hybrid` mode, including lexical retrieval, dense embedding retrieval, candidate union, compatibility verification, final scoring, thresholding, and ambiguity handling.

The goal is to answer: **does the system retrieve and safely authorize the intended cache entry for a reworded query?**

## Existing implementation to use

Use the current public interfaces and implementation patterns in:

- `flashfusion/eval/trace_hybrid_cache.py`
- `flashfusion/eval/audit_hybrid_contracts.py`
- `flashfusion/eval/queries.py`
- `flashfusion/eval/queries_v2.py`
- `flashfusion/eval/queries_v3.py`

Reuse, rather than duplicate, these functions/classes where possible:

```python
from flashfusion.eval.trace_hybrid_cache import (
    HybridMatcher,
    load_config,
    resolve_query,
)
```

If the actual module uses slightly different names, adapt imports to its current exported API. Do not copy/paste a second matcher implementation into the benchmark.

Use the same registries as the contract audit:

```python
REGISTRY_BY_DATASET = {
    "bus": Path("flashfusion/eval/cache/semantic_registry_bus_v1.json"),
    "wisdm": Path("flashfusion/eval/cache/semantic_registry_wisdm_v1.json"),
    "mit_ecg": Path("flashfusion/eval/cache/semantic_registry_mit_ecg_v1.json"),
}

VERSIONS = ("v1", "v2", "v3")
```

## Benchmark unit and ground truth

One benchmark row represents one live query wording:

```text
(dataset, query_id, version)
```

For each row:

1. Resolve the live query text using `resolve_query(query_id, version, dataset)`.
2. The expected registry entry is the entry whose `query_id` equals `query_id` in the dataset-specific registry.
3. Run hybrid retrieval and verification against all reusable entries for that dataset.
4. Record both retrieval-stage and final-decision evidence.

Do not treat a matching `query_id` alone as a cache hit. A cache hit is only an `exact_hit` or `hybrid_hit` final decision whose predicted ID equals the expected ID.

## Required matcher behavior

### Use real hybrid mode

Instantiate `HybridMatcher` with:

```python
mode="hybrid"
```

Do not use fuzzy-only mode. Do not substitute the compatibility-only audit logic for matcher execution.

### Schema-aware extraction

Load each dataset using the same normal dataset-loading path as `trace_hybrid_cache.py`, so the `ContractExtractor` receives actual schema columns and the schema fingerprint can be checked. Do not infer the schema solely from registry signatures in the main benchmark.

The benchmark should support:

- Default dataset path resolution, consistent with `trace_hybrid_cache.py`.
- Optional explicit overrides such as `--data bus=...`, `--data wisdm=...`, and `--data mit_ecg=...`, if practical.

### Keep the model warm

Build one matcher per dataset and reuse it for all query IDs and versions in that dataset. This ensures the embedding model and cached registry embeddings stay warm.

Do not construct a new `HybridMatcher` for every query.

Record initialization timing separately from per-query matching timing.

## Required per-query evidence

For every `(dataset, query_id, version)` row, output a JSON-safe record containing at least:

```json
{
  "dataset": "bus",
  "query_id": "4",
  "version": "v3",
  "query": "...",
  "expected_query_id": "4",

  "dense_top_1": {
    "candidate_id": "4",
    "query_id": "4",
    "score": 0.0,
    "is_expected": true
  },
  "lexical_top_1": {
    "candidate_id": "4",
    "query_id": "4",
    "score": 0.0,
    "is_expected": true
  },
  "dense_top_k_ids": ["..."],
  "lexical_top_k_ids": ["..."],
  "retrieval_union_ids": ["..."],

  "expected_in_dense_top_1": true,
  "expected_in_dense_top_k": true,
  "expected_in_lexical_top_1": true,
  "expected_in_lexical_top_k": true,
  "expected_in_retrieval_union": true,

  "decision": "hybrid_hit",
  "predicted_query_id": "4",
  "final_winner_is_expected": true,
  "authorized_hit": true,
  "correct_authorized_hit": true,
  "false_positive_reuse": false,
  "abstained": false,
  "ambiguous": false,

  "winner": {},
  "runner_up": {},
  "winner_runner_up_margin": 0.0,
  "compatibility_candidate_ids": ["..."],
  "compatible_candidate_count": 1,
  "expected_is_compatible": true,

  "elapsed_ms": {}
}
```

Use `None` for unavailable top candidates, winner, runner-up, margin, or prediction. Keep the full `CandidateEvidence` fields for the winner and runner-up when available, including dense score, lexical score, retrieval score, contract score, component scores, compatibility, and compatibility failures.

## Retrieval-stage instrumentation

The existing `match()` result may not expose raw dense and lexical top-k lists separately. Add a small **read-only diagnostic method** to `HybridMatcher` if needed, rather than recomputing embeddings elsewhere or changing matching behavior.

Suggested interface:

```python
def retrieve_diagnostics(self, query: str, top_k: int | None = None) -> dict[str, Any]:
    """Return raw dense, lexical, and union candidates without verification."""
```

It should return:

- Dense top-k candidates and raw/clamped dense cosine score.
- Lexical top-k candidates and lexical score.
- The candidate-ID union used by hybrid matching.
- Retrieval timing.

The method must use the matcher’s existing warmed `DenseIndex` and `LexicalIndex`. It must not alter matcher state, thresholds, candidate ranking, or cache authorization behavior.

If refactoring `match()` to share retrieval code is cleaner, preserve all existing CLI output and semantics of `trace_hybrid_cache.py`.

## Definitions for metrics

Calculate metrics globally, per dataset, and per version.

### Retrieval metrics

Let each live query have one expected query ID.

- **Dense top-1 accuracy**: fraction where dense top-1 ID equals expected ID.
- **Lexical top-1 accuracy**: fraction where lexical top-1 ID equals expected ID.
- **Dense recall@k**: fraction where expected ID appears in dense top-k.
- **Lexical recall@k**: fraction where expected ID appears in lexical top-k.
- **Union recall@k**: fraction where expected ID appears in the union of dense and lexical candidate sets used for matching.

The primary `k` should default to the configured retrieval top-k values. Also expose `--report-k` so summary reporting can calculate recall at requested values such as 1, 3, 5, 10, and 20 when candidates are available.

### Authorization metrics

Treat these decisions as authorized cache reuse:

```python
AUTHORIZED_DECISIONS = {"exact_hit", "hybrid_hit"}
```

Do not count `unsafe_ablation_hit` as an authorized production hit. It should not normally occur because this benchmark runs hybrid mode.

Definitions:

- **Correct authorized hit / true positive (TP):** authorized decision and predicted ID equals expected ID.
- **False-positive reuse (FP):** authorized decision and predicted ID differs from expected ID.
- **False negative (FN):** no correct authorized hit for a live query. This includes abstentions, ambiguity, incompatible candidate, complete miss, and an authorized hit to the wrong ID.
- **Authorized-hit precision:** `TP / (TP + FP)`; use `null` when denominator is zero.
- **Authorized-hit recall:** `TP / total_live_queries`.
- **False-positive reuse rate:** `FP / total_live_queries`.
- **Authorization rate:** `(TP + FP) / total_live_queries`.

### Abstention and ambiguity

Treat these as abstentions:

```python
ABSTENTION_DECISIONS = {
    "low_confidence_candidate",
    "complete_miss",
    "incompatible_candidate",
    "out_of_scope_hit"
}
```

Note: `out_of_scope_hit` may be handled separately in a future benchmark if there is explicit out-of-scope ground truth. For this query-ID benchmark, report it separately and do not count it as a correct cache reuse unless the expected registry semantics explicitly mark that query as out of scope.

- **Abstention rate:** number of abstentions divided by total live queries.
- **Ambiguity rate:** `ambiguous_multi_candidate / total_live_queries`.
- **Potential ambiguity rate:** fraction where more than one compatible candidate remains after verification, regardless of the final decision.
- **Correct-but-abstained rate:** correct expected candidate is retrieved and compatible, but no correct authorized cache hit is issued.

### Score and margin diagnostics

For each query, retain:


a) winner final score,

b) runner-up final score,

c) margin: `winner.final_score - runner_up.final_score`, when both exist,

d) acceptance threshold,

e) ambiguity threshold/margin,

f) whether the expected candidate was present in retrieval but lost after ranking or verification.

These diagnostics are necessary for threshold calibration.

## Summary JSON format

Write a JSON report with this structure:

```json
{
  "benchmark": {
    "name": "hybrid_cache_end_to_end",
    "timestamp_utc": "...",
    "config_path": "...",
    "config_version": 1,
    "datasets": ["bus", "wisdm", "mit_ecg"],
    "versions": ["v1", "v2", "v3"],
    "mode": "hybrid",
    "dense_top_k": 20,
    "lexical_top_k": 20,
    "report_k": [1, 3, 5, 10, 20]
  },
  "summary": {
    "total_live_queries": 108,
    "initialization_ms_by_dataset": {},
    "overall": {
      "dense_top_1_accuracy": 0.0,
      "lexical_top_1_accuracy": 0.0,
      "dense_recall_at_k": {},
      "lexical_recall_at_k": {},
      "union_recall_at_k": {},
      "authorized_hit_precision": 0.0,
      "authorized_hit_recall": 0.0,
      "authorization_rate": 0.0,
      "false_positive_reuse_rate": 0.0,
      "abstention_rate": 0.0,
      "ambiguity_rate": 0.0,
      "potential_ambiguity_rate": 0.0,
      "correct_but_abstained_rate": 0.0,
      "true_positives": 0,
      "false_positives": 0,
      "false_negatives": 0
    },
    "by_dataset": {},
    "by_version": {},
    "by_dataset_and_version": {}
  },
  "rows": []
}
```

All float values should be rounded consistently for human readability, but retain sufficient precision for later analysis (for example, six decimal places in row evidence and six decimal places in summaries).

## Human-readable terminal summary

In addition to writing JSON, print compact tables for:

1. Overall metrics.
2. Metrics by dataset.
3. Metrics by version.
4. A small failure breakdown:
   - Wrong authorized reuse
   - Abstention decision counts
   - Ambiguous decisions
   - Expected candidate absent from retrieval union
   - Expected candidate retrieved but rejected as incompatible
   - Expected candidate compatible but not final winner
   - Expected candidate final winner but below acceptance floor

Do not print every row by default. Add `--verbose` to print per-query rows or detailed failures.

## CLI requirements

Implement at least:

```bash
python -m flashfusion.eval.benchmark_hybrid_cache \
  --dataset all \
  --output flashfusion/eval/cache/hybrid_cache_benchmark.json
```

Arguments:

```text
--dataset {all,bus,wisdm,mit_ecg}     Default: all
--version {all,v1,v2,v3}              Default: all
--config PATH                         Default: hybrid_match_config.json
--output PATH                         Optional JSON output path
--dense-top-k INT                     Optional override
--lexical-top-k INT                   Optional override
--report-k INT [INT ...]              Default: 1 3 5 10 20
--device STRING                       Default: cuda when available, otherwise cpu
--no-warmup                           Disable one-time model warm-up
--verbose                             Print detailed per-query rows/failures
--failures-only                       Print only incorrect/abstained/ambiguous rows
--data DATASET=PATH                   Repeatable optional data-path override
```

Validate that requested `report-k` values do not exceed the candidate count; report effective values when the registry has fewer entries.

## Performance and determinism

- Load each registry once per dataset.
- Build/warm the embedding model and candidate embedding index once per dataset.
- Reuse that matcher for all query versions and IDs in the dataset.
- Use the existing fixed model and deterministic index ordering/tie handling.
- Record setup time separately from the average and percentile per-query match times.
- Avoid language-model calls and network calls beyond any normal first-run SentenceTransformer model availability.

## Guardrails

- Do not change the cache matching decision logic merely to make benchmark metrics look better.
- Do not count a retrieval top-1 result as a cache hit unless `HybridMatcher.match()` authorizes it.
- Do not silently discard rows that fail query resolution, dataset loading, or matching. Store a structured error row and report the error count.
- Do not use the query ID as an input feature to retrieval or scoring.
- Do not use v1 text as the candidate document for a query version other than what the registry already stores.
- Preserve the distinction between `compatible`, `retrieved`, `ranked winner`, and `authorized hit`.

## Acceptance tests

Before considering the implementation complete:

1. Run one dataset end to end:

```bash
python -m flashfusion.eval.benchmark_hybrid_cache \
  --dataset bus \
  --output /tmp/hybrid_bus_benchmark.json \
  --verbose
```

2. Confirm it creates exactly one row per reusable BUS query ID times the selected versions.

3. Confirm the row for BUS query ID 4 / v3 contains:
   - Expected ID `4`.
   - Dense and lexical top-k diagnostics.
   - Final matcher decision and winner/runner-up evidence.
   - Retrieval-membership booleans.
   - Compatibility count and margin diagnostics.

4. Run all datasets:

```bash
python -m flashfusion.eval.benchmark_hybrid_cache \
  --dataset all \
  --output flashfusion/eval/cache/hybrid_cache_benchmark.json
```

5. Confirm summary counts equal the number of emitted non-error rows and that per-dataset/per-version totals sum to the overall total.

6. Manually inspect at least one example of each available final decision type: `hybrid_hit`, `low_confidence_candidate`, `ambiguous_multi_candidate`, `incompatible_candidate`, and `complete_miss`.

## Desired interpretation

The benchmark should make it possible to distinguish these cases clearly:

| Situation | Desired label/diagnostic |
|---|---|
| Correct ID was not retrieved | Retrieval recall failure |
| Correct ID was retrieved but rejected by compatibility | Contract-extraction or contract-completeness problem |
| Correct ID was compatible but lost ranking | Retrieval/final-score ranking problem |
| Correct ID won but score was below threshold | Threshold or contract-evidence problem |
| Wrong ID was authorized | Unsafe cache reuse / false-positive reuse |
| Several compatible candidates have too-small margin | Ambiguous multi-candidate |
| No candidate can be safely authorized | Abstention |

The benchmark must emphasize safety: a lower authorization rate is acceptable if it avoids false-positive reuse. The final report should let us tune retrieval depth, retrieval/contract weights, acceptance floor, and ambiguity margin on a development split before reporting final test-set metrics.
