# FLASH_FUSION_CACHE Smoke Sanity Check (A/B/C Taxonomy)

Scope: `flashfusion/results/ff_and_react_qwen/smoke/FLASH_FUSION_CACHE/{wisdm,bus,mit_ecg}`

Method:
- Read `metrics.csv`, `report.md`, `raw_results.jsonl`, and `ground_truth_llm_judge/llm_judgments.csv`.
- For each FAIL, classify root cause:
  - A: grounding/semantic slot error
  - B: provenance/judge input incomplete (trace shows correct multi-step execution)
  - C: other layer (skeleton selection, executor, display-only mismatch, etc.)

## FAIL Taxonomy

| Dataset | Query id | Query (short) | Class | Would change help? | Direction | Enough for PASS? | Notes |
|---|---:|---|---|---|---|---|---|
| wisdm | 2 | walking users count | B | yes | provenance fix | yes | Trace shows `FILTER_NOT_EMPTY` + `FILTER_COMPARE` + `COUNT_DISTINCT`; judge saw only `result = df['subject_id'].nunique()`. |
| wisdm | 4 | highest total number of samples | A | yes | grounding fix | yes | Grounded `GROUP_AGGREGATE sum(z)` then rank; should be per-user row count then rank. |
| bus | 4 | accel_variance > 0.20 count | B | yes | provenance fix | yes | Trace includes filter then count; judge saw only `result = len(df)`. |
| bus | 5 | north vs south roughness by average variance | A | yes | grounding fix | yes | Grounded var + ratio (`metric: var accel_variance`, `mode='ratio'`) instead of mean-based roughness comparison. |
| bus | 7 | average peak magnitude from x/y/z p99 | B | yes | provenance fix | yes | Trace includes derive vector magnitude from p99 columns, but judge saw only final mean line. |
| mit_ecg | 3 | record_id 106, MLII > 0 count | A | yes | grounding fix | yes | Grounded `record_id gt 105`; must be `record_id eq 106`. |
| mit_ecg | 13 | predictive first holdout row (logreg) | A | yes | grounding fix | yes | Answer says `last holdout row`; holdout row field mismatched with query text. |
| mit_ecg | 14 | predictive first holdout row (RF) | A | yes | grounding fix | yes | Same holdout-row mismatch. |
| mit_ecg | 15 | predictive first holdout row (1-NN) | A | yes | grounding fix | yes | Same holdout-row mismatch. |

Class C in current FAIL set: none identified from these smoke artifacts.

## Why These Changes Target The Right Layer

- Class A fixes are in cache grounding semantics:
  - prompt contract now explicitly binds entity mentions to equality filters,
  - enforces count semantics for "number of samples" extrema,
  - enforces mean semantics for "average",
  - prevents defaulting to ratio for roughness questions unless ratio is requested,
  - enforces train split vs holdout row independence.
- Class B fixes are provenance-only:
  - emit full typed operator chain (`final_code`) for cache hits,
  - include per-step code in `execution_attempts`,
  - judge resolver now reconstructs full multi-step chain when available.

Explicit non-goal preserved: no filter rewriting for walking/accel_variance because those filters already executed correctly.

## Regression Notes (PASS cases that still need gt/sum/ratio/last)

- Legitimate `gt`/`lt` filters remain supported when query explicitly uses relational language.
- Legitimate `sum` remains valid when query explicitly asks for totals/sums (not counts).
- Legitimate `ratio` remains valid when query asks ratio-like language (`ratio`, `times`, `%`).
- Legitimate `holdout_row='last'` remains valid when query explicitly asks last holdout row.

## Expected PASS Flips

- Likely flips due to provenance: `wisdm Q2`, `bus Q4`, `bus Q7`.
- Likely flips due to grounding semantics: `wisdm Q4`, `bus Q5`, `mit_ecg Q3`, `mit_ecg Q13-15`.
