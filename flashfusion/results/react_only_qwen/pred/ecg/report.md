# Flash-Fusion Benchmark Report

Generated: 2026-08-12T18:28:40+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             0.2500 |           30.1164 |           0.0022 |

## Typed-Operator Coverage

| Baseline   |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject | Typed avg latency (s)   | Fallback avg latency (s)   |
|:-----------|----------:|--------:|:-----------|-----------------:|-------------------:|:------------------------|:---------------------------|
| REACT_ONLY |         4 |       0 | 0%         |                0 |                  0 | -                       | -                          |

## Per-Query Results

### Q13: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a logistic regression model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The result is: false

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 48.93s | Cost: $0.00350

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: from sklearn.linear_model import LogisticRegression
import numpy as np

# Filter to record_id 101 and sort by time_s
record_df = df[df['record_id'] == 101].sort_values('time_s').reset_index(drop=True)

# Exclude rows with missing annotations
record_df = record_df[record_df['annotation'] != ''].reset_index(drop=True)

# Determine split index for 80/20 chronological split
n_rows = len(record_df)
if n_rows == 0:
    result = None
else:
    split_idx = int(np.floor(0.8 * n_rows))
    if split_idx == 0 or split_idx >= n_rows:
        result = None
    else:
        train_df = record_df.iloc[:split_idx]
        holdout_df = record_df.iloc[split_idx:]

        # Prepare features and target
        X_train = train_df[['MLII', 'V1']].values
        y_train = (train_df['annotation'] != '').astype(int).values  # Though already filtered, still binary

        # Fit logistic regression
        model = LogisticRegression()
        model.fit(X_train, y_train)

        # Predict for first row in holdout
        X_holdout_first = holdout_df[['MLII', 'V1']].iloc[[0]].values
        prediction = model.predict(X_holdout_first)[0]
        result = bool(prediction)
Observation: ValueError: This solver needs samples of at least 2 classes in the data, but the data contains only one class: 1
Thought: Attempt 2: generate executable pandas code
Action: python_exec
Action Input: # Filter to record_id 101
df_filtered = df[df['record_id'] == 101].copy()

# Sort by time_s in ascending order
df_sorted = df_filtered.sort_values('time_s').reset_index(drop=True)

# Remove rows with missing annotations (empty string)
df_annotated = df_sorted[df_sorted['annotation'] != ''].copy()

# If no annotated data remains, we cannot train
if len(df_annotated) == 0:
    result = None
else:
    # Create binary target: 1 if annotation is present (non-empty), 0 otherwise
    # But since we've filtered out empty annotations, all remaining are positive
    # However, the error suggests we need both classes. So we must include negative samples.
    
    # Instead, go back to original sorted data and create proper binary labels
    df_with_labels = df_sorted.copy()
    df_with_labels['has_annotation'] = (df_with_labels['annotation'] != '').astype(int)
    
    # Now split chronologically
    n_total = len(df_with_labels)
    n_train = int(0.8 * n_total)
    
    train_df = df_with_labels.iloc[:n_train]
    holdout_df = df_with_labels.iloc[n_train:]
    
    # Check if we have at least one of each class in training
    if train_df['has_annotation'].nunique() < 2:
        result = None
    else:
        from sklearn.linear_model import LogisticRegression
        
        X_train = train_df[['MLII', 'V1']]
        y_train = train_df['has_annotation']
        
        model = LogisticRegression()
        model.fit(X_train, y_train)
        
        # Predict for first row in holdout set
        if len(holdout_df) == 0:
            result = None
        else:
            first_holdout = holdout_df.iloc[0][['MLII', 'V1']].values.reshape(1, -1)
            prediction = model.predict(first_holdout)[0]
            result = bool(prediction)
Observation: False
Final Answer: The result is: false
```

**Final Code Executed**

```python
# Filter to record_id 101
df_filtered = df[df['record_id'] == 101].copy()

# Sort by time_s in ascending order
df_sorted = df_filtered.sort_values('time_s').reset_index(drop=True)

# Remove rows with missing annotations (empty string)
df_annotated = df_sorted[df_sorted['annotation'] != ''].copy()

# If no annotated data remains, we cannot train
if len(df_annotated) == 0:
    result = None
else:
    # Create binary target: 1 if annotation is present (non-empty), 0 otherwise
    # But since we've filtered out empty annotations, all remaining are positive
    # However, the error suggests we need both classes. So we must include negative samples.
    
    # Instead, go back to original sorted data and create proper binary labels
    df_with_labels = df_sorted.copy()
    df_with_labels['has_annotation'] = (df_with_labels['annotation'] != '').astype(int)
    
    # Now split chronologically
    n_total = len(df_with_labels)
    n_train = int(0.8 * n_total)
    
    train_df = df_with_labels.iloc[:n_train]
    holdout_df = df_with_labels.iloc[n_train:]
    
    # Check if we have at least one of each class in training
    if train_df['has_annotation'].nunique() < 2:
        result = None
    else:
        from sklearn.linear_model import LogisticRegression
        
        X_train = train_df[['MLII', 'V1']]
        y_train = train_df['has_annotation']
        
        model = LogisticRegression()
        model.fit(X_train, y_train)
        
        # Predict for first row in holdout set
        if len(holdout_df) == 0:
            result = None
        else:
            first_holdout = holdout_df.iloc[0][['MLII', 'V1']].values.reshape(1, -1)
            prediction = model.predict(first_holdout)[0]
            result = bool(prediction)
```

---

### Q14: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a random forest model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The result is: true

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 23.59s | Cost: $0.00158

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import numpy as np

# Filter to record_id 101 and sort by time_s
record_df = df[df['record_id'] == 101].sort_values('time_s').reset_index(drop=True)

# Remove rows with missing annotations
record_df = record_df[record_df['annotation'] != ''].reset_index(drop=True)

# Determine split index for 80% training, 20% holdout
n_rows = len(record_df)
if n_rows == 0:
    result = None
else:
    split_idx = int(0.8 * n_rows)
    if split_idx == 0 or split_idx >= n_rows:
        result = None
    else:
        train_df = record_df.iloc[:split_idx]
        holdout_df = record_df.iloc[split_idx:]

        # Prepare features and target
        X_train = train_df[['MLII', 'V1']]
        y_train = train_df['annotation'].notna().astype(int)  # Binary: 1 if annotation present, 0 otherwise

        # Train random forest model
        model = RandomForestClassifier(random_state=42)
        model.fit(X_train, y_train)

        # Predict for first row in holdout set
        X_holdout_first = holdout_df[['MLII', 'V1']].iloc[[0]]
        prediction = model.predict(X_holdout_first)[0]
        result = bool(prediction)
Observation: True
Final Answer: The result is: true
```

**Final Code Executed**

```python
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import numpy as np

# Filter to record_id 101 and sort by time_s
record_df = df[df['record_id'] == 101].sort_values('time_s').reset_index(drop=True)

# Remove rows with missing annotations
record_df = record_df[record_df['annotation'] != ''].reset_index(drop=True)

# Determine split index for 80% training, 20% holdout
n_rows = len(record_df)
if n_rows == 0:
    result = None
else:
    split_idx = int(0.8 * n_rows)
    if split_idx == 0 or split_idx >= n_rows:
        result = None
    else:
        train_df = record_df.iloc[:split_idx]
        holdout_df = record_df.iloc[split_idx:]

        # Prepare features and target
        X_train = train_df[['MLII', 'V1']]
        y_train = train_df['annotation'].notna().astype(int)  # Binary: 1 if annotation present, 0 otherwise

        # Train random forest model
        model = RandomForestClassifier(random_state=42)
        model.fit(X_train, y_train)

        # Predict for first row in holdout set
        X_holdout_first = holdout_df[['MLII', 'V1']].iloc[[0]]
        prediction = model.predict(X_holdout_first)[0]
        result = bool(prediction)
```

---

### Q15: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a 1-nearest-neighbor model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The predicted behavior label for the first holdout row is: N.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 23.19s | Cost: $0.00176

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Filter to record_id 101
record_df = df[df['record_id'] == 101].copy()

# Sort by time_s in ascending order
record_df = record_df.sort_values('time_s').reset_index(drop=True)

# Remove rows with missing annotations for training and prediction
record_df = record_df[record_df['annotation'] != ''].reset_index(drop=True)

# Determine split index for 80% training, 20% holdout
n_rows = len(record_df)
if n_rows == 0:
    result = None
else:
    split_idx = int(0.8 * n_rows)
    if split_idx == 0 or split_idx >= n_rows:
        result = None
    else:
        train_df = record_df.iloc[:split_idx]
        holdout_df = record_df.iloc[split_idx:]

        # Features and target for training
        X_train = train_df[['MLII', 'V1']].values
        y_train = train_df['annotation'].values

        # First row in holdout set
        X_holdout = holdout_df[['MLII', 'V1']].iloc[[0]].values

        # Train 1-nearest-neighbor model
        from sklearn.neighbors import KNeighborsClassifier
        knn = KNeighborsClassifier(n_neighbors=1)
        knn.fit(X_train, y_train)

        # Predict annotation for first holdout row
        prediction = knn.predict(X_holdout)[0]
        result = prediction
Observation: N
Final Answer: The predicted behavior label for the first holdout row is: N.
```

**Final Code Executed**

```python
# Filter to record_id 101
record_df = df[df['record_id'] == 101].copy()

# Sort by time_s in ascending order
record_df = record_df.sort_values('time_s').reset_index(drop=True)

# Remove rows with missing annotations for training and prediction
record_df = record_df[record_df['annotation'] != ''].reset_index(drop=True)

# Determine split index for 80% training, 20% holdout
n_rows = len(record_df)
if n_rows == 0:
    result = None
else:
    split_idx = int(0.8 * n_rows)
    if split_idx == 0 or split_idx >= n_rows:
        result = None
    else:
        train_df = record_df.iloc[:split_idx]
        holdout_df = record_df.iloc[split_idx:]

        # Features and target for training
        X_train = train_df[['MLII', 'V1']].values
        y_train = train_df['annotation'].values

        # First row in holdout set
        X_holdout = holdout_df[['MLII', 'V1']].iloc[[0]].values

        # Train 1-nearest-neighbor model
        from sklearn.neighbors import KNeighborsClassifier
        knn = KNeighborsClassifier(n_neighbors=1)
        knn.fit(X_train, y_train)

        # Predict annotation for first holdout row
        prediction = knn.predict(X_holdout)[0]
        result = prediction
```

---

### Q16: Filter to record_id 101 and sort its rows by time_s in ascending order. Use the first 80% of rows for training and the final 20% as the chronological holdout. Train a hist gradient boosting model using the features MLII and V1. Predict whether an annotation is present for the first row in the holdout set.

#### REACT_ONLY

**Answer:** The result is: true

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Path: react_agent
- Latency: 24.76s | Cost: $0.00194

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: # Filter to record_id 101
df_record = df[df['record_id'] == 101].copy()

# Sort by time_s in ascending order
df_record = df_record.sort_values('time_s').reset_index(drop=True)

# Remove rows where annotation is empty string
df_record = df_record[df_record['annotation'] != ''].reset_index(drop=True)

# If no rows remain after filtering, prediction cannot be made
if len(df_record) == 0:
    result = None
else:
    # Determine split index for 80% training
    n = len(df_record)
    split_idx = int(0.8 * n)
    
    # Ensure at least one row in holdout
    if split_idx >= n:
        split_idx = n - 1
    if split_idx < 0:
        split_idx = 0

    # Split into train and holdout
    train_df = df_record.iloc[:split_idx].copy()
    holdout_df = df_record.iloc[split_idx:].copy()
    
    # If training set is empty, cannot train model
    if len(train_df) == 0 or len(holdout_df) == 0:
        result = None
    else:
        from sklearn.ensemble import HistGradientBoostingClassifier
        import numpy as np
        
        # Prepare features and target
        X_train = train_df[['MLII', 'V1']].values
        y_train = (train_df['annotation'] != '').astype(int).values  # Binary: 1 if annotation present
        
        # Train model
        model = HistGradientBoostingClassifier()
        model.fit(X_train, y_train)
        
        # Predict on first row of holdout
        X_holdout_first = holdout_df[['MLII', 'V1']].iloc[[0]].values
        pred = model.predict(X_holdout_first)[0]
        result = bool(pred)
Observation: True
Final Answer: The result is: true
```

**Final Code Executed**

```python
# Filter to record_id 101
df_record = df[df['record_id'] == 101].copy()

# Sort by time_s in ascending order
df_record = df_record.sort_values('time_s').reset_index(drop=True)

# Remove rows where annotation is empty string
df_record = df_record[df_record['annotation'] != ''].reset_index(drop=True)

# If no rows remain after filtering, prediction cannot be made
if len(df_record) == 0:
    result = None
else:
    # Determine split index for 80% training
    n = len(df_record)
    split_idx = int(0.8 * n)
    
    # Ensure at least one row in holdout
    if split_idx >= n:
        split_idx = n - 1
    if split_idx < 0:
        split_idx = 0

    # Split into train and holdout
    train_df = df_record.iloc[:split_idx].copy()
    holdout_df = df_record.iloc[split_idx:].copy()
    
    # If training set is empty, cannot train model
    if len(train_df) == 0 or len(holdout_df) == 0:
        result = None
    else:
        from sklearn.ensemble import HistGradientBoostingClassifier
        import numpy as np
        
        # Prepare features and target
        X_train = train_df[['MLII', 'V1']].values
        y_train = (train_df['annotation'] != '').astype(int).values  # Binary: 1 if annotation present
        
        # Train model
        model = HistGradientBoostingClassifier()
        model.fit(X_train, y_train)
        
        # Predict on first row of holdout
        X_holdout_first = holdout_df[['MLII', 'V1']].iloc[[0]].values
        pred = model.predict(X_holdout_first)[0]
        result = bool(pred)
```

---
