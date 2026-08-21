## Root Cause Analysis for Bus and WISDM Low Confidence Abstentions

### Executive Summary

The common failure mode is not retrieval failure and not compatibility rejection.

The expected candidate is retrieved, marked compatible, and often scored as the top winner. The system still abstains as low_confidence_candidate because the confidence and final-score geometry are under-calibrated when extractor evidence is sparse.

This is a methodology issue in evidence aggregation, not a threshold issue.

### What the Benchmark Evidence Shows

1. In all reported bus abstentions, the winner is correct and compatible, with final_winner_is_expected true and winner final_score at 1.0.
2. In those same bus cases, abstention still happens because extractor confidence is below extractor_confidence_floor.
3. WISDM abstentions show the same pattern for query 4 and query 6: correct compatible winner, final_score 1.0, but extractor confidence below floor.
4. One WISDM case (query 5, v3) is different: winner is correct and compatible, but final_score is 0.5 because contract_score contributes zero while retrieval_score is strong. With fixed 0.5 retrieval weight, that path can never cross acceptance_floor.
5. No failures are due to expected_absent_from_retrieval_union, expected_retrieved_but_rejected_incompatible, or wrong_authorized_reuse.

### Why This Happens

There are two coupled mechanisms.

1. Sparse extraction confidence penalty.
For simple reworded queries, the extractor often only captures aggregate and sometimes one field, without predicate/filter/predictive evidence. Confidence stays at values like 0.35 or 0.45, below the floor, despite winner correctness and compatibility.

2. Fixed-weight scoring when contract dimensions are not applicable.
When extracted contract dimensions are empty or mostly non-applicable, contract_score is zero or low by construction. With fixed retrieval and contract weights (0.5 and 0.5), final_score can be artificially capped (for example 0.5), producing guaranteed abstention even when retrieval and compatibility are correct.

In short: the system treats missing extractable evidence as low confidence even when all available evidence is strongly consistent.

## Non-Threshold Mitigation Plan

### Design Goal

Improve recall to near-perfect while preserving safety and precision, without lowering thresholds and without introducing per-query hacks.

### Technique A: Evidence-Conditioned Confidence Promotion

Introduce a second-stage effective confidence that is computed after compatibility verification.

Promotion rule should activate only when all of the following are true:

1. Winner is compatibility-true and passes safety-critical agreement.
2. Winner is top in retrieval and has clear separation from runner-up (existing ambiguity guard still enforced).
3. All extracted applicable contract dimensions match winner strongly.
4. At least one independent retrieval signal is strong (dense or lexical top support).

When these conditions hold, treat confidence as verification-backed and promote effective confidence above the floor for authorization only.

Rationale:
This does not weaken safety gates. It reinterprets confidence using post-verification evidence that already exists in the pipeline.

Expected impact:
Eliminates the bus and WISDM abstentions where winner is already correct, compatible, and high-scoring but blocked only by pre-verification sparse extraction confidence.

### Technique B: Applicability-Aware Final Score Weighting

Make final score composition depend on contract applicability coverage.

Current issue:
Fixed weights force low scores when contract applicability is near zero.

Proposed weighting:

1. Compute applicable_contract_dimensions count from the same applicability logic used in component scoring.
2. If applicable count is zero, set effective weight as retrieval-only for ranking and floor checks.
3. If applicable count is small, interpolate weights smoothly toward retrieval (for example via coverage ratio), while still using contract when available.

Rationale:
Unknown or non-applicable contract dimensions should not act as implicit disagreement through weight dilution.

Expected impact:
Fixes WISDM query 5 style abstention where correct winner has high retrieval evidence but cannot pass due to contract inapplicability.

### Technique C: Preserve Existing Safety Guardrails

Keep unchanged:

1. Compatibility checks.
2. Safety-critical agreement checks.
3. Ambiguity margin gate.
4. Dataset/schema/operator contract constraints.

This ensures precision and false-positive control remain stable.

## Implementation Sketch

1. Add metadata from component scoring: applicable_dimension_count and matched_applicable_dimension_count.
2. Derive effective_contract_weight from applicability coverage.
3. Compute effective_final_score with adaptive weights.
4. Add effective_confidence computed from extractor confidence plus verification-backed evidence (only if strict promotion conditions hold).
5. Use effective_confidence and effective_final_score in authorization gates, but do not remove safety checks.

## Validation Plan

1. Re-run benchmark_hybrid_cache for bus and wisdm with identical thresholds.
2. Success criteria:
	- Recall near-perfect, ideally matching v1 baseline behavior for v2 and v3 rewordings.
	- Precision remains 1.0.
	- wrong_authorized_reuse stays at 0.
	- expected_retrieved_but_rejected_incompatible remains 0.
3. Re-run mit_ecg benchmark to ensure no regression.
4. Add ablation logs for:
	- promotions triggered count
	- promotions accepted count
	- promotions blocked by ambiguity or safety

## Why This Is Not a Threshold Hack

1. No threshold values are reduced.
2. Authorization still depends on strict compatibility and safety checks.
3. The change improves evidence interpretation and score normalization under sparse applicability, which is the true structural failure mode.

## Anticipated Outcome

This should recover most or all current low_confidence_candidate abstentions in bus and wisdm rewordings, while maintaining high precision and conservative authorization behavior across the full cache benchmark suite.
