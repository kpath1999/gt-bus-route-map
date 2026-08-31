cache validation for q4, q5, q6 does not seem to occur when the benchmark is run.
it fallback to the full Flash-Fusion planner which is why it takes so long.
but then when I trace it, the cache grounding works fine.
issue is not about the cache hits (hybrid/fuzzy) -- irrelevant.

Q: are the differences in how the benchmark and trace script are structured that might be causing this discrepancy?
how can we make it such that the grounding is deterministic and does not vary across identical benchmark and trace runs.

here's what the trace scripts produce correctly:

### Query 4 — Highest sample count

* **Query:** Which user has the highest total number of recorded data samples?
* **Operators:** `GROUP_AGGREGATE → RANK_GROUPS`
* **Cache:** Exact cache hit; typed plan validated.
* **Result:** **User 20 — 56,632 samples**
* **Latency:** 0.78s | **Cost:** $0.000059
* **Validation:** Passed; matched ground truth.  

### Query 5 — Dynamic vs. resting acceleration

* **Query:** Compare overall acceleration magnitude between dynamic and resting states.
* **Operators:** `FILTER_NOT_EMPTY → DERIVE_VECTOR_MAGNITUDE → SPLIT_BY_VALUES ×2 → AGGREGATE_PARTITIONS → COMPARE_PARTITIONS`
* **Cache:** Exact cache hit; typed plan validated.
* **Result:** Dynamic = **11.9625**, Resting = **9.8328**; difference = **2.1297** in favor of dynamic.
* **Latency:** 1.46s | **Cost:** $0.000064
* **Validation:** Passed; matched ground truth.  

### Query 6 — Largest resting-duration margin

* **Query:** Identify the user whose resting duration exceeds dynamic duration by the largest margin.
* **Operators:** `FILTER_NOT_EMPTY → DERIVE_DURATION_SECONDS → PARALLEL_AGGREGATE → DERIVE_BINARY → FILTER_COMPARE → RANK_ROWS`
* **Cache:** Exact cache hit; typed plan validated.
* **Result:** **User 20**, with a resting-over-dynamic margin of **54,004.34s**.
* **Latency:** 4.52s | **Cost:** $0.000124
* **Validation:** Passed; matched ground truth.  

and here's what the benchmark script produces:

### Q4: Determine the user whose total observed duration of resting states (sitting,standing) exceeds their duration of dynamic movements (walking,jogging,upstairs,downstairs) by the largest margin.

#### FLASH_FUSION_CACHE

**Answer:** The result is {'subject_id': 20, 'resting_duration': 56906.660065, 'dynamic_duration': 2902.322246, 'duration_margin': 54004.33781899999}

- Executed: True | Rejected: False | Judge: N/A
- Stages: exact_cache_hit,cache_light_grounding,cache_plan_validated,cache_miss_or_validation_failure,guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: FILTER_NOT_EMPTY,DERIVE_DURATION_SECONDS,PARALLEL_AGGREGATE,DERIVE_BINARY,FILTER_COMPARE,RANK_ROWS
- Latency: 15.14s | Cost: $0.00264

### Q5: Compare the overall acceleration magnitude between dynamic (walking,jogging,upstairs,downstairs) and resting (sitting,standing) states.

#### FLASH_FUSION_CACHE

**Answer:** The result is {'higher': 'dynamic', 'lower': 'resting', 'metric': 'mean acceleration_magnitude', 'dynamic': 11.962538760453763, 'resting': 9.8327917626509, 'difference': 2.1297469978028634}

- Executed: True | Rejected: False | Judge: N/A
- Stages: exact_cache_hit,cache_light_grounding,cache_plan_validated,cache_miss_or_validation_failure,guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: DERIVE_VECTOR_MAGNITUDE,SPLIT_BY_VALUES,SPLIT_BY_VALUES,AGGREGATE_PARTITIONS,COMPARE_PARTITIONS
- Latency: 13.30s | Cost: $0.00471

### Q6: Identify the user whose total recorded duration of resting states (sitting,standing) exceeds their duration of dynamic movements (walking,jogging,upstairs,downstairs) by the largest margin.

#### FLASH_FUSION_CACHE

**Answer:** The result is {'subject_id': 20, 'resting_duration': 56906.660065, 'dynamic_duration': 2902.322246, 'duration_margin': 54004.33781899999}

- Executed: True | Rejected: False | Judge: N/A
- Stages: exact_cache_hit,cache_light_grounding,cache_plan_validated,cache_miss_or_validation_failure,guardrail_plan,plan_validated,typed_exec
- Path: typed_operator
- Operators: FILTER_NOT_EMPTY,DERIVE_DURATION_SECONDS,PARALLEL_AGGREGATE,DERIVE_BINARY,FILTER_COMPARE,RANK_ROWS
- Latency: 15.86s | Cost: $0.00993