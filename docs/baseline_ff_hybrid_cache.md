# Integrate Hybrid Matching into the Flash Fusion Cache Baseline

## Objective

Upgrade `flashfusion/baselines/flash_fusion_cache.py` so the baseline can reuse whole cached plans for paraphrased queries, using the already-developed hybrid retrieval and verification logic in `flashfusion/eval/trace_hybrid_cache.py`. Add an order-randomized, reproducible benchmark path using reusable reporting utilities from `flashfusion/eval/benchmark_hybrid_cache.py`.

The implementation must demonstrate that reported hit rates are not an artifact of a favorable request order.

## Scope and non-goals

### In scope

- Reuse hybrid-cache components rather than copying lexical, dense-retrieval, contract-extraction, or verification code into the baseline.
- Preserve the existing exact-match behavior as the first/fastest path.
- Reuse **whole resolved plans / operator skeletons**, never partial plans and never an answer/result cache.
- Benchmark the baseline under randomized online request orders, with reproducible seeds and per-seed reporting.
- Preserve conservative authorization: similarity retrieves candidates; structural verification authorizes reuse.

### Out of scope

- Changing planner semantics or the implementation of an operator skeleton.
- Replacing the baseline with the evaluation-only tracer CLI.
- Using query ID, version, or benchmark ground truth in runtime cache decisions.
- Pre-populating the runtime cache with plans from evaluation queries.

## Read first

Before editing, inspect these current implementations and adapt to their actual public APIs rather than assuming names/signatures:

1. `flashfusion/baselines/flash_fusion_cache.py`
2. `flashfusion/eval/trace_hybrid_cache.py`
3. `flashfusion/eval/benchmark_hybrid_cache.py`
4. The cache/registry builder and plan-execution code imported by the baseline.
5. Existing baseline tests and benchmark scripts.

The current hybrid tracer provides the reusable building blocks: dataset canonicalization, registry loading/filtering, lexical index, dense index, schema-aware intent extraction, candidate union, compatibility checks, safety-critical agreement, and hybrid matching. The benchmark module provides the benchmark-row/reporting pattern and metrics definitions.

## Target architecture

### 1. Keep one authoritative plan cache

Use the baseline’s existing cache/registry as the source of truth. Each reusable entry must continue to contain, at minimum:

- Dataset identity.
- Original query text (for retrieval).
- Whole operator skeleton / resolved plan (for reuse).
- Plan compatibility metadata already supported by the registry: semantic signature or retrieval contract, schema fingerprint, and operator contract hash when available.
- Reuse status.

Do not store or return partial execution fragments. A hybrid hit must retrieve and execute the same complete cached plan that an exact hit would use.

### 2. Add an adapter, not a second matcher

Create a small importable adapter in the baseline layer, or a narrowly scoped shared module if circular imports make that necessary. The adapter should:

- Accept runtime inputs already available to the baseline: query text, dataset, live schema/dataframe or columns, expected contract hash if available, and cache entries.
- Invoke the hybrid matcher from `trace_hybrid_cache.py` in `hybrid` mode.
- Return a stable baseline-facing result with:
  - `decision`: exact hit, verified hybrid hit, low-confidence abstention, ambiguity, incompatible candidate, or complete miss.
  - `entry` / whole resolved plan only for authorized exact or hybrid hits.
  - concise diagnostic metadata for metrics/logging.

Avoid importing CLI-only behavior (`argparse`, printing, file emission) into the baseline. If the existing tracer mixes reusable logic with CLI code, move only the pure matching/index/extraction pieces into an importable module, then have both `trace_hybrid_cache.py` and the baseline import from it. Do not duplicate logic.

### 3. Runtime flow in the baseline

For each incoming query:

```text
1. Attempt existing exact cache lookup.
2. If exact hit: reuse the whole cached plan.
3. If exact miss: invoke hybrid matcher over current reusable cache entries.
4. If verified hybrid hit: reuse the matched whole cached plan.
5. If abstention / ambiguity / incompatible / miss: run the normal planner.
6. If normal planning succeeds and produces a reusable whole plan: insert/update the cache entry.
```

Required invariants:

- A high lexical or dense score alone must never produce a cache hit.
- Hybrid reuse requires the matcher’s compatibility and safety-critical checks to pass, plus its confidence, score, and ambiguity authorization gates.
- The baseline must record whether each request was an exact hit, hybrid hit, planner fallback, ambiguity, incompatible candidate, or low-confidence abstention.
- Planner fallback is correct behavior; it must not be recorded as a false positive or a cache hit.

### 4. Model/index lifecycle

- Construct the dense model/index once per benchmark run or long-lived baseline process, not per request.
- Warm it once before timed requests.
- Rebuild or incrementally refresh retrieval indexes after a successful reusable-plan insertion. Prefer a correct/simple rebuild initially; optimize incremental updates only after correctness is demonstrated.
- Ensure entries that are non-reusable, invalid, or from a different dataset are excluded from retrieval before scoring.

## Randomized-order benchmark

### Goal

Demonstrate that baseline cache performance is not caused by a hand-picked request ordering. This must be an **online cache experiment**: each request only benefits from plans inserted by earlier requests in the same shuffled run.

## Notes

- Please ensure that model_load_ms and warm_up_ms is 0 when FF-cache is run; in other words, embedding model should be loaded and warmed up across the queries.
- Make sure that cache lookup and validation times are reported, but in @measure.py and @llamas.py, they are reported within the grounding banner.