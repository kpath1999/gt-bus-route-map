# Cache Grounding Issues

Reported 3 FLASH_FUSION_CACHE grounding failure(s) where the cached skeleton could not be reused directly.

## Query 4

**Query text:** Which user has the highest total number of recorded data samples?

**Failure reason:** `cache: light model JSON repair failed: Expecting property name enclosed in double quotes`

**Execution path after fallback:** `typed_operator`

**Plan source after fallback:** `llm`

**Typed plan executed after fallback:**
```json
{
  "version": "1",
  "steps": [
    {
      "op": "GROUP_AGGREGATE",
      "group_by": [
        "subject_id"
      ],
      "aggregate": "count",
      "column": null,
      "freq": null
    },
    {
      "op": "RANK_GROUPS",
      "direction": "max"
    }
  ]
}
```

**Raw planner output (before normalization):**
```json
{
  "in_scope": true,
  "rejection_reason": null,
  "ambiguous_concepts": [],
  "plan": {
    "version": "1",
    "steps": [
      {
        "op": "GROUP_AGGREGATE",
        "group_by": [
          "subject_id"
        ],
        "aggregate": "count",
        "column": null
      },
      {
        "op": "RANK_GROUPS",
        "direction": "max"
      }
    ]
  }
}
```

**Final executed code:**
```python
result = df.groupby('subject_id').size()
result = result.idxmax()
```

**Stages run:** exact_cache_hit → cache_light_grounding → cache_miss_or_validation_failure → guardrail_plan → plan_validated → typed_exec

---

## Query 5

**Query text:** Compare the overall acceleration magnitude between dynamic (walking,jogging,upstairs,downstairs) and resting (sitting,standing) states.

**Failure reason:** `cache: AGGREGATE_PARTITIONS: AGGREGATE_PARTITIONS partition 'dynamic' (mean) received an empty frame — every preceding filter or split removed all rows, so the aggregate would be undefined`

**Execution path after fallback:** `typed_operator`

**Plan source after fallback:** `llm`

**Typed plan executed after fallback:**
```json
{
  "version": "1",
  "steps": [
    {
      "op": "FILTER_NOT_EMPTY",
      "column": "activity_label"
    },
    {
      "op": "DERIVE_VECTOR_MAGNITUDE",
      "columns": [
        "x",
        "y",
        "z"
      ],
      "result": "acceleration_magnitude"
    },
    {
      "op": "SPLIT_BY_VALUES",
      "column": "activity_label",
      "values": [
        "Downstairs",
        "Jogging",
        "Upstairs",
        "Walking"
      ],
      "label": "dynamic"
    },
    {
      "op": "SPLIT_BY_VALUES",
      "column": "activity_label",
      "values": [
        "Sitting",
        "Standing"
      ],
      "label": "resting"
    },
    {
      "op": "AGGREGATE_PARTITIONS",
      "partitions": [
        "dynamic",
        "resting"
      ],
      "aggregate": "mean",
      "column": "acceleration_magnitude"
    },
    {
      "op": "COMPARE_PARTITIONS",
      "mode": "difference"
    }
  ]
}
```

**Raw planner output (before normalization):**
```json
{
  "in_scope": true,
  "rejection_reason": null,
  "ambiguous_concepts": [],
  "plan": {
    "version": "1",
    "steps": [
      {
        "op": "FILTER_NOT_EMPTY",
        "column": "activity_label"
      },
      {
        "op": "DERIVE_VECTOR_MAGNITUDE",
        "columns": [
          "x",
          "y",
          "z"
        ],
        "result": "acceleration_magnitude"
      },
      {
        "op": "SPLIT_BY_VALUES",
        "column": "activity_label",
        "values": [
          "Walking",
          "Jogging",
          "Upstairs",
          "Downstairs"
        ],
        "label": "dynamic"
      },
      {
        "op": "SPLIT_BY_VALUES",
        "column": "activity_label",
        "values": [
          "Sitting",
          "Standing"
        ],
        "label": "resting"
      },
      {
        "op": "AGGREGATE_PARTITIONS",
        "partitions": [
          "dynamic",
          "resting"
        ],
        "aggregate": "mean",
        "column": "acceleration_magnitude"
      },
      {
        "op": "COMPARE_PARTITIONS",
        "mode": "difference"
      }
    ]
  }
}
```

**Final executed code:**
```python
df = df[df['activity_label'].notna() & df['activity_label'].astype(str).str.strip().ne('')]
df['acceleration_magnitude'] = (df['x']**2 + df['y']**2 + df['z']**2)**0.5
dynamic = df[df['activity_label'].isin(['Downstairs', 'Jogging', 'Upstairs', 'Walking'])]
resting = df[df['activity_label'].isin(['Sitting', 'Standing'])]
result = {label: agg(partition) for label in partitions}
result = compare(dynamic, resting, mode='difference')
```

**Stages run:** exact_cache_hit → cache_light_grounding → cache_plan_validated → cache_miss_or_validation_failure → guardrail_plan → plan_validated → typed_exec

---

## Query 6

**Query text:** Identify the user whose total recorded duration of resting states (sitting,standing) exceeds their duration of dynamic movements (walking,jogging,upstairs,downstairs) by the largest margin.

**Failure reason:** `cache: PARALLEL_AGGREGATE: PARALLEL_AGGREGATE branch 0 (sum) received an empty frame — every preceding filter or split removed all rows, so the aggregate would be undefined`

**Execution path after fallback:** `typed_operator`

**Plan source after fallback:** `llm`

**Typed plan executed after fallback:**
```json
{
  "version": "1",
  "steps": [
    {
      "op": "FILTER_NOT_EMPTY",
      "column": "activity_label"
    },
    {
      "op": "DERIVE_DURATION_SECONDS",
      "timestamp_column": "timestamp",
      "group_by": [
        "subject_id"
      ],
      "result": "dt_s",
      "clip_negative": true,
      "fill_first": 0.0
    },
    {
      "op": "PARALLEL_AGGREGATE",
      "branches": [
        {
          "filter_column": "activity_label",
          "filter_values": [
            "Sitting",
            "Standing"
          ],
          "group_by": [
            "subject_id"
          ],
          "aggregate": "sum",
          "column": "dt_s",
          "result_column": "resting_duration"
        },
        {
          "filter_column": "activity_label",
          "filter_values": [
            "Downstairs",
            "Jogging",
            "Upstairs",
            "Walking"
          ],
          "group_by": [
            "subject_id"
          ],
          "aggregate": "sum",
          "column": "dt_s",
          "result_column": "dynamic_duration"
        }
      ]
    },
    {
      "op": "DERIVE_BINARY",
      "left": "resting_duration",
      "right": "dynamic_duration",
      "operation": "subtract",
      "result": "duration_margin"
    },
    {
      "op": "FILTER_COMPARE",
      "column": "duration_margin",
      "comparator": "gt",
      "value": 0
    },
    {
      "op": "RANK_ROWS",
      "column": "duration_margin",
      "direction": "max",
      "return_columns": [
        "subject_id",
        "resting_duration",
        "dynamic_duration",
        "duration_margin"
      ]
    }
  ]
}
```

**Raw planner output (before normalization):**
```json
{
  "in_scope": true,
  "rejection_reason": null,
  "ambiguous_concepts": [],
  "plan": {
    "version": "1",
    "steps": [
      {
        "op": "FILTER_NOT_EMPTY",
        "column": "activity_label"
      },
      {
        "op": "DERIVE_DURATION_SECONDS",
        "timestamp_column": "timestamp",
        "group_by": [
          "subject_id"
        ],
        "result": "dt_s",
        "clip_negative": true,
        "fill_first": 0.0
      },
      {
        "op": "PARALLEL_AGGREGATE",
        "branches": [
          {
            "filter_column": "activity_label",
            "filter_values": [
              "Sitting",
              "Standing"
            ],
            "group_by": [
              "subject_id"
            ],
            "aggregate": "sum",
            "column": "dt_s",
            "result_column": "resting_duration"
          },
          {
            "filter_column": "activity_label",
            "filter_values": [
              "Walking",
              "Jogging",
              "Upstairs",
              "Downstairs"
            ],
            "group_by": [
              "subject_id"
            ],
            "aggregate": "sum",
            "column": "dt_s",
            "result_column": "dynamic_duration"
          }
        ]
      },
      {
        "op": "DERIVE_BINARY",
        "left": "resting_duration",
        "right": "dynamic_duration",
        "operation": "subtract",
        "result": "duration_margin"
      },
      {
        "op": "FILTER_COMPARE",
        "column": "duration_margin",
        "comparator": "gt",
        "value": 0
      },
      {
        "op": "RANK_ROWS",
        "column": "duration_margin",
        "direction": "max",
        "return_columns": [
          "subject_id",
          "resting_duration",
          "dynamic_duration",
          "duration_margin"
        ]
      }
    ]
  }
}
```

**Final executed code:**
```python
df = df[df['activity_label'].notna() & df['activity_label'].astype(str).str.strip().ne('')]
df = df.sort_values(['subject_id', 'timestamp']); df['dt_s'] = df.groupby(['subject_id'])['timestamp'].diff().dt.total_seconds().clip(lower=0).fillna(0.0)
# PARALLEL_AGGREGATE branches:
# Branch 0: filter 'activity_label' in ['Sitting', 'Standing']
branch_0 = df.groupby(['subject_id'])['dt_s'].sum()
# Branch 1: filter 'activity_label' in ['Downstairs', 'Jogging', 'Upstairs', 'Walking']
branch_1 = df.groupby(['subject_id'])['dt_s'].sum()
merged = branch_0.merge(branch_1, on=['subject_id'], how='outer').fillna(0)
df['duration_margin'] = df['resting_duration'] - df['dynamic_duration']
df = df[df['duration_margin'] > 0]
idx = df['duration_margin'].idxmax(); result = df.loc[idx, ['subject_id', 'resting_duration', 'dynamic_duration', 'duration_margin']].to_dict()
```

**Stages run:** exact_cache_hit → cache_light_grounding → cache_plan_validated → cache_miss_or_validation_failure → guardrail_plan → plan_validated → typed_exec

---

