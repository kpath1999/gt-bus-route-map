# Flash-Fusion: CLAUDE.md Implementation Plan

## Objective

Design a modular, scalable evaluation framework inside `flashfusion/` that answers natural language queries over WISDM IoT sensor data while preventing hallucinations. Benchmarks four progressive baselines to highlight what each architectural component actually contributes.

---

## File Structure

```
flashfusion/
├── CLAUDE.md                   ← master implementation guide
├── pyproject.toml              ← deps declaration
├── config.py                   ← MODEL_RATES, path constants, thresholds
├── pipeline/
│   ├── __init__.py
│   ├── loader.py               ← load_wisdm(), build_column_metadata(), meta_to_str()
│   ├── stages.py               ← Stage1_ConceptExtraction, Stage2_SchemaGrounding, Stage3_SubqueryGeneration
│   ├── executor.py             ← ResilientReActOutputParser, ExecutionLayer, ExecutionDetails
│   └── runner.py               ← LLMClient, LLMCallLog, RunResult, BaselineRunner
├── prompts/
│   ├── __init__.py
│   └── templates.py            ← 6 canonical prompt strings (no inline format calls)
├── baselines/
│   ├── __init__.py
│   ├── llm_only.py             ← B0: raw 20-row CSV sample + question → LLM
│   ├── wellmax_only.py         ← B3: S1+S2+S3 → single grounded LLM call, no agent
│   ├── agent_only.py         ← Agent with raw schema metadata, no stage rewriting
│   └── flash_fusion.py         ← B4: S1+S2+S3 + agent + judge retry loop
├── eval/
│   ├── __init__.py
│   ├── queries.py              ← WISDM_QUERIES (10), EXPECTED_OUTCOMES
│   ├── metrics.py              ← compute_accuracy(), compute_latency(), compute_cost(), aggregate_metrics()
│   ├── reporter.py             ← save_markdown(), save_csv(), print_table()
│   └── benchmark.py            ← CLI: run_benchmark(), __main__ with argparse
├── adapters/
│   ├── __init__.py
│   └── wisdm_adapter.py        ← WISDMAdapter: ACTIVITY_CODEBOOK, derived magnitude + activity_name columns
└── tests/
    ├── __init__.py
    ├── test_stages.py
    ├── test_executor.py
    └── test_metrics.py
```

---

## 4 Baseline Definitions

| Baseline | Internal Mode | Stages Used | Agent? | Judge? | Guardrail? |
|----------|--------------|-------------|--------|--------|------------|
| **LLM-Only** | B0 | None (raw 20-row CSV + question) | No | No | No |
| **WellMax-Only** | B3 | S1 + S2 + S3 → single grounded LLM | No | No | Yes |
| **Agent-Only** | Agent | Schema metadata injected, no rewriting | Yes | No | Yes |
| **Flash-Fusion** | B4 | S1 + S2 + S3 + agent | Yes | Yes | Yes |

**What each component contributes and what it can't do alone:**

- **LLM-Only**: No schema awareness, no code execution. Will fabricate column names, statistics, and activity labels from training-set priors. Useful baseline for measuring hallucination rate.
- **WellMax-Only**: Grounds the query to real column names and maps qualitative concepts to proxy operations — but never executes code. Answers are schema-correct descriptions, not computations.
- **Agent-Only**: Executes real pandas code against real data. However, without concept extraction, it receives abstract queries it cannot reliably map to code — especially when activity codes or derived features are involved.
- **Flash-Fusion**: Combines all three layers. Concept extraction (S1) + schema grounding (S2) + sub-query decomposition (S3) feed an agent that executes code, followed by a judge that verifies intent alignment.

---

## Class Interfaces

### `pipeline/runner.py`

```python
@dataclass
class LLMCallLog:
    model: str
    stage: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float

class LLMClient:
    model_name: str
    llm: ChatGroq
    call_log: list[LLMCallLog]

    def invoke_chain(self, chain, inputs: dict, stage: str) -> str: ...
    def record_estimated_usage(self, stage: str, prompt_text: str, output_text: str) -> None: ...
    def total_latency(self) -> float: ...
    def total_input_tokens(self) -> int: ...
    def total_output_tokens(self) -> int: ...
    def total_tokens(self) -> int: ...
    def total_cost_usd(self) -> float: ...

@dataclass
class RunResult:
    baseline: str
    model: str
    query: str
    answer: str
    trace: str
    latency_s: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    executed: bool          # True if pandas agent ran code
    stages_run: list[str]   # e.g. ["S1", "S2", "S3", "agent", "judge"]
    judge_verdict: dict     # {"verdict": "PASS"|"FAIL", "issue": str, "suggestion": str}
    rejected: bool          # True if guardrail blocked the query
    rejection_reason: str
    final_code: str
    agent_tries: int
    execution_attempts: list[dict]

class BaselineRunner:
    MODES = {"LLM_ONLY", "WELLMAX_ONLY", "AGENT_ONLY", "FLASH_FUSION"}

    def __init__(
        self,
        mode: str,
        df: pd.DataFrame,
        client: LLMClient,
        adapter: DatasetAdapter | None = None,
    ): ...

    def run(self, query: str) -> RunResult: ...

    # Internal dispatch — one method per baseline
    def _run_llm_only(self, query: str, r: RunResult) -> RunResult: ...
    def _run_wellmax_only(self, query: str, r: RunResult) -> RunResult: ...
    def _run_agent_only(self, query: str, r: RunResult) -> RunResult: ...
    def _run_flash_fusion(self, query: str, r: RunResult) -> RunResult: ...
```

### `pipeline/stages.py`

```python
class Stage1_ConceptExtraction:
    """Classifies every query concept as DATA (column-mappable) or REASONING (qualitative proxy needed)."""

    def __init__(self, client: LLMClient): ...

    def run(self, query: str) -> dict:
        # Returns {"DATA": [...], "REASONING": [...]}
        # Retry: if both lists empty and len(query) > 20, retry once
        # Last resort: keyword extraction from query text
        ...

class Stage2_SchemaGrounding:
    """Maps DATA concepts to columns; maps REASONING concepts to column+operation proxies."""

    def __init__(self, client: LLMClient): ...

    def run(
        self,
        concepts: dict,
        query: str,
        meta_str: str,
        df: pd.DataFrame,
        enriched_defs: dict = {},
    ) -> dict:
        # Returns {"mappings": [...], "unmappable": [...], "raw_grounding": str}
        # Retry: if no MAPPINGS lines in output, retry with stricter prompt
        # Validation: flag hallucinated column names as INVALID:*
        ...

class Stage3_SubqueryGeneration:
    """Decomposes abstract query into 2–4 concrete, column-grounded sub-questions."""

    VALID_OPS = {"FILTER", "AGGREGATE", "GROUPBY", "CORRELATE", "WINDOW", "RANK"}

    def __init__(self, client: LLMClient): ...

    def run(self, query: str, grounding_raw: str, meta_str: str) -> dict:
        # Returns {"sub_queries": [...], "synthesis_hint": str, "raw_subqueries": str}
        # Each sub_query prefixed with [OPERATION] tag
        ...
```

### `pipeline/executor.py`

```python
@dataclass
class ExecutionDetails:
    final_code: str
    tries: int
    attempts: list[dict]    # per-attempt failure category stats

class ResilientReActOutputParser(ReActSingleInputOutputParser):
    """Hardens the standard ReAct parser against three documented LLM failure modes."""

    MAX_IDENTICAL: int = 2          # break loop after N identical consecutive outputs
    MAX_PARSE_FAILURES: int = 2     # fall back to answer extraction after N failures

    def parse(self, text: str) -> AgentAction | AgentFinish: ...
    def _sanitize_action_input(self, raw: str) -> str: ...     # strip stray Thought: lines after code
    def _extract_best_answer(self, text: str) -> AgentFinish: ...  # essay fallback

class ExecutionLayer:
    def __init__(self, df: pd.DataFrame, client: LLMClient): ...

    def guardrail(self, query: str) -> tuple[bool, str]:
        # Returns (proceed, reason)
        # PROCEED if all concepts are schema-mappable; REJECT otherwise
        ...

    def execute_single(self, query: str) -> tuple[str, str, ExecutionDetails]:
        # Returns (raw_answer, agent_trace, details)
        ...

    def judge_result(self, question: str, code: str, result: str) -> dict:
        # Returns {"verdict": "PASS"|"FAIL", "issue": str, "suggestion": str}
        ...

    def synthesize(self, question: str, sub_answers: list[str], hint: str) -> str: ...

    def reset_agent(self) -> None:
        # Reinitialise with a fresh DataFrame copy between sub-queries
        ...

    def _build_prefix(self, df: pd.DataFrame, source_path: str) -> str: ...
    def _compact_answer_text(self, text: str, max_lines: int = 6) -> str: ...
```

### `adapters/wisdm_adapter.py`

```python
ACTIVITY_CODEBOOK: dict[str, dict[str, str]] = {
    "activity_label": {
        "A": "Walking",
        "B": "Jogging",
        "C": "Stairs",
        "D": "Sitting",
        "E": "Standing",
        "F": "Typing",
        "G": "Brushing Teeth",
        "H": "Eating Soup",
        "I": "Eating Chips",
        "J": "Eating Pasta",
        "K": "Drinking",
        "L": "Eating Sandwich",
        "M": "Kicking Soccer Ball",
        "O": "Playing Catch",
        "P": "Dribbling Basketball",
        "Q": "Writing",
        "R": "Clapping",
        "S": "Folding Clothes",
    }
}

class WISDMAdapter:
    """Domain-specific enrichment for the WISDM activity recognition dataset."""

    def get_codebook(self, df: pd.DataFrame) -> dict:
        # Returns ACTIVITY_CODEBOOK injected into Stage 2's grounding prompt
        ...

    def get_derived_features(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        # Adds:
        #   magnitude    = sqrt(x² + y² + z²)  — overall acceleration intensity
        #   activity_name = activity_label mapped through codebook
        # Returns (enriched_df, provenance_map)
        ...
```

### `eval/metrics.py`

```python
def compute_accuracy(result: RunResult, ground_truth: str | None = None) -> dict:
    """
    Scoring:
        1.0  — executed=True AND judge_verdict["verdict"] == "PASS"
        0.5  — executed=True AND judge_verdict["verdict"] == "FAIL"
        0.0  — rejected=True OR executed=False (no code ran)
    If ground_truth provided, override with exact-match or semantic check.
    """
    ...

def compute_latency(result: RunResult) -> dict:
    # Returns {"total_s": float}
    ...

def compute_cost(result: RunResult) -> dict:
    # Returns {"total_usd": float, "input_tokens": int, "output_tokens": int}
    ...

def aggregate_metrics(results: list[RunResult]) -> pd.DataFrame:
    """
    Produces a DataFrame with one row per (baseline, query).
    Columns: baseline, query_id, accuracy_score, latency_s, cost_usd,
             executed, rejected, judge_verdict, stages_run
    Also appends a summary row per baseline: mean accuracy, mean latency, mean cost.
    """
    ...
```

---

## WISDM Dataset Schema

**Source**: `chat/data/imu/WISDM_ar_v1.1_raw.txt`
**Format**: headerless CSV, semicolon-terminated rows — one reading per line
**Load function**: `pipeline/loader.py::load_wisdm(path)` adds column headers and strips trailing semicolons

### Raw Columns (6)

| Column | Type | Range | Description |
|--------|------|-------|-------------|
| `subject_id` | int | 1600–1650 | Participant identifier (51 subjects) |
| `activity_label` | char | A–S (excl. N) | Activity code (18 distinct activities) |
| `timestamp` | int64 | Unix ms | Recording timestamp |
| `x` | float | ~±20 m/s² | X-axis accelerometer reading |
| `y` | float | ~±20 m/s² | Y-axis accelerometer reading |
| `z` | float | ~±20 m/s² | Z-axis accelerometer reading |

### Derived Columns (added by `WISDMAdapter`)

| Column | Formula | Description |
|--------|---------|-------------|
| `magnitude` | `sqrt(x² + y² + z²)` | Scalar acceleration intensity |
| `activity_name` | `ACTIVITY_CODEBOOK[activity_label]` | Human-readable activity label |

### Activity Codebook

```
A=Walking  B=Jogging  C=Stairs       D=Sitting    E=Standing
F=Typing   G=Brushing Teeth          H=Eating Soup I=Eating Chips
J=Eating Pasta         K=Drinking    L=Eating Sandwich
M=Kicking Soccer Ball  O=Playing Catch P=Dribbling Basketball
Q=Writing  R=Clapping  S=Folding Clothes
```

---

## 10 Evaluation Queries (Designed to Expose Baseline Gaps)

Each query is annotated with the **expected differential outcome** across the four baselines.

### Q1 — Simple aggregation (cost/latency reference baseline)
> "How many data samples were recorded for each activity?"

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Guesses plausible-sounding counts from training priors — wrong values |
| WellMax-Only | Describes a `groupby(activity_label).count()` approach — no actual numbers |
| Agent-Only | Executes correctly, returns exact counts per letter code (no human labels) |
| Flash-Fusion | Executes correctly, codebook maps codes to activity names in response |

**Stress point**: LLM-Only hallucination vs. real computation; Agent missing activity name context.

---

### Q2 — Derived feature required (magnitude not in raw schema)
> "Which 3 activities have the highest average overall acceleration magnitude?"

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Lists activities from training knowledge (e.g., "jogging, kicking, stairs") — not computed |
| WellMax-Only | Maps "magnitude" → `sqrt(x²+y²+z²)` proxy description — no computation |
| Agent-Only | May use `mean(x)` or `(x+y+z)/3` as a proxy — semantically wrong |
| Flash-Fusion | WISDMAdapter materialises `magnitude` column; Stage 2 grounds to it; correct top-3 computed |

**Stress point**: Only Flash-Fusion's adapter layer creates the correct derived column.

---

### Q3 — Semantic activity grouping (no explicit codes in query)
> "Compare average acceleration magnitude between sedentary activities and locomotion activities."

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Fabricates numbers; may correctly label which are sedentary from training data |
| WellMax-Only | Maps sedentary={D,E}, locomotion={A,B,C} via codebook; describes comparison — no numbers |
| Agent-Only | Has no codebook → likely filters wrong codes or fails entirely |
| Flash-Fusion | Codebook resolves groups; Stage 2 maps; `magnitude` derived; correct comparison executed |

**Stress point**: Agent-Only breaks on semantic group resolution without codebook injection.

---

### Q4 — Schema-trap: column that does not exist
> "What is the average heart rate recorded during jogging?"

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Fabricates a plausible answer (e.g., "approximately 142 bpm") |
| WellMax-Only | Stage 2 marks `heart_rate` as UNMAPPABLE → guardrail REJECT with explanation |
| Agent-Only | Guardrail REJECT |
| Flash-Fusion | Stage 1 classifies `heart_rate` as DATA; Stage 2 marks UNMAPPABLE; guardrail REJECT |

**Stress point**: LLM-Only is the only baseline that does not reject — it hallucinates confidently.

---

### Q5 — Multi-filter + percentage (requires subject + activity code mapping)
> "For subject 1610, what percentage of their samples involve hand-related activities — typing, writing, and clapping?"

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Guesses a percentage |
| WellMax-Only | Maps typing=F, writing=Q, clapping=R via codebook; describes filter + division — no numbers |
| Agent-Only | No codebook → does not know F/Q/R map to hand activities → wrong or empty filter |
| Flash-Fusion | Codebook resolves F/Q/R; Stage 3 decomposes: FILTER(1610) → FILTER(F,Q,R) → count/total; judge verifies |

**Stress point**: Agent-Only cannot resolve English activity names to raw letter codes.

---

### Q6 — Statistical outlier detection with undefined threshold
> "Which subjects show unusually high peak acceleration values that could indicate sensor noise or data errors?"

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Lists generic advice or fabricated subject IDs |
| WellMax-Only | Describes z-score or IQR methodology; no computation |
| Agent-Only | Executes some outlier logic but threshold is arbitrary (no grounding for "unusually high") |
| Flash-Fusion | Stage 2 maps "unusually high" → z-score > 3 on `magnitude`; agent executes; judge checks interpretation |

**Stress point**: Threshold semantics — only Flash-Fusion grounds "unusual" to a concrete statistical rule before execution.

---

### Q7 — Axis-specific correlation with activity filter
> "Is x-axis acceleration positively correlated with z-axis acceleration during stair climbing?"

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | States yes or no from training knowledge |
| WellMax-Only | Describes Pearson correlation between x and z — no number |
| Agent-Only | Filters `activity_label == "C"`, computes `.corr()` — likely correct |
| Flash-Fusion | Stage 3: FILTER(C) → CORRELATE(x, z); judge checks sign + significance claim |

**Stress point**: This is a case where Agent-Only may match Flash-Fusion accuracy (simple filter + corr) — useful for exposing when the overhead of rewriting does not add value.

---

### Q8 — Subject-level temporal reasoning (timestamp domain)
> "Which subject has the longest total recording duration based on their timestamp range?"

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Picks a random or fabricated subject ID |
| WellMax-Only | Describes `(max - min) per subject_id groupby` — no computation |
| Agent-Only | Executes `groupby(subject_id).agg(lambda x: x.max()-x.min()).idxmax()` correctly |
| Flash-Fusion | Same execution + judge confirms units (milliseconds) are correctly interpreted |

**Stress point**: Both Agent-Only and Flash-Fusion should succeed; LLM-Only and WellMax-Only reveal the value of actual execution.

---

### Q9 — Abstract activity similarity (requires centroid computation + comparison)
> "Based on their three-axis acceleration patterns, which two distinct activities are most similar to each other?"

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Asserts "walking and jogging" from training knowledge — not computed from this dataset |
| WellMax-Only | Proposes groupby → mean(x,y,z) → closest pair — describes methodology correctly |
| Agent-Only | May attempt `groupby.mean().corr()` or fail with incomplete code generation |
| Flash-Fusion | Stage 3: GROUPBY activity → AGGREGATE mean(x,y,z) → CORRELATE centroid matrix → RANK off-diagonal max; judge validates approach |

**Stress point**: Highest complexity — only Flash-Fusion reliably decomposes and executes the full multi-step chain.

---

### Q10 — Entirely out-of-scope (no relevant columns, not rejectable via schema alone)
> "Based on the data, predict which activity this subject is likely to perform next."

| Baseline | Expected outcome |
|----------|-----------------|
| LLM-Only | Generates a plausible prediction narrative — completely fabricated |
| WellMax-Only | Guardrail REJECT: requires temporal forecasting, not supported by available columns |
| Agent-Only | Guardrail REJECT: schema cannot support next-activity prediction |
| Flash-Fusion | Stage 1: identifies "predict next activity" as REASONING; Stage 2: UNMAPPABLE (no sequence model); REJECT |

**Stress point**: Tests rejection correctness for a query that sounds data-driven but requires capabilities the schema and pipeline cannot support.

---

## Prompts (`prompts/templates.py`)

All six prompts are module-level string constants. `str.format()` is called at runtime by the stage/executor that owns each prompt — never at module import time.

```python
CONCEPT_EXTRACTION_PROMPT: str   # → DATA: ..., REASONING: ...
SCHEMA_GROUNDING_PROMPT: str     # placeholders: {column_metadata}, {codebook}
SUBQUERY_GENERATION_PROMPT: str  # placeholders: {column_metadata}, {grounding}
GUARDRAIL_PROMPT: str            # placeholders: {column_metadata}, {rewritten_query}
SYNTHESIS_PROMPT: str            # placeholders: {question}, {sub_answers}, {synthesis_hint}
JUDGE_PROMPT: str                # placeholders: {question}, {code}, {result}
```

**SCHEMA_GROUNDING_PROMPT** must include `{codebook}` (injected by WISDMAdapter) so Stage 2 can map English activity names to letter codes when the query uses natural language group names.

**JUDGE_PROMPT** must instruct the model to flag: (a) fabricated column names, (b) incorrect activity code mapping, (c) arithmetic errors visible in the agent trace.

---

## Benchmarking CLI (`eval/benchmark.py`)

```bash
python -m flashfusion.eval.benchmark \
  --data  chat/data/imu/WISDM_ar_v1.1_raw.txt \
  --baselines  all \                  # or: LLM_ONLY,WELLMAX_ONLY,AGENT_ONLY,FLASH_FUSION
  --queries  all \                    # or: 1,2,3  (1-indexed)
  --model  llama-3.3-70b-versatile \
  --output  eval_results/
```

**Output files** written to `--output` directory:
- `metrics.csv` — one row per (baseline × query), columns: accuracy_score, latency_s, cost_usd
- `report.md` — human-readable summary table + per-query narrative
- `raw_results.jsonl` — full `RunResult` serialised per line (for debugging)

---

## Implementation Phases

### Phase 1 — Foundation (steps can run in parallel)
1. `pyproject.toml` — declare all deps (groq, langchain-groq, langchain-classic, langchain-experimental, pandas, pydantic, tabulate, scipy)
2. `config.py` — MODEL_RATE_PER_1M_TOKENS dict, WISDM_DEFAULT_PATH, VALID_OPS set, guardrail thresholds
3. `pipeline/loader.py` — `load_wisdm(path) → pd.DataFrame`, `build_column_metadata(df) → dict`, `meta_to_str(meta) → str`
4. `adapters/wisdm_adapter.py` — `WISDMAdapter` with `ACTIVITY_CODEBOOK`, `get_codebook()`, `get_derived_features()`

### Phase 2 — Pipeline Core (sequential — each depends on previous)
5. `prompts/templates.py` — all 6 prompt strings (reference `chat/playground/playground.py` for exact wording; tighten format instructions)
6. `pipeline/stages.py` — Stage1, Stage2, Stage3 with documented retry + fallback logic
7. `pipeline/executor.py` — `ResilientReActOutputParser`, `ExecutionLayer`, `ExecutionDetails`
8. `pipeline/runner.py` — `LLMClient`, `LLMCallLog`, `RunResult`, `BaselineRunner` dispatch

### Phase 3 — Baselines (can run in parallel after Phase 2)
9. `baselines/llm_only.py` — raw 20-row DataFrame `.to_csv(index=False)` sample + query → single LLM call
10. `baselines/wellmax_only.py` — run S1+S2+S3, format grounding into a single prompt, call LLM once (no agent)
11. `baselines/agent_only.py` — inject schema metadata as system prefix, run guardrail, run agent (skip S1/S2/S3)
12. `baselines/flash_fusion.py` — full B4: S1 → S2 → S3 → per-sub-query agent → synthesise → judge; retry once on FAIL

### Phase 4 — Evaluation (sequential)
13. `eval/queries.py` — `WISDM_QUERIES` list (10 dicts with `id`, `text`, `complexity`, `expected_operation`), `EXPECTED_OUTCOMES` dict
14. `eval/metrics.py` — `compute_accuracy`, `compute_latency`, `compute_cost`, `aggregate_metrics`
15. `eval/reporter.py` — `save_markdown(results, path)`, `save_csv(df, path)`, `print_table(df)` using `tabulate`
16. `eval/benchmark.py` — argparse CLI, orchestrates all baselines × all queries, writes output files

### Phase 5 — Tests + Verification
17. `tests/test_stages.py` — mock LLMClient, assert Stage1/2/3 output shapes and retry behaviour
18. `tests/test_executor.py` — mock AgentExecutor, assert guardrail pass/reject; assert ResilientParser fallback paths
19. `tests/test_metrics.py` — construct synthetic RunResults, assert score 1.0/0.5/0.0 rules

**Integration smoke test:**
```bash
python -m flashfusion.eval.benchmark \
  --data chat/data/imu/WISDM_ar_v1.1_raw.txt \
  --baselines all \
  --queries 1,4,10 \
  --output eval_results/
```
Expected:
- Q1: Agent-Only + Flash-Fusion return `executed=True`; LLM-Only returns `executed=False`
- Q4: WellMax-Only, Agent-Only, Flash-Fusion return `rejected=True`; LLM-Only does not
- Q10: WellMax-Only, Agent-Only, Flash-Fusion return `rejected=True`

---

## Design Decisions and Exclusions

| Decision | Rationale |
|----------|-----------|
| WISDM raw `.txt` only (not ARFF windows) | Simpler 6-column schema; derived features added explicitly via adapter |
| No Tavily enrichment | Removed from scope; all concept grounding handled by Stage 2 + codebook |
| Accuracy defined by judge verdict | No labelled ground truth; score = 1.0 (PASS) / 0.5 (executed, FAIL) / 0.0 (rejected/no exec) |
| ECG and Bus datasets excluded | WISDM-only per evaluation scope |
| Vercel API layer excluded | Framework is CLI-only; no HTTP endpoints in `flashfusion/` |
| Default model: `llama-3.3-70b-versatile` | Override via `--model`; rate table in `config.py` covers all three pool models |
| `flashfusion/` is self-contained | No imports from `chat/playground/` or `chat/experiments/`; `playground.py` is reference only |
