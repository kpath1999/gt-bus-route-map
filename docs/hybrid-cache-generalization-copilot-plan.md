# Hybrid Cache Generalization Plan

## Objective

Replace hand-authored lexical rules in `flashfusion/eval/trace_hybrid_cache.py` with a conservative, data-driven retrieval and verification pipeline. The implementation must handle major paraphrases, preserve low latency by precomputing registry features, and abstain rather than return an unsafe cache hit.

A cache cannot prove that it has zero false positives from similarity alone. Treat “absent false positives” as the operational requirement: only reuse an entry when independent evidence supports it, the result has a decisive margin, and an offline calibration suite demonstrates the requested false-positive bound. Otherwise return an explicit miss and route to the normal planner.

## Repository scope

Inspect and preserve the public behavior of these modules before changing code:

- `flashfusion/eval/trace_hybrid_cache.py`
- `flashfusion/eval/trace_query.py`
- `flashfusion/eval/semantic_scorer.py`
- `flashfusion/eval/build_semantic_registry.py`
- `flashfusion/eval/queries.py`, `queries_v2.py`, and `queries_v3.py`
- The registry format under `flashfusion/eval/cache/`

Do not change unrelated cache execution behavior. Keep existing exact-match behavior as the first and fastest path. Do not import SentenceTransformers until a non-exact lookup needs dense retrieval.

## Design principles

1. **No hard-coded query-language regexes.** Remove query-intent functions such as `_detect_aggregate` and `_detect_predictive_model` from the matching decision. Do not replace them with a larger synonym list.
2. **Use independent signals.** Retrieve using lexical and dense representations, then verify candidates using a structured, schema-aware query contract. A single high embedding score must never be enough to reuse a cached plan.
3. **Optimize for precision and abstention.** A false cache hit can execute the wrong operator skeleton. When evidence is weak or candidates are close, emit an explicit miss category and use the normal non-cache path.
4. **Precompute immutable registry data.** Encode registry entries and build lexical indexes once during registry build/load, not per incoming query.
5. **Make every threshold measurable.** Thresholds, weights, and ambiguity margins must be calibrated on held-out paraphrase and confuser traces, versioned in a config file, and reported in the trace output.

## Target pipeline

### 1. Build canonical candidate documents

Extend the semantic-registry builder to persist a `retrieval_document` for every reusable entry. It should concatenate only stable, meaningful fields:

- original query text
- dataset key
- normalized field names and aliases derived from the live schema
- canonical operator skeleton
- canonical semantic contract

Use deterministic serialization with sorted keys. Do not include query ID in the document; including it would leak ground truth into retrieval.

Persist a versioned `retrieval_contract` alongside every registry entry. The contract should represent operator semantics rather than wording, for example aggregate, projected fields, filters, predicate operators, grouping, ordering, model type, target, and output shape. Store `null` when a component is unknown; distinguish unknown from absent.

### 2. Build two retrieval indexes

Create an in-memory `HybridIndex` initialized once per process.

- **Dense index:** encode all `retrieval_document` values using the configured SentenceTransformer, normalize embeddings, and search with cosine similarity. Cache the model, corpus embeddings, and candidate-ID-to-row mapping for process lifetime. Perform one startup warm-up only when dense retrieval is enabled.
- **Lexical index:** use a standard token-based ranker such as BM25 over the same documents. Tokenization may lowercase and split on non-alphanumeric boundaries, but must not apply domain-specific regex extraction or hard-coded synonym rules. Persist or cheaply rebuild this small index at startup.

For an incoming query, retrieve top `K_dense` and top `K_lexical` candidates, take their union, then apply dataset and immutable schema/contract compatibility filters. Start with K=20 for each retriever and make it configurable.

### 3. Produce a structured query contract

Create one interface, for example:

```python
@dataclass(frozen=True)
class QueryContract:
    admissibility: Literal["in_scope", "out_of_scope", "unknown", "ambiguous"]
    operator_skeleton_hint: tuple[str, ...] | None
    aggregate: str | None
    fields: frozenset[str]
    predicate_ops: tuple[tuple[str, str], ...]
    filter_values: tuple[tuple[str, str], ...]
    output_shape: str | None
    predictive: tuple[tuple[str, str], ...]
    confidence: float
```

Implement extraction behind a `ContractExtractor` protocol. Reuse the existing schema-aware semantic extraction if present, but remove lexical regex-only intent detection from it. The extractor may use a small local model, a constrained parser, or a supplied planner analysis; it must return confidence and unknown/ambiguous states rather than inventing fields.

Validate every extracted field against the live dataframe schema. Reject fields and values not grounded in the current schema. This prevents an embedding match from bypassing schema safety.

### 4. Score and verify candidates

Use retrieval scores only to select a small candidate set. Make the final reuse decision through contract verification.

For each candidate, calculate:

- `dense_score`: cosine similarity of normalized embeddings
- `lexical_score`: normalized BM25 score, used for retrieval evidence only
- `contract_score`: component-wise similarity between live and cached contracts
- `compatibility`: boolean for dataset, schema fingerprint, operator contract hash, and required known contract components

Use deterministic component scoring. Exact agreement for known aggregate, operator skeleton, field bindings, predicate operators, output shape, and predictive-model attributes receives full credit. Compare sets with Jaccard similarity only for inherently multi-valued components. Unknown live components must not count as agreement.

Do not use a weighted average as the sole acceptance criterion. A weighted score may rank compatible candidates, but acceptance requires all of the following:

1. immutable compatibility passes;
2. the structured extractor is `in_scope` and has calibrated minimum confidence;
3. all safety-critical known contract components agree;
4. the top candidate exceeds its calibrated acceptance threshold; and
5. its score exceeds the second-best compatible candidate by a calibrated margin.

This separates **ranking** from **authorization**. It prevents a strong embedding score from compensating for an aggregate, field, or predicate mismatch.

### 5. Decisions and trace output

Return a typed result, not a tuple with overloaded strings:

```python
@dataclass
class CacheMatchResult:
    decision: Literal[
        "exact_hit", "hybrid_hit", "out_of_scope_hit",
        "ambiguous_multi_candidate", "low_confidence_candidate",
        "complete_miss", "incompatible_candidate"
    ]
    entry: dict[str, Any] | None
    winner: CandidateEvidence | None
    runner_up: CandidateEvidence | None
    elapsed_ms: dict[str, float]
    candidates: list[CandidateEvidence]
```

Classify outcomes precisely:

- `ambiguous_multi_candidate`: at least two safe-compatible candidates pass the acceptance floor but their difference is below the calibrated margin.
- `low_confidence_candidate`: a best candidate exists but fails the acceptance floor or contract-extractor confidence requirement.
- `incompatible_candidate`: retrieval found candidates, but every candidate fails schema, dataset, hash, or a safety-critical contract check.
- `complete_miss`: neither retriever surfaced a candidate with meaningful evidence.

For every trace, print and optionally write JSON containing the query ID/version when supplied, expected ID, predicted ID, candidate IDs, dense and lexical scores, contract-component scores, compatibility failures, decision, timing, and `false_positive` if ground truth is known. Never expose misleading “hit” language for an abstention.

## Timing and warm-process behavior

Add a `HybridMatcher` lifecycle:

```python
matcher = HybridMatcher.load(registry_path, config)
matcher.warm_up()  # optional, once
result = matcher.match(query, dataset, dataframe, expected_contract_hash)
```

- Load model and indexes once at CLI/program start, never inside a per-query function.
- Use `time.perf_counter()` for timing.
- On CUDA, call `torch.cuda.synchronize()` immediately before starting and immediately after ending the dense-encoding interval.
- Report `registry_load_ms`, `model_load_ms`, `warm_up_ms`, `contract_extract_ms`, `retrieve_ms`, `verify_ms`, and `total_match_ms` separately.
- Batch all registry encodes. Batch incoming requests when the serving interface allows it.
- Add `--no-warmup`, `--device`, `--dense-top-k`, `--lexical-top-k`, and `--timing` flags.

## CLI behavior

Preserve current query resolution via `--query-id` and `--version`, with `--query-text` as an override. Add:

- `--mode {hybrid,fuzzy}`
- `--config PATH`
- `--emit-json PATH`
- `--explain`
- `--calibration-split {dev,test}`

`--fuzzy` may remain as a backwards-compatible alias for `--mode fuzzy`, but define it clearly as a retrieval ablation. In fuzzy mode, use only a generic edit-distance or token similarity scorer, bypass all other signals and verification, and label the result `UNSAFE_ABLATION`. Do not claim fuzzy-only results are safe cache hits.

## Calibration and evaluation

Create a versioned configuration file such as `flashfusion/eval/cache/hybrid_match_config.json` containing weights, top-K values, floors, ambiguity margin, and model name. Do not scatter constants in Python.

Add an evaluation command that constructs labeled pairs from v1/v2/v3 variants:

- positive pairs: same query ID across different versions
- hard negatives: same dataset with changed aggregate, field, filter, predicate, operator, or predictive model
- cross-dataset negatives: semantically similar wording from different datasets

Split by query ID, never by individual wording, so paraphrases of one intent cannot appear in both calibration and test sets. Choose thresholds on development data to satisfy a configured false-positive target. Report recall, precision, false-positive rate, false-negative rate, coverage, abstention rate, top-1 retrieval recall, ambiguous-miss rate, and p50/p95 end-to-end latency. Evaluate exact, lexical-only, dense-only, fuzzy-only, and verified-hybrid baselines.

If the requested false-positive target is not reached on held-out data, the implementation must abstain more often or fail the acceptance check; it must not lower safety constraints merely to improve hit rate.

## Required tests

Add focused tests that do not depend on model downloads; inject fake encoders and deterministic index scores where needed.

1. Exact query returns `exact_hit` without invoking dense encoding.
2. Strong paraphrase with the same verified contract returns `hybrid_hit`.
3. A semantically similar query with a different aggregate abstains as `incompatible_candidate`.
4. A field, predicate operator, filter value, or predictive-model change abstains despite a high dense score.
5. Two safe-compatible near-tied candidates return `ambiguous_multi_candidate`.
6. One weak winner returns `low_confidence_candidate`.
7. No retrieved evidence returns `complete_miss`.
8. Dataset aliasing and schema fingerprint checks remain enforced.
9. Fuzzy-only mode never calls contract extraction or dense encoding and clearly reports its false-positive metrics.
10. Model loading and warm-up happen once per matcher lifecycle; repeated `match()` calls reuse the same model/index objects.
11. CUDA timing synchronizes only when CUDA is active.

## Acceptance criteria

- No domain-specific regex or hand-maintained synonym list influences hybrid match acceptance.
- Exact lookup remains deterministic and faster than hybrid lookup.
- Registry embeddings are computed once per registry build/load, not once per request.
- A dense, lexical, or weighted score by itself cannot produce a reusable cache hit.
- Every non-hit is one of the explicit, machine-readable categories above.
- `--explain` supplies enough evidence to reproduce every decision.
- Tests cover paraphrase success, semantic-neighbor safety failures, ambiguity, complete misses, fuzzy-only isolation, and warm-process timing.
- The held-out evaluation reports the false-positive rate and does not claim zero false positives unless the measured test result and its sample size support that statement.

## Deliverables

1. Refactored matcher and index implementation.
2. Registry-builder changes for retrieval documents, contracts, and precomputed dense vectors or a compatible sidecar index.
3. Versioned calibration configuration.
4. Updated `trace_hybrid_cache.py` CLI and JSON trace format.
5. Unit tests plus a reproducible evaluation command and a concise results document describing the selected thresholds and their sensitivity.
