# Applicability-Normalized Confidence Plan for Bus and WISDM Low-Confidence Abstentions

## Executive Summary

The common failure mode is not retrieval failure and not compatibility rejection.

The expected candidate is retrieved, marked compatible, and often scored as the top winner. The system still abstains as `low_confidence_candidate` because extractor confidence treats a category with no applicable evidence the same as a category with missing evidence. This is fixed by normalizing confidence against what the query actually needed, using the same `matched / applicable` ratio already proven in component scoring — without touching how the contract score itself is weighted into the final score.

This is a methodology issue in evidence aggregation, not a threshold issue.

## What the Benchmark Evidence Shows

1. In all reported bus abstentions, the winner is correct and compatible, with `final_winner_is_expected: true` and winner `final_score` at `1.0`.
2. In those same bus cases, abstention still happens because extractor confidence is below `extractor_confidence_floor`.
3. WISDM abstentions show the same pattern for query 4 and query 6: correct compatible winner, `final_score: 1.0`, but extractor confidence below floor.
4. One WISDM case (query 5, v3) is different: winner is correct and compatible, but `final_score` is `0.5` because `contract_score` contributes zero while `retrieval_score` is strong. This case is a genuine contract-inapplicability signal, not a confidence artifact — it is intentionally left unresolved by this plan, since discounting the contract's weight whenever it goes quiet would blunt the very check meant to catch disagreement.
5. No failures are due to `expected_absent_from_retrieval_union`, `expected_retrieved_but_rejected_incompatible`, or `wrong_authorized_reuse`.

## Why This Happens

`confidence` is computed as a sum of fixed point-values across five categories (`fields`, `aggregate`, `predicate_ops`, `filter_values`, `predictive`), regardless of whether a given query's phrasing ever implied those categories. A query with only a max aggregate and one field structurally cannot generate `predicate_ops`, `filter_values`, or `predictive` evidence — so it caps out around `0.35`–`0.45`, below the `0.55` floor, even when every applicable fact was extracted correctly.

In short: the system treats **"this category wasn't applicable to the query"** as **"we're missing evidence,"** which drags down confidence even when the available evidence is fully consistent.

## Mitigation Plan: Applicability-Normalized Confidence

### Design Goal

Improve recall on the sparse-extraction failure mode while preserving safety and precision, without lowering thresholds, without introducing per-query hacks, and without weakening the contract score's role in authorization.

### Core Principle

For any evidence category, first determine whether the query text gives structural reason to expect that category (`applicable`), then check whether extraction succeeded for it (`matched`). Score coverage as `matched / applicable`, not `matched / fixed_total`. This is the exact normalization already used for `contract_score` in component scoring — apply it consistently to extractor confidence as well, since confidence and contract score are answering different questions: confidence asks "do we trust this extraction enough to verify," contract score asks "does the verified extraction agree with the candidate." Only the former needs correction here.

### Technique A: Applicability-Normalized Extractor Confidence

Replace the fixed-point confidence formula with an applicability-aware ratio computed directly from structural cues in the query text, before verification:

```python
applicable = 0
matched = 0

if fields:
    applicable += 1
    matched += 1  # non-empty by construction

if has_aggregate_cue(query_lc):       # matched an AGGREGATE_PHRASES entry
    applicable += 1
    if aggregate is not None:
        matched += 1

if has_comparison_cue(query_lc):      # matched a COMPARISON_PHRASES entry
    applicable += 1
    if predicate_ops:
        matched += 1

if has_numeric_literal(query_lc):
    applicable += 1
    if filter_values:
        matched += 1

if has_model_name_cue(query_lc):
    applicable += 1
    if predictive:
        matched += 1

confidence = matched / applicable if applicable else 0.0
```

Rationale:
A query that only implies two evidence categories and successfully extracts both should register as fully confident, not partially confident. This does not weaken safety — it only affects the pre-verification gate that decides whether a candidate is scored at all. `safety_critical_agreement`, `compatibility`, `contract_score` weighting, and the `ambiguity_margin` gate all stay exactly as they are.

Expected impact:
Eliminates the bus and WISDM abstentions where the winner is already correct, compatible, and high-scoring but blocked only by structurally sparse (not incorrect) extraction. Does not address the WISDM query 5 style case, where the shortfall is in `contract_score` itself rather than confidence — that case is left to the contract check by design, since a quiet contract score there may reflect genuine unresolved disagreement rather than pure inapplicability.

### Technique C: Preserve Existing Safety Guardrails

Keep unchanged:

1. Compatibility checks.
2. Safety-critical agreement checks.
3. Ambiguity margin gate.
4. Dataset/schema/operator contract constraints.
5. The fixed `0.5 / 0.5` retrieval/contract weighting in `final_score`.

Technique A operates strictly before these gates, as a pre-verification filter. It never substitutes for or discounts the contract check. This ensures precision and false-positive control remain stable, and that a low contract score continues to carry its full intended weight in the final authorization decision.

## Implementation Sketch

1. Add structural applicability detectors to the extractor: `has_aggregate_cue`, `has_comparison_cue`, `has_numeric_literal`, `has_model_name_cue` (reusable, generic — not per-dataset).
2. Replace the fixed-point `confidence` calculation in `extract()` with the `matched / applicable` ratio from Technique A.
3. Log both `applicable` and `matched` counts per query in trace output for auditability.
4. Leave `_component_scores`, `contract_score`, and `final_score` weighting untouched.

## Validation Plan

1. Re-run `benchmark_hybrid_cache` for bus and wisdm with identical thresholds.
2. Success criteria:
   - Recall improves substantially for v2/v3 rewordings, driven specifically by resolution of confidence-floor abstentions.
   - Precision remains `1.0`.
   - `wrong_authorized_reuse` stays at `0`.
   - `expected_retrieved_but_rejected_incompatible` remains `0`.
   - WISDM query 5 style abstentions (contract-score-driven) may persist — confirm this is expected, not a regression.
3. Re-run mit_ecg benchmark to ensure no regression.
4. Add ablation logs for:
   - Distribution of `applicable` counts per query (to confirm sparsity assumption).
   - Confidence values before and after the change, for previously abstained queries.
   - Any new abstentions introduced (should be zero if hard gates are unchanged).

## Why This Is Not a Threshold Hack

1. No threshold values are reduced — `extractor_confidence_floor` and `acceptance_floor` stay as configured.
2. Authorization still depends on strict compatibility and safety-critical agreement checks.
3. The contract score's weight in `final_score` is left fully intact, so cases where contract evidence is genuinely inconclusive continue to abstain rather than being waved through by a discounted weighting scheme.
4. The change corrects how *confidence in the extraction itself* is measured relative to what the query needed — a narrower, more defensible fix than adjusting how contract evidence is weighted into authorization.

## Anticipated Outcome

This should recover most of the current `low_confidence_candidate` abstentions in bus and wisdm rewordings — specifically the sparse-confidence cases — while leaving the contract-score-driven WISDM query 5 style case for separate, more careful treatment. Precision and conservative authorization behavior should remain unchanged across the full cache benchmark suite.
