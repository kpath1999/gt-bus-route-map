# Semantic Cache Matching: Gap Analysis and Improvement Plan

## Scope and Current Result

This report analyzes the deterministic semantic matcher in
`flashfusion/baselines/flash_fusion_cache.py`, its offline registry builder in
`flashfusion/eval/build_semantic_registry.py`, and the one-run v2/v3
evaluation in `results/cache_match_eval.json`.

The evaluation generated a v1 registry from the exact cache and matched each
lightly reworded v2/v3 query to a v1 template. Ground truth is the preserved
query ID. Both rewrite versions produced the same result:

| Dataset | Correct / total per version | Match rate |
| --- | ---: | ---: |
| WISDM | 6 / 16 | 37.5% |
| MIT ECG | 6 / 16 | 37.5% |
| Bus | 7 / 16 | 43.8% |
| Overall | 38 / 96 | 39.6% |

The extractor is deterministic, so one run is sufficient for this version of
the experiment. The low score is not primarily caused by the wording changes:
the signatures intentionally retain too little intent to distinguish related
templates, and `_find_semantic_entry` accepts the first candidate that passes
the gates. Registry insertion order therefore selects the cached skeleton.

## Failure Classification

There are 58 incorrect evaluations. Of these, 30 are false-positive semantic
hits and 28 are abstentions. The false positives are the immediate safety risk:
they send a reworded query to the light model with the wrong operator skeleton.
The abstentions currently fall back to full Flash-Fusion and are conservative,
though they reduce the cache-hit rate.

### Unsafe false-positive hits

`_find_semantic_entry` iterates candidates and returns as soon as
`_semantic_entry_matches` succeeds. The current signature contains only
aggregate, mentioned schema fields, predicate comparators, and a coarse output
shape. It omits operator intent, derived expressions, grouping/ranking intent,
model choice, and entity values.

Observed collisions in both v2 and v3 include:

| Dataset | Ground-truth IDs | Incorrect selected ID | Missing discriminator |
| --- | --- | --- | --- |
| WISDM | 1, 4 | 6 | Filter target/value, aggregate target, and rank-vs-scalar intent |
| WISDM | 13, 14, 16 | 15 | Predictive model identity |
| MIT ECG | 1 | 6 | `record_id` value and scalar-min target |
| MIT ECG | 2 | 7 | Duration/max-time intent versus annotated-beat windowing |
| MIT ECG | 4, 9, 12, 14-16 | 13 | Last-event/medical out-of-scope/model identity intent |
| Bus | 3, 6 | 7 | Output projection and derived percentile-difference intent |
| Bus | 13, 15, 16 | 14 | Predictive model identity |

The predictive cases make the defect particularly clear. Their shared fields,
schema, and `unknown` output shape all pass the gates, but logistic regression,
random forest, 1-NN, and histogram gradient boosting require distinct cached
plans. Returning the first compatible template is incorrect.

### Conservative abstentions

Several in-scope analytic requests are marked `admissibility_unknown` because
they lack a literal schema field or one of the small aggregate vocabulary:

- WISDM 5 (dynamic versus resting magnitude comparison), 11 (sex/cadence
	comparison), and 12 (future activity prediction).
- Bus 9 (occupancy versus roughness), 10 (weather/roughness), and 11
	(schedule compliance).
- MIT ECG 11 (family history) and 10 (BMI correlation, which reaches
	`semantic_gate_reject_all`).

These are appropriate full-planner fallbacks until a signature can establish a
unique compatible template. They should not be forced into a semantic hit.

The evaluation also scores all out-of-scope queries as incorrect because no
operator-skeleton template is selected. That conflates cache-template matching
with guardrail correctness. For example, WISDM 10 and Bus 12 are explicitly
identified as `admissibility_out_of_scope`; this is a correct rejection signal,
not an unsafe semantic match. Future reports must break out: correct template
hit, safe abstention, correct out-of-scope rejection, false-positive hit, and
false-negative in-scope abstention.

## Improvement Plan

### 1. Make ambiguity abstain before grounding

Change `_find_semantic_entry` to evaluate every compatible candidate rather
than return the first one. If more than one candidate passes hard gates, return
`None` with `semantic_ambiguous_candidates`, include all candidate IDs in the
trace, and fall back to the full planner.

Acceptance criteria:

- Reordering entries in a semantic registry cannot change a semantic hit.
- The WISDM, ECG, and Bus predictive-model collisions abstain until a unique
	model-aware signature is available.
- No query with multiple compatible templates invokes `cache_light_grounding`.

This is the first change because it removes incorrect skeleton reuse without
requiring any new extraction heuristic.

### 2. Expand the signature to represent operator-level intent

Replace the coarse `output_shape`-only signature with stable fields derived
from the query and cached operator skeleton. Add fields only where deterministic
extraction is reliable:

- `operator_skeleton`: require equality during semantic matching, not merely
	presence in the registry.
- `aggregation_target` and `projection_fields`: distinguish maximum `MLII`,
	timestamp-at-maximum, and a location projection.
- `filter_values`: capture explicit IDs such as `record_id=101` and
	`subject_id=15` in addition to the current `eq` comparator.
- `intent_flags`: deterministic booleans/enums for `rank`, `group_by`,
	`difference`, `derived_magnitude`, `time_bin`, `last_event`, and
	`correlation`.
- `predictive`: capture `model`, `target_column`, feature references,
	`sort_by`, `train_fraction`, and holdout position.

`build_semantic_registry.py` must create the same fields from v1 definitions;
the live extractor must extract the corresponding values from the reworded
query. A missing required field should reject that candidate, not behave as a
wildcard.

Acceptance criteria:

- The four predictive queries within each dataset no longer match one another.
- ECG query 1 cannot match query 6, and ECG query 2 cannot match query 7.
- Bus query 3 and query 6 cannot match query 7.
- A semantic hit’s extracted operator intent agrees with the selected entry.

### 3. Add a scoring layer only after hard uniqueness

Some valid paraphrases use a synonym or omit a nonessential literal field. For
these cases, score candidates after the hard safety gates rather than relying
on a first-pass Boolean match. Score exact matches on operator intent, target,
filters, and predictive fields more strongly than aggregate or broad field
overlap. Hit only when the best score meets a configured threshold and exceeds
the runner-up by a margin; otherwise abstain.

Do not use an embedding similarity threshold as the sole safety decision. The
light model should remain a slot filler after a unique skeleton is selected,
not the mechanism that chooses between incompatible skeletons.

Acceptance criteria:

- Trace evidence records each candidate score, winner, runner-up, threshold,
	and abstention reason.
- A tie or insufficient margin falls back without a light-model call.
- The score and winner are invariant to registry ordering.

### 4. Treat out-of-scope matching as a separate guarded class

Extend the semantic registry format to include reusable out-of-scope templates
or use a separate guarded out-of-scope classifier keyed by unavailable concept
families. Match an out-of-scope template only if it is unique and the reason
does not depend on a fabricated field. Continue to use the existing light-model
rejection-reason step after the class is selected.

Acceptance criteria:

- Evaluation counts recognized out-of-scope queries separately from skeleton
	template matches.
- Out-of-scope queries never fall through to an executable skeleton.
- Existing exact out-of-scope cache behavior remains unchanged.

### 5. Upgrade evaluation and tests before enabling broader reuse

Update `cache_match_eval.py` to report a confusion table with the five outcome
classes above, a false-positive rate among semantic hits, and per-query winner
and runner-up evidence. Keep the current query-ID correctness measure for
in-scope reusable templates.

Add focused tests in `flashfusion/tests/test_flash_fusion_cache.py` for:

- Ambiguous candidates abstain and do not invoke the light model.
- Registry order does not affect the result.
- Predictive model variants select only their own template.
- Explicit identifier values reject otherwise similar entries.
- Out-of-scope recognition is scored independently of skeleton matching.
- The v2/v3 regression corpus has no false-positive semantic hits, even if its
	hit rate is initially lower due to safe abstention.

## Proposed Rollout Metric

Do not optimize the current 39.6% query-ID accuracy directly. The first
release gate should be zero false-positive semantic hits on the v2/v3 corpus;
abstentions are acceptable because they execute the normal full-planner
fallback. Once that is met, add discriminative signature fields and raise the
unique semantic-hit rate while maintaining a zero false-positive rate.
