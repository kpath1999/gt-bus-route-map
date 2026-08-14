# Flash-Fusion Semantic Template Cache Plan (Integrated with Existing Light Grounding)

Status: Planning document only (no code changes)
Date: 2026-08-14
Scope: Add a semantic cache layer without replacing the existing exact-cache and typed-plan safety path.

## 0) Goal and Safety Principle

This plan extends the current cache design so paraphrases and value changes can reuse validated templates, while keeping the current safety model intact:

- Preserve exact-cache-first behavior.
- Preserve existing light-model grounding mechanism.
- Preserve existing typed validators as non-negotiable backstop.
- Treat abstention as safe and preferred when uncertain.

Primary optimization target:
- High precision on accepted cache hits.

Secondary target:
- Hit rate.

---

## 1) Current System and Additive Integration Point

## 1.1 What exists today

Existing exact cache flow in flashfusion/baselines/flash_fusion_cache.py:

1. Exact lookup by dataset + literal query text.
2. On hit, call _grounding_prompt(query, entry, df).
3. Call _invoke_light_for_plan(client, prompt, trace).
4. Validate grounded plan with:
- DeterministicPlan.model_validate(...)
- validate_plan_against_dataframe(...)
5. Execute typed plan deterministically.
6. On any failure, fallback to run_flash_fusion(...).

Observed runtime artifacts and metadata already include:
- plan_source
- deterministic_fallback_reason
- schema_fingerprint
- typed_plan_sha256
- operator_contract_hash
- stages_run

## 1.2 Required additive placement

New semantic layer sits between exact-cache miss and full planner fallback.

Flow order:

1. Exact cache check (unchanged, fast path).
2. If exact hit: run existing light grounding path directly (no semantic extraction).
3. If exact miss: run semantic retrieval and hard gates.
4. If semantic match accepted: run existing light grounding path.
5. If semantic path abstains/fails: full planner fallback.

This preserves current behavior when semantic layer is disabled.

---

## 2) Light-Model Grounding as Typed Parameter Binding

## 2.1 Mapping to binding semantics

Current mechanism already implements slot filling over a fixed skeleton:

- Cached operator_skeleton = logical skeleton with placeholders.
- _grounding_prompt(query, entry, df) = binding request for live query.
- _invoke_light_for_plan(...) = binding execution by light model.
- DeterministicPlan.model_validate(...) = structural and type validation.
- validate_plan_against_dataframe(...) = live schema compatibility validation.

No replacement is proposed. Semantic layer only changes how entry is selected.

## 2.2 Field classes in typed plan

(a) Structural fields: must match template exactly unless explicitly allowed
- op
- column
- comparator
- aggregate
- group_by
- direction
- return_columns
- join keys
- window semantics
- ordering semantics

(b) Bindable value slots (normal case)
- value in FILTER_COMPARE
- values in FILTER_IN
- literal thresholds/ranges
- duration/window numeric literals when encoded as value parameters
- list/set literal members

(c) Structural-but-parameterizable only with explicit template metadata and tests
- column as a slot (rare, template-authorized only)
- comparator as a slot (rare and high risk)
- aggregate as a slot (generally unsafe; usually disallowed)
- group_by/direction as slots (high risk; default disallowed)

Default policy remains strict: only literal value fields are bindable.

## 2.3 Why this is safe

The current cache path already enforces skeleton immutability in _parse_and_validate_cached_plan by comparing actual_skeleton with expected_skeleton before execution. The semantic layer reuses this exact guard.

---

## 3) Hard Gate Ordering Relative to Light Invocation

## 3.1 Stage ordering

Stage E0: Exact cache check (existing)
- If exact hit: go straight to existing grounding path.
- If miss: continue to semantic path.

Stage S0: Intent signature extraction (new)
- Extract IntentSignatureV1 from live query with schema context.
- If admissibility is out_of_scope, ambiguous, or unknown: ABSTAIN (no light call).

Stage S1: Candidate retrieval (new)
- Retrieve by dataset_key + schema_fingerprint bucket.
- Primary retrieval by canonical skeleton/signature key.
- Optional embedding shortlist for candidate generation only.

Stage S2: Hard compatibility gates (new, before light call)
- Dataset/schema compatibility.
- Operator topology identity.
- Aggregate identity.
- Predicate field identity.
- Comparator identity.
- Grouping/window/rank semantics identity.
- Output shape/contract identity.
- Planner/operator contract hash compatibility.

Stage S3: Light slot grounding (existing, reused)
- _grounding_prompt(live_query, matched_template_entry, df)
- _invoke_light_for_plan(client, prompt, trace)

Stage S4: Post-grounding validators (existing, reused)
- DeterministicPlan.model_validate(grounded_plan)
- validate_plan_against_dataframe(grounded_plan, df)

Stage S5: Decision
- ACCEPT only if S2 pass + S3 success + S4 pass.
- Else ABSTAIN and run full planner.

## 3.2 Safety value of this ordering

- S2 prevents spending a light-model call on incompatible templates.
- S2 prevents the model from attempting to silently "repair" structural mismatches.
- S4 catches residual model failures even when output JSON is well-formed.
- Combined S2 + S4 gives defense in depth.

---

## 4) Concrete Walkthroughs

## 4.1 Example A: Semantic hit (paraphrase + value change)

Template in semantic registry:
- skeleton:
  - FILTER_COMPARE(column=record_id, comparator=eq, value=@slot)
  - AGGREGATE_COLUMN(column=MLII, aggregate=min)
- slot schema:
  - slot_id=record_id_value, type=integer, bound to FILTER_COMPARE.value

Live query:
- For record 107, what is the smallest MLII measurement?

Flow:
1. Exact lookup misses.
2. S0 extraction returns in_scope, aggregate=min, field=MLII, predicate=record_id eq int, output=scalar.
3. S1 retrieves candidate template.
4. S2 hard gates pass.
5. S3 light grounding fills value=107.
6. S4 validators pass.
7. ACCEPT and typed execution.

Expected metadata:
- plan_source = semantic_cache_light_grounded
- execution_path remains typed_operator_cache-style typed execution path.

## 4.2 Example B: Adversarial near miss (aggregate mismatch)

Same cached template as above (aggregate=min).

Live query:
- What is the median MLII for record_id 101?

Flow:
1. Exact lookup misses.
2. S0 extraction returns aggregate=median.
3. S1 retrieves candidate with similar shape.
4. S2 gate fails at aggregate identity (min != median).
5. ABSTAIN immediately.
6. Full planner fallback runs.

Expected metadata:
- plan_source = llm on fallback path
- deterministic_fallback_reason includes aggregate mismatch gate reason.

No light-model grounding call is made for this candidate.

---

## 5) Minimal Interface Changes

## 5.1 _grounding_prompt(query, entry, df)

Change required:
- None in core behavior.

Only requirement:
- semantic registry entry must provide fields compatible with current prompt assembly (operator_skeleton and related template metadata).

## 5.2 _invoke_light_for_plan(client, prompt, trace)

Change required:
- None.

This function remains the parameter-binding executor.

## 5.3 Cache lookup orchestration in flash_fusion_cache.py

Current:
- exact_text_lookup -> entry or miss -> fallback.

Planned:
1. exact_text_lookup
2. if hit: existing path unchanged
3. if miss:
- extract signature
- retrieve semantic candidates
- run hard gates
- if matched entry: call existing grounding path with that entry
- else fallback to run_flash_fusion

New plan_source value:
- semantic_cache_light_grounded

## 5.4 Trace additions

Add semantic evidence field in cache trace payload:
- candidate_ids_considered
- hard_gate_results per candidate
- extraction_confidence
- abstention_reason

Keep existing provenance fields untouched:
- typed_plan_sha256
- schema_fingerprint
- operator_contract_hash

## 5.5 Cache learning from successful fallback

On planner success:
1. canonicalize validated typed_plan to semantic template record
2. derive slot schema from literal fields that are safe to bind
3. store signature + provenance + compatibility metadata

This does not change existing exact registry behavior.

---

## 6) Safety Boundaries for Light Model

The light model cannot be treated as planner or structure selector.

Rules:

1. Cannot change operator sequence.
- Any add/remove/reorder causes validation failure and abstention.

2. Cannot change structural semantics.
- column/comparator/aggregate/grouping/direction drift is rejected.

3. Can fill value slots only.
- Value filling is constrained by template slot schema and validators.

4. Must pass live schema validation.
- Even structurally valid JSON fails if schema-incompatible.

5. Incomplete grounding is not accepted.
- Missing required slot values causes validation failure and fallback.

---

## 7) Relationship Between IntentSignatureV1 and DeterministicPlan

- IntentSignatureV1 = matching-time representation.
- DeterministicPlan = executable representation.
- Light grounding bridges IntentSignatureV1-aligned template to executable grounded plan.

Validation timing:
- IntentSignatureV1 validated at S0.
- DeterministicPlan validated at S4.

Audit linkage recommendation:
- Add template_id and skeleton_hash into execution trace/result metadata (not necessarily inside DeterministicPlan schema).
- Optional: include these in typed_execution_certificate to map grounded plan back to source template deterministically.

---

## 8) Fallback and Safety Properties

The integrated design preserves the following:

1. Semantic cache disabled:
- Behavior remains exact-cache-first, then full planner fallback.

2. Semantic cache always abstains:
- Behavior remains safe and equivalent to existing miss fallback behavior.

3. Light model outputs invalid/garbage:
- Existing validators reject and fallback.

4. Gates too strict:
- More false negatives, no new false positives.

5. Gates too loose:
- Validators remain backstop, but valid-looking wrong bindings are still possible; mitigate via confidence calibration and abstain-first thresholds.

---

## 9) Latency Budget Mapping

Exact-cache hit (current fast path):
- semantic extraction cost: zero
- grounding cost: existing light call only

Semantic path after exact miss:
- S0 extraction: about 100 tokens (or zero for deterministic extractor)
- S2 hard gates: zero tokens
- S3 grounding: existing light call
- S4 validators: zero tokens

Approximate costs:
- Semantic hit total: extraction + grounding
- Semantic miss total: extraction + full planner

Tradeoff:
- Adds extraction overhead on exact-miss queries.
- Enables cache reuse across paraphrases and literal-value changes that exact cache cannot hit.

---

## 10) Updated Architecture Diagram (Exact-First + Reused Grounding)

+------------------+
| User Query       |
+---------+--------+
          |
          v
+------------------------------+
| Exact Cache Lookup (existing)|
+---------+--------------------+
          | hit                                 | miss
          v                                     v
+------------------------------+      +------------------------------+
| Light Grounding (EXISTING,  |      | Intent Extraction (new)      |
| REUSED)                      |      +---------------+--------------+
| _grounding_prompt            |                      |
| _invoke_light_for_plan       |                      v
+---------------+--------------+      +------------------------------+
                |                     | Candidate Retrieval (new)    |
                v                     +---------------+--------------+
+------------------------------+                      |
| Post-Ground Validators       |                      v
| (EXISTING, REUSED)           |      +------------------------------+
| DeterministicPlan.validate   |      | Hard Gates (new, pre-light)  |
| validate_plan_against_df     |      +-------+----------------------+
+-------+----------------------+              | pass        | fail
        | ACCEPT                              v             v
        v                          +------------------+   +------------------+
+------------------------------+   | Light Grounding  |   | ABSTAIN          |
| Typed Deterministic Execute  |   | (EXISTING,       |   | -> Full Planner  |
| + answer                     |   | REUSED)          |   | run_flash_fusion |
+------------------------------+   +--------+---------+   +------------------+
                                            |
                                            v
                                 +------------------------------+
                                 | Post-Ground Validators       |
                                 | (EXISTING, REUSED)           |
                                 +-------+----------------------+
                                         | pass        | fail
                                         v             v
                                    ACCEPT       ABSTAIN -> Full Planner

---

## 11) Updated Acceptance Policy Pseudocode

function run_cache_then_semantic(query, df, client, dataset):
    exact_entry = exact_lookup(query, dataset)
    if exact_entry is not None:
        return run_existing_grounding_and_validation(query, exact_entry, df, client,
                                                     plan_source="exact_query_cache_light_grounded")

    sig = extract_intent_signature(query, df_schema(df))
    if sig.admissibility in {"out_of_scope", "ambiguous", "unknown"}:
        return fallback_full_planner(reason=sig.admissibility)

    candidates = retrieve_semantic_candidates(sig, dataset, schema_fingerprint(df))
    for cand in rank_candidates(candidates):
        gates = evaluate_hard_gates(sig, cand)
        if not gates.all_pass:
            continue

        grounded = invoke_existing_light_grounding(query, cand, df, client)
        if grounded.failed:
            continue

        if not deterministic_plan_validate(grounded.plan):
            continue
        if not schema_validate_against_dataframe(grounded.plan, df):
            continue

        return accept_and_execute(grounded.plan,
                                  plan_source="semantic_cache_light_grounded",
                                  evidence={"gates": gates, "candidate": cand.id})

    return fallback_full_planner(reason="semantic_abstain_no_safe_candidate")

---

## 12) Diff-Style Summary for flash_fusion_cache.py

Minimal changes only:

1. Lookup path
- before: exact lookup -> ground -> validate -> execute -> fallback
- after: exact lookup -> (if miss) semantic lookup+gates -> ground -> validate -> execute -> fallback

2. New plan_source
- add semantic_cache_light_grounded

3. Trace extension
- add semantic_match_evidence (candidate IDs, gate outcomes, extraction confidence, abstention reason)

4. Reused unchanged functions
- _grounding_prompt
- _invoke_light_for_plan
- _parse_and_validate_cached_plan
- validate_plan_against_dataframe path

5. No safety downgrades
- fallback behavior unchanged
- validators remain mandatory

---

## 13) Test Plan (Extends Existing Patterns)

Reuse testing style from flashfusion/tests/test_flash_fusion_cache.py and add semantic scenarios.

New test groups:

1. Semantic paraphrase hit
- Exact miss, semantic match passes, light grounding succeeds, typed execution succeeds.
- Assert plan_source is semantic_cache_light_grounded.

2. Aggregate mismatch abstain
- Candidate retrieved but aggregate gate fails.
- Assert no light invocation and fallback called.

3. Comparator mismatch abstain
- Same topology, different comparator.
- Assert pre-light gate failure.

4. Schema fingerprint mismatch
- Candidate exists but schema fingerprint mismatch.
- Assert abstain and fallback.

5. Light grounding drift rejected
- Semantic candidate chosen, light model changes skeleton.
- Assert _parse_and_validate_cached_plan rejects and fallback runs.

6. Light grounding hallucinated column
- JSON valid but schema validation fails.
- Assert fallback runs.

7. Exact-hit short circuit preserved
- Literal exact match still bypasses semantic extraction.
- Assert latency path and stage markers match existing behavior.

8. Semantic evidence trace completeness
- Assert semantic_match_evidence fields are populated on semantic attempts.

---

## 14) Open Decisions for Maintainers

1. Should semantic registry be separate from exact registry or co-located with dual indices?
2. Should template_id/skeleton_hash be stored in RunResult fields or only in trace payload?
3. What precision floor should gate semantic acceptance in CI (for example, 99%+ accepted-hit precision)?
4. Should any structural fields be allowed as explicit slots in v1, or deferred until v2 after safety studies?
5. Should exact-cache out-of-scope shortcut remain exact-only in v1 or also support semantic out-of-scope templates?

---

## Final Constraint Check

This plan explicitly preserves:
- existing exact-cache fast path
- existing light grounding mechanism
- existing DeterministicPlan and schema validators
- additive semantic layer only between exact miss and full planner fallback
- abstain-first safety behavior
