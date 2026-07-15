# Flash-Fusion Benchmark Report

Generated: 2026-07-14T18:50:53+00:00

## Summary Table

| Baseline   |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-----------|-------------------:|------------------:|-----------------:|
| REACT_ONLY |             0.0000 |           21.0146 |           0.0004 |

## Per-Query Results

### Q9: Did the patient corresponding to record_id 101 experience a fatal cardiac event within a year?

#### REACT_ONLY

**Answer:** [TIMEOUT] Query exceeded 20.0s latency budget; skipped to next query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: timeout
- Latency: 20.00s | Cost: $0.00027

---

### Q10: Is there a statistical correlation between patient BMI and their maximum MLII amplitude?

#### REACT_ONLY

**Answer:** [TIMEOUT] Query exceeded 20.0s latency budget; skipped to next query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: timeout
- Latency: 20.44s | Cost: $0.00043

---

### Q11: Which patients in this dataset have a recorded family history of atrial fibrillation?

#### REACT_ONLY

**Answer:** Based on the provided execution output, there is no direct information about the patients' family history of atrial fibrillation. The columns provided are 'sample_idx', 'time_s', 'record_id', and 'annotation', but none of them indicate a family history of atrial fibrillation. 

To determine which patients have a recorded family history of atrial fibrillation, you would need to examine other columns or tables in the dataset that are not shown in this output.

- Executed: True | Rejected: False | Judge: N/A
- Stages: react_agent
- Latency: 23.61s | Cost: $0.00048

**Agent Trace**

```
Thought: Attempt 1: generate executable pandas code
Action: python_exec
Action Input: result = df[df['annotation'] == 'A']
Observation:           sample_idx       time_s  ...  record_id  annotation
135299        135299   375.830556  ...        101           A
215567        215567   598.797222  ...        101           A
532014        532014  1477.816667  ...        101           A
2863192       263192   731.088889  ...        108           A
3128246       528246  1467.350000  ...        108           A
...              ...          ...  ...        ...         ...
24747012       47012   130.588889  ...        233           A
25071558      371558  1032.105556  ...        233           A
25091265      391265  1086.847222  ...        233           A
25094401      394401  1095.558333  ...        233           A
25315575      615575  1709.930556  ...        233           A

[2498 rows x 6 columns]
Final Answer: Based on the provided execution output, there is no direct information about the patients' family history of atrial fibrillation. The columns provided are 'sample_idx', 'time_s', 'record_id', and 'annotation', but none of them indicate a family history of atrial fibrillation. 

To determine which patients have a recorded family history of atrial fibrillation, you would need to examine other columns or tables in the dataset that are not shown in this output.
```

**Final Code Executed**

```python
result = df[df['annotation'] == 'A']
```

---

### Q12: Estimate the patient's blood pressure during the time of the recording for record_id 105.

#### REACT_ONLY

**Answer:** [TIMEOUT] Query exceeded 20.0s latency budget; skipped to next query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: timeout
- Latency: 20.01s | Cost: $0.00037

---

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.