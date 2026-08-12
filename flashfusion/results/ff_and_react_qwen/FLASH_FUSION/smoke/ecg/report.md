# Flash-Fusion Benchmark Report

Generated: 2026-08-12T15:21:37+00:00

## Summary Table

| Baseline     |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-------------|-------------------:|------------------:|-----------------:|
| FLASH_FUSION |             1.0000 |            7.4771 |           0.0070 |

## Typed-Operator Coverage

| Baseline     |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject |   Typed avg latency (s) | Fallback avg latency (s)   |
|:-------------|----------:|--------:|:-----------|-----------------:|-------------------:|------------------------:|:---------------------------|
| FLASH_FUSION |         3 |       3 | 100%       |                0 |                  0 |                    7.48 | -                          |

## Per-Query Results

### Q5: For record_id 208, what is the average count of rows with a non-empty annotation in each 60-second time_s bin?

#### FLASH_FUSION

**Answer:** The result is 98.06451612903226

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: FILTER_COMPARE,FILTER_NOT_EMPTY,DERIVE_BIN,GROUP_AGGREGATE,AGGREGATE_GROUPS
- Latency: 5.20s | Cost: $0.00696

**Agent Trace**

```
Thought: typed operator step 1 (FILTER_COMPARE)
Action: typed_operator_exec
Action Input: df = df[df['record_id'] eq 208]
Observation: rows=650000
Thought: typed operator step 2 (FILTER_NOT_EMPTY)
Action: typed_operator_exec
Action Input: df = df[df['annotation'].notna() & df['annotation'].astype(str).str.strip().ne('')]
Observation: rows=3040
Thought: typed operator step 3 (DERIVE_BIN)
Action: typed_operator_exec
Action Input: df['time_bin_60s'] = (df['time_s'] // 60.0) * 60.0
Observation: derived 'time_bin_60s' (kind=numeric width=60.0)
Thought: typed operator step 4 (GROUP_AGGREGATE)
Action: typed_operator_exec
Action Input: result = df.groupby('time_bin_60s').size()
Observation: {'0.0': 111, '60.0': 105, '120.0': 106, '180.0': 109, '240.0': 106, '300.0': 109, '360.0': 108, '420.0': 95, '480.0': 94, '540.0': 97, '600.0': 101, '660.0': 98, '720.0': 95, '780.0': 98, '840.0': 99, '900.0': 103, '960.0': 97, '1020.0': 97, '1080.0': 94, '1140.0': 106, '1200.0': 112, '1260.0': 103, '1320.0': 103, '1380.0': 107, '1440.0': 103, '1500.0': 108, '1560.0': 93, '1620.0': 93, '1680.0': 89, '1740.0': 92, '1800.0': 9}
Thought: typed operator step 5 (AGGREGATE_GROUPS)
Action: typed_operator_exec
Action Input: result = result.mean()
Observation: 98.06451612903226
Final Answer: 98.06451612903226
```

**Final Code Executed**

```python
df = df[df['record_id'] eq 208]
df = df[df['annotation'].notna() & df['annotation'].astype(str).str.strip().ne('')]
df['time_bin_60s'] = (df['time_s'] // 60.0) * 60.0
result = df.groupby('time_bin_60s').size()
result = result.mean()
```

---

### Q6: Which record_id exhibits the largest difference between maximum and minimum MLII?

#### FLASH_FUSION

**Answer:** The result is {'record_id': 116, 'MLII_range': 10.235}

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: PARALLEL_AGGREGATE,DERIVE_BINARY,RANK_ROWS
- Latency: 5.44s | Cost: $0.00918

**Agent Trace**

```
Thought: typed operator step 1 (PARALLEL_AGGREGATE)
Action: typed_operator_exec
Action Input: # PARALLEL_AGGREGATE branches:
branch_0 = df.groupby(['record_id'])['MLII'].max()
branch_1 = df.groupby(['record_id'])['MLII'].min()
merged = branch_0.merge(branch_1, on=['record_id'], how='outer').fillna(0)
Observation: {'groups': 40, 'columns': ['record_id', 'max_MLII', 'min_MLII']}
Thought: typed operator step 2 (DERIVE_BINARY)
Action: typed_operator_exec
Action Input: df['MLII_range'] = (df['max_MLII'] - df['min_MLII']).abs()
Observation: derived 'MLII_range' (rows=40)
Thought: typed operator step 3 (RANK_ROWS)
Action: typed_operator_exec
Action Input: idx = df['MLII_range'].idxmax(); result = df.loc[idx, ['record_id', 'MLII_range']].to_dict()
Observation: {'record_id': 116, 'MLII_range': 10.235}
Final Answer: {'record_id': 116, 'MLII_range': 10.235}
```

**Final Code Executed**

```python
# PARALLEL_AGGREGATE branches:
branch_0 = df.groupby(['record_id'])['MLII'].max()
branch_1 = df.groupby(['record_id'])['MLII'].min()
merged = branch_0.merge(branch_1, on=['record_id'], how='outer').fillna(0)
df['MLII_range'] = (df['max_MLII'] - df['min_MLII']).abs()
idx = df['MLII_range'].idxmax(); result = df.loc[idx, ['record_id', 'MLII_range']].to_dict()
```

---

### Q14: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is Random forest predicts present '0' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 11.79s | Cost: $0.00476

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
