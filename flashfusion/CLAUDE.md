# CLAUDE.md — Flash-Fusion Implementation Guide

> **For Claude Code**: Read this entire document before writing any code.
>
> Files that are **complete** (do not modify):
> - `prompts/templates.py` — all 6 prompt strings
> - `eval/queries.py` — all 10 benchmark queries
> - `adapters/wisdm_adapter.py` — `ACTIVITY_CODEBOOK` dict (implement the class stubs)
> - `config.py` — model rates and constants
>
> Files that contain **stubs** (implement the bodies following the specs below):
> - `pipeline/loader.py`, `pipeline/stages.py`, `pipeline/executor.py`, `pipeline/runner.py`
> - `baselines/*.py`, `eval/metrics.py`, `eval/reporter.py`, `eval/benchmark.py`
> - `adapters/wisdm_adapter.py` (the class methods)
> - `tests/*.py`
>
> **Reference implementation** (read-only — do not import from):
> `chat/playground/playground.py` — working implementations of Stages 1–3,
> `ExecutionLayer`, `BaselineRunner`, `ThinkingCaptureHandler`, `ResilientReActOutputParser`.

---

## Quick Start

```bash
# From repo root (flash-fusion/)
pip install -e flashfusion/
export GROQ_API_KEY="gsk_..."

# Smoke test: 3 queries × 4 baselines (~5 min)
python -m flashfusion.eval.benchmark \
  --data chat/data/imu/WISDM_ar_v1.1_raw.txt \
    --baselines all --queries 1,5,12 \
  --output flashfusion/eval_results/

# Full benchmark: 12 queries × 4 baselines (~50 min)
python -m flashfusion.eval.benchmark \
  --data chat/data/imu/WISDM_ar_v1.1_raw.txt \
  --baselines all \
  --output flashfusion/eval_results/

# Unit tests
pytest flashfusion/tests/ -v
```

---

## System Architecture

```
User Query (natural language)
        │
        ├──► LLM-Only        20-row CSV sample + query → LLM → answer
        │                    No schema grounding · No code execution · No guardrail
        │
        ├──► AutoIOT-Only    raw_query → Pandas agent → answer
        │                    Real execution, but no concept extraction/codebook/guardrail
        │
        ├──► WellMax-Only    S1 → S2 (codebook) → S3 → grounded_query
        │                    → Pandas agent(grounded_query) → answer
        │                    Grounded execution — no judge
        │
        └──► Flash-Fusion    S1 → S2 (codebook) → S3 → guardrail(grounded_query)
                             → Pandas agent(grounded_query) → judge
                             → [agent retry with correction note if FAIL] → answer
```

### What Each Component Contributes

| Capability | LLM-Only | AutoIOT-Only | WellMax-Only | Flash-Fusion |
|------------|:---:|:---:|:---:|:---:|
| Real data execution (pandas agent) | ✗ | ✓ | ✓ | ✓ |
| Column grounding via S1+S2 | ✗ | ✗ | ✓ | ✓ |
| Activity codebook injection | ✗ | ✗ | ✓ | ✓ |
| Derived feature materialisation (`magnitude`) | ✗ | ✗ | ✓ | ✓ |
| Query decomposition via S3 | ✗ | ✗ | ✓ | ✓ |
| Guardrail on grounded query | ✗ | ✗ | ✗ | grounded query |
| Post-execution intent judge + retry | ✗ | ✗ | ✗ | ✓ |

---

## Dataset: WISDM Accelerometer

**File**: `chat/data/imu/WISDM_ar_v1.1_raw.txt`
**Format**: Headerless CSV, each row ends with `;`. Load via `pipeline/loader.py::load_wisdm()`.

### Raw Columns (after loading)

| Column | Python type | Range | Notes |
|--------|-------------|-------|-------|
| `subject_id` | `int` | 1600–1650 | 51 participants |
| `activity_label` | `str` | A–S (no N) | 18 activities — always `.strip()` after parsing |
| `timestamp` | `int64` | Unix ms | |
| `x` | `float64` | ~±20 m/s² | X-axis accelerometer |
| `y` | `float64` | ~±20 m/s² | Y-axis accelerometer |
| `z` | `float64` | ~±20 m/s² | Z-axis accelerometer |

### Derived Columns (added by `WISDMAdapter.get_derived_features()`)

| Column | Formula | Purpose |
|--------|---------|---------|
| `magnitude` | `(x**2 + y**2 + z**2) ** 0.5` | Scalar intensity — critical for Q2, Q3, Q6 |
| `activity_name` | `ACTIVITY_CODEBOOK["activity_label"][label]` | English label for LLM synthesis |

### Activity Codebook

```
A=Walking      B=Jogging         C=Stairs             D=Sitting     E=Standing
F=Typing       G=Brushing Teeth  H=Eating Soup         I=Eating Chips J=Eating Pasta
K=Drinking     L=Eating Sandwich M=Kicking Soccer Ball O=Playing Catch P=Dribbling Basketball
Q=Writing      R=Clapping        S=Folding Clothes
```

---

## 12 Evaluation Queries

Full definitions with metadata in `eval/queries.py`. These queries are deliberately designed
to expose capability gaps between baselines.

| # | Query | Primary Stress Point |
|---|-------|---------------------|
| Q1 | "What is the maximum recorded x-acceleration for user 15?" | Control query for scalar filter+aggregate correctness |
| Q2 | "How many total samples ... Walking activity?" | Label-normalization check on categorical counting |
| Q3 | "Average y-accel for user 5 during Sitting" | Conjunctive filter fidelity (subject + activity) |
| Q4 | "Which user has the highest total number of samples?" | Groupby+rank execution baseline |
| Q5 | "Compare magnitude for dynamic vs resting states" | Derived-feature grounding (`magnitude`) + semantic grouping |
| Q6 | "User with stationary duration exceeding locomotion" | Multi-step decomposition and per-user comparison |
| Q7 | "Median vector length for user 20 ascending steps" | Derived metric with activity-synonym handling |
| Q8 | "Difference in average z between ascending/descending" | Comparative aggregate with signed-difference reporting |
| Q9 | "Walking speed vs age correlation" | Out-of-scope (missing speed/age schema) |
| Q10 | "Predict exact geographic location for jogging" | Out-of-scope geolocation inference |
| Q11 | "Female vs male cadence during stair climbing" | Out-of-scope demographic + cadence fields |
| Q12 | "Personalized workout routine recommendation" | Out-of-scope prescriptive recommendation |

---

## File Structure

```
flashfusion/
├── CLAUDE.md               ← this file (master guide)
├── __init__.py
├── pyproject.toml          ← package + deps declaration
├── config.py               ← MODEL_RATES, path constants, thresholds (complete)
├── pipeline/
│   ├── __init__.py
│   ├── loader.py           ← load_wisdm(), build_column_metadata(), meta_to_str()
│   ├── stages.py           ← Stage1_ConceptExtraction, Stage2_SchemaGrounding, Stage3_SubqueryGeneration
│   ├── executor.py         ← ThinkingCaptureHandler, ResilientReActOutputParser,
│   │                          ExecutionLayer, ExecutionDetails
│   └── runner.py           ← LLMClient, LLMCallLog, RunResult, BaselineRunner
├── prompts/
│   ├── __init__.py
│   └── templates.py        ← 6 canonical prompt strings (COMPLETE — do not modify)
├── baselines/
│   ├── __init__.py
│   ├── llm_only.py         ← _run_llm_only
│   ├── wellmax_only.py     ← _run_wellmax_only
│   ├── autoiot_only.py     ← _run_autoiot_only
│   └── flash_fusion.py     ← _run_flash_fusion
├── eval/
│   ├── __init__.py
│   ├── queries.py          ← WISDM_QUERIES list (COMPLETE — do not modify)
│   ├── metrics.py          ← compute_accuracy(), compute_latency(), compute_cost(), aggregate_metrics()
│   ├── reporter.py         ← save_markdown(), save_csv(), print_table()
│   └── benchmark.py        ← CLI entry point (__main__)
├── adapters/
│   ├── __init__.py
│   └── wisdm_adapter.py    ← WISDMAdapter (codebook complete; implement class methods)
└── tests/
    ├── __init__.py
    ├── test_stages.py
    ├── test_executor.py
    └── test_metrics.py
```

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

    def __init__(self, model_name: str, api_key: str): ...
    def invoke_chain(self, chain, inputs: dict, stage: str) -> str: ...
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
    answer: str = ""
    trace: str = ""
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    executed: bool = False        # True if pandas agent ran code
    stages_run: list = field(default_factory=list)
    judge_verdict: dict = field(default_factory=dict)
    rejected: bool = False
    rejection_reason: str = ""
    final_code: str = ""
    agent_tries: int = 0
    execution_attempts: list = field(default_factory=list)

class BaselineRunner:
    MODES = {"LLM_ONLY", "WELLMAX_ONLY", "AUTOIOT_ONLY", "FLASH_FUSION"}

    def __init__(self, mode: str, df: pd.DataFrame, client: LLMClient,
                 adapter=None, data_path: str = "WISDM"): ...
    def run(self, query: str) -> RunResult: ...
```

### `pipeline/stages.py`

```python
class Stage1_ConceptExtraction:
    def __init__(self, client: LLMClient): ...
    def run(self, query: str) -> dict:
        # Returns {"DATA": list[str], "REASONING": list[str]}

class Stage2_SchemaGrounding:
    codebook_str: str = ""  # injected by BaselineRunner via adapter
    def __init__(self, client: LLMClient): ...
    def run(self, concepts: dict, query: str, meta_str: str,
            df: pd.DataFrame, enriched_defs: dict = {}) -> dict:
        # Returns {"mappings": list[str], "unmappable": list[str], "raw_grounding": str}

class Stage3_SubqueryGeneration:
    VALID_OPS: ClassVar[set] = {"FILTER","AGGREGATE","GROUPBY","CORRELATE","WINDOW","RANK"}
    def __init__(self, client: LLMClient): ...
    def run(self, query: str, grounding_raw: str, meta_str: str) -> dict:
        # Returns {"sub_queries": list[str], "synthesis_hint": str, "raw_subqueries": str}
```

### `pipeline/executor.py`

```python
@dataclass
class ExecutionDetails:
    final_code: str
    tries: int
    attempts: list[dict]  # per-attempt stats dicts

class ThinkingCaptureHandler(BaseCallbackHandler):
    steps: list[str]
    action_inputs: list[str]
    final_output: str
    last_successful_action_input: str

    def get_trace(self) -> str: ...
    def get_execution_details(self) -> tuple[str, int]: ...  # (final_code, n_tries)

class ResilientReActOutputParser(ReActSingleInputOutputParser):
    MAX_IDENTICAL: ClassVar[int] = 2
    MAX_PARSE_FAILURES: ClassVar[int] = 2

    def parse(self, text: str) -> AgentAction | AgentFinish: ...
    def _sanitize_action_input(self, raw: str) -> str: ...
    def _extract_best_answer(self, text: str) -> AgentFinish: ...

class ExecutionLayer:
    def __init__(self, df: pd.DataFrame, client: LLMClient): ...
    def guardrail(self, query: str) -> tuple[bool, str]: ...
    def execute_single(self, query: str) -> tuple[str, str, ExecutionDetails]: ...
    def judge_result(self, question: str, code: str, result: str) -> dict: ...
    def synthesize(self, question: str, sub_answers: list[str], hint: str) -> str: ...
    def reset_agent(self) -> None: ...
```

### `adapters/wisdm_adapter.py`

```python
class WISDMAdapter:
    def get_codebook(self, df: pd.DataFrame) -> dict:
        # Returns ACTIVITY_CODEBOOK

    def get_codebook_str(self) -> str:
        # Returns formatted string for prompt injection:
        # "activity_label codes: A=Walking, B=Jogging, ..."

    def get_derived_features(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        # Adds magnitude and activity_name columns
        # Returns (enriched_df, provenance_map)
        # provenance_map = {"magnitude": "sqrt(x^2+y^2+z^2)", "activity_name": "codebook lookup"}
```

### `eval/metrics.py`

```python
def compute_accuracy(result: RunResult, ground_truth: str | None = None) -> dict:
    # Score: 1.0=executed+PASS, 0.5=executed+(FAIL or no judge), 0.0=rejected/not-executed
    # Returns {"score": float, "executed": bool, "judge_pass": bool | None, "rejected": bool}

def compute_latency(result: RunResult) -> dict:
    # Returns {"total_s": float}

def compute_cost(result: RunResult) -> dict:
    # Returns {"total_usd": float, "input_tokens": int, "output_tokens": int}

def aggregate_metrics(results: list[RunResult]) -> pd.DataFrame:
    # Returns DataFrame: one row per (baseline, query_id)
    # Columns: baseline, query_id, accuracy_score, latency_s, cost_usd,
    #          executed, rejected, judge_verdict, stages_run
```

---

## Implementation Specifications

### `pipeline/loader.py`

#### `load_wisdm(path: str) -> pd.DataFrame`

```
Algorithm:
  rows = []
  for each line in file:
    line = line.strip().rstrip(";")
    if not line: continue
    parts = line.split(",")
    if len(parts) < 6: continue
    try:
      subject_id = int(parts[0].strip())
      activity_label = parts[1].strip()
      timestamp = int(parts[2].strip())
      x = float(parts[3].strip())
      y = float(parts[4].strip())
      z = float(parts[5].strip())
    except (ValueError, IndexError):
      continue
    rows.append([subject_id, activity_label, timestamp, x, y, z])
  df = pd.DataFrame(rows, columns=["subject_id","activity_label","timestamp","x","y","z"])
  df["subject_id"] = df["subject_id"].astype(int)
  df["timestamp"] = df["timestamp"].astype("int64")
  df["x"] = df["x"].astype(float)
  df["y"] = df["y"].astype(float)
  df["z"] = df["z"].astype(float)
  return df
```

#### `build_column_metadata(df: pd.DataFrame) -> dict`

```
For each col in df.columns:
  meta = {"dtype": str(df[col].dtype), "n_unique": int(df[col].nunique()),
          "sample_values": df[col].dropna().unique()[:5].tolist()}
  if pd.api.types.is_numeric_dtype(df[col]):
    meta["min"] = float(df[col].min())
    meta["max"] = float(df[col].max())
    meta["mean"] = float(df[col].mean())
    meta["std"]  = float(df[col].std())
return {col: meta for col in df.columns}
```

#### `meta_to_str(metadata: dict) -> str`

```
Lines:
  "{col} ({dtype}): n_unique={n_unique} | sample={sample_values}"
  for numeric cols append: " | min={min:.3f} max={max:.3f} mean={mean:.3f} std={std:.3f}"
Join with "\n". Return string.
```

---

### `pipeline/stages.py`

All stages share this chain construction pattern:
```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# In each stage __init__:
chain = (
    ChatPromptTemplate.from_messages([
        ("system", SOME_PROMPT),
        ("human", "{input}"),
    ])
    | client.llm
    | StrOutputParser()
)
# Invoke via:
result = client.invoke_chain(chain, {"input": formatted_input_text}, stage="S1")
```

#### `Stage1_ConceptExtraction.run(query)`

```
Step 1: input_text = query
        result = client.invoke_chain(chain, {"input": input_text}, stage="S1")

Step 2: Parse result:
        data_line = [l for l in result.splitlines() if l.strip().startswith("DATA:")]
        reasoning_line = [l for l in result.splitlines() if l.strip().startswith("REASONING:")]
        data = parse_concept_line(data_line[0]) if data_line else []
        reasoning = parse_concept_line(reasoning_line[0]) if reasoning_line else []

        def parse_concept_line(line):
            content = line.split(":", 1)[1].strip()
            if content.upper() in ("NONE", ""):
                return []
            return [c.strip() for c in content.split(",") if c.strip()]

Step 3: Retry if both empty and len(query.strip()) > 20:
        input_text = query + "\n\nBe explicit. List every distinct semantic concept individually."
        [repeat parse]

Step 4: Keyword fallback if still both empty:
        stopwords = {"the","a","an","is","are","was","were","what","which","how","many",
                     "in","of","for","by","with","from","to","and","or","not","that","this"}
        data = [w for w in query.lower().split() if w not in stopwords and len(w) > 3][:5]

Return {"DATA": data, "REASONING": reasoning}
```

#### `Stage2_SchemaGrounding.run(concepts, query, meta_str, df, enriched_defs={})`

```
Step 1: Build input:
        input_text = (
            f"DATA concepts: {', '.join(concepts['DATA']) or 'NONE'}\n"
            f"REASONING concepts: {', '.join(concepts['REASONING']) or 'NONE'}\n"
            f"Query: {query}"
        )
        Note: The SCHEMA_GROUNDING_PROMPT has {column_metadata} and {codebook} as
        system-level placeholders. Format the PROMPT before building the chain:
        system_prompt = SCHEMA_GROUNDING_PROMPT.format(
            column_metadata=meta_str,
            codebook=self.codebook_str or "No codebook provided."
        )
        Build chain with system_prompt (not the raw SCHEMA_GROUNDING_PROMPT constant).

Step 2: result = client.invoke_chain(chain, {"input": input_text}, stage="S2")

Step 3: Parse result:
        lines = result.splitlines()
        mappings = []
        unmappable = []
        in_mappings = False
        for line in lines:
            if line.strip().startswith("MAPPINGS:"):
                in_mappings = True; continue
            if line.strip().startswith("UNMAPPABLE:"):
                in_mappings = False
                raw = line.split(":", 1)[1].strip()
                if raw.upper() != "NONE":
                    unmappable = [u.strip() for u in raw.split(",") if u.strip()]
                continue
            if in_mappings and line.strip() and line.strip().startswith("-") or "→" in line:
                mappings.append(line.strip())

Step 4: Validate column references:
        valid_cols = set(df.columns)
        validated = []
        for m in mappings:
            words = set(m.replace("→","").split())
            hallucinated = [w for w in words if w in valid_cols or True]  # conservative
            # Simpler: check if the mapping references a word that looks like a col name but isn't
            mentioned_cols = [w for w in words if "_" in w or w.lower() in valid_cols]
            invalid_cols = [c for c in mentioned_cols if c not in valid_cols]
            if invalid_cols:
                validated.append(f"INVALID({','.join(invalid_cols)}): " + m)
            else:
                validated.append(m)
        mappings = validated

Step 5: Retry once if len(mappings) == 0:
        input_text += "\n\nCRITICAL: output at least one MAPPINGS line. Example:\n  acceleration → x, y, z columns"
        [repeat from step 2 and parse again]

Return {"mappings": mappings, "unmappable": unmappable, "raw_grounding": result}
```

#### `Stage3_SubqueryGeneration.run(query, grounding_raw, meta_str)`

```
Step 1: system_prompt = SUBQUERY_GENERATION_PROMPT.format(
            column_metadata=meta_str,
            grounding=grounding_raw
        )
        input_text = f"Original query: {query}"

Step 2: result = client.invoke_chain(chain, {"input": input_text}, stage="S3")

Step 3: Parse:
        import re
        sub_queries = re.findall(r"SUB_Q\d+:\s*(.+)", result)
        hints = re.findall(r"SYNTHESIS_HINT:\s*(.+)", result)
        synthesis_hint = hints[0].strip() if hints else "Combine all sub-answers into a direct response."

Return {"sub_queries": sub_queries, "synthesis_hint": synthesis_hint, "raw_subqueries": result}
```

---

### `pipeline/executor.py`

#### `ThinkingCaptureHandler` (full implementation)

```python
class ThinkingCaptureHandler(BaseCallbackHandler):
    def __init__(self):
        super().__init__()
        self.steps: list[str] = []
        self.action_inputs: list[str] = []
        self.final_output: str = ""
        self.last_successful_action_input: str = ""

    def on_agent_action(self, action: AgentAction, **kwargs) -> None:
        self.steps.append(f"Thought: {action.log.strip()}")
        self.action_inputs.append(str(action.tool_input))

    def on_tool_end(self, output: str, **kwargs) -> None:
        self.steps.append(f"Observation: {output}")
        if not self._looks_like_tool_error(str(output)):
            if self.action_inputs:
                self.last_successful_action_input = self.action_inputs[-1]

    def on_agent_finish(self, finish: AgentFinish, **kwargs) -> None:
        self.final_output = finish.return_values.get("output", "")

    def get_trace(self) -> str:
        return "\n".join(self.steps)

    def get_execution_details(self) -> tuple[str, int]:
        return (self.last_successful_action_input, len(self.action_inputs))

    def _looks_like_tool_error(self, output: str) -> bool:
        error_keywords = ["Error", "Traceback", "Exception", "SyntaxError",
                          "NameError", "ValueError", "KeyError", "AttributeError"]
        return any(k in output for k in error_keywords)
```

#### `ResilientReActOutputParser` — three failure modes

```
P0-loop-1: Essay output (no Action / no Final Answer)
  → consecutive parse failures increment _parse_failure_count
  → if >= MAX_PARSE_FAILURES: call _extract_best_answer(text)

P0-loop-2: Both "Action:" and "Final Answer:" in same output
  → detect using: "Action:" in text and "Final Answer:" in text
  → if "Final Answer:" appears AFTER the action block: strip it (prefer executing the action)
  → if "Final Answer:" appears BEFORE "Action:": strip the preamble, keep Action onward
  → if super().parse() still fails: fall back to _extract_best_answer

P0-loop-3: Stray "Thought:" lines after Action Input code block
  → called in _sanitize_action_input(raw):
    if "\nThought:" in raw: raw = raw.split("\nThought:")[0]
    if "\n\n" in raw: raw = raw.split("\n\n")[0]
    return raw.strip()

P3-retry-dedup: Identical consecutive outputs
  → append text to _output_history (keep last MAX_IDENTICAL entries)
  → if all entries are identical: call _extract_best_answer(text) immediately

_extract_best_answer(text):
  → search for "Final Answer:" (case-insensitive) → extract everything after it
  → if not found: search for "Answer:" → extract
  → if not found: use last non-empty paragraph
  → return AgentFinish(return_values={"output": answer.strip()}, log=text)
```

Use `pydantic.PrivateAttr` for `_output_history` and `_parse_failure_count` since
`ResilientReActOutputParser` is a Pydantic model (inherits from a LangChain class).

#### `ExecutionLayer` — agent construction

Use lazy construction for the pandas agent executor:

- Do NOT build the agent in `ExecutionLayer.__init__`.
- Keep `self._agent_executor = None` initially.
- Build it only on first execution call (e.g. `_ensure_agent()` inside `execute_single()`).
- `reset_agent()` must invalidate (`self._agent_executor = None`) instead of eagerly rebuilding.

Rationale: non-executing paths (guardrail-only, rejected queries, WellMax-only synthesis)
must avoid importing or constructing the `langchain_classic`/pandas-agent stack, which can
deadlock on this macOS environment.

Build the ReAct agent as follows (see `chat/playground/playground.py` ~line 750 for reference):

```python
from langchain_experimental.tools import PythonAstREPLTool
from langchain_classic.agents import create_react_agent, AgentExecutor
from langchain_experimental.agents.agent_toolkits.pandas.base import _get_prompt

tool = PythonAstREPLTool(locals={"df": self._df})
prompt = _get_prompt(self._df)
# Inject system prefix into the prompt template (first human message slot)
# Build agent with ResilientReActOutputParser replacing the default parser
react_agent = create_react_agent(self._client.llm, [tool], prompt)
self._agent_executor = AgentExecutor(
    agent=react_agent,
    tools=[tool],
    max_iterations=6,
    handle_parsing_errors=True,
    return_intermediate_steps=True,
    verbose=False,
)
```

**IMPORTANT**: Replace the default output parser with `ResilientReActOutputParser`.
In `chat/playground/playground.py` this is done by directly setting
`react_agent.output_parser = ResilientReActOutputParser()` after `create_react_agent()`.

#### `ExecutionLayer.guardrail(query) -> tuple[bool, str]`

```
1. Build meta_str from current df
2. Format GUARDRAIL_PROMPT.format(column_metadata=meta_str)
3. Build chain: ChatPromptTemplate.from_messages([("system", formatted_prompt), ("human", query)])
4. Invoke (stage="guardrail")
5. response = response.strip()
6. if response.startswith("PROCEED"): return True, ""
7. else: return False, response.replace("REJECT:", "").strip()
```

#### `ExecutionLayer.execute_single(query) -> tuple[str, str, ExecutionDetails]`

```
1. handler = ThinkingCaptureHandler()
2. result = self._agent_executor.invoke(
       {"input": query},
       config={"callbacks": [handler]}
   )
3. raw_answer = result.get("output", "")
4. trace = handler.get_trace()
5. final_code, tries = handler.get_execution_details()
6. details = ExecutionDetails(final_code=final_code, tries=tries, attempts=[])
7. return raw_answer, trace, details
```

#### `ExecutionLayer.judge_result(question, code, result) -> dict`

```
1. system = JUDGE_PROMPT  (no placeholders — all info goes in human message)
2. human = f"Question: {question}\n\nCode executed:\n{code}\n\nResult:\n{result}"
3. Invoke chain (stage="judge")
4. Parse:
   if "VERDICT: PASS" in response: return {"verdict": "PASS", "issue": "", "suggestion": ""}
   if "VERDICT: FAIL" in response:
     issue_match = re.search(r"ISSUE:\s*(.+)", response)
     suggestion_match = re.search(r"SUGGESTION:\s*(.+)", response)
     return {"verdict": "FAIL",
             "issue": issue_match.group(1).strip() if issue_match else "",
             "suggestion": suggestion_match.group(1).strip() if suggestion_match else ""}
   return {"verdict": "UNKNOWN", "issue": response, "suggestion": ""}
```

#### `ExecutionLayer.synthesize(question, sub_answers, hint) -> str`

```
1. sub_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(sub_answers))
2. human = f"Question: {question}\n\nSub-answers:\n{sub_text}\n\nSynthesis guidance: {hint}"
3. Invoke synthesizer_chain (stage="synthesis")
4. Return response.strip()
```

---

### `pipeline/runner.py`

#### `LLMClient`

```python
def __init__(self, model_name: str, api_key: str):
    from langchain_groq import ChatGroq
    self.model_name = model_name
    self.llm = ChatGroq(model=model_name, groq_api_key=api_key, temperature=0)
    self.call_log: list[LLMCallLog] = []

def invoke_chain(self, chain, inputs: dict, stage: str) -> str:
    import time
    t0 = time.time()
    result = chain.invoke(inputs)
    latency = time.time() - t0
    # Estimate tokens
    prompt_text = str(inputs)
    output_text = str(result)
    in_tok = self._estimate_tokens(prompt_text)
    out_tok = self._estimate_tokens(output_text)
    cost = self._compute_cost(in_tok, out_tok)
    self.call_log.append(LLMCallLog(
        model=self.model_name, stage=stage,
        input_tokens=in_tok, output_tokens=out_tok,
        latency_s=latency, cost_usd=cost
    ))
    return result if isinstance(result, str) else str(result)

def _estimate_tokens(self, text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))

def _compute_cost(self, in_tok: int, out_tok: int) -> float:
    from flashfusion.config import MODEL_RATE_PER_1M_TOKENS
    rates = MODEL_RATE_PER_1M_TOKENS.get(self.model_name, {"input": 0.0, "output": 0.0})
    return (in_tok * rates["input"] + out_tok * rates["output"]) / 1_000_000

def total_latency(self) -> float: return sum(c.latency_s for c in self.call_log)
def total_input_tokens(self) -> int: return sum(c.input_tokens for c in self.call_log)
def total_output_tokens(self) -> int: return sum(c.output_tokens for c in self.call_log)
def total_tokens(self) -> int: return self.total_input_tokens() + self.total_output_tokens()
def total_cost_usd(self) -> float: return sum(c.cost_usd for c in self.call_log)
```

#### `BaselineRunner.run(query) -> RunResult`

```
1. r = RunResult(baseline=self.mode, model=self.client.model_name, query=query)
2. t0 = time.time()
3. If self.adapter is not None:
     self.df, _ = self.adapter.get_derived_features(self.df)
     (do this once, not every call — guard with a flag)
4. Dispatch: {
     "LLM_ONLY":     self._run_llm_only,
     "WELLMAX_ONLY":  self._run_wellmax_only,
     "AUTOIOT_ONLY":  self._run_autoiot_only,
     "FLASH_FUSION":  self._run_flash_fusion,
   }[self.mode](query, r)
5. r.latency_s = time.time() - t0
6. r.input_tokens = self.client.total_input_tokens()
7. r.output_tokens = self.client.total_output_tokens()
8. r.cost_usd = self.client.total_cost_usd()
9. return r
```

#### `BaselineRunner._run_llm_only(query, r)`

```
1. sample_csv = self.df.head(20).to_csv(index=False)
2. prompt = ChatPromptTemplate.from_messages([
       ("system", "You are a data analyst. Answer the question using the dataset sample provided."),
       ("human", f"Dataset sample (first 20 rows):\n{sample_csv}\n\nQuestion: {query}\n\nAnswer directly and concisely.")
   ])
3. chain = prompt | self.client.llm | StrOutputParser()
4. r.answer = self.client.invoke_chain(chain, {}, stage="llm_only")
5. r.executed = False; r.stages_run = ["llm_only"]; r.rejected = False
6. return r
```

#### `BaselineRunner._run_wellmax_only(query, r)`

```
1. meta_str = meta_to_str(build_column_metadata(self.df))
2. stage1..stage3 setup (inject codebook if adapter)
3. concepts = stage1.run(query); r.stages_run.append("S1")
4. grounding = stage2.run(concepts, query, meta_str, self.df)
    r.stages_run.append("S2")
5. sub_result = stage3.run(query, grounding["raw_grounding"], meta_str)
   r.stages_run.append("S3")
6. grounded_query = _build_grounded_query(query, grounding, sub_result)
7. executor = ExecutionLayer(self.df, self.client)
8. raw_answer, trace, details = executor.execute_single(grounded_query)
   r.answer = raw_answer; r.trace = trace
   r.executed = True
   r.final_code = details.final_code; r.agent_tries = details.tries
   r.stages_run.append("agent")
   r.judge_verdict = {}  # no judge for WellMax
   return r
```

#### `BaselineRunner._run_autoiot_only(query, r)`

```
1. executor = ExecutionLayer(self.df, self.client)
2. raw_answer, trace, details = executor.execute_single(query)
5. r.answer = raw_answer; r.trace = trace
6. r.executed = True
7. r.final_code = details.final_code; r.agent_tries = details.tries
8. r.stages_run.append("agent")
9. return r
```

#### `BaselineRunner._run_flash_fusion(query, r)`

```
1. meta_str = meta_to_str(build_column_metadata(self.df))
2. stage1..stage3 setup (inject codebook if adapter)
3. executor = ExecutionLayer(self.df, self.client)

4. concepts = stage1.run(query); r.stages_run.append("S1")
5. grounding = stage2.run(concepts, query, meta_str, self.df); r.stages_run.append("S2")
   # No early exit on unmappable — guardrail decides
6. sub_result = stage3.run(query, grounding["raw_grounding"], meta_str); r.stages_run.append("S3")

7. grounded_query = _build_grounded_query(query, grounding, sub_result)
8. proceed, reason = executor.guardrail(grounded_query); r.stages_run.append("guardrail")
   if not proceed:
       r.rejected = True; r.rejection_reason = reason
       r.answer = f"Query rejected: {reason}"
       r.executed = False; return r

9. raw_answer, trace, details = executor.execute_single(grounded_query)
   r.trace = trace; r.executed = True
   r.final_code = details.final_code; r.agent_tries = details.tries
   r.stages_run.append("agent")

10. verdict = executor.judge_result(query, r.final_code, raw_answer)
    r.stages_run.append("judge"); r.judge_verdict = verdict
    r.alignment_explanation = executor.explain_alignment(query, verdict)

11. if verdict.get("verdict") == "FAIL" and verdict.get("suggestion"):
        # Retry: re-execute with correction note appended to grounded query
        retry_query = grounded_query + f"\n\nCorrection note: {verdict['suggestion']}"
        executor.reset_agent()
        retry_answer, retry_trace, retry_details = executor.execute_single(retry_query)
        r.trace += "\n---[RETRY]---\n" + (retry_trace or "")
        if retry_details.final_code: r.final_code = retry_details.final_code
        r.agent_tries += retry_details.tries
        r.stages_run.extend(["agent_retry"])
        retry_verdict = executor.judge_result(query, r.final_code, retry_answer)
        r.stages_run.append("judge_retry")
        r.judge_verdict = retry_verdict
        r.alignment_explanation = executor.explain_alignment(query, retry_verdict)
        raw_answer = retry_answer

12. r.answer = raw_answer; return r
```

---

### `eval/metrics.py`

#### Accuracy Scoring Rules

```
AutoIOT-Only has no judge → judge_verdict == {}
WellMax-Only has no judge → judge_verdict == {}  (but executed == True)
LLM-Only has no agent and no guardrail → executed == False, rejected == False

Score matrix:
  result.rejected                           → 0.0
  not result.executed                       → 0.0
  result.executed and verdict == "PASS"     → 1.0
  result.executed and verdict == "FAIL"     → 0.5
  result.executed and no judge (empty dict) → 0.5  ← AutoIOT-Only and WellMax-Only
```

#### `aggregate_metrics(results) -> pd.DataFrame`

```
Build list of dicts, one per RunResult:
  {
    "baseline": r.baseline,
    "query_id": i+1,  # index in WISDM_QUERIES order
    "accuracy_score": compute_accuracy(r)["score"],
    "latency_s": r.latency_s,
    "cost_usd": r.cost_usd,
    "executed": r.executed,
    "rejected": r.rejected,
    "judge_verdict": r.judge_verdict.get("verdict", "N/A"),
    "stages_run": ",".join(r.stages_run),
  }
df = pd.DataFrame(rows)
return df
```

---

### `eval/reporter.py`

#### `print_table(df: pd.DataFrame) -> None`

```
Print summary using tabulate:
  summary = df.groupby("baseline")[["accuracy_score","latency_s","cost_usd"]].mean().reset_index()
  summary.columns = ["Baseline", "Avg Accuracy", "Avg Latency (s)", "Avg Cost (USD)"]
  print(tabulate(summary, headers="keys", tablefmt="github", floatfmt=".4f"))
```

#### `save_markdown(results, path) -> None`

```
Write report.md with:
  1. ## Summary Table (aggregate metrics)
  2. ## Per-Query Results (one section per query: answer, trace snippet, judge verdict)
  3. ## Baseline Comparison (side-by-side for each query)
```

#### `save_csv(df, path) -> None`

```
df.to_csv(path, index=False)
```

---

### `eval/benchmark.py` — CLI

```python
def main():
    parser = argparse.ArgumentParser(description="Flash-Fusion Benchmark")
    parser.add_argument("--data", required=True)
    parser.add_argument("--baselines", default="all")
    parser.add_argument("--queries", default="all")
    parser.add_argument("--model", default="llama-3.3-70b-versatile")
    parser.add_argument("--output", default="flashfusion/eval_results/")
    args = parser.parse_args()

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("Error: GROQ_API_KEY environment variable not set")

    # Resolve baselines
    all_baselines = ["LLM_ONLY", "WELLMAX_ONLY", "AUTOIOT_ONLY", "FLASH_FUSION"]
    baselines = all_baselines if args.baselines == "all" else args.baselines.upper().split(",")

    # Resolve queries
    from flashfusion.eval.queries import WISDM_QUERIES
    all_query_ids = list(range(1, len(WISDM_QUERIES) + 1))
    if args.queries == "all":
        query_ids = all_query_ids
    else:
        query_ids = [int(x) for x in args.queries.split(",")]

    # Load data
    df_base = load_wisdm(args.data)
    adapter = WISDMAdapter()

    os.makedirs(args.output, exist_ok=True)
    results: list[RunResult] = []
    raw_results_path = os.path.join(args.output, "raw_results.jsonl")

    for baseline in baselines:
        for qid in query_ids:
            query_def = WISDM_QUERIES[qid - 1]
            query_text = query_def["text"]
            print(f"\n[{baseline}] Q{qid}: {query_text[:60]}...", flush=True)

            client = LLMClient(model_name=args.model, api_key=api_key)
            runner = BaselineRunner(
                mode=baseline, df=df_base.copy(),
                client=client, adapter=adapter
            )
            result = runner.run(query_text)
            results.append(result)

            # Print progress line
            j = result.judge_verdict.get("verdict", "N/A")
            print(f"  → executed={result.executed} rejected={result.rejected} "
                  f"judge={j} latency={result.latency_s:.1f}s cost=${result.cost_usd:.4f}")

            # Append to jsonl
            with open(raw_results_path, "a") as f:
                import json, dataclasses
                f.write(json.dumps(dataclasses.asdict(result)) + "\n")

    # Aggregate + report
    metrics_df = aggregate_metrics(results)
    save_csv(metrics_df, os.path.join(args.output, "metrics.csv"))
    save_markdown(results, os.path.join(args.output, "report.md"))
    print("\n=== Summary ===")
    print_table(metrics_df)
    print(f"\nResults written to {args.output}")

if __name__ == "__main__":
    main()
```

---

### `tests/`

#### `test_stages.py`

Test `Stage1_ConceptExtraction.run()`:
- Mock `LLMClient.invoke_chain` to return `"DATA: acceleration, timestamp\nREASONING: intensity"`
- Assert `result["DATA"] == ["acceleration", "timestamp"]`
- Assert `result["REASONING"] == ["intensity"]`
- Test retry: mock to return empty string first, then valid on second call
- Test keyword fallback: mock always returns empty string → assert DATA is non-empty

Test `Stage2_SchemaGrounding.run()`:
- Mock invoke_chain to return `"MAPPINGS:\n  acceleration → x, y, z\nUNMAPPABLE: NONE"`
- Assert `result["mappings"]` has 1 entry, `result["unmappable"] == []`

Test `Stage3_SubqueryGeneration.run()`:
- Mock to return `"SUB_Q1: [AGGREGATE] How many rows per activity?\nSYNTHESIS_HINT: List counts"`
- Assert `len(result["sub_queries"]) == 1`
- Assert `result["synthesis_hint"] == "List counts"`

#### `test_executor.py`

Test `ResilientReActOutputParser`:
- Test `_sanitize_action_input("df.head()\nThought: let me check")` → `"df.head()"`
- Test `_extract_best_answer("some text\nFinal Answer: 42")` → AgentFinish with output="42"
- Test P3 dedup: call parse() with same text MAX_IDENTICAL times → returns AgentFinish

Test `ExecutionLayer.guardrail()`:
- Mock invoke_chain to return "PROCEED" → assert returns (True, "")
- Mock invoke_chain to return "REJECT: no heart_rate column" → assert returns (False, "no heart_rate column")

#### `test_metrics.py`

```
Test compute_accuracy():
- RunResult(executed=True, judge_verdict={"verdict":"PASS"}) → score == 1.0
- RunResult(executed=True, judge_verdict={"verdict":"FAIL"}) → score == 0.5
- RunResult(executed=True, judge_verdict={}) → score == 0.5  (AutoIOT-Only)
- RunResult(executed=False) → score == 0.0
- RunResult(rejected=True) → score == 0.0

Test aggregate_metrics():
- Provide 4 RunResults (one per baseline) → DataFrame has 4 rows
- Assert columns: baseline, accuracy_score, latency_s, cost_usd
```

---

## Verification Checklist

```bash
# 1. Smoke test (3 queries × 4 baselines, ~5 min)
python -m flashfusion.eval.benchmark \
  --data chat/data/imu/WISDM_ar_v1.1_raw.txt \
    --baselines all --queries 1,5,12 \
  --output flashfusion/eval_results/

# Expected:
#   Q1:  all baselines should execute successfully
#   Q5:  FLASH_FUSION should produce grounded magnitude comparison
#   Q12: FLASH_FUSION should reject; AUTOIOT_ONLY and WELLMAX_ONLY typically attempt execution

# 2. Unit tests
pytest flashfusion/tests/ -v
# Expected: 15+ tests passing

# 3. Output files
ls flashfusion/eval_results/
# Expected: metrics.csv  report.md  raw_results.jsonl

# 4. Summary table
python -c "
import pandas as pd
df = pd.read_csv('flashfusion/eval_results/metrics.csv')
print(df.groupby('baseline')[['accuracy_score','latency_s','cost_usd']].mean())
"
# Expected: 4-row table with LLM_ONLY accuracy < FLASH_FUSION accuracy
```

---

## Common Pitfalls

1. **WISDM trailing semicolons**: Each data line ends with `;` — always `.rstrip(";")` before parsing.
2. **Activity label whitespace**: `parts[1].strip()` — raw values have leading spaces.
3. **`magnitude` column required by Q2, Q3, Q5, Q6**: Must be added by `WISDMAdapter.get_derived_features()` before agent execution for Flash-Fusion and before description for WellMax-Only.
4. **WellMax-Only has no judge**: `judge_verdict` must be `{}` — never add a judge call.
5. **AutoIOT-Only has no judge**: Same — `judge_verdict = {}` — accuracy scorer treats this as 0.5.
6. **Stage 2 codebook injection**: Format `SCHEMA_GROUNDING_PROMPT` with `{codebook}=adapter.get_codebook_str()` before building the chain. Without this, AutoIOT-Only and LLM-Only cannot resolve group names.
7. **Agent state leakage**: Always call `executor.reset_agent()` between sub-queries in Flash-Fusion. The `PythonAstREPLTool` is stateful.
8. **LLMClient per benchmark run**: Create a **new** `LLMClient` for each `(baseline, query)` pair so `call_log` is isolated and `total_cost_usd()` reflects only that one run.
9. **`PrivateAttr` in Pydantic models**: `ResilientReActOutputParser` inherits from a Pydantic model. Use `_output_history: list = PrivateAttr(default_factory=list)` — not a regular class attribute.
10. **Guardrail prompt formatting**: GUARDRAIL_PROMPT contains `{column_metadata}`. Format it at call time: `GUARDRAIL_PROMPT.format(column_metadata=meta_str)` — then use the formatted string as system message.
