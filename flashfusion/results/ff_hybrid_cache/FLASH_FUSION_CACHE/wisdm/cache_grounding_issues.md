# Cache Grounding Issues

Reported 5 FLASH_FUSION_CACHE grounding failure(s) where the cached skeleton could not be reused directly.

## Query 4 (run 1)

**Query text:** Which user has the highest total number of recorded data samples?

**Failure reason:** `cache: Grounding step count mismatch: expected 2 for skeleton ['GROUP_AGGREGATE', 'RANK_GROUPS'], got 1`

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

## Query 6 (run 1)

**Query text:** Identify the user whose total recorded duration of resting states (sitting,standing) exceeds their duration of dynamic movements (walking,jogging,upstairs,downstairs) by the largest margin.

**Failure reason:** `cache: 17 validation errors for DeterministicPlan
steps.2.PARALLEL_AGGREGATE.branches
  Field required [type=missing, input_value={'filter_column': 'activi...': 'PARALLEL_AGGREGATE'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.2.PARALLEL_AGGREGATE.filter_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='activity_label', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.filter_values
  Extra inputs are not permitted [type=extra_forbidden, input_value=['Sitting', 'Standing'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.group_by
  Extra inputs are not permitted [type=extra_forbidden, input_value=['subject_id'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.aggregate
  Extra inputs are not permitted [type=extra_forbidden, input_value='sum', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dt_s', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.result_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='resting_duration', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.left
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.right
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.operation
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.result
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.filter_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='activity_label', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.filter_values
  Extra inputs are not permitted [type=extra_forbidden, input_value=['Downstairs', 'Jogging', 'Upstairs', 'Walking'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.group_by
  Extra inputs are not permitted [type=extra_forbidden, input_value=['subject_id'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.aggregate
  Extra inputs are not permitted [type=extra_forbidden, input_value='sum', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dt_s', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.result_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dynamic_duration', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden`

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

**Stages run:** exact_cache_hit → cache_light_grounding → cache_miss_or_validation_failure → guardrail_plan → plan_validated → typed_exec

---

## Query 4 (run 2)

**Query text:** Which user has the highest total number of observed data samples?

**Failure reason:** `cache: Grounding step count mismatch: expected 2 for skeleton ['GROUP_AGGREGATE', 'RANK_GROUPS'], got 1`

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

## Query 6 (run 2)

**Query text:** Determine the user whose total observed duration of resting states (sitting,standing) exceeds their duration of dynamic movements (walking,jogging,upstairs,downstairs) by the largest margin.

**Failure reason:** `cache: 17 validation errors for DeterministicPlan
steps.2.PARALLEL_AGGREGATE.branches
  Field required [type=missing, input_value={'filter_column': 'activi...': 'PARALLEL_AGGREGATE'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.2.PARALLEL_AGGREGATE.filter_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='activity_label', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.filter_values
  Extra inputs are not permitted [type=extra_forbidden, input_value=['Sitting', 'Standing'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.group_by
  Extra inputs are not permitted [type=extra_forbidden, input_value=['subject_id'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.aggregate
  Extra inputs are not permitted [type=extra_forbidden, input_value='sum', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dt_s', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.result_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='resting_duration', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.left
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.right
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.operation
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.result
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.filter_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='activity_label', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.filter_values
  Extra inputs are not permitted [type=extra_forbidden, input_value=['Downstairs', 'Jogging', 'Upstairs', 'Walking'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.group_by
  Extra inputs are not permitted [type=extra_forbidden, input_value=['subject_id'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.aggregate
  Extra inputs are not permitted [type=extra_forbidden, input_value='sum', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dt_s', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.result_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dynamic_duration', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden`

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

**Stages run:** exact_cache_hit → cache_light_grounding → cache_miss_or_validation_failure → guardrail_plan → plan_validated → typed_exec

---

## Query 6 (run 3)

**Query text:** Identify the user whose total recorded duration of resting states (sitting,standing) exceeds their duration of dynamic movements (walking,jogging,upstairs,downstairs) by the largest margin.

**Failure reason:** `cache: 17 validation errors for DeterministicPlan
steps.2.PARALLEL_AGGREGATE.branches
  Field required [type=missing, input_value={'filter_column': 'activi...': 'PARALLEL_AGGREGATE'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.2.PARALLEL_AGGREGATE.filter_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='activity_label', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.filter_values
  Extra inputs are not permitted [type=extra_forbidden, input_value=['Sitting', 'Standing'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.group_by
  Extra inputs are not permitted [type=extra_forbidden, input_value=['subject_id'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.aggregate
  Extra inputs are not permitted [type=extra_forbidden, input_value='sum', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dt_s', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.2.PARALLEL_AGGREGATE.result_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='resting_duration', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.left
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.right
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.operation
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.result
  Field required [type=missing, input_value={'filter_column': 'activi..., 'op': 'DERIVE_BINARY'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
steps.3.DERIVE_BINARY.filter_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='activity_label', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.filter_values
  Extra inputs are not permitted [type=extra_forbidden, input_value=['Downstairs', 'Jogging', 'Upstairs', 'Walking'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.group_by
  Extra inputs are not permitted [type=extra_forbidden, input_value=['subject_id'], input_type=list]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.aggregate
  Extra inputs are not permitted [type=extra_forbidden, input_value='sum', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dt_s', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden
steps.3.DERIVE_BINARY.result_column
  Extra inputs are not permitted [type=extra_forbidden, input_value='dynamic_duration', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden`

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

**Stages run:** exact_cache_hit → cache_light_grounding → cache_miss_or_validation_failure → guardrail_plan → plan_validated → typed_exec

---

