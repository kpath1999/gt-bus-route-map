# Latency Reporting Accuracy Plan

## Context
The latest latency debug run shows two issues that were previously conflated:

1. A reporting/classification bug: cache out-of-scope rejections were counted as cache hits.
2. Real runtime regressions: some true cache-hit executions are slower than full planner runs.

These must be fixed in order. If reporting is wrong, optimization work chases noise.

## Root Causes

### 1. Cache outcome over-labeling
In metrics aggregation, cache outcomes were inferred using broad plan_source prefixes such as exact_query_cache_* and semantic_*.

Problem:
- Out-of-scope rejection templates (for example exact_query_cache_out_of_scope and semantic_query_cache_out_of_scope) also match those prefixes.
- Guardrail rejections therefore appeared as hit instead of a distinct rejection outcome.

Impact:
- Rejection latency got folded into hit aggregates.
- Hit-vs-planner speedup claims became pessimistic and misleading.

### 2. Stage expectation mismatch in reconciliation
Latency reconciliation relies on expected stage sets by branch.

Problem:
- Rows marked as hit were matched to hit stages even when execution_path was guardrail_reject.
- For rejection rows, real work in cache_rejection_latency_s was treated as stray/unaccounted.

Impact:
- Artificially high unaccounted_pct and stray stage mass.

### 3. True cache-path runtime tails
After removing mislabeling noise, some true typed cache-hit rows still regress.

Observed patterns:
- Predictive queries with heavy typed_exec tails.
- Sporadic cache_grounding spikes likely tied to provider variance/retry behavior.

Impact:
- Genuine speed regressions remain and require runtime-level fixes.

## Remediation Plan

## Phase 1: Correct outcome semantics first
Update cache outcome classification to be execution-path-first.

Decision:
- hit: execution_path == typed_operator_cache
- hit_rejected: execution_path == guardrail_reject and out-of-scope cache source
- miss: all other FLASH_FUSION_CACHE rows
- not_applicable: non-cache baselines

Why this resolves gaps:
- Rejection-path rows stop contaminating hit metrics.
- Hit speedup now reflects only true typed cache hits.

Primary file:
- flashfusion/eval/metrics.py

## Phase 2: Make reconciliation branch-accurate
Use execution_path precedence in expected stage routing.

Decision:
- guardrail_reject always maps to rejection stages
- typed_operator_cache maps to hit stages
- hit_rejected explicitly maps to rejection stages

Why this resolves gaps:
- cache_rejection latency is accounted in its own branch.
- unaccounted_pct and stray-stage alerts no longer spike from classification errors.

Primary file:
- flashfusion/viz/latencydebug.py

## Phase 3: Repair speedup semantics
Restrict hit-vs-planner regression checks to true typed cache-hit rows.

Decision:
- Exclude hit_rejected from hit-speedup calculations.
- Track hit_rejected latency separately as context telemetry.

Why this resolves gaps:
- The regression report focuses on real typed execution regressions.
- Rejection quality can still be monitored without invalid comparisons.

Primary file:
- flashfusion/viz/latencydebug.py

## Phase 4: Address real regressions (post-reporting fix)

### 4a) Predictive typed_exec drift
Likely cause:
- Non-deterministic exact vs semantic template selection in predictive families can alter grounded feature sets and inflate fit time.

Fix direction:
- Strengthen matching discriminators and ambiguity abstention before grounding.
- Pin/validate feature-set identity for predictive templates.

Primary files:
- flashfusion/eval/trace_hybrid_cache.py
- flashfusion/baselines/flash_fusion_cache.py
- docs/cache_match_gaps.md

### 4b) Grounding latency spikes
Likely cause:
- Provider retry/timeouts and request-size variability.

Fix direction:
- Add explicit grounding telemetry: retries, attempt count, token/request size, final attempt latency.
- Report p50/p95 latency and retry incidence per dataset/query.

Primary files:
- flashfusion/baselines/flash_fusion_cache.py
- flashfusion/eval/metrics.py
- flashfusion/viz/latencydebug.py

## Verification and Acceptance Criteria

## Reporting correctness criteria
1. No guardrail_reject row is labeled hit.
2. Rejection rows are labeled hit_rejected when sourced from out-of-scope cache templates.
3. Reconciler expected stages include cache_rejection for rejection path rows.
4. Post-fix unaccounted_pct drops substantially for previously mislabeled rejection rows.

## Runtime regression criteria
1. hit_slower_than_planner report contains only typed_operator_cache rows.
2. Repeated runs for predictive queries do not alternate to broader feature templates without explicit abstention.
3. Grounding spike rows include sufficient telemetry for provider-vs-code-path attribution.

## CI gate proposal
Run latency debug on a stable benchmark slice and fail when:
1. Any typed_operator_cache row exceeds planner latency by configured threshold.
2. Non-rejection rows exceed unaccounted latency tolerance.
3. guardrail_reject rows appear in hit bucket.

Thresholds should be configurable to avoid brittle failures in noisy environments.

## Implementation Status
Completed in this iteration:
1. Metrics outcome labeling changed to include hit_rejected and execution-path-first logic.
2. Latencydebug reconciliation now prioritizes execution_path and recognizes hit_rejected branch expectations.
3. Hit-vs-planner detection is restricted to true typed cache-hit rows.
4. Tests added for outcome labeling and latencydebug branch semantics.

Planned next:
1. Re-run latencydebug and compare before/after output tables.
2. Implement predictive feature-set determinism checks and telemetry expansion.
3. Wire latency regression thresholds into CI.
