# Flash-Fusion Benchmark Report

Generated: 2026-08-12T18:17:06+00:00

## Summary Table

| Baseline     |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-------------|-------------------:|------------------:|-----------------:|
| FLASH_FUSION |             1.0000 |            6.0816 |           0.0022 |

## Typed-Operator Coverage

| Baseline     |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject |   Typed avg latency (s) | Fallback avg latency (s)   |
|:-------------|----------:|--------:|:-----------|-----------------:|-------------------:|------------------------:|:---------------------------|
| FLASH_FUSION |         4 |       4 | 100%       |                0 |                  0 |                    6.08 | -                          |

## Per-Query Results

### Q13: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is Logistic regression predicts present '0' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 4.03s | Cost: $0.00464

**Agent Trace**

```
Thought: typed operator step 1 (PREDICTIVE_PIPELINE)
Action: typed_operator_exec
Action Input: # sort_by=['time_s'] split=520000/650000 model='logistic_regression' features=['MLII', 'V1']
result = "Logistic regression predicts present '0' for the first holdout row."
Observation: Logistic regression predicts present '0' for the first holdout row.
Final Answer: Logistic regression predicts present '0' for the first holdout row.
```

**Final Code Executed**

```python
# sort_by=['time_s'] split=520000/650000 model='logistic_regression' features=['MLII', 'V1']
result = "Logistic regression predicts present '0' for the first holdout row."
```

---

### Q14: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is Random forest predicts present '0' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 11.51s | Cost: $0.00139

**Agent Trace**

```
Thought: typed operator step 1 (PREDICTIVE_PIPELINE)
Action: typed_operator_exec
Action Input: # sort_by=['time_s'] split=520000/650000 model='random_forest' features=['MLII', 'V1']
result = "Random forest predicts present '0' for the first holdout row."
Observation: Random forest predicts present '0' for the first holdout row.
Final Answer: Random forest predicts present '0' for the first holdout row.
```

**Final Code Executed**

```python
# sort_by=['time_s'] split=520000/650000 model='random_forest' features=['MLII', 'V1']
result = "Random forest predicts present '0' for the first holdout row."
```

---

### Q15: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a 1-nearest-neighbor model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is 1-nearest-neighbor predicts present '0' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 3.92s | Cost: $0.00140

**Agent Trace**

```
Thought: typed operator step 1 (PREDICTIVE_PIPELINE)
Action: typed_operator_exec
Action Input: # sort_by=['time_s'] split=520000/650000 model='one_nearest_neighbor' features=['MLII', 'V1']
result = "1-nearest-neighbor predicts present '0' for the first holdout row."
Observation: 1-nearest-neighbor predicts present '0' for the first holdout row.
Final Answer: 1-nearest-neighbor predicts present '0' for the first holdout row.
```

**Final Code Executed**

```python
# sort_by=['time_s'] split=520000/650000 model='one_nearest_neighbor' features=['MLII', 'V1']
result = "1-nearest-neighbor predicts present '0' for the first holdout row."
```

---

### Q16: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a hist gradient boosting model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is Hist gradient boosting predicts present '0' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 4.86s | Cost: $0.00139

**Agent Trace**

```
Thought: typed operator step 1 (PREDICTIVE_PIPELINE)
Action: typed_operator_exec
Action Input: # sort_by=['time_s'] split=520000/650000 model='hist_gradient_boosting' features=['MLII', 'V1']
result = "Hist gradient boosting predicts present '0' for the first holdout row."
Observation: Hist gradient boosting predicts present '0' for the first holdout row.
Final Answer: Hist gradient boosting predicts present '0' for the first holdout row.
```

**Final Code Executed**

```python
# sort_by=['time_s'] split=520000/650000 model='hist_gradient_boosting' features=['MLII', 'V1']
result = "Hist gradient boosting predicts present '0' for the first holdout row."
```

---
