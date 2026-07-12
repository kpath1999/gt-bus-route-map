# Flash-Fusion Benchmark Report

Generated: 2026-07-03T15:18:29+00:00

## Summary Table

| Baseline       |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:---------------|-------------------:|------------------:|-----------------:|
| LLMSENSE_PAPER |             0.4167 |           18.5571 |           0.0673 |

## Per-Query Results

### Q1: What is the maximum accel_variance observed in this dataset?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 43.90s | Cost: $0.06733

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of vibrations with a mean acceleration of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The ride is characterized by low-intensity vibrations, with the highest intensity observed at 0.7660. The dataset also shows a consistent latitude and longitude, indicating a steady movement. There are no significant anomalies or abrupt transitions observed during this period. The overall ride quality is smooth, with minimal roughness.
```

---

### Q2: What is the average accel_mean across all recorded samples?

#### LLMSENSE_PAPER

**Answer:** The average accel_mean across all recorded samples is 9.27.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 37.96s | Cost: $0.06734

**Agent Trace**

```
[Group all - chunk 1] The data shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100, indicating a relatively smooth ride. The ride lasts for approximately 30 minutes, with a total distance traveled of around 1.5 kilometers. The acceleration variance remains relatively constant throughout, with some minor fluctuations. There are no significant anomalies or abrupt transitions observed during this period. The ride is characterized by a steady, low-intensity acceleration, with no sharp turns or sudden changes in speed. The overall trend of the data suggests a steady, consistent movement with minimal changes in acceleration or direction.
```

---

### Q3: At what exact timestamp was the highest accel_stats_z_p99 recorded?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 4.09s | Cost: $0.06728

**Agent Trace**

```
[Group all - chunk 1] The data shows a dominant activity pattern of relatively constant acceleration, with a mean acceleration of 9.2690 and a variance of 0.2100. The duration of this activity is approximately 30 minutes, with a few instances of increased acceleration variance. There are no significant anomalies or abrupt transitions observed during this period. The overall trend is consistent with a steady, low-intensity activity.
```

---

### Q4: How many data samples show an accel_variance strictly greater than 0.20?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 5.33s | Cost: $0.06733

**Agent Trace**

```
[Group all - chunk 1] The data shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100, indicating a relatively smooth ride. The ride lasts for approximately 30 minutes, with a total distance traveled of around 1.5 kilometers. The acceleration variance remains relatively constant throughout, with some minor fluctuations. There are no significant anomalies or abrupt transitions observed during this period. The ride is characterized by a steady, moderate acceleration, with no extreme changes in speed or direction.
```

---

### Q5: Is the northern half of the route (latitude above median) rougher than the southern half, based on average acceleration variance?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 3.36s | Cost: $0.06729

**Agent Trace**

```
[Group all - chunk 1] The data shows a dominant activity pattern of relatively constant acceleration, with a mean acceleration of 9.2690 and a variance of 0.2100. The duration of this activity is approximately 30 minutes, with a few instances of increased acceleration variance. There are no significant anomalies or abrupt transitions observed during this period. The data also indicates a relatively stable ride, with minimal changes in acceleration over time.
```

---

### Q6: Which location (latitude, longitude) recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 4.86s | Cost: $0.06729

**Agent Trace**

```
[Group all - chunk 1] The data shows a dominant activity pattern of relatively constant acceleration, with a mean acceleration of 9.2690 and a variance of 0.2100. The duration of this activity is approximately 30 minutes, with a few instances of increased acceleration variance. There are no significant anomalies or abrupt transitions observed during this period. The overall trend is consistent, with minor fluctuations in acceleration.
```

---

### Q7: Calculate the average overall magnitude of peak acceleration using the 99th percentiles of the X, Y, and Z axes.

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 7.55s | Cost: $0.06734

**Agent Trace**

```
[Group all - chunk 1] The data shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100, indicating a relatively smooth ride. The ride lasts for approximately 30 minutes, with a few instances of increased acceleration variance, but overall, the ride remains stable. There are no significant anomalies or abrupt transitions observed during this period. The ride's intensity remains consistent, with no notable changes in acceleration or deceleration. The data does not indicate any rough segments, and the overall ride quality is good.
```

---

### Q8: If we group the data into 1-minute intervals, which time window experienced the most sustained turbulence?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 5.56s | Cost: $0.06731

**Agent Trace**

```
[Group all - chunk 1] The data shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100, indicating a relatively smooth ride. The ride is mostly stable with minor fluctuations in acceleration, with a few instances of increased variance. The data also reveals a slight increase in acceleration variance around 16:33:00, which may indicate a minor bump or rough patch. Overall, the ride is characterized by low to moderate acceleration, with some minor variations in intensity.
```

---

### Q9: How does passenger occupancy correlate with road roughness during this trip?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 4.98s | Cost: $0.06731

**Agent Trace**

```
[Group all - chunk 1] The data shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100, indicating a relatively smooth ride. The ride lasts for approximately 30 minutes, with a total distance traveled of around 1.5 kilometers. The acceleration variance remains relatively low throughout, with a few minor fluctuations. There are no significant anomalies or abrupt transitions observed during this period. The ride is characterized by a steady, low-intensity acceleration, with minimal changes in speed or direction.
```

---

### Q10: Did rainy weather cause the roughest segments in this route?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 5.46s | Cost: $0.06731

**Agent Trace**

```
[Group all - chunk 1] The data shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100, indicating a relatively smooth ride. The ride lasts for approximately 30 minutes, with a total distance traveled of around 1.5 kilometers. The acceleration variance remains relatively constant throughout the ride, with some minor fluctuations. There are no significant anomalies or abrupt transitions observed during this period. The ride is characterized by a steady, moderate acceleration, with no extreme changes in speed or direction.
```

---

### Q11: Which bus driver generated the smoothest driving profile?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 5.74s | Cost: $0.06731

**Agent Trace**

```
[Group all - chunk 1] The data shows a dominant activity pattern of relatively constant acceleration, with minor fluctuations in the range of 0.2100 to 0.2100 g, and a duration of approximately 30 minutes. The intensity of the activity is relatively low, with a mean acceleration of 9.2690 and a variance of 0.2100. There are no significant anomalies or abrupt transitions observed during this period. The activity remains relatively stable, with no major changes in acceleration or variance.
```

---

### Q12: Predict next week's pothole repairs for the road segments in this dataset.

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 93.90s | Cost: $0.06734

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of vibrations with varying intensity and duration. The data is collected over a 30-minute window with a history of 6 hours, indicating a prolonged period of activity. The vibrations are characterized by a mean acceleration of 9.2690, with percentile values ranging from -0.7660 to 2.1450. The data also exhibits a relatively low variance, with a standard deviation of 0.2100, indicating a stable and consistent vibration pattern. The dataset does not show any significant anomalies or abrupt transitions, suggesting a smooth and continuous activity pattern.
```

---

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.