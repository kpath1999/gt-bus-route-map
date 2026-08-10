# Flash-Fusion Plan Reuse Design (Descriptor -> Cache -> Planner Fallback)

## Objective

Add a lightweight descriptor stage that enables plan reuse by skipping the full planner call on cache hits.

Required runtime flow:
1. Extract descriptor with lightweight LLM.
2. Compute cache key from descriptor + schema fingerprint.
3. Cache lookup for a stored plan skeleton.
4. On hit: adapt skeleton to a concrete plan and route directly to typed execution path.
5. On miss: run existing full planner path unchanged, then store validated output as a new skeleton.
6. Always run structural and schema validation before execution.

Fail-open guarantee:
- Any descriptor parse error, unsupported descriptor, cache read error, cache adapt error, or cache miss must continue to the existing planner path with unchanged behavior.

## Why cache belongs in a separate module

Recommendation: keep descriptor and cache separate.

Use:
- flashfusion/pipeline/plan_descriptor.py for descriptor extraction and validation.
- flashfusion/pipeline/plan_cache.py for cache keying, skeleton storage, lookup, and adaptation.

Rationale:
- Single responsibility: descriptor logic is LLM-facing; cache logic is data-structure and lifecycle logic.
- Lower risk in a large codebase: operators.py and stages.py are already dense and should not absorb cache complexity.
- Testability: cache behavior can be tested without mocking LLM calls; descriptor behavior can be tested with mocked LLM only.

## Revised module API

## flashfusion/pipeline/plan_descriptor.py

Public types:
- PlanDescriptor (Pydantic, extra=forbid)

Public functions:
- compact_schema_summary(meta_str: str) -> str
- extract_plan_descriptor(query: str, compact_schema: str, client: LLMClient) -> PlanDescriptor | None
- parse_plan_descriptor(payload: str | dict[str, object]) -> PlanDescriptor

Descriptor schema (fixed enums):
- version: "1"
- intent: "overall_compare" | "per_entity_compare" | "arg_extreme" | "threshold_count" | "correlation" | "predictive_holdout" | "unsupported"
- time_semantics: "none" | "duration_required" | "time_bin_required"
- terminal_shape: "scalar" | "entity_row" | "group_row" | "prediction"
- compare_mode: "none" | "difference" | "abs_difference" | "ratio"
- confidence: "high" | "low"

Behavior:
- Invalid JSON or enum mismatch returns None at extraction layer.
- Unsupported intent or low confidence is treated as non-cacheable.

## flashfusion/pipeline/plan_cache.py

Public types:
- PlanSkeleton (Pydantic, extra=forbid)

Public functions:
- descriptor_cache_key(descriptor: PlanDescriptor, schema_fingerprint: str, vocab_version: str, plan_version: str) -> str
- lookup_skeleton(key: str) -> PlanSkeleton | None
- adapt_skeleton_to_plan(skeleton: PlanSkeleton, descriptor: PlanDescriptor) -> dict[str, object]
- store_skeleton(key: str, skeleton: PlanSkeleton) -> None
- build_skeleton_from_validated_plan(plan: DeterministicPlan) -> PlanSkeleton

Notes:
- No network and no persistence requirement means in-memory cache for first implementation.
- Cache can be process-local module state with optional size cap.
- Cache write happens only after full planner output has passed structural + schema validation.

## Skeleton format (minimum)

Store the minimum needed to reconstruct a raw deterministic plan payload:
- version: "1"
- steps: list of operator payload templates
- metadata:
  - operators_used
  - source_plan_digest

Do not store DataFrame-specific resolved values outside plan steps.

## Explicit parameterization rule (slot vs constant)

This boundary is field-semantic and allowlist-driven, not value-pattern-driven.

Rule 1: only explicitly allowlisted fields may become slots.
Rule 2: every non-allowlisted field is copied as an immutable constant.
Rule 3: if a plan contains query-dependent literals outside the allowlist, that
plan is marked non-cacheable (fail-open to full planner path).

### Slot-eligible fields (v1)

Only these fields may be parameterized by build_skeleton_from_validated_plan:

- FILTER_COMPARE.value
- FILTER_IN.values[]
- SPLIT_BY_VALUES.values[]
- PARALLEL_AGGREGATE.branches[].filter_values[]
- PREDICTIVE_PIPELINE.filter_value

Everything else remains fixed in the skeleton.

### Constant-only fields (never slot in v1)

All enum/shape/operator-control fields are constants, including:

- op, aggregate, comparator, direction, operation, kind, epoch_unit, mode,
   threshold, holdout_row, model
- column names and group keys: column, left, right, result, group_by,
   return_columns, timestamp_column, feature_columns, target_column, sort_by,
   partitions, label, label_a, label_b, result_column
- numeric/time operator constants: DERIVE_BIN.width, DERIVE_BIN.freq,
   DERIVE_DURATION_SECONDS.fill_first, PREDICTIVE_PIPELINE.train_fraction

Important safety example:
- A percentile-like token in a column name (for example accel_stats_z_p99) is
   part of a column identifier and must remain constant.
- Any statistical literal encoded in a non-allowlisted field remains constant.

### Why this conservative boundary

The structural/schema gates can still pass a semantically wrong adapted plan.
So v1 prioritizes correctness over hit rate: only obvious query-value bindings
in filter operands are slotted.

### Cacheability gate

build_skeleton_from_validated_plan must emit one of:

- cacheable skeleton with explicit slots
- non-cacheable reason (and caller skips cache write)

Mark non-cacheable when either condition holds:

1. A query-varying literal appears in a non-allowlisted field.
2. A slot value cannot be represented as scalar or list[scalar] safely.

## Slot metadata contract (PlanSkeleton)

PlanSkeleton must carry explicit slot metadata, not implicit diffs:

- template_plan: raw plan payload where slotted fields are replaced by slot refs
- slots: list of slot specs
   - slot_id (stable, for example s0, s1)
   - op_index
   - field_path (for example steps[0].value, steps[1].values[2])
   - value_type: scalar | list_scalar
   - original_python_type: str | int | float | bool
- metadata
   - operators_used
   - source_plan_digest
   - non_cacheable_reason (optional, when not cacheable)

Adaptation rule:
- adapt_skeleton_to_plan only writes to declared slot field paths.
- No other field may be rewritten during adaptation.

## Runtime integration plan

Primary integration file:
- flashfusion/baselines/flash_fusion.py

Flow update inside run_flash_fusion:
1. Build meta_str and schema_fingerprint exactly as today.
2. Descriptor phase (new):
   - compact schema summary from existing meta_str
   - extract_plan_descriptor with stage label plan_descriptor
3. If descriptor is valid and cacheable:
   - compute cache key
   - lookup skeleton
4. On cache hit:
   - adapt skeleton to raw plan payload
   - run structural_validate(raw)
   - run validate_plan_against_dataframe(plan, df)
   - if both pass, run execute_plan(df, plan)
   - route as typed_operator path, skipping request_guardrail_and_plan
5. If cache hit path fails at any point:
   - mark cache miss/fail-open reason in debug/telemetry
   - continue to existing planner path
6. Existing planner path unchanged on miss/fail-open:
   - request_guardrail_and_plan
   - parse_guardrail_response
   - schema validation
   - execute typed plan or existing fallback behavior
7. After a successful full planner typed path:
   - build skeleton from validated plan
   - store only when build_skeleton_from_validated_plan reports cacheable
   - otherwise skip cache write and keep runtime behavior unchanged

Important invariant:
- operators.py gates remain authoritative. No execution occurs without structural + schema validation.

## File-by-file change plan

1. Add new file flashfusion/pipeline/plan_descriptor.py
- descriptor model
- compact schema summary helper
- lightweight LLM request and parse helpers

2. Add new file flashfusion/pipeline/plan_cache.py
- cache key builder
- in-memory cache store/lookup
- skeleton model
- skeleton build/adapt helpers

3. Update flashfusion/baselines/flash_fusion.py
- integrate descriptor -> cache lookup before full planner call
- add cache-hit typed path branch
- keep planner miss path unchanged
- store skeleton after validated full planner success

4. Update flashfusion/prompts/templates.py
- add lightweight descriptor prompt template only
- no changes to full planner prompt template content

5. Update flashfusion/tests/test_baselines.py
- add integration tests for cache-hit skip planner, cache-miss planner fallback, and fail-open behavior

6. Add new file flashfusion/tests/test_plan_descriptor.py
- unit tests for descriptor parsing/extraction with mocked LLM responses

7. Add new file flashfusion/tests/test_plan_cache.py
- pure unit tests for keying, lookup/store, adapt, and fail-open safety

## Required tests (mocked LLM responses where applicable)

Descriptor tests:
1. valid descriptor parses and is cacheable
2. malformed descriptor returns None
3. unsupported intent is non-cacheable
4. low confidence is non-cacheable
5. non-schema descriptor prompt section remains within token budget policy

Cache tests:
1. same descriptor + same schema fingerprint yields stable key
2. cache hit returns skeleton and adaptation yields a raw plan payload
3. cache miss returns None
4. adaptation failure triggers fail-open caller behavior contract

Baseline integration tests:
1. cache hit bypasses request_guardrail_and_plan and executes typed path
2. cache miss calls request_guardrail_and_plan unchanged
3. cache hit with invalid adapted plan falls through to full planner path
4. planner success on miss stores skeleton for subsequent hit

## Open decisions (implementation should lock these)

1. Cache scope:
- Default recommendation: process-local in-memory only.

2. Cache eviction:
- Minimal recommendation: fixed-size FIFO or simple max entries.

3. Telemetry fields in RunResult:
- Optional but useful: descriptor_used, cache_lookup, cache_hit, cache_key_prefix, plan_source value extension (for example: cache or llm).

4. Descriptor bindings for slots:
- Current descriptor schema is shape-only. To adapt slotted fields on cache hit,
   implementation must define where concrete binding values come from.
- Recommended v1: add optional descriptor.bindings with strict typed values
   only for slot-eligible fields; if bindings are missing/incomplete, fail-open
   to planner path.

## Non-goals (explicit)

- No change to typed operator vocabulary or semantics.
- No persistence layer.
- No embeddings, fuzzy matching, or vector retrieval.
- No network calls in tests.
- No planner prompt augmentation for descriptor hinting in this design.
