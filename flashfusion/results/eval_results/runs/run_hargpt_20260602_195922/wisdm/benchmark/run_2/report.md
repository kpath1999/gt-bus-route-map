# Flash-Fusion Benchmark Report

Generated: 2026-06-03T00:02:20+00:00

## Summary Table

| Baseline     |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-------------|-------------------:|------------------:|-----------------:|
| HARGPT_PAPER |             0.3333 |            0.0000 |           0.0000 |

## Per-Query Results

### Q1: What is the maximum recorded x-acceleration for user 15?

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q2: How many total samples in the dataset are classified as the Walking activity?

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q3: What is the average y-accel value for user 5 during the Sitting activity?

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q4: Which user has the highest total number of recorded data samples?

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q5: Compare the overall acceleration magnitude between dynamic movements and resting states.

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q6: Identify the user whose total recorded duration of stationary activities exceeds their duration of active locomotion by the largest margin.

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q7: What is the median net acceleration vector length for user 20 while ascending steps?

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q8: Calculate the difference between the average z-axis acceleration between Upstairs and Downstairs activities for all users.

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q9: How does the average walking speed in miles per hour correlate with the age of the users?

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q10: Based on the acceleration data, predict the exact geographic location where user 10 was jogging.

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q11: Are female subjects more likely to have a higher cadence during stair climbing than male subjects?

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

### Q12: Predict whether user 3 will meet the WHO recommended weekly moderate-to-vigorous physical activity guideline next week.

#### HARGPT_PAPER

**Answer:** 

- Executed: False | Rejected: True | Judge: N/A
- Stages: hargpt_scope
- Latency: 0.00s | Cost: $0.00000

**Alignment Explanation**

Rejected before execution: empty dataset excerpt.

**Rejection Reasoning**

- HARGPT_PAPER requires at least one data row to construct a prompt.

---

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.