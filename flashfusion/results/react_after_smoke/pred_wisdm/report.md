# Flash-Fusion Benchmark Report

Generated: 2026-07-27T00:13:31+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             0.7500 |           28.7858 |           0.0007 |

## Per-Query Results

### Q13: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the training rows. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted activity label for the first row in the holdout set is: Jogging.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Latency: 21.79s | Cost: $0.00053

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression

# Filter out empty strings from activity_label
df = df[df['activity_label'] != '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df, holdout_df = df[:train_size], df[train_size:]

# Define features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train logistic regression model
model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)

# Predict activity label for the first row in the holdout set
first_holdout_row = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
result = model.predict(first_holdout_row)[0]
Observation: Jogging
Final Answer: The predicted activity label for the first row in the holdout set is: Jogging.
```

**Final Code Executed**

```python
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression

# Filter out empty strings from activity_label
df = df[df['activity_label'] != '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df, holdout_df = df[:train_size], df[train_size:]

# Define features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train logistic regression model
model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)

# Predict activity label for the first row in the holdout set
first_holdout_row = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
result = model.predict(first_holdout_row)[0]
```

---

### Q14: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the training rows. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** [ERROR] Safe backend failed after 3 attempts: TimeoutError: Safe code execution exceeded 10.0s and was terminated

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Latency: 60.73s | Cost: $0.00132

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# Filter out empty strings from activity_label
df = df[df['activity_label']!= '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df, holdout_df = df[:train_size], df[train_size:]

# Define features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train a random forest model
model = RandomForestClassifier(n_estimators=100)
model.fit(X_train, y_train)

# Predict the activity label for the first row in the holdout set
first_holdout_row = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
prediction = model.predict(first_holdout_row)

result = prediction[0]
Observation: TimeoutError: Safe code execution exceeded 10.0s and was terminated
Thought: Attempt 2: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# Filter out empty strings from activity_label
df = df[df['activity_label'] != '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df, holdout_df = df[:train_size], df[train_size:]

# Define features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train a random forest model
model = RandomForestClassifier()
model.fit(X_train, y_train)

# Predict the activity label for the first row in the holdout set
first_holdout_row = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
result = model.predict(first_holdout_row)[0]
Observation: TimeoutError: Safe code execution exceeded 10.0s and was terminated
Thought: Attempt 3: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# Filter out rows with missing activity labels
df = df[df['activity_label'] != '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df, holdout_df = df[:train_size], df[train_size:]

# Define features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train a random forest model
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)

# Predict the activity label for the first row in the holdout set
result = model.predict(holdout_df[['x', 'y', 'z']].head(1))
Observation: TimeoutError: Safe code execution exceeded 10.0s and was terminated
Final Answer: [ERROR] Safe backend failed after 3 attempts: TimeoutError: Safe code execution exceeded 10.0s and was terminated
```

**Final Code Executed**

```python
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# Filter out rows with missing activity labels
df = df[df['activity_label'] != '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df, holdout_df = df[:train_size], df[train_size:]

# Define features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train a random forest model
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)

# Predict the activity label for the first row in the holdout set
result = model.predict(holdout_df[['x', 'y', 'z']].head(1))
```

---

### Q15: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a 1-nearest-neighbor model using the training rows. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted activity label for the first row in the holdout set is: Sitting

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Latency: 15.57s | Cost: $0.00051

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.neighbors import KNeighborsClassifier

# Filter out rows with empty activity labels
df = df[df['activity_label']!= '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df = df[:train_size]
holdout_df = df[train_size:]

# Train a 1-nearest-neighbor model
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(X_train, y_train)

# Predict the activity label for the first row in the holdout set
X_holdout = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
y_pred = knn.predict(X_holdout)

result = y_pred[0]
Observation: Sitting
Final Answer: The predicted activity label for the first row in the holdout set is: Sitting
```

**Final Code Executed**

```python
import pandas as pd
from sklearn.neighbors import KNeighborsClassifier

# Filter out rows with empty activity labels
df = df[df['activity_label']!= '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df = df[:train_size]
holdout_df = df[train_size:]

# Train a 1-nearest-neighbor model
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']
knn = KNeighborsClassifier(n_neighbors=1)
knn.fit(X_train, y_train)

# Predict the activity label for the first row in the holdout set
X_holdout = holdout_df[['x', 'y', 'z']].iloc[0].values.reshape(1, -1)
y_pred = knn.predict(X_holdout)

result = y_pred[0]
```

---

### Q16: Sort all WISDM rows by timestamp in ascending order, using subject_id as the tie-breaker. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a hist gradient boosting model using the training rows. Predict the activity label for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted activity label for the first row in the holdout set is: Sitting.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Latency: 17.05s | Cost: $0.00055

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import LabelEncoder

# Filter out rows with missing activity labels
df = df[df['activity_label'] != '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df, holdout_df = df[:train_size], df[train_size:]

# Encode activity labels
le = LabelEncoder()
train_df['activity_label'] = le.fit_transform(train_df['activity_label'])
holdout_df['activity_label'] = le.transform(holdout_df['activity_label'])

# Define features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train hist gradient boosting model
model = HistGradientBoostingClassifier()
model.fit(X_train, y_train)

# Predict activity label for the first row in the holdout set
X_holdout = holdout_df[['x', 'y', 'z']].iloc[[0]]
result = le.inverse_transform(model.predict(X_holdout))[0]
Observation: Sitting
Final Answer: The predicted activity label for the first row in the holdout set is: Sitting.
```

**Final Code Executed**

```python
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import LabelEncoder

# Filter out rows with missing activity labels
df = df[df['activity_label'] != '']

# Sort rows by timestamp and subject_id
df = df.sort_values(by=['timestamp', 'subject_id'])

# Split data into training and holdout sets
train_size = int(0.8 * len(df))
train_df, holdout_df = df[:train_size], df[train_size:]

# Encode activity labels
le = LabelEncoder()
train_df['activity_label'] = le.fit_transform(train_df['activity_label'])
holdout_df['activity_label'] = le.transform(holdout_df['activity_label'])

# Define features and target
X_train = train_df[['x', 'y', 'z']]
y_train = train_df['activity_label']

# Train hist gradient boosting model
model = HistGradientBoostingClassifier()
model.fit(X_train, y_train)

# Predict activity label for the first row in the holdout set
X_holdout = holdout_df[['x', 'y', 'z']].iloc[[0]]
result = le.inverse_transform(model.predict(X_holdout))[0]
```

---
