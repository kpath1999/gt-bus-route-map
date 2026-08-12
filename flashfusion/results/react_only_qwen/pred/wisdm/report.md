# Flash-Fusion Benchmark Report

Generated: 2026-08-12T17:09:58+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             0.7500 |           61.7792 |           0.0020 |

## Typed-Operator Coverage

| Baseline   |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject | Typed avg latency (s)   | Fallback avg latency (s)   |
|:-----------|----------:|--------:|:-----------|-----------------:|-------------------:|:------------------------|:---------------------------|
| REACT_ONLY |         4 |       0 | 0%         |                0 |                  0 | -                       | -                          |

## Per-Query Results

### Q13: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the features x, y and z. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: Jogging.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 23.30s | Cost: $0.00143

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Filter out rows with empty activity_label
df = df[df['activity_label'] != '']

# Sort by timestamp ascending, then by subject_id as tie-breaker
df_sorted = df.sort_values(by=['timestamp', 'subject_id']).reset_index(drop=True)

# Split into first 80% (training) and last 20% (holdout)
n_total = len(df_sorted)
n_train = int(0.8 * n_total)

train_df = df_sorted.iloc[:n_train]
holdout_df = df_sorted.iloc[n_train:]

# Train logistic regression model using x, y, z to predict activity_label
from sklearn.linear_model import LogisticRegression

X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)

# Predict activity label for the first row in the holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
prediction = model.predict(first_holdout_row)[0]

result = prediction
Observation: Jogging
Final Answer: The predicted behavior label for the first holdout row is: Jogging.
```

**Final Code Executed**

```python
# Filter out rows with empty activity_label
df = df[df['activity_label'] != '']

# Sort by timestamp ascending, then by subject_id as tie-breaker
df_sorted = df.sort_values(by=['timestamp', 'subject_id']).reset_index(drop=True)

# Split into first 80% (training) and last 20% (holdout)
n_total = len(df_sorted)
n_train = int(0.8 * n_total)

train_df = df_sorted.iloc[:n_train]
holdout_df = df_sorted.iloc[n_train:]

# Train logistic regression model using x, y, z to predict activity_label
from sklearn.linear_model import LogisticRegression

X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)

# Predict activity label for the first row in the holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
prediction = model.predict(first_holdout_row)[0]

result = prediction
```

---

### Q14: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the features x, y and z. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** [ERROR] Safe backend failed after 5 attempts: TimeoutError: Safe code execution exceeded 30.0s and was terminated

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 192.00s | Cost: $0.00407

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Sort by timestamp, then subject_id
df_sorted = df.sort_values(['timestamp', 'subject_id'])

# Remove rows with empty activity_label
df_clean = df_sorted[df_sorted['activity_label'] != ''].copy()

# Split into 80% training and 20% holdout
n = len(df_clean)
split_idx = int(0.8 * n)
train_df = df_clean.iloc[:split_idx]
holdout_df = df_clean.iloc[split_idx:]

# Train random forest model
from sklearn.ensemble import RandomForestClassifier
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
model = RandomForestClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict activity label for first row in holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
result = model.predict(first_holdout_row)[0]
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Thought: Attempt 2: generate executable pandas code
Action: python_exec
Action Input: df = df[df['activity_label'] != '']
df = df.sort_values(['timestamp', 'subject_id'])
n = len(df)
split_idx = int(0.8 * n)
train_df = df.iloc[:split_idx]
holdout_df = df.iloc[split_idx:]
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
first_holdout_row = holdout_df.iloc[[0]]
X_pred = first_holdout_row[['x', 'y', 'z']]
result = model.predict(X_pred)[0]
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Thought: Attempt 3: generate executable pandas code
Action: python_exec
Action Input: df = df[df['activity_label'] != '']
df = df.sort_values(['timestamp', 'subject_id'])
n = len(df)
split_idx = int(0.8 * n)
train_df = df.iloc[:split_idx]
holdout_df = df.iloc[split_idx:]
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
X_holdout = holdout_df[['x', 'y', 'z']]
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
first_holdout_pred = model.predict(X_holdout.iloc[[0]])[0]
result = first_holdout_pred
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Thought: Attempt 4: generate executable pandas code
Action: python_exec
Action Input: df = df[df['activity_label'] != '']
df = df.sort_values(['timestamp', 'subject_id'])
n = len(df)
split_idx = int(0.8 * n)
train_df = df.iloc[:split_idx]
holdout_df = df.iloc[split_idx:]
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
X_holdout = holdout_df[['x', 'y', 'z']]
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
first_holdout_pred = model.predict(X_holdout.iloc[[0]])[0]
result = first_holdout_pred
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Thought: Attempt 5: generate executable pandas code
Action: python_exec
Action Input: df = df[df['activity_label'] != '']
df = df.sort_values(['timestamp', 'subject_id'])
n = len(df)
split_idx = int(0.8 * n)
train_df = df.iloc[:split_idx]
holdout_df = df.iloc[split_idx:]
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
X_holdout = holdout_df[['x', 'y', 'z']]
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
first_holdout_pred = model.predict(X_holdout.iloc[[0]])[0]
result = first_holdout_pred
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Final Answer: [ERROR] Safe backend failed after 5 attempts: TimeoutError: Safe code execution exceeded 30.0s and was terminated
```

**Final Code Executed**

```python
df = df[df['activity_label'] != '']
df = df.sort_values(['timestamp', 'subject_id'])
n = len(df)
split_idx = int(0.8 * n)
train_df = df.iloc[:split_idx]
holdout_df = df.iloc[split_idx:]
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
X_holdout = holdout_df[['x', 'y', 'z']]
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
first_holdout_pred = model.predict(X_holdout.iloc[[0]])[0]
result = first_holdout_pred
```

---

### Q15: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a 1-nearest-neighbor model using the features x, y and z. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: Sitting.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 11.79s | Cost: $0.00126

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Filter out rows with empty activity_label
df = df[df['activity_label'] != '']

# Sort by timestamp ascending, then by subject_id ascending as tie-breaker
df_sorted = df.sort_values(by=['timestamp', 'subject_id'])

# Split into 80% training and 20% holdout (chronological)
n = len(df_sorted)
split_idx = int(0.8 * n)

train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Prepare training data
X_train = train_df[['x', 'y', 'z']].values
y_train = train_df['activity_label'].values

# Get the first row in the holdout set
first_holdout = holdout_df.iloc[0]
X_test = first_holdout[['x', 'y', 'z']].values.reshape(1, -1)

# Train 1-NN model and predict
from sklearn.neighbors import KNeighborsClassifier
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(X_train, y_train)
prediction = knn.predict(X_test)[0]

result = prediction
Observation: Sitting
Final Answer: The predicted behavior label for the first holdout row is: Sitting.
```

**Final Code Executed**

```python
# Filter out rows with empty activity_label
df = df[df['activity_label'] != '']

# Sort by timestamp ascending, then by subject_id ascending as tie-breaker
df_sorted = df.sort_values(by=['timestamp', 'subject_id'])

# Split into 80% training and 20% holdout (chronological)
n = len(df_sorted)
split_idx = int(0.8 * n)

train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Prepare training data
X_train = train_df[['x', 'y', 'z']].values
y_train = train_df['activity_label'].values

# Get the first row in the holdout set
first_holdout = holdout_df.iloc[0]
X_test = first_holdout[['x', 'y', 'z']].values.reshape(1, -1)

# Train 1-NN model and predict
from sklearn.neighbors import KNeighborsClassifier
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(X_train, y_train)
prediction = knn.predict(X_test)[0]

result = prediction
```

---

### Q16: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a hist gradient boosting model using the features x, y and z. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: Sitting.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 20.02s | Cost: $0.00114

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Filter out rows with empty activity_label
df = df[df['activity_label'] != '']

# Sort by timestamp and subject_id
df_sorted = df.sort_values(['timestamp', 'subject_id'])

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
train_end = int(0.8 * n)
train_df = df_sorted.iloc[:train_end]
holdout_df = df_sorted.iloc[train_end:]

# Train histogram gradient boosting model
from sklearn.ensemble import HistGradientBoostingClassifier

X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

model = HistGradientBoostingClassifier()
model.fit(X_train, y_train)

# Predict for first row in holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
prediction = model.predict(first_holdout_row)[0]

result = prediction
Observation: Sitting
Final Answer: The predicted behavior label for the first holdout row is: Sitting.
```

**Final Code Executed**

```python
# Filter out rows with empty activity_label
df = df[df['activity_label'] != '']

# Sort by timestamp and subject_id
df_sorted = df.sort_values(['timestamp', 'subject_id'])

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
train_end = int(0.8 * n)
train_df = df_sorted.iloc[:train_end]
holdout_df = df_sorted.iloc[train_end:]

# Train histogram gradient boosting model
from sklearn.ensemble import HistGradientBoostingClassifier

X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

model = HistGradientBoostingClassifier()
model.fit(X_train, y_train)

# Predict for first row in holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
prediction = model.predict(first_holdout_row)[0]

result = prediction
```

---
