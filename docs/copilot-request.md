# Copilot task: harden Flash-Fusion cache grounding

You are working in the `kpath1999/flash-fusio/flashfusion` repo. Improve how **FLASH_FUSION_CACHE** grounds values into fixed typed-operator skeletons, then **sanity-check every proposed change** against the existing smoke failures under:

`flashfusion/results/ff_and_react_qwen/smoke/FLASH_FUSION_CACHE/`

## Primary files

- `flashfusion/baselines/flash_fusion_cache.py` — especially `GROUNDING_SYSTEM_PROMPT` and the human prompt / grounding path that fills skeleton slots
- `flashfusion/pipeline/operators.py` — operator field schemas (`FILTER_COMPARE`, `GROUP_AGGREGATE`, `COMPARE`, predictive pipeline / `holdout_row`, etc.) and execution (`_run_predictive`, etc.)
- Smoke artifacts per dataset (`wisdm/`, `bus/`, `mit_ecg/`): `raw_results.jsonl`, `metrics.csv`, `report.md`, and any judge outputs under `ground_truth_llm_judge/`

## Goal

Cache hits currently re-ground a **skeleton** (operator sequence) with a light model. The typed executor then runs whatever was grounded. Real failures are mostly **semantic slot errors** (wrong comparator, wrong aggregation, wrong metric/compare mode, wrong holdout row)—not executor bugs and not always “missing filters.”

Fix grounding so filled plans match query intent. Separately, fix **code provenance / judge inputs** so multi-step plans are not scored as if only the final expression ran.

Do **not** paper over bad plans by relaxing the judge. Do **not** treat every FAIL as a grounding bug—some FAILs are correct executions with incomplete reported code.

---

## Observed failure modes

Use these as regression targets. Map each to the correct layer: grounding prompt, skeleton source, provenance emission, executor, or judge.

### A. Real grounding / semantic-slot failures (fix in `GROUNDING_SYSTEM_PROMPT` and related path)

#### 1. Wrong aggregation for “number of samples”

- **Q:** “Which user has the highest total number of recorded data samples?”
- **Bad plan:**
  ```text
  GROUP_AGGREGATE: groupby('subject_id')['z'].sum()
  RANK: idxmax()
  → {'subject_id': 20, 'sum_z': 173480.728509492}
  ```
- **Need:** count rows per subject (or equivalent sample count), then argmax—not `SUM(z)`.
- **Note:** Subject id can match ground truth while the reported statistic is wrong. This is a true semantic fail.

#### 2. Wrong filter comparator / value

- **Q:** “For record_id 106, how many samples have MLII > 0?”
- **Bad plan:**
  ```text
  FILTER_COMPARE: record_id gt 105
  FILTER_COMPARE: MLII gt 0
  COUNT_ROWS: len(df)
  → 3590912
  ```
- **Need:** `record_id eq 106` (entity/key mention → equality), then `MLII > 0`, then count.
- **Root cause:** grounding chooses inequality from a nearby number instead of equality to the named entity.

#### 3. Predictive holdout row confused with train split

- **ECG Qs 13–15** ask for the **first row in the holdout set** after an 80/20 chronological split.
- **Bad answers** say **“last holdout row”** (and/or select the last row) even when the predicted label matches.
- **`train_fraction` and `holdout_row` are independent:**
  - `train_fraction` ← “first 80%” / split language
  - `holdout_row` ← only the phrase describing which holdout row to predict
  - If the query says “first row in the holdout set”, `holdout_row` **must** be `"first"`.

Mandatory rule text to include in `GROUNDING_SYSTEM_PROMPT` (or equivalent):

> The query may mention both a training split (e.g., 'first 80%') and a holdout row position (e.g., 'first row in the holdout set'). These are independent. `train_fraction` controls the split; `holdout_row` must reflect only the phrase that describes which holdout row to predict. If the query says 'first row in the holdout set', `holdout_row` must be 'first'.

#### 4. Wrong statistic / compare mode (bus roughness)

- **Q:** northern vs southern half roughness via **average** acceleration variance
- **Bad:** variance-of-column and/or `mode='ratio'` yielding wrong metric values (e.g. north≈0.847, south≈0.009 vs GT means ≈0.37 / 0.16)
- **Need:** means of `accel_variance` on each half; report the comparison the question asks for (which side is rougher; difference when requested). Ratio alone is not a substitute when the task is about average variance and magnitude gap.

---

### B. Correct grounding + execution, but FAIL due to incomplete provenance / judge input (do NOT “fix” by adding redundant filters)

These cases **already filter correctly**. The logged/generated code often shows only the **last** expression, so the judge claims filters are missing.

#### 5. Walking users — filters present; provenance incomplete

**Q:** “How many users have the activity label walking?”

**Actual successful typed execution (do not misdiagnose as dropped predicate):**

```text
Cache hit: exact query text; light model grounded cached skeleton; validated typed execution.
Thought: typed operator step 1 (FILTER_NOT_EMPTY)
Action: typed_operator_exec
Action Input: df = df[df['activity_label'].notna() & df['activity_label'].astype(str).str.strip().ne('')]
Observation: rows=1098198
Thought: typed operator step 2 (FILTER_COMPARE)
Action: typed_operator_exec
Action Input: df = df[df['activity_label'] eq 'Walking']
Observation: rows=424397
Thought: typed operator step 3 (COUNT_DISTINCT)
Action: typed_operator_exec
Action Input: result = df['subject_id'].nunique()
Observation: 36
Final Answer: 36
```

**What went wrong in evaluation:** reported “generated code” looked like only:

```python
result = df['subject_id'].nunique()
```

Judge: *“computes total unique users… not those with activity label walking.”*

**Correct diagnosis:** multi-step plan is right; **emitted code/provenance for the judge omits prior steps**. Fix provenance (or judge input construction), not the walking filter grounding.

#### 6. accel_variance > 0.20 — filters present; provenance incomplete

**Q:** “How many data samples show an accel_variance strictly greater than 0.20?”

**Actual successful typed execution:**

```text
Cache hit: exact query text; light model grounded cached skeleton; validated typed execution.
Thought: typed operator step 1 (FILTER_COMPARE)
Action: typed_operator_exec
Action Input: df = df[df['accel_variance'] gt 0.2]
Observation: rows=352
Thought: typed operator step 2 (COUNT_ROWS)
Action: typed_operator_exec
Action Input: result = len(df)
Observation: 352
Final Answer: 352
```

**What went wrong in evaluation:** judge saw only `result = len(df)` and said it counts all rows, even though the answer **352 is correct** and the filter ran.

**Correct diagnosis:** same class as (5)—**incomplete code artifact**, not a missing filter. Do not change grounding to “fix” a bug that is not in grounding.

---

## Required work

### 1. Read the implementation

- Current `GROUNDING_SYSTEM_PROMPT`
- How the grounding user message is built (`REQUIRED OUTPUT checklist`, `build_compact_operator_spec(skeleton)`, live schema)
- How grounded JSON becomes an executable plan
- How `generated_code` / plan text is written into smoke `raw_results.jsonl` (this is critical for class B)

### 2. Improve `GROUNDING_SYSTEM_PROMPT` (class A)

Keep existing invariants (JSON-only; preserve skeleton ops/order/count; only allowed fields; no invented columns). Add explicit, testable rules:

| Cue in question | Required grounding |
|---|---|
| Bare entity/id (“record_id 106”, “user 20”) | `eq` to that value—not `gt`/`ge` derived from the number |
| Explicit relational language (`>`, strictly greater, above, …) | matching inequality |
| “how many” / “number of samples/rows” | row count / `COUNT` / `COUNT_ROWS`; use `nunique` only when asking unique entities |
| “highest/lowest total number of … samples” | per-entity **count**, then rank/argmax—not `sum` of a sensor channel |
| “average X” / “mean X” | mean of column X—not `var(X)` unless asked |
| Compare / “rougher” on averages | means + requested comparison (diff / which higher); do not default to ratio when inappropriate |
| Train split vs holdout row | independent fields; see mandatory holdout rule above |

### 3. Fix provenance for judge/metrics (class B)

Ensure the artifact used for scoring includes the **full operator chain**, e.g.:

```python
df = df[df['activity_label'].notna() & df['activity_label'].astype(str).str.strip().ne('')]
df = df[df['activity_label'] == 'Walking']
result = df['subject_id'].nunique()
```

and:

```python
df = df[df['accel_variance'] > 0.2]
result = len(df)
```

not only the final line. Prefer one shared serialization used by logs, `raw_results.jsonl`, and the judge.

### 5. Do not

- Change the judge to accept semantically wrong plans that only match a number by accident
- “Fix” walking / accel_variance by rewriting filters that already ran correctly
- Mislabel class B as dropped-predicate grounding failures in docs or comments

---

## Sanity-check protocol (required before claiming success)

For **each** proposed prompt or code change, check smoke results:

**Datasets:**  
`flashfusion/results/ff_and_react_qwen/smoke/FLASH_FUSION_CACHE/{wisdm,bus,mit_ecg}/`

For each dataset:

1. Open `raw_results.jsonl`, `metrics.csv`, and `report.md`.
2. Classify each FAIL as:
   - **A – grounding/semantics** (wrong op fields; execution trace shows wrong filter/agg/holdout)
   - **B – provenance/judge** (trace shows correct multi-step plan; `generated_code` is only the last step; answer often matches GT)
   - **C – other** (skeleton selection, executor, display string only, etc.)
3. Extract: query, full execution trace if present, generated code field, candidate answer, ground truth, judge reason.
4. Fill a short table:

| Query / id | Class (A/B/C) | Would this change help? | Direction | Enough for PASS? | Notes |
|---|---|---|---|---|---|
| … | … | yes/no/partial | fix / none / regression risk | yes/no + missing piece | … |

Be honest when a change would **not** improve a case:

- Walking + accel_variance should flip to PASS from **provenance/code emission** (or judge reading the full plan), not from filter-prompt tweaks.
- ECG 13–15 need verification: wrong `holdout_row` in plan vs wrong display string only.
- If skeleton omits a needed op, prompt grounding cannot add it—call out the correct layer.

---

## Deliverables

1. **Concrete patch** to `GROUNDING_SYSTEM_PROMPT` and any minimal supporting code (especially full-plan code serialization).
2. **Before/after reasoning** for:
   - max samples / `sum` vs `count` (A)
   - record_id 106 / `gt` vs `eq` (A)
   - north/south average accel_variance (A)
   - ECG holdout first vs last (A or display)
   - walking users (B — provenance)
   - accel_variance > 0.2 (B — provenance)
3. **Sanity-check report** tied to files under `flashfusion/results/ff_and_react_qwen/smoke/FLASH_FUSION_CACHE/`, stating which FAILs would likely flip to PASS, which would not, and why.
4. **Regression notes:** PASS cases that legitimately need `gt`, `sum`, `ratio`, or `holdout_row='last'`.
5. If you can run tests, re-smoke FLASH_FUSION_CACHE; otherwise state the exact command and what to grep in `raw_results.jsonl` (full traces vs `generated_code`).

---

## Constraints

- Prefer minimal, surgical changes; do not rewrite the whole baseline.
- Keep JSON-only grounding output and skeleton length/order invariants unless you intentionally redesign caching.
- Active, direct engineering notes; no fluff.
- Cite specific result lines/queries from the smoke dirs when arguing impact.
- When in doubt, trust the **typed execution trace** over the one-line `generated_code` field.

## Start here

1. Read `flash_fusion_cache.py` (`GROUNDING_SYSTEM_PROMPT` + ground path + how code is recorded).
2. Sample FAIL records from all three smoke dataset folders.
3. Label each FAIL A/B/C using the full trace when available.
4. Implement grounding fixes for A and provenance fixes for B.
5. Justify impact against smoke artifacts before claiming improved results.