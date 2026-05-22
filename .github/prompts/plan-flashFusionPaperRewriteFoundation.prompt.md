## Flash-Fusion Paper Outline Builder — Claude Code Brief

**Purpose**: Build a high-quality outline for `flash-fusion-paper/sensys27.tex` with descriptive author comments, evidence placeholders, and section-level intent notes. Do not rewrite the full paper prose. Do not run new experiments or modify source code.

---

## Primary Task (What Claude Must Do)

Create an outline-first drafting scaffold, similar to how `\franky{}` comments guide writing, so `\claude{}` can assist section-by-section without replacing the author's voice.

Claude must produce:

1. A section-by-section outline for the paper (`Abstract`, `1 Introduction`, `2 Challenges and Limitations`, `3 System/Method`, `4 Baselines`, `5 Experimental Setup`, `6 Results`, `7 Limitations`,   `8 Conclusion`).
2. Under each section, short subsection bullets describing what to cover.
3. Descriptive author comments for each subsection in the style of editorial guidance, not final prose.
4. Evidence mapping notes that cite which repository files justify each claim.
5. Number slots and claim slots (for verified metrics only) that the author can fill or keep verbatim.
6.   LaTeX comment blocks that can be pasted into `sensys27.tex` directly.

Do not output polished paragraph text for the manuscript except brief one-line starter examples where explicitly useful.

---

## CRITICAL: Current Paper ≠ Current Codebase

Before outlining, Claude must internalize this mismatch. The existing narrative in older drafts does not match the implemented system.

| Dimension | Legacy framing (retire) | Current codebase (use) |
|---|---|---|
| Domain | University bus fleet / vehicular telematics | Human activity recognition (WISDM wrist accelerometer) |
| Dataset | GPS + IMU from campus buses | WISDM: 51 subjects, 18 activities, x/y/z accel, 424k+ rows |
| Core contribution | Edge summarization + cloud clustering | 3-stage query planning (S1/S2/S3) + guardrail + judge + retry |
| Baselines | Flash-Fusion vs raw-data-to-LLM | LLM-Only -> AutoIOT-Only -> WellMax-Only -> Flash-Fusion |
| Key claims | 95% latency reduction, 98% token/cost decrease | Flash-Fusion GT score 0.6448; 100% out-of-scope rejection |
| Framing | Raw telemetry token explosion only | Schema opacity + scope-boundary failures in sensor querying |

Keep valid high-level IoT motivation where appropriate, but repoint it to schema-grounding and query-planning reliability.

---

## Ground Truth Sources (Normative)

Read these before drafting the outline. Every outline claim must map to at least one source.

### Architecture

1. `flashfusion/CLAUDE.md`
2. `flashfusion/prompts/templates.py`
3. `flashfusion/baselines/flash_fusion.py`
4. `flashfusion/baselines/wellmax_only.py`
5. `flashfusion/baselines/autoiot_only.py`
6. `flashfusion/pipeline/runner.py`
7. `flashfusion/diagram/baseline_comparison.md`

### Evaluation

8. `flashfusion/eval/queries.py`
9. `flashfusion/eval/ground_truth_wisdm.json`
10. `flashfusion/eval/metrics.py`
11. `flashfusion/eval/benchmark.py`
12. `flashfusion/eval/visualize_comparison.py`

### Latest Run Artifacts (only these for measured claims)

13. `flashfusion/eval_results/runs/latest/benchmark/metrics.csv`
14. `flashfusion/eval_results/runs/latest/visuals/baseline_summary.md`
15. `flashfusion/eval_results/runs/latest/visuals/accuracy_by_query_type.md`
16. `flashfusion/eval_results/runs/latest/visuals/latency_by_query_type.md`
17. `flashfusion/eval_results/runs/latest/visuals/cost_by_query_type.md`
18. `flashfusion/eval_results/runs/latest/benchmark/report.md`
19. `flashfusion/eval_results/runs/latest/visuals/*.png`

---

## Verified Numbers (for Outline Claim Slots)

Use these exact values when creating metric placeholders in the outline. Do not invent or interpolate.

**Overall GT score (avg across 12 queries):**
- Flash-Fusion: 0.6448
- WellMax-Only: 0.3052
- AutoIOT-Only: 0.2805

**Accuracy by query type:**
- Direct (Q1-Q4): Flash-Fusion 57.7%, WellMax 56.7%, AutoIOT 60.0%
- Reasoning (Q5-Q8): Flash-Fusion 35.7%, WellMax 34.9%, AutoIOT 24.2%
- Out-of-Scope (Q9-Q12): Flash-Fusion 100.0%, WellMax 0.0%, AutoIOT 0.0%

**Latency (avg seconds):**
- Flash-Fusion 5.64s, WellMax 5.80s, AutoIOT 4.97s

**Cost (avg USD/query):**
- Flash-Fusion $0.000682, WellMax $0.000653, AutoIOT $0.000231

**Token usage (avg/query):**
- Flash-Fusion 1053.6 total (752.2 in / 301.4 out)
- WellMax 981.8 total (612.7 in / 369.2 out)
- AutoIOT 339.2 total (185.7 in / 153.6 out)

**Main interpretation to preserve in outline comments:**
- Flash-Fusion wins on reliable out-of-scope rejection and stronger reasoning performance.
- Flash-Fusion pays moderate latency/cost/token overhead.

---

## Output Format Requirements

Claude must return the result in this structure:

1. `Outline Version`: short label (for example: `v1-outline-foundation`).
2. `Assumptions Checked`: bullets listing what was verified from code/artifacts.
3. `Paper Outline with Descriptive Comments`:
   - Section heading
   - Subsection bullets
   - `Author Comment:` guidance block (tone, logical flow, what to emphasize)
   - `Evidence:` file paths
   - `Claim Slots:` metric placeholders or factual statements
4. `  LaTeX Skeleton`:
   - A comment-only scaffold for `sensys27.tex` using `%` comments and section/subsection stubs.
5. `Risk Flags`:
   - Any claim that is weakly supported or needs manual verification.

Keep the outline concise but specific. Prefer actionable guidance over long explanation.

---

## Section-by-Section Outline Map (Replace Rewrite Map)

Use this map to generate an outline, not full prose.

### Abstract
- Provide 4 bullet intentions only: problem, method, key result, trade-off.
- Add `Author Comment` on avoiding overclaiming speed/cost.
- Add claim slot for: "2.1x GT score improvement vs best baseline" and "100% out-of-scope rejection".

### 1 Introduction
- Outline motivation flow: IoT data growth -> NL query promise -> schema opacity bottleneck.
- Suggest one WISDM-grounded motivating scenario.
- Add `Author Comment` on transitioning from legacy bus framing to current benchmark framing.

### 2 Challenges and Limitations (Problem Framing)
- Challenge A: schema opacity and concept-to-column grounding.
- Challenge B: scope boundary and refusal reliability.
- Add `Author Comment` to tie each challenge to benchmark stressors.

### 3 System / Method
- Outline subsections for data layer, S1, S2, S3, guardrail, execution, judge+retry.
- For each stage, include:
  - one-line purpose
  - expected input/output artifact
  - likely failure mode
  - supporting source file
- Add `Author Comment` about why guardrail and judge are distinct controls.

### 4 Baselines
- Present incremental ablation chain:
  - LLM-Only
  - AutoIOT-Only
  - WellMax-Only
  - Flash-Fusion
- Add `Author Comment` explaining causal interpretation (what each added component contributes).

### 5 Experimental Setup
- Dataset facts, query taxonomy, scoring protocol, model/runtime details, reproducibility command.
- Add claim slot templates for dataset statistics and metric definition language.
- Add `Author Comment` to keep terminology consistent: use "GT score" and "text similarity".

### 6 Results
- Subsections:
  - Overall GT score summary
  - By query type
  - Out-of-scope rejection behavior
  - Latency/cost/token trade-off
  - Qualitative cases from report
- Add `Author Comment` to lead with strongest empirical distinction: out-of-scope behavior.
- Add claim slots populated from verified numbers.

### 7 Limitations
- Reasoning query ceiling, scorer-vs-judge mismatch risk, single-dataset scope, cost scaling concern.
- Add `Author Comment` on honest limitation phrasing without undermining contribution.

### 8 Conclusion ( )
- 3 bullet takeaway scaffold only.
- Add `Author Comment` for non-hype ending.

---

## Scope Boundaries

**In scope now:**
- Build outline and descriptive comments.
- Map each outline subsection to evidence files.
- Provide placeholder claims grounded in verified numbers.
- Provide   comment-style LaTeX scaffold.

**Out of scope now:**
- Writing full final manuscript prose.
- Editing model code or running new experiments.
- Adding figures not already present in latest visuals.
- Using any values from `eval_results/archive_may19/`.

---

## Verification Checklist (for Outline Quality)

1. Every numeric claim slot maps to latest run artifacts.
2. Every method subsection maps to architecture/control-flow source files.
3. Baseline descriptions match implementation reality.
4. Terminology uses `GT score` and `text similarity` consistently.
5. No references to retired bus-specific performance claims.
6. Trade-offs are explicit (accuracy gain vs latency/cost overhead).

---

## Minimum Viable Context Pack (if token budget is tight)

1. `flashfusion/CLAUDE.md`
2. `flashfusion/prompts/templates.py`
3. `flashfusion/eval/queries.py`
4. `flashfusion/eval/ground_truth_wisdm.json`
5. `flashfusion/eval_results/runs/latest/benchmark/metrics.csv`
6. `flashfusion/eval_results/runs/latest/visuals/baseline_summary.md`
7. `flashfusion/eval_results/runs/latest/visuals/accuracy_by_query_type.md`

---

## Working Files

- Outline target context: `flash-fusion-paper/sensys27.tex`
- Macros reference: `flash-fusion-paper/macros.tex`
- Figure assets: `flashfusion/eval_results/runs/latest/visuals/`
- Archive to ignore: `flashfusion/eval_results/archive_may19/`
