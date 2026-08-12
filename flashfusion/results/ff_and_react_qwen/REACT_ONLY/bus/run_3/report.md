# Flash-Fusion Benchmark Report

Generated: 2026-08-12T18:38:52+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             1.0000 |            5.9849 |           0.0007 |

## Typed-Operator Coverage

| Baseline   |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject | Typed avg latency (s)   | Fallback avg latency (s)   |
|:-----------|----------:|--------:|:-----------|-----------------:|-------------------:|:------------------------|:---------------------------|
| REACT_ONLY |        16 |       0 | 0%         |                0 |                  0 | -                       | -                          |

## Per-Query Results

### Q1: What is the maximum accel_variance observed in this dataset?

#### REACT_ONLY

**Answer:** The result is: 5.869

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 5.99s | Cost: $0.00031

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: result = df['accel_variance'].max()
Observation: 5.869
Final Answer: The result is: 5.869
```

**Final Code Executed**

```python
result = df['accel_variance'].max()
```

---

### Q2: What is the average accel_mean across all recorded samples?

#### REACT_ONLY

**Answer:** The result is: 9.283388022969648

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 5.89s | Cost: $0.00031

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: result = df['accel_mean'].mean()
Observation: 9.283388022969648
Final Answer: The result is: 9.283388022969648
```

**Final Code Executed**

```python
result = df['accel_mean'].mean()
```

---

### Q3: List all timestamps where accel_stats_z_p99 reaches its maximum value across the dataset.

#### REACT_ONLY

**Answer:** The result is: [Timestamp('2025-06-06 16:01:13'), Timestamp('2025-06-06 16:01:16'), Timestamp('2025-06-06 16:01:19'), Timestamp('2025-06-06 16:01:22'), Timestamp('2025-06-06 16:01:25'), Timestamp('2025-06-06 16:01:28'), Timestamp('2025-06-06 16:01:31'), Timestamp('2025-06-06 16:01:34'), Timestamp('2025-06-06 16:01:40'), Timestamp('2025-06-06 16:01:43'), Timestamp('2025-06-06 16:01:46'), Timestamp('2025-06-06 16:01:49'), Timestamp('2025-06-06 16:01:52'), Timestamp('2025-06-06 16:01:55'), Timestamp('2025-06-06 16:01:58'), Timestamp('2025-06-06 16:02:01')]

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 6.30s | Cost: $0.00033

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: max_value = df['accel_stats_z_p99'].max()
result = df[df['accel_stats_z_p99'] == max_value]['timestamp'].tolist()
Observation: [Timestamp('2025-06-06 16:01:13'), Timestamp('2025-06-06 16:01:16'), Timestamp('2025-06-06 16:01:19'), Timestamp('2025-06-06 16:01:22'), Timestamp('2025-06-06 16:01:25'), Timestamp('2025-06-06 16:01:28'), Timestamp('2025-06-06 16:01:31'), Timestamp('2025-06-06 16:01:34'), Timestamp('2025-06-06 16:01:40'), Timestamp('2025-06-06 16:01:43'), Timestamp('2025-06-06 16:01:46'), Timestamp('2025-06-06 16:01:49'), Timestamp('2025-06-06 16:01:52'), Timestamp('2025-06-06 16:01:55'), Timestamp('2025-06-06 16:01:58'), Timestamp('2025-06-06 16:02:01')]
Final Answer: The result is: [Timestamp('2025-06-06 16:01:13'), Timestamp('2025-06-06 16:01:16'), Timestamp('2025-06-06 16:01:19'), Timestamp('2025-06-06 16:01:22'), Timestamp('2025-06-06 16:01:25'), Timestamp('2025-06-06 16:01:28'), Timestamp('2025-06-06 16:01:31'), Timestamp('2025-06-06 16:01:34'), Timestamp('2025-06-06 16:01:40'), Timestamp('2025-06-06 16:01:43'), Timestamp('2025-06-06 16:01:46'), Timestamp('2025-06-06 16:01:49'), Timestamp('2025-06-06 16:01:52'), Timestamp('2025-06-06 16:01:55'), Timestamp('2025-06-06 16:01:58'), Timestamp('2025-06-06 16:02:01')]
```

**Final Code Executed**

```python
max_value = df['accel_stats_z_p99'].max()
result = df[df['accel_stats_z_p99'] == max_value]['timestamp'].tolist()
```

---

### Q4: How many data samples show an accel_variance strictly greater than 0.20?

#### REACT_ONLY

**Answer:** The result is: 352

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 5.99s | Cost: $0.00028

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: result = (df['accel_variance'] > 0.20).sum()
Observation: 352
Final Answer: The result is: 352
```

**Final Code Executed**

```python
result = (df['accel_variance'] > 0.20).sum()
```

---

### Q5: Is the northern half of the route (latitude above median) rougher than the southern half, based on average acceleration variance?

#### REACT_ONLY

**Answer:** The result is: True

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 6.10s | Cost: $0.00122

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: median_lat = df['latitude'].median()
north_avg_var = df[df['latitude'] > median_lat]['accel_variance'].mean()
south_avg_var = df[df['latitude'] <= median_lat]['accel_variance'].mean()
result = north_avg_var > south_avg_var
Observation: True
Final Answer: The result is: True
```

**Final Code Executed**

```python
median_lat = df['latitude'].median()
north_avg_var = df[df['latitude'] > median_lat]['accel_variance'].mean()
south_avg_var = df[df['latitude'] <= median_lat]['accel_variance'].mean()
result = north_avg_var > south_avg_var
```

---

### Q6: Which location (latitude, longitude) recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration?

#### REACT_ONLY

**Answer:** The result is: [33.776765, -84.389963]

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 7.06s | Cost: $0.00056

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Compute the difference between 99th and 1st percentile for z-axis acceleration
df['z_diff'] = df['accel_stats_z_p99'] - df['accel_stats_z_p1']

# Find the row with the maximum difference
max_diff_row = df.loc[df['z_diff'].idxmax()]

# Return the latitude and longitude of that location
result = (max_diff_row['latitude'], max_diff_row['longitude'])
Observation: (33.776765, -84.389963)
Final Answer: The result is: [33.776765, -84.389963]
```

**Final Code Executed**

```python
# Compute the difference between 99th and 1st percentile for z-axis acceleration
df['z_diff'] = df['accel_stats_z_p99'] - df['accel_stats_z_p1']

# Find the row with the maximum difference
max_diff_row = df.loc[df['z_diff'].idxmax()]

# Return the latitude and longitude of that location
result = (max_diff_row['latitude'], max_diff_row['longitude'])
```

---

### Q7: Calculate the average overall magnitude of peak acceleration using the 99th percentiles of the X, Y, and Z axes.

#### REACT_ONLY

**Answer:** The result is: 11.458505525284506

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 7.01s | Cost: $0.00066

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Compute the magnitude using 99th percentiles of X, Y, and Z axes
x_p99 = df['accel_stats_x_p99']
y_p99 = df['accel_stats_y_p99']
z_p99 = df['accel_stats_z_p99']

# Calculate overall magnitude for each row
magnitudes = (x_p99**2 + y_p99**2 + z_p99**2)**0.5

# Compute the average of these magnitudes
result = magnitudes.mean()
Observation: 11.458505525284506
Final Answer: The result is: 11.458505525284506
```

**Final Code Executed**

```python
# Compute the magnitude using 99th percentiles of X, Y, and Z axes
x_p99 = df['accel_stats_x_p99']
y_p99 = df['accel_stats_y_p99']
z_p99 = df['accel_stats_z_p99']

# Calculate overall magnitude for each row
magnitudes = (x_p99**2 + y_p99**2 + z_p99**2)**0.5

# Compute the average of these magnitudes
result = magnitudes.mean()
```

---

### Q8: If we group the data into 1-minute intervals, which time window has the highest mean instability_score?

#### REACT_ONLY

**Answer:** The result is: 2025-06-06 16:01:00

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 7.78s | Cost: $0.00033

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: df['time_window'] = df['timestamp'].dt.floor('1T')
result = df.groupby('time_window')['instability_score'].mean().idxmax()
Observation: 2025-06-06 16:01:00
Final Answer: The result is: 2025-06-06 16:01:00
```

**Final Code Executed**

```python
df['time_window'] = df['timestamp'].dt.floor('1T')
result = df.groupby('time_window')['instability_score'].mean().idxmax()
```

---

### Q9: How does passenger occupancy correlate with road roughness during this trip?

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): passenger occupancy, road roughness

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.87s | Cost: $0.00036

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): passenger occupancy, road roughness
```

**Rejection Reasoning**

- Missing required dataset concept(s): passenger occupancy, road roughness

---

### Q10: Did rainy weather cause the roughest segments in this route?

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): rainy weather, road roughness.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.42s | Cost: $0.00036

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

**Answer:** REJECT: Missing required dataset concept(s): operating schedule, scheduled route, timetable, or expected arrival/departure times.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.55s | Cost: $0.00039

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): operating schedule, scheduled route, timetable, or expected arrival/departure times.
```

**Rejection Reasoning**

- Missing required dataset concept(s): operating schedule, scheduled route, timetable, or expected arrival/departure times.

---

### Q12: Predict next week's pothole repairs for the road segments in this dataset.

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): pothole repairs, road segments, historical repair records, target variable for prediction.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.63s | Cost: $0.00040

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): pothole repairs, road segments, historical repair records, target variable for prediction.
```

**Rejection Reasoning**

- Missing required dataset concept(s): pothole repairs, road segments, historical repair records, target variable for prediction.

---

### Q13: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 9.16s | Cost: $0.00115

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Determine split index
n = len(df_sorted)
split_idx = int(0.8 * n)

# Split into train and holdout
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Select acceleration features (all columns starting with 'accel')
accel_features = [col for col in df.columns if col.startswith('accel')]

# Encode behavior labels
le = LabelEncoder()
y_train = le.fit_transform(train_df['behavior'])
X_train = train_df[accel_features]

# Train logistic regression model
model = LogisticRegression(max_iter=1000, random_state=42)
model.fit(X_train, y_train)

# Predict on first row of holdout
X_holdout_first = holdout_df[accel_features].iloc[[0]]
pred_encoded = model.predict(X_holdout_first)[0]
pred_label = le.inverse_transform([pred_encoded])[0]

result = pred_label
Observation: moderate
Final Answer: The predicted behavior label for the first holdout row is: moderate.
```

**Final Code Executed**

```python
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder

# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Determine split index
n = len(df_sorted)
split_idx = int(0.8 * n)

# Split into train and holdout
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Select acceleration features (all columns starting with 'accel')
accel_features = [col for col in df.columns if col.startswith('accel')]

# Encode behavior labels
le = LabelEncoder()
y_train = le.fit_transform(train_df['behavior'])
X_train = train_df[accel_features]

# Train logistic regression model
model = LogisticRegression(max_iter=1000, random_state=42)
model.fit(X_train, y_train)

# Predict on first row of holdout
X_holdout_first = holdout_df[accel_features].iloc[[0]]
pred_encoded = model.predict(X_holdout_first)[0]
pred_label = le.inverse_transform([pred_encoded])[0]

result = pred_label
```

---

### Q14: Sort all bus rows by timestamp in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the acceleration features. Predict the label in the behavior column for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: moderate.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 9.45s | Cost: $0.00139

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
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99',
    'extreme_event_magnitude', 'instability_score'
]

# Train random forest model
from sklearn.ensemble import RandomForestClassifier
X_train = train_df[accel_features]
y_train = train_df['behavior']
model = RandomForestClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict behavior for first row in holdout set
first_holdout = holdout_df.iloc[[0]]
X_holdout = first_holdout[accel_features]
prediction = model.predict(X_holdout)[0]
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
    'accel_stats_z_p1', 'accel_stats_z_p10', 'accel_stats_z_p90', 'accel_stats_z_p99',
    'extreme_event_magnitude', 'instability_score'
]

# Train random forest model
from sklearn.ensemble import RandomForestClassifier
X_train = train_df[accel_features]
y_train = train_df['behavior']
model = RandomForestClassifier(random_state=42)
model.fit(X_train, y_train)

# Predict behavior for first row in holdout set
first_holdout = holdout_df.iloc[[0]]
X_holdout = first_holdout[accel_features]
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
- Latency: 10.38s | Cost: $0.00138

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

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Prepare the first row of holdout set for prediction
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

# Prepare training data
X_train = train_df[accel_features]
y_train = train_df['behavior']

# Prepare the first row of holdout set for prediction
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
- Latency: 8.18s | Cost: $0.00103

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Determine split index
n = len(df_sorted)
split_idx = int(0.8 * n)

# Split into training and holdout sets
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Select acceleration features (all columns starting with 'accel_' except 'accel_mean' and 'accel_variance')
accel_features = [col for col in df.columns if col.startswith('accel_') and col not in ['accel_mean', 'accel_variance']]

# Train HistGradientBoostingClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
model = HistGradientBoostingClassifier()
model.fit(train_df[accel_features], train_df['behavior'])

# Predict behavior for first row in holdout set
first_holdout_row = holdout_df.iloc[[0]]
prediction = model.predict(first_holdout_row[accel_features])[0]

result = prediction
Observation: moderate
Final Answer: The predicted behavior label for the first holdout row is: moderate.
```

**Final Code Executed**

```python
# Sort by timestamp
df_sorted = df.sort_values('timestamp').reset_index(drop=True)

# Determine split index
n = len(df_sorted)
split_idx = int(0.8 * n)

# Split into training and holdout sets
train_df = df_sorted.iloc[:split_idx]
holdout_df = df_sorted.iloc[split_idx:]

# Select acceleration features (all columns starting with 'accel_' except 'accel_mean' and 'accel_variance')
accel_features = [col for col in df.columns if col.startswith('accel_') and col not in ['accel_mean', 'accel_variance']]

# Train HistGradientBoostingClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
model = HistGradientBoostingClassifier()
model.fit(train_df[accel_features], train_df['behavior'])

# Predict behavior for first row in holdout set
first_holdout_row = holdout_df.iloc[[0]]
prediction = model.predict(first_holdout_row[accel_features])[0]

result = prediction
```

---
