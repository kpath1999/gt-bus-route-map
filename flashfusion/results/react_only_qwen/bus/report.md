# Flash-Fusion Benchmark Report

Generated: 2026-08-12T04:01:07+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             1.0000 |            1.6796 |           0.0011 |

## Typed-Operator Coverage

| Baseline   |   Queries |   Typed | Coverage   |   ReAct fallback |   Guardrail reject | Typed avg latency (s)   | Fallback avg latency (s)   |
|:-----------|----------:|--------:|:-----------|-----------------:|-------------------:|:------------------------|:---------------------------|
| REACT_ONLY |         4 |       0 | 0%         |                0 |                  0 | -                       | -                          |

## Per-Query Results

### Q9: How does passenger occupancy correlate with road roughness during this trip?

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): passenger occupancy, road roughness

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.50s | Cost: $0.00107

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
- Latency: 1.57s | Cost: $0.00108

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

**Answer:** REJECT: Missing required dataset concept(s): operating schedule, scheduled route times, planned timetable.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 1.45s | Cost: $0.00109

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): operating schedule, scheduled route times, planned timetable.
```

**Rejection Reasoning**

- Missing required dataset concept(s): operating schedule, scheduled route times, planned timetable.

---

### Q12: Predict next week's pothole repairs for the road segments in this dataset.

#### REACT_ONLY

**Answer:** REJECT: Missing required dataset concept(s): pothole repairs, road segment identifiers, historical repair records, or any target variable indicating past or future pothole repair events.

- Executed: False | Rejected: True | Judge: N/A
- Stages: react_agent
- Path: react_reject
- Latency: 2.20s | Cost: $0.00116

**Agent Trace**

```
Thought: Scope check rejected the request before code generation
Final Answer: REJECT: Missing required dataset concept(s): pothole repairs, road segment identifiers, historical repair records, or any target variable indicating past or future pothole repair events.
```

**Rejection Reasoning**

- Missing required dataset concept(s): pothole repairs, road segment identifiers, historical repair records, or any target variable indicating past or future pothole repair events.

---
