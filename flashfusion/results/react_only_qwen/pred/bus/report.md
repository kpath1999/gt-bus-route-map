# Flash-Fusion Benchmark Report

Generated: 2026-08-12T17:04:08+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             1.0000 |           10.1054 |           0.0022 |

## Typed-Operator Coverage

| Baseline   |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject | Typed avg latency (s)   | Fallback avg latency (s)   |
|:-----------|----------:|--------:|:-----------|-----------------:|-------------------:|:------------------------|:---------------------------|
| REACT_ONLY |         4 |       0 | 0%         |                0 |                  0 | -                       | -                          |

## Per-Query Results

### Q13: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 9.90s | Cost: $0.00238

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

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

# Split chronologically: first 80% train, last 20% holdout
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Train logistic regression model
model = LogisticRegression(max_iter=1000, multi_class='multinomial', solver='lbfgs')
model.fit(X_train, y_train)

# Predict behavior for the first row in the holdout set
first_holdout_row = holdout_df.iloc[[0]]
X_holdout_first = first_holdout_row[accel_features]
prediction = model.predict(X_holdout_first)[0]

result = prediction
Observation: moderate
Final Answer: The predicted behavior label for the first holdout row is: moderate.
```

**Final Code Executed**

```python
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

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

# Split chronologically: first 80% train, last 20% holdout
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Train logistic regression model
model = LogisticRegression(max_iter=1000, multi_class='multinomial', solver='lbfgs')
model.fit(X_train, y_train)

# Predict behavior for the first row in the holdout set
first_holdout_row = holdout_df.iloc[[0]]
X_holdout_first = first_holdout_row[accel_features]
prediction = model.predict(X_holdout_first)[0]

result = prediction
```

---

### Q14: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 10.05s | Cost: $0.00217

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Identify acceleration-related features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Split into train (80%) and holdout (20%)
n_total = len(df_sorted)
n_train = int(0.8 * n_total)

train_df = df_sorted.iloc[:n_train]
holdout_df = df_sorted.iloc[n_train:]

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Train random forest model
from sklearn.ensemble import RandomForestClassifier
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
# Identify acceleration-related features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Split into train (80%) and holdout (20%)
n_total = len(df_sorted)
n_train = int(0.8 * n_total)

train_df = df_sorted.iloc[:n_train]
holdout_df = df_sorted.iloc[n_train:]

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Train random forest model
from sklearn.ensemble import RandomForestClassifier
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
- Latency: 10.08s | Cost: $0.00218

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx].copy()
holdout_df = df_sorted.iloc[split_idx:].copy()

# Define acceleration features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Get the first row of holdout set
X_holdout_first = holdout_df[accel_features].iloc[[0]]

# Train 1-NN model and predict
from sklearn.neighbors import KNeighborsClassifier
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(X_train, y_train)
prediction = knn.predict(X_holdout_first)[0]

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
train_df = df_sorted.iloc[:split_idx].copy()
holdout_df = df_sorted.iloc[split_idx:].copy()

# Define acceleration features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Get the first row of holdout set
X_holdout_first = holdout_df[accel_features].iloc[[0]]

# Train 1-NN model and predict
from sklearn.neighbors import KNeighborsClassifier
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(X_train, y_train)
prediction = knn.predict(X_holdout_first)[0]

result = prediction
```

---

### Q16: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a hist gradient boosting model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 10.39s | Cost: $0.00213

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

# Select acceleration features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

X_train = train_df[accel_features]
y_train = train_df['behavior']

X_holdout = holdout_df[accel_features]

# Train HistGradientBoostingClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
model = HistGradientBoostingClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict the first row in holdout set
result = model.predict(X_holdout.iloc[[0]])[0]
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

# Select acceleration features
accel_features = [
    'accel_mean', 'accel_variance',
    'accel_stats_x_p1', 'accel_stats_x_p10', 'accel_stats_x_p90', 'accel_stats_x_p99',
    'accel_stats_y_p1', 'accel_stats_y_p10', 'accel_stats_y_p90', 'accel_stats_y_p99',
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99'
]

X_train = train_df[accel_features]
y_train = train_df['behavior']

X_holdout = holdout_df[accel_features]

# Train HistGradientBoostingClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
model = HistGradientBoostingClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict the first row in holdout set
result = model.predict(X_holdout.iloc[[0]])[0]
```

---
