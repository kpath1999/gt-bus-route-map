# Cache Grounding Issues

Reported 1 FLASH_FUSION_CACHE grounding failure(s) where the cached skeleton could not be reused directly.

## Query 7 (run 1)

**Query text:** Calculate the average overall magnitude of peak acceleration using the 99th percentiles of the X, Y, and Z axes.

**Failure reason:** `cache: Grounding step count mismatch: expected 2 for skeleton ['DERIVE_VECTOR_MAGNITUDE', 'AGGREGATE_COLUMN'], got 1`

**Execution path after fallback:** `typed_operator`

**Plan source after fallback:** `llm`

**Typed plan executed after fallback:**
```json
{
  "version": "1",
  "steps": [
    {
      "op": "DERIVE_VECTOR_MAGNITUDE",
      "columns": [
        "accel_stats_x_p99",
        "accel_stats_y_p99",
        "accel_stats_z_p99"
      ],
      "result": "peak_accel_magnitude"
    },
    {
      "op": "AGGREGATE_COLUMN",
      "column": "peak_accel_magnitude",
      "aggregate": "mean"
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
        "op": "DERIVE_VECTOR_MAGNITUDE",
        "columns": [
          "accel_stats_x_p99",
          "accel_stats_y_p99",
          "accel_stats_z_p99"
        ],
        "result": "peak_accel_magnitude"
      },
      {
        "op": "AGGREGATE_COLUMN",
        "column": "peak_accel_magnitude",
        "aggregate": "mean"
      }
    ]
  }
}
```

**Final executed code:**
```python
df['peak_accel_magnitude'] = (df['accel_stats_x_p99']**2 + df['accel_stats_y_p99']**2 + df['accel_stats_z_p99']**2)**0.5
result = df['peak_accel_magnitude'].mean()
```

**Stages run:** exact_cache_hit → cache_light_grounding → cache_miss_or_validation_failure → guardrail_plan → plan_validated → typed_exec

---

