# Copilot Tasks: Correct ReAct Structural Outcomes and Grounded Final Answers

## Goal

Update the ReAct baseline so benchmark scores reflect its actual outcomes:

1. A genuine abstention is emitted and propagated as `rejected=True` with a reason.
2. A successful Python execution result cannot be overwritten by free-form model hedging.
3. Abstention is structurally distinct from `False`, `None`, or prose saying insufficient data.
4. ReAct does not silently substitute proxy columns or invent predictive targets for concepts absent from the schema.

This work must **not** add Flash-Fusion typed operators, the Flash-Fusion guardrail, extra schema-grounding LLM calls, a new model, or additional data to ReAct. It only makes ReAct's existing execution and abstention behavior machine-checkable and prevents its final prose from contradicting successful tool execution.

Relevant files:

- `flashfusion/pipeline/executor.py`
- `flashfusion/baselines/react_only.py`
- the `RunResult`/benchmark serialization implementation under `flashfusion/eval/`
- `flashfusion/tests/test_executor.py`
- relevant evaluation/metrics tests

## Task 1 — Propagate structural rejection

### Problem

The ReAct benchmark output currently has `rejected=False` and an empty rejection reason for every query, including queries where ReAct explains that the data are unavailable. Thus evaluation cannot distinguish a genuine abstention from a normal answer.

### Implement

1. Ensure the return details from `ExecutionLayer.execute_single()` always include:

```python
{
    "rejected": bool,
    "rejection_reason": str | None,
    "answer_source": "executed_observation" | "structured_rejection" | "model_final_answer",
}
```

2. In `flashfusion/baselines/react_only.py`, transfer these values directly into `RunResult` (or its equivalent), including the JSONL/CSV serialization fields:

```python
result.rejected = bool(details["rejected"])
result.rejection_reason = details.get("rejection_reason") or ""
result.execution_path = "react_reject" if result.rejected else "react_agent"
```

3. Add a defensive assertion in the ReAct path:

```python
assert "rejected" in details
assert "answer_source" in details
```

4. Verify `raw_results.jsonl` preserves these fields per query; do not infer rejection later from arbitrary natural-language keywords.

### Acceptance tests

- A simulated explicit ReAct rejection yields `rejected=True`, a non-empty `rejection_reason`, `execution_path="react_reject"`, and `answer_source="structured_rejection"` in the saved run artifact.
- A normal successful query yields `rejected=False` and `answer_source="executed_observation"`.

## Task 2 — Terminate on successful execution

### Problem

For the chronological holdout prediction queries, ReAct executes correct code and obtains `Observation: moderate`, but then emits prose claiming that `moderate` is insufficient. The free-form `Final Answer` is currently allowed to override a valid executed result.

### Implement

When the `python_exec` action succeeds and returns a defined `result`, end the ReAct run immediately. Do not issue another model turn that lets it reinterpret or reject its own successful observation.

Implement a canonical renderer:

```python
def render_executed_result(query: str, value: object) -> str:
    """Serialize a successful Python result as the final ReAct answer.

    This is formatting only: the returned value is exactly the result of the
    code generated and executed by ReAct. It must not use an LLM.
    """
```

The returned `ReActResult` / details must contain:

```python
{
    "rejected": False,
    "rejection_reason": None,
    "answer_source": "executed_observation",
    "executed_value": <actual result>,
}
```

For scalar label predictions, produce a direct answer. Example:

```text
The predicted behavior label for the first holdout row is: moderate.
```

For general values, a safe generic format is:

```text
The result is: <serialized value>
```

Do not alter the generated Python, model choice, tool implementation, or result value.

### Acceptance tests

Simulate this trace:

```text
Action: python_exec
Action Input: result = "moderate"
Observation: moderate
Final Answer: The output "moderate" is insufficient information.
```

Assert that final saved answer contains `moderate`, does not contain the hedge, has `rejected=False`, and sets `answer_source="executed_observation"`.

Repeat for a number, boolean, list, and dictionary result.

## Task 3 — Add a parseable abstention action

### Problem

Natural-language statements such as `"No, rainy weather did not cause..."`, `result = False`, `result = None`, and `"Insufficient data"` have different meanings. None reliably communicates that the model is abstaining. The evaluator should not guess from prose.

### Implement

Add one terminal ReAct action:

```text
Action: reject_query
Action Input: <one concise reason naming the missing required concept(s)>
```

Add this exact instruction to the ReAct prompt when the abstention clause is enabled:

```text
If a query cannot be answered from the available dataset columns, do not call
python_exec. Return exactly:

Action: reject_query
Action Input: Missing required dataset concept(s): <concepts>.
```

Extend the parser/executor so `reject_query` is terminal and constructs:

```python
{
    "rejected": True,
    "rejection_reason": action_input,
    "answer_source": "structured_rejection",
    "executed_value": None,
}
```

Important requirements:

- `False`, `None`, a string, or a natural-language refusal produced through `python_exec` are **not** rejections.
- Only the explicit `reject_query` action sets `rejected=True`.
- A successful `python_exec` result remains a normal answer, even if it is falsey (`0`, `False`, empty list, or empty string). Do not classify successful output by truthiness.

### Acceptance tests

- `Action: reject_query` with a reason produces structural rejection.
- `Action: python_exec` returning `False` remains `rejected=False`.
- `Action: python_exec` returning `None` remains `rejected=False`; it may receive an explicit "no result" answer, but it is not an abstention.
- `Action: python_exec` returning a text explanation such as `"weather unavailable"` remains `rejected=False`.

## Task 4 — Block proxy substitution and invented targets

### Problem

ReAct currently does two invalid things on unsupported queries:

1. For passenger-occupancy versus road-roughness, it substitutes unrelated available columns (`accel_variance`, `instability_score`) and reports a correlation as if those columns represented the requested real-world concepts.
2. For pothole-repair forecasting, it invents a target label from an `instability_score` threshold, trains a regressor, and reports the resulting proxy forecast as a pothole-repair prediction.

These are semantic failures. They should be prevented by the ReAct prompt's abstention rules, not excused by the judge.

### Implement

Add the following prompt rules to the ReAct abstention-enabled prefix:

```text
Schema-grounding rule:
Every named real-world concept in the query must map to either:
(1) an exact dataset column,
(2) an explicitly documented derived feature, or
(3) a value computed only from such grounded columns.

Never substitute a semantically related sensor feature for a requested concept.
If any required concept has no exact or documented mapping, use reject_query.
Do not call python_exec.
```

```text
Predictive-task rule:
A predictive query is executable only when the requested target column exists
in the dataset and the target is observed in the training rows.

Do not invent labels, proxy targets, event definitions, thresholds, future
outcomes, road segments, maintenance records, or external metadata. If the
requested prediction target or its required historical labels are absent, use
reject_query. Do not call python_exec.
```

Do not add an LLM grounding call or Flash-Fusion's typed operator system. This remains a prompt-level capability constraint for ReAct.

### Acceptance tests

Use mocked model responses to confirm these prompt rules are present in the ReAct-abstention prefix.

Add integration fixtures that assert the desired ReAct action for these cases:

| Query type | Expected terminal outcome |
|---|---|
| Passenger occupancy correlated with road roughness, neither concept in schema | `reject_query` naming both missing concepts |
| Rain/weather cause query with no weather column | `reject_query` naming weather metadata |
| Driver identity/schedule query with no corresponding metadata | `reject_query` naming the missing concept |
| Forecast pothole repairs with no repair label/history | `reject_query` naming pothole repair labels/history |
| Explicit chronological model training with existing target column `behavior` | `python_exec`, then a grounded executed prediction |

## Benchmark/evaluation acceptance criteria

After implementation, run the BUS ReAct suite and verify:

1. Rejection-required examples include `rejected: true` and `rejection_reason` in `raw_results.jsonl`.
2. The correct model prediction in chronological holdout examples is returned directly from the successful executed observation, e.g. `moderate`; no generated hedge paragraph replaces it.
3. Passenger-occupancy and pothole-repair examples do not execute proxy/invented-target Python code when the abstention clause is enabled.
4. The judge remains strict: it should continue to fail a substantive unsupported claim such as `"No, rainy weather did not cause..."` when no structural rejection was emitted.
5. Do not change the LLM judge's semantic standards. The executor must emit correct structured state; the evaluator should consume that state rather than infer it from prose.

## Fairness constraint

This task improves only output-contract reliability and scope obedience for ReAct. It must not import or invoke Flash-Fusion's typed vocabulary, typed plan validator, full guardrail planner, operator router, or an additional LLM call. ReAct must remain a free-form Python-generating agent with the same model and tools.