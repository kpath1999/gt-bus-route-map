# Task: Diagnose Q6/Q8 grounding failures in FLASH_FUSION_CACHE (wisdm)

## Context
`flashfusion/baselines/flash_fusion_cache.py` defines `GROUNDING_SYSTEM_PROMPT`,
which asks a light model to ground a fixed operator skeleton into a
Pydantic `DeterministicPlan`. Two cached wisdm queries pass semantic cache
matching but fail Pydantic validation at grounding time:

- **Q6** ("user whose resting duration exceeds dynamic duration by the
  largest margin"): light model emits
  `FILTER_COMPARE(comparator="max", value=null)` (invalid enum + null value)
  and leaves `PARALLEL_AGGREGATE` branch `filter_column` as `null` despite
  non-empty `filter_values`.
- **Q8** ("absolute difference between mean z-axis acceleration of Upstairs
  vs Downstairs"): light model emits two identical
  `SPLIT_BY_VALUES(..., values=["Upstairs","Downstairs"], label="activity")`
  steps, so `AGGREGATE_PARTITIONS.partitions=["activity"]` fails the
  minimum-length-2 validator.

## Your job
1. Open `flashfusion/pipeline/operators.py` and confirm the actual Pydantic
   field definitions/enums for `FILTER_COMPARE`, `PARALLEL_AGGREGATE`,
   `SPLIT_BY_VALUES`, and `AGGREGATE_PARTITIONS` (required fields, enum
   values, min/max list lengths). Report any mismatch between the schema
   and the proposed prompt-rule wording below.
2. Open `flashfusion/baselines/flash_fusion_cache.py` and locate:
   - The full `GROUNDING_SYSTEM_PROMPT` string.
   - Whatever assembles the "REQUIRED OUTPUT checklist" and
     "OPERATOR FIELD SPEC" sections referenced by the prompt (these are
     not in the excerpt already reviewed) — confirm they correctly list
     `filter_column` as a required, non-null field for these operators when
     `filter_values` is present, and confirm the field spec for
     `SPLIT_BY_VALUES`/`AGGREGATE_PARTITIONS` communicates the one-label-per-
     group and two-partition-minimum constraints.
   - The exact call site where `GROUNDING_SYSTEM_PROMPT` is populated with
     the per-query skeleton (to confirm the light model receives the
     REQUIRED OUTPUT checklist and OPERATOR FIELD SPEC as separate
     injected sections, not baked into this string).
3. Apply this minimal patch to `GROUNDING_SYSTEM_PROMPT` (add-only, no
   deletions) under "Semantic grounding rules":
   - Forbid `FILTER_COMPARE.comparator` values outside
     `{eq,ne,gt,gte,lt,lte}` and forbid null `value`; direct extremum
     selection to `RANK_ROWS` only.
   - Require `filter_column` to be non-null whenever `filter_values` is
     non-empty, in any operator/branch.
   - Map "A exceeds/greater than B by the largest margin" to a *signed*
     `DERIVE_BINARY(operation="subtract")` in stated order (not
     `abs_difference`), optionally gated by `FILTER_COMPARE(comparator="gt",
     value=0)`, before `RANK_ROWS`.
   - Require one `SPLIT_BY_VALUES` step per comparison group with a
     distinct `label`, and require `AGGREGATE_PARTITIONS.partitions` to
     list every distinct label produced (minimum two).
4. Here are commands you can use to verify Q6 and Q8 ground correctly; verify for both query versions v2 and v3:
    - python -m flashfusion.eval.trace_query --dataset wisdm --query-id 6 --cache --query-version v2 --semantic-cache-path flashfusion/eval/cache/semantic_registry_wisdm_v1.json
    python -m flashfusion.eval.trace_query --dataset wisdm --query-id 6 --cache --query-version v3 --semantic-cache-path flashfusion/eval/cache/semantic_registry_wisdm_v1.json
    - python -m flashfusion.eval.trace_query --dataset wisdm --query-id 8 --cache --query-version v2 --semantic-cache-path flashfusion/eval/cache/semantic_registry_wisdm_v1.json
    - python -m flashfusion.eval.trace_query --dataset wisdm --query-id 8 --cache --query-version v3 --semantic-cache-path flashfusion/eval/cache/semantic_registry_wisdm_v1.json
5. If step 4 still fails for Q6 or Q8, capture the new `LIGHT MODEL RAW
   OUTPUT` and `CACHE VERDICT` blocks verbatim and identify which added
   rule is being ignored — do not guess; quote the raw model output.