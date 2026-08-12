# Flash-Fusion Benchmark Report

Generated: 2026-08-12T15:46:49+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             0.8750 |           33.1999 |           0.0013 |

## Typed-Operator Coverage

| Baseline   |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject | Typed avg latency (s)   | Fallback avg latency (s)   |
|:-----------|----------:|--------:|:-----------|-----------------:|-------------------:|:------------------------|:---------------------------|
| REACT_ONLY |         8 |       0 | 0%         |                0 |                  0 | -                       | -                          |

## Per-Query Results

### Q9: How does the average walking speed in miles per hour correlate with the age of the users?

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): walking speed in miles per hour, age of the users.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 2.47s | Cost: $0.00056

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): walking speed in miles per hour, age of the users.
```

**Rejection Reasoning**

- Missing required dataset concept(s): walking speed in miles per hour, age of the users.

---

### Q10: Based on the acceleration data, predict the exact geographic location where user 10 was jogging.

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): geographic location coordinates (e.g., latitude, longitude).

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 2.01s | Cost: $0.00056

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): geographic location coordinates (e.g., latitude, longitude).
```

**Rejection Reasoning**

- Missing required dataset concept(s): geographic location coordinates (e.g., latitude, longitude).

---

### Q11: Are female subjects more likely to have a higher cadence during stair climbing than male subjects?

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): gender, cadence.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.98s | Cost: $0.00053

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): gender, cadence.
```

**Rejection Reasoning**

- Missing required dataset concept(s): gender, cadence.

---

### Q12: Predict whether user 3 will meet the WHO recommended weekly moderate-to-vigorous physical activity guideline next week.

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): future physical activity duration, WHO guideline compliance label, weekly activity summary, or any target variable indicating whether the guideline was met.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.88s | Cost: $0.00062

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): future physical activity duration, WHO guideline compliance label, weekly activity summary, or any target variable indicating whether the guideline was met.
```

**Rejection Reasoning**

- Missing required dataset concept(s): future physical activity duration, WHO guideline compliance label, weekly activity summary, or any target variable indicating whether the guideline was met.

---

### Q13: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the features x, y and z. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: Jogging.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 22.82s | Cost: $0.00125

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Filter out rows with empty activity_label
df = df[df['activity_label'] != '']

# Sort by timestamp, then subject_id
df_sorted = df.sort_values(['timestamp', 'subject_id'])

# Split into 80% training and 20% holdout
n = len(df_sorted)
train_size = int(0.8 * n)
train_df = df_sorted.iloc[:train_size]
holdout_df = df_sorted.iloc[train_size:]

# Train logistic regression model
from sklearn.linear_model import LogisticRegression
model = LogisticRegression(max_iter=1000)
model.fit(train_df[['x', 'y', 'z']], train_df['activity_label'])

# Predict activity label for first row in holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
result = model.predict(first_holdout_row)[0]
Observation: Jogging
Final Answer: The predicted behavior label for the first holdout row is: Jogging.
```

**Final Code Executed**

```python
# Filter out rows with empty activity_label
df = df[df['activity_label'] != '']

# Sort by timestamp, then subject_id
df_sorted = df.sort_values(['timestamp', 'subject_id'])

# Split into 80% training and 20% holdout
n = len(df_sorted)
train_size = int(0.8 * n)
train_df = df_sorted.iloc[:train_size]
holdout_df = df_sorted.iloc[train_size:]

# Train logistic regression model
from sklearn.linear_model import LogisticRegression
model = LogisticRegression(max_iter=1000)
model.fit(train_df[['x', 'y', 'z']], train_df['activity_label'])

# Predict activity label for first row in holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
result = model.predict(first_holdout_row)[0]
```

---

### Q14: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the features x, y and z. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** [ERROR] Safe backend failed after 5 attempts: TimeoutError: Safe code execution exceeded 30.0s and was terminated

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 198.87s | Cost: $0.00529

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.ensemble import RandomForestClassifier

# Filter out rows with empty activity_label
df_clean = df[df['activity_label'] != '']

# Sort by timestamp, then subject_id
df_sorted = df_clean.sort_values(by=['timestamp', 'subject_id']).reset_index(drop=True)

# Split into 80% training and 20% holdout
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Prepare features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train Random Forest model
model = RandomForestClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict the first row in the holdout set
first_holdout = holdout_df.iloc[[0]]
X_holdout = first_holdout[['x', 'y', 'z']]
prediction = model.predict(X_holdout)[0]

result = prediction
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Thought: Attempt 2: generate executable pandas code
Action: python_exec
Action Input: # Sort by timestamp and subject_id
df_sorted = df.sort_values(['timestamp', 'subject_id'])

# Remove rows with empty activity_label
df_clean = df_sorted[df_sorted['activity_label'] != '']

# Split into train (80%) and holdout (20%)
n_total = len(df_clean)
n_train = int(0.8 * n_total)

train_df = df_clean.iloc[:n_train]
holdout_df = df_clean.iloc[n_train:]

# Train random forest model
from sklearn.ensemble import RandomForestClassifier

X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

model = RandomForestClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict activity label for first row in holdout set
first_holdout_features = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
result = model.predict(first_holdout_features)[0]
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Thought: Attempt 3: generate executable pandas code
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

# Train random forest model
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(random_state=42)
model.fit(train_df[['x', 'y', 'z']], train_df['activity_label'])

# Predict for first row in holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
result = model.predict(first_holdout_row)[0]
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Thought: Attempt 4: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.ensemble import RandomForestClassifier

# Filter out rows with empty activity_label
df_clean = df[df['activity_label'] != ''].copy()

# Sort by timestamp, then subject_id
df_sorted = df_clean.sort_values(['timestamp', 'subject_id']).reset_index(drop=True)

# Split into 80% training and 20% holdout
n = len(df_sorted)
train_end = int(0.8 * n)
train_df = df_sorted.iloc[:train_end]
holdout_df = df_sorted.iloc[train_end:]

# Prepare features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train Random Forest model
rf_model = RandomForestClassifier(random_state=42)
rf_model.fit(X_train, y_train)

# Predict the first row in holdout set
first_holdout_features = holdout_df[['x', 'y', 'z']].iloc[[0]]
prediction = rf_model.predict(first_holdout_features)[0]

result = prediction
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Thought: Attempt 5: generate executable pandas code
Action: python_exec
Action Input: df = df[df['activity_label'] != '']
df_sorted = df.sort_values(['timestamp', 'subject_id'])
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
first_holdout_features = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
result = model.predict(first_holdout_features)[0]
Observation: TimeoutError: Safe code execution exceeded 30.0s and was terminated
Final Answer: [ERROR] Safe backend failed after 5 attempts: TimeoutError: Safe code execution exceeded 30.0s and was terminated
```

**Final Code Executed**

```python
df = df[df['activity_label'] != '']
df_sorted = df.sort_values(['timestamp', 'subject_id'])
n = len(df_sorted)
split_idx = int(0.8 * n)
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
first_holdout_features = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
result = model.predict(first_holdout_features)[0]
```

---

### Q15: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a 1-nearest-neighbor model using the features x, y and z. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: Sitting.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 12.58s | Cost: $0.00110

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Filter out rows with missing activity labels
df = df[df['activity_label'] != '']

# Sort by timestamp and subject_id
df_sorted = df.sort_values(['timestamp', 'subject_id'])

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
train_size = int(0.8 * n)
train_df = df_sorted.iloc[:train_size]
holdout_df = df_sorted.iloc[train_size:]

# Train 1-NN model
from sklearn.neighbors import KNeighborsClassifier
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(train_df[['x', 'y', 'z']], train_df['activity_label'])

# Predict for first row in holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
result = knn.predict(first_holdout_row)[0]
Observation: Sitting
Final Answer: The predicted behavior label for the first holdout row is: Sitting.
```

**Final Code Executed**

```python
# Filter out rows with missing activity labels
df = df[df['activity_label'] != '']

# Sort by timestamp and subject_id
df_sorted = df.sort_values(['timestamp', 'subject_id'])

# Split into train (80%) and holdout (20%)
n = len(df_sorted)
train_size = int(0.8 * n)
train_df = df_sorted.iloc[:train_size]
holdout_df = df_sorted.iloc[train_size:]

# Train 1-NN model
from sklearn.neighbors import KNeighborsClassifier
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(train_df[['x', 'y', 'z']], train_df['activity_label'])

# Predict for first row in holdout set
first_holdout_row = holdout_df.iloc[0][['x', 'y', 'z']].values.reshape(1, -1)
result = knn.predict(first_holdout_row)[0]
```

---

### Q16: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a hist gradient boosting model using the features x, y and z. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: Sitting.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 22.99s | Cost: $0.00081

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: df = df[df['activity_label'] != '']
df = df.sort_values(['timestamp', 'subject_id'])
n = len(df)
split_idx = int(0.8 * n)
train_df = df.iloc[:split_idx]
holdout_df = df.iloc[split_idx:]
from sklearn.ensemble import HistGradientBoostingClassifier
model = HistGradientBoostingClassifier()
model.fit(train_df[['x', 'y', 'z']], train_df['activity_label'])
result = model.predict(holdout_df[['x', 'y', 'z']].iloc[[0]])[0]
Observation: Sitting
Final Answer: The predicted behavior label for the first holdout row is: Sitting.
```

**Final Code Executed**

```python
df = df[df['activity_label'] != '']
df = df.sort_values(['timestamp', 'subject_id'])
n = len(df)
split_idx = int(0.8 * n)
train_df = df.iloc[:split_idx]
holdout_df = df.iloc[split_idx:]
from sklearn.ensemble import HistGradientBoostingClassifier
model = HistGradientBoostingClassifier()
model.fit(train_df[['x', 'y', 'z']], train_df['activity_label'])
result = model.predict(holdout_df[['x', 'y', 'z']].iloc[[0]])[0]
```

---
