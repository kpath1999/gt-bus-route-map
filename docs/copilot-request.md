You are extending the Flash-Fusion semantic template-cache design plan
(not implementing code yet) to integrate the existing light-model slot
grounding mechanism from flash_fusion_cache.py with the new semantic
cache lookup and match layer.

═══════════════════════════════════════════════════════════════════
REPOSITORY CONTEXT — what already exists and must be reused
═══════════════════════════════════════════════════════════════════

Read these files before planning:
  1. flashfusion/baselines/flash_fusion_cache.py
  2. flashfusion/baselines/flash_fusion.py
  3. flashfusion/pipeline/runner.py
  4. flashfusion/pipeline/operators.py
  5. flashfusion/pipeline/executor.py
  6. flashfusion/pipeline/build_operator_skeleton_cache.py
  7. flashfusion/eval/trace_query.py
  8. flashfusion/tests/test_flash_fusion_cache.py
  9. flashfusion/scripts/key_extraction_match.py (if it exists)

EXISTING EXACT-CACHE FLOW (from flash_fusion_cache.py):

  query_text
    → exact text match against cache_registry.json (keyed by
      dataset + literal query_text hash via SkeletonEntry.cache_key())
    → HIT:
        _grounding_prompt(query, entry, df)
          Builds a prompt containing:
            - the cached operator_skeleton (fixed step sequence)
            - the live query text
            - schema metadata (column names, dtypes, sample values)
          Asks the light model (client.light) to produce a
          DeterministicPlan JSON with concrete values filled in.
        _invoke_light_for_plan(client, prompt, trace)
          Calls client.light, parses JSON output.
        DeterministicPlan.model_validate(raw_plan)
          Pydantic structural validation of the filled plan.
        validate_plan_against_dataframe(plan, df)
          Schema-level validation: columns exist, dtypes match,
          comparators are valid for the column type.
        If both pass → typed execution (deterministic).
        If either fails → fallback to run_flash_fusion (full planner).
    → MISS:
        run_flash_fusion(query, df, client, ...)
        Full S1→S2→S3→guardrail→agent pipeline.

KEY OBSERVATIONS FROM THE CODEBASE:

  a. The light model ALREADY performs slot binding:
     Given a cached skeleton like:
       step 1: FILTER_COMPARE(column=record_id, comparator=eq, value=?)
       step 2: AGGREGATE_COLUMN(column=MLII, aggregate=min)
     ...and a live query "What is the minimum MLII for record 107?",
     it fills value=107 into the existing step structure.
     The skeleton (operator sequence, columns, comparators, aggregates)
     is immutable; only literal values are grounded.

  b. operators.py explicitly states:
     "Cache grounding never chooses operators — the skeleton is
      already fixed — so all that [vocabulary] prose is dropped."
     This means the grounding prompt is lighter than the full planner
     prompt: it only needs to resolve values, not select operators.

  c. The typed_plan JSON already has a natural slot/structure split:
       "value" fields        → bindable parameters (integers, floats,
                                strings, lists)
       "column" fields        → structural (field names)
       "comparator" fields    → structural (eq, gt, lt, etc.)
       "aggregate" fields     → structural (min, max, mean, etc.)
       "group_by" fields      → structural
       "direction" fields     → structural (max, min for ranking)
       "op" fields            → structural (operator name)
     The existing Pydantic validators already enforce that structural
     fields match the cached skeleton and that value fields are
     type-compatible with their column.

  d. The trace record (from raw_results.jsonl) shows:
     - plan_source: "exact_query_cache_light_grounded" on hit
     - plan_source: "llm" on planner fallback
     - stages_run: ["exact_cache_hit", "cache_light_grounding",
                    "cache_plan_validated", "typed_exec"]
     - deterministic_fallback_reason: "cache: exact_query_miss" on miss
     - schema_fingerprint, typed_plan_sha256, operator_contract_hash

  e. The grounding prompt (from _grounding_prompt) includes:
     - The cached operator skeleton (step-by-step op descriptions)
     - The live query text
     - Schema metadata string (column names, dtypes, value samples,
       notes about empty strings, etc.)
     - Instructions to fill in concrete values only, not change
       operators or columns

═══════════════════════════════════════════════════════════════════
INTEGRATION OBJECTIVE
═══════════════════════════════════════════════════════════════════

The current exact-cache path requires literal query text match.
The new semantic cache layer should:

  1. Extract an intent signature from the live query (Architecture A
     from the plan: small structured extractor or schema-grounded
     extraction).
  2. Retrieve candidate templates from a semantic registry (not
     exact-text registry).
  3. Run hard compatibility gates (from the plan's Section 4) to
     verify the candidate skeleton is structurally compatible.
  4. IF gates pass: feed the matched template skeleton to the EXISTING
     _grounding_prompt / _invoke_light_for_plan mechanism, which
     already binds literal values into a fixed skeleton.
  5. The light model output then passes through the EXISTING
     DeterministicPlan.model_validate and
     validate_plan_against_dataframe validators.
  6. If all validators pass → ACCEPT (typed execution).
  7. If any gate, grounding, or validator fails → ABSTAIN → full
     planner fallback.

The critical insight: the light model slot binding IS the parameter
binding validation step from the plan's Section 4.3. The existing
Pydantic + schema validators are the hard-gate enforcement. The
new layer only adds:
  - semantic candidate retrieval (replacing exact text match)
  - structural compatibility pre-checks (before invoking the light
    model, to avoid wasting a light-model call on an incompatible
    skeleton)
  - risk/confidence scoring (from extraction confidence)

═══════════════════════════════════════════════════════════════════
WHAT TO PLAN
═══════════════════════════════════════════════════════════════════

Produce a design plan (no code changes) covering:

1. LIGHT-MODEL GROUNDING AS PARAMETER BINDING

   Explain precisely how the existing _grounding_prompt +
   _invoke_light_for_plan mechanism maps to the plan's
   "typed slot binding" concept:

   - The cached operator skeleton = the logical skeleton with
     parameter placeholders.
   - _grounding_prompt(query, entry, df) = the binding request,
     where entry now comes from the semantic registry instead of
     exact-text registry.
   - _invoke_light_for_plan = the binding execution (light model
     fills value fields).
   - DeterministicPlan.model_validate = structural type validation
     of bound values (Pydantic enforces value types match column
     dtypes, comparators are valid, etc.).
   - validate_plan_against_dataframe = schema-level validation
     (columns exist in the live dataframe, value types are
     compatible with actual column dtypes).

   Specify which fields in the typed_plan JSON are:
   (a) structural and must match the cached template exactly
       (op, column, comparator, aggregate, group_by, direction,
        return_columns, etc.)
   (b) bindable value slots that the light model fills
       (value fields in FILTER_COMPARE, width in DERIVE_BIN,
        threshold values, etc.)
   (c) structural-but-parameterizable only if the template
       explicitly marks them as slots and tests prove safety
       (e.g., a template might mark "column" as slot if the
        query shape is "max of any single column" — but this
        requires explicit template metadata)

2. WHERE HARD GATES SIT RELATIVE TO LIGHT-MODEL INVOCATION

   Define the ordering:

   Stage 0: Intent signature extraction (Architecture A)
     - Extract structured signature from live query.
     - If admissibility is out_of_scope/ambiguous/unknown → ABSTAIN
       immediately (no light model call, no candidate retrieval).
     - This is the ~100-token extraction step from the pipeline
       diagram.

   Stage 1: Candidate retrieval
     - Retrieve by dataset_key + schema_fingerprint bucket.
     - Primary: exact canonical skeleton hash match.
     - Optional secondary: embedding shortlist top-k (candidate
       generation only, never acceptance).

   Stage 2: Hard compatibility gates (BEFORE light model)
     - Schema/dataset fingerprint compatibility.
     - Operator topology / logical skeleton identity (the step
       sequence of ops must match — e.g., [FILTER_COMPARE,
       AGGREGATE_COLUMN] matches [FILTER_COMPARE,
       AGGREGATE_COLUMN] but not [FILTER_COMPARE, COUNT_ROWS]).
     - Aggregate function identity (min vs mean vs median — these
       are structural and must match the template, not be re-bound).
     - Group-by key identity.
     - Predicate field identity (column names must match the
       template, not be re-bound, unless explicitly marked as
       parameterizable).
     - Comparator identity (eq must match eq, gt must match gt;
       these are structural).
     - Output contract / shape identity.
     - Planner/operator contract hash compatibility.
     These gates run WITHOUT invoking the light model. They
     compare the extracted signature against the candidate
     template's stored signature. Their purpose is to reject
     incompatible templates cheaply before spending a light-model
     call.

   Stage 3: Light-model slot grounding (EXISTING mechanism, reused)
     - _grounding_prompt(live_query, matched_template_entry, df)
     - _invoke_light_for_plan(client, prompt, trace)
     - The light model fills in value slots from the live query
       into the fixed template skeleton.
     - This IS the parameter binding step from the plan.

   Stage 4: Post-grounding validation (EXISTING, reused)
     - DeterministicPlan.model_validate(grounded_plan)
     - validate_plan_against_dataframe(grounded_plan, df)
     - These are the existing backstop validators. They catch:
       - Light model hallucinated a column not in the schema.
       - Light model changed an operator or structural field.
       - Light model produced a value of the wrong type.
       - Light model filled a value that is incompatible with
         the comparator (e.g., string value for gt on a numeric
         column).

   Stage 5: Decision
     - If all gates (Stage 2) pass AND grounding (Stage 3)
       succeeds AND validators (Stage 4) pass → ACCEPT.
     - Else → ABSTAIN → run_flash_fusion (full planner).

   Explain why this ordering is safe:
   - Hard gates (Stage 2) prevent invoking the light model on
     a structurally incompatible skeleton (saving tokens and
     preventing the light model from "fixing" an incompatible
     template by silently changing its structure).
   - The existing validators (Stage 4) catch any residual
     light-model errors (hallucinated columns, wrong types,
     structural drift).
   - The combination of Stage 2 + Stage 4 provides defense in
     depth: even if the light model produces valid-looking JSON,
     it must still pass schema validation against the live
     dataframe.

3. CONCRETE WALKTHROUGH

   Walk through two examples end-to-end:

   Example A — Semantic HIT (paraphrase + value change):

     Cached template (from registry, originally built from
     "What is the minimum MLII value recorded for record_id 101?"):
       typed_plan:
         step 1: FILTER_COMPARE(column=record_id, comparator=eq, value=?)
         step 2: AGGREGATE_COLUMN(column=MLII, aggregate=min)
       logical_skeleton hash: FILTER_COMPARE+AGGREGATE_COLUMN:min:MLII
       slot_schema: [{slot_id: record_id_value, type: integer,
                      column: record_id, comparator: eq}]

     Live query: "For record 107, what is the smallest MLII measurement?"

     Stage 0: Extract signature:
       aggregate=MIN, field=MLII, predicate=record_id eq <int>,
       output=scalar
       admissibility=in_scope
     Stage 1: Retrieve candidate → template above (matched by
       skeleton hash).
     Stage 2: Hard gates:
       - schema_fingerprint matches ✓
       - operator topology [FILTER_COMPARE, AGGREGATE_COLUMN] ✓
       - aggregate=min ✓
       - field=MLII ✓
       - comparator=eq ✓
       - output=scalar ✓
     Stage 3: Light model grounding:
       _grounding_prompt("For record 107...", template_entry, df)
       → light model produces:
         step 1: FILTER_COMPARE(column=record_id, comparator=eq,
                                 value=107)
         step 2: AGGREGATE_COLUMN(column=MLII, aggregate=min)
     Stage 4: Validators:
       DeterministicPlan.model_validate → passes (value=107 is int,
         column=record_id exists, comparator=eq is valid for int)
       validate_plan_against_dataframe → passes (record_id column
         exists in df, MLII column exists in df)
     Stage 5: ACCEPT → typed execution.
       plan_source: "semantic_cache_light_grounded"
       (new plan_source value to distinguish from exact-cache hit)

   Example B — Adversarial near-miss (different aggregate):

     Cached template (same as above: MIN of MLII filtered by record_id).

     Live query: "What is the median MLII for record_id 101?"

     Stage 0: Extract signature:
       aggregate=MEDIAN, field=MLII, predicate=record_id eq <int>,
       output=scalar
       admissibility=in_scope
     Stage 1: Retrieve candidate → template above (retrieved because
       operator topology [FILTER_COMPARE, AGGREGATE_COLUMN] matches
       and field/column match — the embedding or skeleton hash may
       surface it as a candidate).
     Stage 2: Hard gates:
       - schema_fingerprint matches ✓
       - operator topology matches ✓
       - aggregate: template=min, live=median → GATE FAILS ✗
     → ABSTAIN → run_flash_fusion (full planner)
       plan_source: "llm"
       abstention_reason: "aggregate_mismatch: template=min, live=median"

     The light model is never invoked. The gate prevents the
     expensive and dangerous case where the light model might
     "helpfully" fill in median into a template that specifies min,
     or vice versa.

4. MODIFICATIONS TO EXISTING INTERFACES

   Specify minimal changes to flash_fusion_cache.py:

   a. _grounding_prompt(query, entry, df):
      - Currently receives an entry from the exact-text registry.
      - Should also accept an entry from the semantic registry,
        which has the same operator_skeleton and field_level_skeleton
        fields but is keyed by canonical skeleton hash instead of
        query text hash.
      - No change to the prompt construction itself — the light model
        doesn't care whether the skeleton came from exact text match
        or semantic match. It just sees the skeleton + query + schema.

   b. _invoke_light_for_plan(client, prompt, trace):
      - No change. This is a pure function: prompt in, plan JSON out.

   c. Cache lookup flow (the main function in flash_fusion_cache.py):
      - Current: exact_text_lookup(query, dataset) → entry or None
      - New: semantic_lookup(signature, dataset, schema_fp) →
               candidate list → hard gates → matched_entry or None
      - If semantic_lookup returns a matched_entry → proceed to
        _grounding_prompt with matched_entry (same as before).
      - If semantic_lookup returns None → proceed to run_flash_fusion
        (same as current MISS path).
      - Add a new plan_source value:
        "semantic_cache_light_grounded" to distinguish from
        "exact_query_cache_light_grounded".

   d. Trace record additions:
      - Add semantic_match_evidence field to the trace, including:
        candidate_ids_considered, hard_gate_results (pass/fail per
        gate), extraction_confidence, abstention_reason.
      - Keep existing fields (typed_plan_sha256, schema_fingerprint,
        operator_contract_hash) for the matched template.

   e. Cache learning (on planner fallback success):
      - After run_flash_fusion succeeds and produces a validated
        typed_plan, canonicalize it into a semantic registry entry
        (extract skeleton hash, slot schema, provenance).
      - This is the same _grounding_prompt input format — just
        stored under a canonical key instead of query text.

5. WHAT THE LIGHT MODEL CANNOT DO (safety boundaries)

   Explicitly document:

   - The light model CANNOT change the operator sequence. If the
     template says [FILTER_COMPARE, AGGREGATE_COLUMN], the grounded
     plan must have exactly those two steps in that order. If the
     light model adds, removes, or reorders steps, the
     DeterministicPlan validator catches this (the cached skeleton
     is compared structally).

   - The light model CANNOT change structural fields (column,
     comparator, aggregate, group_by, direction). If it does, the
     validator catches the mismatch against the cached skeleton.

   - The light model CAN ONLY fill in value slots. This is enforced
     by comparing the grounded plan's structural fields against the
     cached template's structural fields.

   - The light model's output is ALWAYS validated against the live
     dataframe schema. Even if it produces a plan that is structurally
     valid, if it references a column that doesn't exist in the live
     df, validate_plan_against_dataframe rejects it.

   - If the light model fails to ground a value (e.g., the query
     doesn't mention a record_id value), the plan is incomplete and
     validation fails → ABSTAIN.

6. RELATIONSHIP TO THE PLAN'S PYDANTIC MODELS

   Explain how the existing DeterministicPlan Pydantic model relates
   to the plan's proposed IntentSignatureV1:

   - DeterministicPlan is the EXECUTABLE representation (what the
     executor runs). It has concrete values filled in.
   - IntentSignatureV1 is the MATCHING representation (what the
     semantic cache uses for retrieval and gating). It has
     placeholders where values will be.
   - The light model bridges the two: given an IntentSignatureV1
     (with placeholders) + live query + schema, it produces a
     DeterministicPlan (with concrete values).
   - Both are validated by Pydantic, but at different stages:
     IntentSignatureV1 is validated after extraction (Stage 0),
     DeterministicPlan is validated after grounding (Stage 4).

   Specify whether the existing DeterministicPlan model needs
   augmentation (e.g., a `template_id` or `skeleton_hash` field)
   to link a grounded plan back to its source template for
   auditability.

7. FALLBACK AND SAFETY PROPERTIES

   Confirm these properties hold in the integrated design:

   - If the semantic cache is disabled → system behaves exactly like
     the current FLASH_FUSION_CACHE (exact text match or full planner).
   - If the semantic cache always abstains → system behaves exactly
     like current behavior (all queries go to full planner).
   - If the light model produces garbage → existing validators catch
     it and fall back to full planner.
   - If the hard gates are too strict → false negatives increase but
     no false positives are introduced (abstention is always safe).
   - If the hard gates are too loose → the light model + validators
     are the backstop, but there is residual risk if the light model
     produces a valid-but-wrong plan (e.g., grounds value=107 when
     the query asked about value=207). This risk is mitigated by
     extraction confidence scoring and calibration.

8. LATENCY BUDGET

   The pipeline diagram specifies ~100 tokens for key extraction and
   ~100 tokens for parameter binding. Map these to the integrated flow:

   - Stage 0 (intent extraction): ~100 tokens if using a small
     structured LLM extractor. Could be 0 tokens if using a
     deterministic extractor (regex-free, schema-grounded).
   - Stage 2 (hard gates): 0 tokens (deterministic comparison).
   - Stage 3 (light model grounding): ~100 tokens (existing
     _invoke_light_for_plan budget — this is already measured in
     the benchmark results, e.g., 635-968 input tokens for the
     grounding prompt, 26-165 output tokens).
   - Stage 4 (validation): 0 tokens (deterministic Pydantic +
     schema check).
   - Total on HIT: ~200 tokens (extraction + grounding).
   - Total on MISS: ~100 tokens (extraction) + full planner cost.
   - Compare to current exact-cache HIT: ~100 tokens (grounding only,
     no extraction needed because text match is free).

   Explain the tradeoff: the semantic cache adds ~100 tokens of
   extraction cost on every query (even hits) but unlocks hits for
   paraphrases and value changes that the exact-cache misses entirely.
   The exact-cache path remains as a fast first check: if the query
   text matches exactly, skip extraction and go straight to grounding.

9. DELIVERABLES

   End with:
   - An updated ASCII architecture diagram showing the integrated flow
     with the light-model grounding step clearly marked as "EXISTING,
     REUSED."
   - Updated acceptance policy pseudocode that includes the light-model
     grounding step between hard gates and validators.
   - A diff-style summary of what changes in flash_fusion_cache.py
     (minimal: lookup function + plan_source value + trace fields).
   - A test plan that reuses existing test_flash_fusion_cache.py
     patterns but adds semantic-match scenarios.

CONSTRAINTS
- Do not propose replacing _grounding_prompt or _invoke_light_for_plan.
  They are the existing, tested, validated slot-binding mechanism.
- Do not propose removing DeterministicPlan.model_validate or
  validate_plan_against_dataframe. They are the existing backstop.
- Do not propose any change that would make the system less safe than
  the current exact-cache + full-planner fallback.
- The semantic cache layer must be additive: it sits between the
  exact-cache check and the full-planner fallback.
- Preserve the existing exact-cache path as a fast first check.
  If query text matches exactly, skip semantic extraction and go
  straight to grounding (current behavior, zero added latency).