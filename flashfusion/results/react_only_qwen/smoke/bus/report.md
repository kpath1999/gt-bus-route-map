# Flash-Fusion Benchmark Report

Generated: 2026-08-12T15:40:23+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             1.0000 |            5.6258 |           0.0016 |

## Typed-Operator Coverage

| Baseline   |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject | Typed avg latency (s)   | Fallback avg latency (s)   |
|:-----------|----------:|--------:|:-----------|-----------------:|-------------------:|:------------------------|:---------------------------|
| REACT_ONLY |         8 |       0 | 0%         |                0 |                  0 | -                       | -                          |

## Per-Query Results

### Q9: How does passenger occupancy correlate with road roughness during this trip?

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): passenger occupancy, road roughness.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.74s | Cost: $0.00108

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): passenger occupancy, road roughness.
```

**Rejection Reasoning**

- Missing required dataset concept(s): passenger occupancy, road roughness.

---

### Q10: Did rainy weather cause the roughest segments in this route?

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): rainy weather, road roughness.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.57s | Cost: $0.00108

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): rainy weather, road roughness.
```

**Rejection Reasoning**

- Missing required dataset concept(s): rainy weather, road roughness.

---

### Q11: Was the bus driver complying with their operating schedule?

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): scheduled arrival/departure times, route schedule, planned stops, or any column indicating expected vs. actual timing.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.95s | Cost: $0.00114

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): scheduled arrival/departure times, route schedule, planned stops, or any column indicating expected vs. actual timing.
```

**Rejection Reasoning**

- Missing required dataset concept(s): scheduled arrival/departure times, route schedule, planned stops, or any column indicating expected vs. actual timing.

---

### Q12: Predict next week's pothole repairs for the road segments in this dataset.

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): pothole repairs, road segments, future repair counts or labels.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.89s | Cost: $0.00111

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): pothole repairs, road segments, future repair counts or labels.
```

**Rejection Reasoning**

- Missing required dataset concept(s): pothole repairs, road segments, future repair counts or labels.

---

### Q13: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 8.74s | Cost: $0.00179

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: from sklearn.linear_model import LogisticRegression
import numpy as np

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Determine split index
n = len(df_sorted)
split_idx = int(0.8 * n)

# Define features and target
feature_cols = [col for col in df.columns if col.startswith('accel_')]
X = df_sorted[feature_cols]
y = df_sorted['behavior']

# Split data
X_train = X.iloc[:split_idx]
y_train = y.iloc[:split_idx]
X_holdout = X.iloc[split_idx:]
y_holdout = y.iloc[split_idx:]

# Train logistic regression model
model = LogisticRegression(max_iter=1000, random_state=0)
model.fit(X_train, y_train)

# Predict the first row in the holdout set
result = model.predict(X_holdout.iloc[[0]])[0]
Observation: moderate
Final Answer: The predicted behavior label for the first holdout row is: moderate.
```

**Final Code Executed**

```python
from sklearn.linear_model import LogisticRegression
import numpy as np

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Determine split index
n = len(df_sorted)
split_idx = int(0.8 * n)

# Define features and target
feature_cols = [col for col in df.columns if col.startswith('accel_')]
X = df_sorted[feature_cols]
y = df_sorted['behavior']

# Split data
X_train = X.iloc[:split_idx]
y_train = y.iloc[:split_idx]
X_holdout = X.iloc[split_idx:]
y_holdout = y.iloc[split_idx:]

# Train logistic regression model
model = LogisticRegression(max_iter=1000, random_state=0)
model.fit(X_train, y_train)

# Predict the first row in the holdout set
result = model.predict(X_holdout.iloc[[0]])[0]
```

---

### Q14: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 9.34s | Cost: $0.00215

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Extract acceleration-related features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Train Random Forest model
from sklearn.ensemble import RandomForestClassifier
X_train = train_df[accel_features]
y_train = train_df['behavior']
model = RandomForestClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict behavior for first row in holdout set
first_holdout_row = holdout_df.iloc[[0]]
X_holdout = first_holdout_row[accel_features]
prediction = model.predict(X_holdout)[0]

result = prediction
Observation: moderate
Final Answer: The predicted behavior label for the first holdout row is: moderate.
```

**Final Code Executed**

```python
# Extract acceleration-related features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Train Random Forest model
from sklearn.ensemble import RandomForestClassifier
X_train = train_df[accel_features]
y_train = train_df['behavior']
model = RandomForestClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict behavior for first row in holdout set
first_holdout_row = holdout_df.iloc[[0]]
X_holdout = first_holdout_row[accel_features]
prediction = model.predict(X_holdout)[0]

result = prediction
```

---

### Q15: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a 1-nearest-neighbor model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 8.61s | Cost: $0.00209

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Define acceleration features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

# Train 1-NN model
from sklearn.neighbors import KNeighborsClassifier
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(train_df[accel_features], train_df['behavior'])

# Predict behavior for first row in holdout set
first_holdout_row = holdout_df.iloc[[0]]
prediction = knn.predict(first_holdout_row[accel_features])[0]

result = prediction
Observation: moderate
Final Answer: The predicted behavior label for the first holdout row is: moderate.
```

**Final Code Executed**

```python
# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Define acceleration features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

# Train 1-NN model
from sklearn.neighbors import KNeighborsClassifier
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(train_df[accel_features], train_df['behavior'])

# Predict behavior for first row in holdout set
first_holdout_row = holdout_df.iloc[[0]]
prediction = knn.predict(first_holdout_row[accel_features])[0]

result = prediction
```

---

### Q16: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a hist gradient boosting model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 11.16s | Cost: $0.00230

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: from sklearn.ensemble import HistGradientBoostingClassifier
import numpy as np

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Identify acceleration-related features (excluding non-acceleration columns)
accel_features = [
    'accel_mean',
    'accel_variance',
    'accel_stats_x_p1',
    'accel_stats_x_p10',
    'accel_stats_x_p90',
    'accel_stats_x_p99',
    'accel_stats_y_p1',
    'accel_stats_y_p10',
    'accel_stats_y_p90',
    'accel_stats_y_p99',
    'accel_stats_z_p1',
    'accel_stats_z_p10',
    'accel_stats_z_p90',
    'accel_stats_z_p99'
]

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Train model
model = HistGradientBoostingClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict behavior for first row in holdout set
first_holdout_row = holdout_df.iloc[[0]]
X_holdout_first = first_holdout_row[accel_features]
prediction = model.predict(X_holdout_first)[0]

result = prediction
Observation: moderate
Final Answer: The predicted behavior label for the first holdout row is: moderate.
```

**Final Code Executed**

```python
from sklearn.ensemble import HistGradientBoostingClassifier
import numpy as np

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Identify acceleration-related features (excluding non-acceleration columns)
accel_features = [
    'accel_mean',
    'accel_variance',
    'accel_stats_x_p1',
    'accel_stats_x_p10',
    'accel_stats_x_p90',
    'accel_stats_x_p99',
    'accel_stats_y_p1',
    'accel_stats_y_p10',
    'accel_stats_y_p90',
    'accel_stats_y_p99',
    'accel_stats_z_p1',
    'accel_stats_z_p10',
    'accel_stats_z_p90',
    'accel_stats_z_p99'
]

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Train model
model = HistGradientBoostingClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict behavior for first row in holdout set
first_holdout_row = holdout_df.iloc[[0]]
X_holdout_first = first_holdout_row[accel_features]
prediction = model.predict(X_holdout_first)[0]

result = prediction
```

---
