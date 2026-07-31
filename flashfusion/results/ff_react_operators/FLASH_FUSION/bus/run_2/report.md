# Flash-Fusion Benchmark Report

Generated: 2026-07-30T20:17:38+00:00

## Summary Table

| Baseline     |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-------------|-------------------:|------------------:|-----------------:|
| FLASH_FUSION |             0.9375 |           10.0593 |           0.0036 |

## Typed-Operator Coverage

| Baseline     |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject |   Typed avg latency (s) |   Fallback avg latency (s) |
|:-------------|----------:|--------:|:-----------|-----------------:|-------------------:|------------------------:|---------------------------:|
| FLASH_FUSION |        15 |       9 | 60%        |                2 |                  4 |                    8.68 |                      27.87 |

Fallback causes: structural=2

## Per-Query Results

### Q1: What is the maximum accel_variance observed in this dataset?

#### FLASH_FUSION

**Answer:** [ERROR] ResponseValidationError: Response validation failed: EOF while parsing a value at line 505 column 0

- Executed: False | Rejected: False | Judge: N/A
- Stages: 
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Exception during FLASH_FUSION execution:
    response = self.client.chat.send(messages=sdk_messages, **params)
  File "/Users/kausar/Documents/research/flash-fusion/.venv/lib/python3.14/site-packages/openrouter/chat.py", line 644, in send
    return unmarshal_json_response(
        components.ChatResult, http_res, http_res_text
    )
  File "/Users/kausar/Documents/research/flash-fusion/.venv/lib/python3.14/site-packages/openrouter/utils/unmarshal_json_response.py", line 33, in unmarshal_json_response
    raise errors.ResponseValidationError(
    ...<4 lines>...
    ) from e
openrouter.errors.responsevalidationerror.ResponseValidationError: Response validation failed: EOF while parsing a value at line 505 column 0

---

### Q2: What is the average accel_mean across all recorded samples?

#### FLASH_FUSION

**Answer:** The result is 9.283388022969648

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: AGGREGATE_COLUMN
- Latency: 3.37s | Cost: $0.00505

**Agent Trace**

```
Thought: typed operator step 1 (AGGREGATE_COLUMN)
Action: typed_operator_exec
Action Input: result = df['accel_mean'].mean()
Observation: 9.283388022969648
Final Answer: 9.283388022969648
```

**Final Code Executed**

```python
result = df['accel_mean'].mean()
```

---

### Q3: List all timestamps where accel_stats_z_p99 reaches its maximum value across the dataset.

#### FLASH_FUSION

**Answer:** The result is ['2025-06-06T16:01:13', '2025-06-06T16:01:16', '2025-06-06T16:01:19', '2025-06-06T16:01:22', '2025-06-06T16:01:25', '2025-06-06T16:01:28', '2025-06-06T16:01:31', '2025-06-06T16:01:34', '2025-06-06T16:01:40', '2025-06-06T16:01:43', '2025-06-06T16:01:46', '2025-06-06T16:01:49', '2025-06-06T16:01:52', '2025-06-06T16:01:55', '2025-06-06T16:01:58', '2025-06-06T16:02:01']

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: AGGREGATE_COLUMN,FILTER_EQ_AGGREGATE,SELECT_COLUMN
- Latency: 2.58s | Cost: $0.00510

**Agent Trace**

```
Thought: typed operator step 1 (AGGREGATE_COLUMN)
Action: typed_operator_exec
Action Input: result = df['accel_stats_z_p99'].max()
Observation: 16.702
Thought: typed operator step 2 (FILTER_EQ_AGGREGATE)
Action: typed_operator_exec
Action Input: _v = df['accel_stats_z_p99'].max(); df = df[df['accel_stats_z_p99'] == _v]
Observation: rows=16 (accel_stats_z_p99==16.702)
Thought: typed operator step 3 (SELECT_COLUMN)
Action: typed_operator_exec
Action Input: result = df['timestamp'].tolist()
Observation: ['2025-06-06T16:01:13', '2025-06-06T16:01:16', '2025-06-06T16:01:19', '2025-06-06T16:01:22', '2025-06-06T16:01:25', '2025-06-06T16:01:28', '2025-06-06T16:01:31', '2025-06-06T16:01:34', '2025-06-06T16:01:40', '2025-06-06T16:01:43', '2025-06-06T16:01:46', '2025-06-06T16:01:49', '2025-06-06T16:01:52', '2025-06-06T16:01:55', '2025-06-06T16:01:58', '2025-06-06T16:02:01']
Final Answer: ['2025-06-06T16:01:13', '2025-06-06T16:01:16', '2025-06-06T16:01:19', '2025-06-06T16:01:22', '2025-06-06T16:01:25', '2025-06-06T16:01:28', '2025-06-06T16:01:31', '2025-06-06T16:01:34', '2025-06-06T16:01:40', '2025-06-06T16:01:43', '2025-06-06T16:01:46', '2025-06-06T16:01:49', '2025-06-06T16:01:52', '2025-06-06T16:01:55', '2025-06-06T16:01:58', '2025-06-06T16:02:01']
```

**Final Code Executed**

```python
result = df['accel_stats_z_p99'].max()
_v = df['accel_stats_z_p99'].max(); df = df[df['accel_stats_z_p99'] == _v]
result = df['timestamp'].tolist()
```

---

### Q4: How many data samples show an accel_variance strictly greater than 0.20?

#### FLASH_FUSION

**Answer:** The result is 352

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: FILTER_COMPARE,COUNT_ROWS
- Latency: 62.01s | Cost: $0.00507

**Agent Trace**

```
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

**Final Code Executed**

```python
df = df[df['accel_variance'] gt 0.2]
result = len(df)
```

---

### Q5: Is the northern half of the route (latitude above median) rougher than the southern half, based on average acceleration variance?

#### FLASH_FUSION

**Answer:** The result is {'higher': 'north', 'lower': 'south', 'metric': 'mean accel_variance', 'north': 0.3703333333333333, 'south': 0.16309016393442624, 'difference': 0.20724316939890705}

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: SPLIT_BY_THRESHOLD,SPLIT_BY_THRESHOLD,AGGREGATE_PARTITIONS,COMPARE_PARTITIONS
- Latency: 4.18s | Cost: $0.00513

**Agent Trace**

```
Thought: typed operator step 1 (SPLIT_BY_THRESHOLD)
Action: typed_operator_exec
Action Input: north = df[df['latitude'] gt df['latitude'].median()]
Observation: north: rows=609 (latitude gt median=33.776676)
Thought: typed operator step 2 (SPLIT_BY_THRESHOLD)
Action: typed_operator_exec
Action Input: south = df[df['latitude'] lte df['latitude'].median()]
Observation: south: rows=610 (latitude lte median=33.776676)
Thought: typed operator step 3 (AGGREGATE_PARTITIONS)
Action: typed_operator_exec
Action Input: result = {label: agg(partition) for label in partitions}
Observation: {'north': 0.3703333333333333, 'south': 0.16309016393442624}
Thought: typed operator step 4 (COMPARE_PARTITIONS)
Action: typed_operator_exec
Action Input: result = compare(north, south, mode='difference')
Observation: {'higher': 'north', 'lower': 'south', 'metric': 'mean accel_variance', 'north': 0.3703333333333333, 'south': 0.16309016393442624, 'difference': 0.20724316939890705}
Final Answer: {'higher': 'north', 'lower': 'south', 'metric': 'mean accel_variance', 'north': 0.3703333333333333, 'south': 0.16309016393442624, 'difference': 0.20724316939890705}
```

**Final Code Executed**

```python
north = df[df['latitude'] gt df['latitude'].median()]
south = df[df['latitude'] lte df['latitude'].median()]
result = {label: agg(partition) for label in partitions}
result = compare(north, south, mode='difference')
```

---

### Q6: Which location (latitude, longitude) recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration?

#### FLASH_FUSION

**Answer:** The location that recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration is: 
Latitude: 33.776765, Longitude: -84.389963.

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,react_fallback,agent
- Path: react_fallback
- Fallback (structural): structural: 2 validation errors for GuardrailAndPlan
plan.steps.0.PARALLEL_AGGREGATE.branches.0.aggregate
  Input should be 'min', 'max', 'mean', 'median', 'sum', 'count', 'std', 'var', 'nunique' or 'rms' [type=literal_error, input_value='percentile_99', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/literal_error
plan.steps.0.PARALLEL_AGGREGATE.branches.1.aggregate
  Input should be 'min', 'max', 'mean', 'median', 'sum', 'count', 'std', 'var', 'nunique' or 'rms' [type=literal_error, input_value='percentile_1', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/literal_error
- Latency: 20.15s | Cost: $0.00587

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: result = df.loc[(df['accel_stats_z_p99'] - df['accel_stats_z_p1']).idxmax()][['latitude', 'longitude']]
Observation: latitude     33.776765
longitude   -84.389963
Name: 721, dtype: object
Final Answer: The location that recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration is: 
Latitude: 33.776765, Longitude: -84.389963.
```

**Final Code Executed**

```python
result = df.loc[(df['accel_stats_z_p99'] - df['accel_stats_z_p1']).idxmax()][['latitude', 'longitude']]
```

---

### Q7: Calculate the average overall magnitude of peak acceleration using the 99th percentiles of the X, Y, and Z axes.

#### FLASH_FUSION

**Answer:** The result is 11.458505525284506

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: DERIVE_VECTOR_MAGNITUDE,AGGREGATE_COLUMN
- Latency: 5.45s | Cost: $0.00511

**Agent Trace**

```
Thought: typed operator step 1 (DERIVE_VECTOR_MAGNITUDE)
Action: typed_operator_exec
Action Input: df['peak_magnitude'] = (df['accel_stats_x_p99']**2 + df['accel_stats_y_p99']**2 + df['accel_stats_z_p99']**2)**0.5
Observation: derived 'peak_magnitude' (rows=1219)
Thought: typed operator step 2 (AGGREGATE_COLUMN)
Action: typed_operator_exec
Action Input: result = df['peak_magnitude'].mean()
Observation: 11.458505525284506
Final Answer: 11.458505525284506
```

**Final Code Executed**

```python
df['peak_magnitude'] = (df['accel_stats_x_p99']**2 + df['accel_stats_y_p99']**2 + df['accel_stats_z_p99']**2)**0.5
result = df['peak_magnitude'].mean()
```

---

### Q8: If we group the data into 1-minute intervals, which time window experienced the most sustained turbulence, based on instability score?

#### FLASH_FUSION

**Answer:** The 1-minute interval starting at 2025-06-06 16:01:00 experienced the most sustained turbulence.

- Executed: True | Rejected: False | Judge: N/A
- Stages: guardrail_plan,react_fallback,agent
- Path: react_fallback
- Fallback (structural): structural: 1 validation error for GuardrailAndPlan
plan.steps.1.PARALLEL_AGGREGATE.branches
  List should have at least 2 items after validation, not 1 [type=too_short, input_value=[{'filter_column': None, ...n': 'mean_instability'}], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/too_short
- Latency: 35.60s | Cost: $0.00581

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Group the data into 1-minute intervals
df['timestamp_minute'] = df['timestamp'].dt.floor('min')
grouped_df = df.groupby('timestamp_minute')['instability_score'].sum().reset_index()

# Find the time window with the most sustained turbulence
result = grouped_df.loc[grouped_df['instability_score'].idxmax()]['timestamp_minute']
Observation: 2025-06-06 16:01:00
Final Answer: The 1-minute interval starting at 2025-06-06 16:01:00 experienced the most sustained turbulence.
```

**Final Code Executed**

```python
# Group the data into 1-minute intervals
df['timestamp_minute'] = df['timestamp'].dt.floor('min')
grouped_df = df.groupby('timestamp_minute')['instability_score'].sum().reset_index()

# Find the time window with the most sustained turbulence
result = grouped_df.loc[grouped_df['instability_score'].idxmax()]['timestamp_minute']
```

---

### Q9: How does passenger occupancy correlate with road roughness during this trip?

#### FLASH_FUSION

**Answer:** Query rejected. Reason: The question requires external data about road roughness, which is not present in the provided schema.. This request is not supported by the current dataset schema or task scope.

- Executed: False | Rejected: True | Judge: N/A
- Stages: guardrail_plan
- Path: guardrail_reject
- Latency: 10.34s | Cost: $0.00503

**Alignment Explanation**

Rejected by the guardrail because the query cannot be answered from available dataset fields. Reason: The question requires external data about road roughness, which is not present in the provided schema.

**Rejection Reasoning**

- The question requires external data about road roughness, which is not present in the provided schema.

---

### Q10: Did rainy weather cause the roughest segments in this route?

#### FLASH_FUSION

**Answer:** Query rejected. Reason: The question requires external data about the weather, which is not present in the provided schema.. This request is not supported by the current dataset schema or task scope.

- Executed: False | Rejected: True | Judge: N/A
- Stages: guardrail_plan
- Path: guardrail_reject
- Latency: 3.26s | Cost: $0.00505

**Alignment Explanation**

Rejected by the guardrail because the query cannot be answered from available dataset fields. Reason: The question requires external data about the weather, which is not present in the provided schema.

**Rejection Reasoning**

- The question requires external data about the weather, which is not present in the provided schema.

---

### Q11: Was the bus driver complying with their operating schedule?

#### FLASH_FUSION

**Answer:** Query rejected. Reason: The question requires external data about the operating schedule, which is not present in the provided schema.. This request is not supported by the current dataset schema or task scope.

- Executed: False | Rejected: True | Judge: N/A
- Stages: guardrail_plan
- Path: guardrail_reject
- Latency: 1.16s | Cost: $0.00503

**Alignment Explanation**

Rejected by the guardrail because the query cannot be answered from available dataset fields. Reason: The question requires external data about the operating schedule, which is not present in the provided schema.

**Rejection Reasoning**

- The question requires external data about the operating schedule, which is not present in the provided schema.

---

### Q12: Predict next week's pothole repairs for the road segments in this dataset.

#### FLASH_FUSION

**Answer:** Query rejected. Reason: The question requires a forecast based on external data and cannot be computed from the provided columns.. This request is not supported by the current dataset schema or task scope.

- Executed: False | Rejected: True | Judge: N/A
- Stages: guardrail_plan
- Path: guardrail_reject
- Latency: 12.33s | Cost: $0.00503

**Alignment Explanation**

Rejected by the guardrail because the query cannot be answered from available dataset fields. Reason: The question requires a forecast based on external data and cannot be computed from the provided columns.

**Rejection Reasoning**

- The question requires a forecast based on external data and cannot be computed from the provided columns.

---

### Q13: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is Logistic regression predicts behavior 'moderate' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: plan_bypass_predictive,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 0.02s | Cost: $0.00000

**Agent Trace**

```
Thought: typed operator step 1 (PREDICTIVE_PIPELINE)
Action: typed_operator_exec
Action Input: # sort_by=['timestamp'] split=975/1219 model='logistic_regression' features=['accel_mean', 'accel_variance', 'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99', 'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99', 'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99', 'extreme_event_magnitude', 'instability_score']
result = "Logistic regression predicts behavior 'moderate' for the first holdout row."
Observation: Logistic regression predicts behavior 'moderate' for the first holdout row.
Final Answer: Logistic regression predicts behavior 'moderate' for the first holdout row.
```

**Final Code Executed**

```python
# sort_by=['timestamp'] split=975/1219 model='logistic_regression' features=['accel_mean', 'accel_variance', 'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99', 'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99', 'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99', 'extreme_event_magnitude', 'instability_score']
result = "Logistic regression predicts behavior 'moderate' for the first holdout row."
```

---

### Q14: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is Random forest predicts behavior 'moderate' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: plan_bypass_predictive,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 0.21s | Cost: $0.00000

**Agent Trace**

```
Thought: typed operator step 1 (PREDICTIVE_PIPELINE)
Action: typed_operator_exec
Action Input: # sort_by=['timestamp'] split=975/1219 model='random_forest' features=['accel_mean', 'accel_variance', 'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99', 'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99', 'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99', 'extreme_event_magnitude', 'instability_score']
result = "Random forest predicts behavior 'moderate' for the first holdout row."
Observation: Random forest predicts behavior 'moderate' for the first holdout row.
Final Answer: Random forest predicts behavior 'moderate' for the first holdout row.
```

**Final Code Executed**

```python
# sort_by=['timestamp'] split=975/1219 model='random_forest' features=['accel_mean', 'accel_variance', 'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99', 'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99', 'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99', 'extreme_event_magnitude', 'instability_score']
result = "Random forest predicts behavior 'moderate' for the first holdout row."
```

---

### Q15: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a 1-nearest-neighbor model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is 1-nearest-neighbor predicts behavior 'moderate' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: plan_bypass_predictive,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 0.00s | Cost: $0.00000

**Agent Trace**

```
Thought: typed operator step 1 (PREDICTIVE_PIPELINE)
Action: typed_operator_exec
Action Input: # sort_by=['timestamp'] split=975/1219 model='one_nearest_neighbor' features=['accel_mean', 'accel_variance', 'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99', 'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99', 'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99', 'extreme_event_magnitude', 'instability_score']
result = "1-nearest-neighbor predicts behavior 'moderate' for the first holdout row."
Observation: 1-nearest-neighbor predicts behavior 'moderate' for the first holdout row.
Final Answer: 1-nearest-neighbor predicts behavior 'moderate' for the first holdout row.
```

**Final Code Executed**

```python
# sort_by=['timestamp'] split=975/1219 model='one_nearest_neighbor' features=['accel_mean', 'accel_variance', 'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99', 'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99', 'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99', 'extreme_event_magnitude', 'instability_score']
result = "1-nearest-neighbor predicts behavior 'moderate' for the first holdout row."
```

---

### Q16: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a hist gradient boosting model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### FLASH_FUSION

**Answer:** The result is Hist gradient boosting predicts behavior 'moderate' for the first holdout row.

- Executed: True | Rejected: False | Judge: N/A
- Stages: plan_bypass_predictive,plan_validated,typed_exec
- Path: typed_operator
- Operators: PREDICTIVE_PIPELINE
- Latency: 0.31s | Cost: $0.00000

**Agent Trace**

```
Thought: typed operator step 1 (PREDICTIVE_PIPELINE)
Action: typed_operator_exec
Action Input: # sort_by=['timestamp'] split=975/1219 model='hist_gradient_boosting' features=['accel_mean', 'accel_variance', 'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99', 'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99', 'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99', 'extreme_event_magnitude', 'instability_score']
result = "Hist gradient boosting predicts behavior 'moderate' for the first holdout row."
Observation: Hist gradient boosting predicts behavior 'moderate' for the first holdout row.
Final Answer: Hist gradient boosting predicts behavior 'moderate' for the first holdout row.
```

**Final Code Executed**

```python
# sort_by=['timestamp'] split=975/1219 model='hist_gradient_boosting' features=['accel_mean', 'accel_variance', 'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99', 'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99', 'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99', 'extreme_event_magnitude', 'instability_score']
result = "Hist gradient boosting predicts behavior 'moderate' for the first holdout row."
```

---
