Scope: metrics only. Do not change planner semantics.

Edit:
- flashfusion/pipeline/runner.py
- flashfusion/baselines/flash_fusion.py
- flashfusion/tests/test_metrics.py
- flashfusion/eval/benchmark.py

Goals:
1. Extend RunResult with fields to record, per-query:
   - whether Flash-Fusion used the fast-path,
   - fast-path latency and token counts,
   - whether the full planner was used,
   - planner latency and token counts.

2. In baselines/flash_fusion.run_flash_fusion:
   - Time and record the attempt_fast_path_plan call.
   - Time and record the request_guardrail_and_plan call.
   - Set the new RunResult fields accordingly.
   - Do not change existing behavior: all fallbacks and validation gates must stay identical.

3. In eval/benchmark.py and viz helpers:
   - Ensure new RunResult fields are saved in CSV/JSONL.
   - Add a small summary of:
     - fraction of queries where ff_fast_path_used is True,
     - median/p95 latency for fast path vs planner,
     - average token counts for fast path vs planner.

4. Update tests in test_metrics.py so they can construct RunResult with new fields without breaking existing tests.

Constraints:
- No changes to ReAct baseline or other baselines.
- No changes to operators.py or stages.py.
- No network/LLM calls in tests.

---

Scope: eager-warm the planner prefix/suffix so Flash-Fusion does not pay extra latency on the first query.

Read:
- flashfusion/baselines/flash_fusion.py
- flashfusion/prompts/templates.py
- flashfusion/pipeline/runner.py

Goals:
1. Identify the function(s) that build:
   - the planner prefix (e.g., FLASH_FUSION_PLANNER_PREFIX),
   - the dynamic suffix template (PLANNER_DYNAMIC_SUFFIX_TEMPLATE),
   from schema/meta_str.

2. Add a small helper, e.g.:
   - flashfusion/baselines/flash_fusion.py: `def warm_flash_fusion_prefix(df, client) -> None`
   that:
   - builds `meta_str` from df,
   - initializes any cached prefix/suffix structures used inside request_guardrail_and_plan,
   - does NOT run a full LLM planning call or execute any plan.

3. In flashfusion/pipeline/runner.BaselineRunner.__init__ or where the DataFrame is first available,
   - call warm_flash_fusion_prefix(self.df, self.client) once
   - but only when the selected baseline mode is FLASH_FUSION.

Constraints:
- No change to the content of PLANNER_DYNAMIC_SUFFIX_TEMPLATE, only when it is built.
- No additional planner calls; if any LLM call is unavoidable, it must be a single, low-cost one without touching operators or execution.
- Tests:
   - Add or update a test in test_baselines.py to assert that, after constructing a BaselineRunner in FLASH_FUSION mode, the prefix/suffix cache is populated before the first query.