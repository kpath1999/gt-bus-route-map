# Flash-Fusion Benchmark Report

Generated: 2026-07-02T23:29:14+00:00

## Summary Table

| Baseline      |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:--------------|-------------------:|------------------:|-----------------:|
| AUTOIOT_PAPER |             1.0000 |          613.3617 |           0.0329 |

## Per-Query Results

### Q1: What is the minimum MLII value recorded for record_id 101?

#### AUTOIOT_PAPER

**Answer:** The minimum MLII value recorded for record_id 101 is **-3.175**.

- Executed: True | Rejected: False | Judge: N/A
- Stages: autoiot_terms,autoiot_search_queries,autoiot_retrieval,autoiot_design_high,autoiot_design_detail,autoiot_module_gen,autoiot_code_integration,autoiot_agent_loop,autoiot_select
- Latency: 613.36s | Cost: $0.03286

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: result = df.loc[df['record_id'] == 101, 'MLII'].min()
Observation: -3.175
Final Answer: The minimum MLII value recorded for record_id 101 is **-3.175**.
```

**Final Code Executed**

```python
result = df.loc[df['record_id'] == 101, 'MLII'].min()
```

---

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.