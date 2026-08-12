# Copilot Task: Give paper-faithful ReAct the resilient parser and a structural verdict, without changing reasoning, tools, or abstention wording

## Context

`flashfusion/pipeline/executor.py` (`ExecutionLayer.__init__`) currently branches on
`react_faithful`:

- `react_faithful=True` → paper-faithful ReAct prompt (includes the out-of-scope
  abstention clause) + the **default** `ReActSingleInputOutputParser`.
- `react_faithful=False` → Flash-Fusion's own **resilient** parser (documented in
  `CLAUDE.md §ResilientReActOutputParser`), which explicitly handles two known
  failure modes:
  - `P0-loop-1`: essay output (no `Action:` / no `Final Answer:`) → counted as a
    parse failure; after `MAX_PARSE_FAILURES`, extract the best candidate answer.
  - `P0-loop-2`: both `Action:` and `Final Answer:` present → prefer `Action`.

`flashfusion/baselines/react_only.py` reads `REACT_NO_ABSTENTION` (default `"0"`)
and sets `react_faithful=not no_abstention`. So today, **the only way to get the
resilient parser is to also drop the abstention clause** — these two independent
concerns (prompt content vs. output-parsing robustness) are incorrectly coupled to
the same flag.

Evaluation evidence (paired run comparison) shows the paper-faithful path failing
almost exclusively on P0-loop-1: the model computes a correct `Observation` via
tool execution, then writes free-form prose that doubts or restates that value
instead of emitting a clean `Final Answer:`. The **non-resilient** parser has no
fallback for this, so a correct execution becomes a graded failure. This is a
format/parsing problem, not a reasoning gap — Qwen3-Max clearly computed the right
value in these traces.

## Goal

Make the resilient parser (P0-loop-1 / P0-loop-2 handling) available on the
**paper-faithful** path too, without changing:

- the abstention clause wording or its presence/absence,
- the tools available to the agent,
- the underlying model, temperature, or any other generation parameter,
- the reasoning task itself.

This isolates the comparison to what it should measure: model + prompt reasoning
quality, not incidental output-format brittleness. It must remain possible to
still run the *original* strict paper-faithful parser as an explicit opt-in, for
anyone who wants the literal, unmodified ReAct paper behavior.

## Required changes

### 1. Decouple "abstention clause" from "parser choice" in `ExecutionLayer`

In `flashfusion/pipeline/executor.py`, change the constructor signature from a
single `react_faithful: bool` to two independent flags:

```python
def __init__(
    self,
    df: pd.DataFrame,
    client: "LLMClient",
    include_abstention_clause: bool = False,
    use_resilient_parser: bool = True,
) -> None:
    """
    Args:
        df: ...
        client: ...
        include_abstention_clause: When True, includes the paper-style
            out-of-scope abstention rule in the prompt prefix. Controls
            PROMPT CONTENT only.
        use_resilient_parser: When True, uses Flash-Fusion's resilient
            output parser (P0-loop-1 / P0-loop-2 handling — see
            CLAUDE.md §ResilientReActOutputParser). When False, uses the
            strict default ReActSingleInputOutputParser. Controls OUTPUT
            PARSING only, independent of prompt content.
    """
```

- `_build_prefix(...)` should branch only on `include_abstention_clause`.
- Parser selection should branch only on `use_resilient_parser`.
- Keep the old `react_faithful` parameter accepted for backward compatibility,
  mapped internally to `include_abstention_clause=react_faithful,
  use_resilient_parser=not react_faithful`, but emit a deprecation warning
  directing callers to the two new flags.

### 2. Update `react_only.py` to use both flags explicitly

In `flashfusion/baselines/react_only.py`, replace the single-flag branch:

```python
import os

no_abstention = os.environ.get("REACT_NO_ABSTENTION", "0").strip().lower() in {"1", "true", "yes"}
no_resilient_parser = os.environ.get("REACT_STRICT_PARSER", "0").strip().lower() in {"1", "true", "yes"}

executor = ExecutionLayer(
    df,
    client,
    include_abstention_clause=not no_abstention,
    use_resilient_parser=not no_resilient_parser,
)
raw_answer, trace, details = executor.execute_single(query)
```

Default behavior (no env vars set) becomes: abstention clause **present** AND
resilient parser **enabled**. This is the fair default for benchmark comparisons.

`REACT_STRICT_PARSER=1` remains available for anyone who explicitly wants the
literal unmodified-paper parser behavior (with the caveat that it will reproduce
the P0-loop-1/P0-loop-2 failure modes on purpose, for ablation studies only — not
for the headline FF-vs-ReAct comparison).

### 3. Extend the resilient parser's `P0-loop-1` handling to require answer grounding

The existing resilient-parser fallback ("after `MAX_PARSE_FAILURES` → extract best
answer") should be tightened so that when a tool call already produced a valid,
non-error `Observation` for the current step, the extractor prefers that value
over any hedging text the model wrote afterward. Add a small grounding utility:

```python
def _ground_final_answer(
    last_observation: str | None,
    last_observation_ok: bool,
    model_text: str,
) -> str:
    """
    If the most recent tool observation succeeded and the model's free-form
    text does not contain an explicit new Action, prefer the observation
    value over any hedging/self-doubting prose ("insufficient information",
    "cannot determine", "please provide", "I will outline the steps", etc.).

    This does not change what the model reasoned or computed — it only
    changes which of the model's own outputs (Observation vs. free text)
    is treated as authoritative when they conflict, given that Observation
    is a value that was actually executed and the free text was not.
    """
```

Wire this into the same fallback path already triggered by `P0-loop-1` in
`executor.py`, so a correct `result = <value>` execution can no longer be
silently overridden by a hedged natural-language paragraph.

### 4. Add a structural verdict field alongside the natural-language answer

Currently, out-of-scope rejection for ReAct is expressed only as free-form prose
embedded in the final answer, so the benchmark harness cannot reliably tell
"answered" apart from "explained why it can't answer." Flash-Fusion's guardrail
path already emits a structured `rejected: bool` — extend `execute_single` to
return an equivalent structural field for ReAct:

```python
@dataclass
class ReActResult:
    raw_answer: str
    trace: str
    rejected: bool          # NEW: True only if the agent explicitly used the
                             # abstention action/format, not inferred from prose
    rejection_reason: str | None
    details: dict
```

Populate `rejected` by requiring the abstention clause (when
`include_abstention_clause=True`) to specify a single, parser-recognizable
action name, e.g.:

```text
If the request cannot be answered from the available columns, respond with
exactly:
Action: reject_query
Action Input: <one-sentence reason citing the missing column/concept>
```

Have the resilient parser recognize `Action: reject_query` as a first-class
terminal action (distinct from `Final Answer:`) and set `rejected=True,
rejection_reason=<Action Input>` directly, instead of requiring the harness to
infer rejection from the wording of a natural-language answer. This does not add
new reasoning capability or new information to the model — it only gives the
existing abstention instruction an unambiguous, machine-checkable output channel,
mirroring how Flash-Fusion's guardrail already reports rejection.

### 5. Tests

Add/extend tests in `flashfusion/tests/test_executor.py`:

- `test_include_abstention_clause_independent_of_parser`: construct
  `ExecutionLayer` with all four combinations of the two new flags and assert the
  prefix contains/excludes the abstention clause independently of parser choice.
- `test_resilient_parser_grounds_final_answer_to_observation`: simulate a trace
  where the last tool `Observation` is a valid computed value and the model's
  subsequent text hedges ("insufficient information", "cannot determine") with no
  new `Action`; assert the resilient parser returns the observation value, not the
  hedge text.
- `test_reject_query_action_sets_structural_verdict`: simulate a trace containing
  `Action: reject_query` with a reason; assert `ReActResult.rejected is True` and
  `rejection_reason` matches the action input.
- `test_backward_compatible_react_faithful_flag`: confirm the old
  `react_faithful=True/False` constructor call still works via the internal
  mapping and emits a deprecation warning.

## Acceptance criteria

- Default benchmark run (`run_react_flashfusion_qwen_all_datasets.sh`, no env
  overrides) now uses: abstention clause present + resilient parser enabled +
  answer grounding + structural `reject_query` action.
- No change to: model, temperature, max tokens, tool set, dataset, or the wording
  of the abstention rule's *content* (only its output-format contract changes, to
  make it parser-recognizable).
- Re-run the paired evaluation on the same query set used in this analysis
  (BUS/MIT-ECG/WISDM). The P0-loop-1 cases identified above (chronological
  train/holdout predictions where a correct `Observation` was overridden by a
  hedge) should now score PASS, since the grounding step reads the executed value
  directly.
- Rejection-required cases (weather, passenger occupancy, driver identity,
  pothole-repair prediction) should now be scored by the structural `rejected`
  flag rather than by string-matching prose, removing the current inconsistency
  where correct reasoning was graded as FAIL purely because it was phrased as an
  answer instead of a formal rejection.
- `REACT_STRICT_PARSER=1` and `REACT_NO_ABSTENTION=1` remain available,
  independently, for ablations — but neither is set by default, so the headline
  comparison against Flash-Fusion is not confounded by ReAct's own parser
  brittleness.