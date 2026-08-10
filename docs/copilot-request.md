Implement a "Static Fast-Path Semantic Router" to bypass the full planner for 5 strict, highly common query templates. 

Please read:
1. `flashfusion/baselines/flash_fusion.py`
2. `flashfusion/pipeline/operators.py`
3. `flashfusion/prompts/templates.py`

### Goal
We want to use a lightweight LLM call to attempt to zero-shot a plan using only 5 allowed skeletons. If the lightweight model is not 100% confident, or if the query requires operators outside these exact skeletons (like derivations, temporal binning, etc.), it must fall back. 

### Step 1: Add the Fast-Path Prompt to `templates.py`
Create a new constant `FAST_PATH_PLANNER_TEMPLATE`. The prompt must be extremely strict. It should read:
"""
You are a highly conservative fast-path query router. 
Schema: {meta_str}
Query: {query}

Your job is to map the query to one of the 5 EXACT plan skeletons below. You may NOT invent operators, change the sequence, or add derivation steps (no DeriveVectorMagnitude, no DeriveBin). 
If the query requires ANY computation not perfectly described by these skeletons, or if you are not 100% confident, output exactly: {{"fallback": true}}

Allowed Skeletons:
1. Simple Aggregate: [AggregateColumn]
2. Filtered Aggregate: [FilterIn(1 or 2 times)] -> [AggregateColumn]
3. Filtered Count: [FilterCompare] -> [CountRows] OR [FilterIn] -> [CountDistinct]
4. Partition Compare: [SplitByValues (x2)] -> [AggregatePartitions] -> [ComparePartitions]
5. Group & Rank: [GroupAggregate] -> [AggregateGroups] -> [RankGroups]

If confident, output valid JSON matching the DeterministicPlan schema: {{"version": "1", "steps": [{{...}}]}}. 
Ensure exact argument setup for each operator (e.g., provide `column`, `function`, `result_column` for AggregateColumn).
Output ONLY JSON.
"""

### Step 2: Create the fast path function in `flash_fusion.py`
Add `def attempt_fast_path_plan(query: str, meta_str: str, client) -> dict | None:`
- Use `FAST_PATH_PLANNER_TEMPLATE`.
- Call `client.invoke_json(..., model=...)` (Assume the caller will configure the lightweight model in the client).
- If the response contains `"fallback": True` or fails to parse, return `None`.

### Step 3: Integrate into `run_flash_fusion`
Inside `run_flash_fusion` (before `request_guardrail_and_plan`), add the fast-path check:
1. Call `attempt_fast_path_plan`.
2. If it returns a raw plan dictionary:
   - Wrap it in a `try/except Exception` block.
   - Run `validated = structural_validate(raw_plan)`.
   - Run `validate_plan_against_dataframe(validated, df)`.
   - If both pass, `return execute_plan(df, validated)` immediately.
3. If `attempt_fast_path_plan` returns `None` OR if the `try/except` block catches ANY validation/execution error, silently fall through to the existing `request_guardrail_and_plan` logic.

### Constraints for this implementation:
- Look up the exact arguments required for `FilterIn`, `AggregateColumn`, `FilterCompare`, `CountRows`, `CountDistinct`, `SplitByValues`, `AggregatePartitions`, `ComparePartitions`, `GroupAggregate`, `AggregateGroups`, and `RankGroups` in `operators.py` and ensure the prompt description or your implementation respects them.
- Do NOT modify the existing fallback/slow-path logic.
- Do NOT add any caching state or memory structures.
- Prioritize fail-open safety: if the fast path fails structurally, semantically, or execution-wise, it must seamlessly delegate to the full planner without crashing the run.

Output the code changes file-by-file.