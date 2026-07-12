# Flash-Fusion Benchmark Report

Generated: 2026-07-03T15:14:08+00:00

## Summary Table

| Baseline       |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:---------------|-------------------:|------------------:|-----------------:|
| LLMSENSE_PAPER |             0.3333 |           29.3686 |           0.0674 |

## Per-Query Results

### Q1: What is the maximum accel_variance observed in this dataset?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 49.87s | Cost: $0.06740

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The acceleration values range from -0.7660 to 2.1450, with a 1-sigma range of 0.6130 to 1.8390. The dataset contains 2025-06-06 11:08:35 to 2025-06-06 11:21:08, with 2025-06-06 11:21:08 being the end of the window. The ride is generally smooth, with some minor fluctuations. There are no significant anomalies or abrupt transitions observed during this period.
```

---

### Q2: What is the average accel_mean across all recorded samples?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 46.67s | Cost: $0.06738

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of vibrations with varying intensity and duration. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride condition. The accelerometer readings indicate a mix of low and high-intensity vibrations, with some periods of higher intensity lasting around 1-2 minutes. The variance of the accelerometer readings is generally low, around 0.1-0.2, indicating a relatively smooth ride. However, there are some instances of higher variance, up to 0.5, which may indicate rougher ride conditions. The data does not show any significant anomalies or abrupt transitions, suggesting a consistent and predictable vibration pattern.
```

---

### Q3: At what exact timestamp was the highest accel_stats_z_p99 recorded?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 15.27s | Cost: $0.06736

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of low-intensity vibrations with a mean acceleration of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The ride is characterized by a low level of roughness, with a variance of 0.2100, indicating a smooth ride. The data also shows a consistent pattern of vibrations, with a mean acceleration of 9.2690 and a variance of 0.2100, indicating a consistent level of vibration. There are no significant anomalies or abrupt transitions observed during this period.
```

---

### Q4: How many data samples show an accel_variance strictly greater than 0.20?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 36.89s | Cost: $0.06738

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The acceleration values range from -0.7660 to 2.1450, with 1.0730 being the most frequent value. The dataset also includes percentile summaries, such as the 1st, 10th, 90th, and 99th percentiles, which provide additional insights into the distribution of acceleration values. The ride is generally smooth, with some minor fluctuations in acceleration. There are no significant anomalies or abrupt transitions in the data.
```

---

### Q5: Is the northern half of the route (latitude above median) rougher than the southern half, based on average acceleration variance?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 18.01s | Cost: $0.06733

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of vibrations with varying intensity and duration. The data is collected over a 30-minute period with a 6-hour history, indicating a relatively stable ride with some fluctuations. The accelerometer readings reveal a mix of low and high-intensity vibrations, with the highest intensity observed around 9.1940. The data also shows a gradual increase in vibration intensity over time, with the most intense period occurring between 16:30 and 16:35. There are no significant anomalies or abrupt transitions observed during this period.
```

---

### Q6: Which location (latitude, longitude) recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 21.80s | Cost: $0.06759

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The ride is characterized by low-intensity vibrations, with the highest intensity observed at 0.7660. The dataset contains 2025-06-06 11:08:35 to 2025-06-06 11:21:08, with a total of 2025-06-06 11:08:35 to 2025-06-06 11:21:08 being the most intense period. The ride remains relatively smooth throughout, with no significant changes in intensity or pattern. 

There are no significant anomalies or abrupt transitions observed during this period. The data indicates a consistent and stable ride, with the bus maintaining a relatively constant speed and direction. The acceleration patterns are consistent, with no sudden changes or spikes in acceleration. The variance in acceleration is relatively low, indicating a smooth ride with minimal turbulence. 

The dataset does not show any significant deviations from the expected ride patterns, and the overall ride quality can be characterized as smooth and stable. The bus telemetry vibration dataset provides a comprehensive view of the ride, allowing for the identification of patterns and trends in the data. The data can be used to inform decisions about the ride quality and to identify areas for improvement.
```

---

### Q7: Calculate the average overall magnitude of peak acceleration using the 99th percentiles of the X, Y, and Z axes.

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 43.90s | Cost: $0.06737

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of low-intensity vibrations with a mean acceleration of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The accelerometer readings are highly correlated, with a strong positive relationship between the x, y, and z axes. The data also shows a slight increase in intensity over time, but this increase is gradual and does not indicate any abrupt transitions or anomalies. The overall trend suggests a smooth and consistent ride with minimal roughness, but with some minor fluctuations in acceleration.
```

---

### Q8: If we group the data into 1-minute intervals, which time window experienced the most sustained turbulence?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 10.30s | Cost: $0.06741

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The ride is characterized by low-intensity vibrations, with the highest intensity observed at 9.9600. The dataset contains 2025-06-06 11:08:35 to 2025-06-06 11:21:08, with a total of 2025-06-06 11:21:08 to 2025-06-06 11:21:08 being the most intense period. The ride remains relatively smooth throughout, with no significant changes in intensity or pattern.
```

---

### Q9: How does passenger occupancy correlate with road roughness during this trip?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 45.46s | Cost: $0.06738

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of low-intensity vibrations with a mean acceleration of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The ride is characterized by a low level of roughness, with the highest intensity of vibrations observed at 0.7660, 1.0730, and 1.9920. The data also shows a gradual increase in intensity over time, with the highest intensity observed at the end of the 30-minute window. There are no significant anomalies or abrupt transitions observed during this period.
```

---

### Q10: Did rainy weather cause the roughest segments in this route?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 8.71s | Cost: $0.06731

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of vibrations with varying intensity and duration, indicating a relatively smooth ride. The accelerometer readings reveal a mix of low and high-frequency vibrations, with the most prominent frequency range between 1-3 Hz. The variance of the accelerometer readings is generally low, indicating minimal roughness. However, there are occasional spikes in variance, suggesting brief periods of increased vibration. The overall trend is one of consistent, low-intensity vibrations, with some minor fluctuations in intensity and duration.
```

---

### Q11: Which bus driver generated the smoothest driving profile?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 49.22s | Cost: $0.06732

**Agent Trace**

```
[Group all - chunk 1] The data shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100, indicating a relatively smooth ride. The ride lasts for approximately 30 minutes, with a total distance traveled of around 1.5 kilometers. The acceleration variance remains relatively low throughout, with a few instances of higher variance, but these are not significant enough to impact the overall ride quality. The data does not reveal any significant anomalies or abrupt transitions, suggesting a consistent and uneventful ride.
```

---

### Q12: Predict next week's pothole repairs for the road segments in this dataset.

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 6.32s | Cost: $0.06732

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of vibrations with varying intensity and duration. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride segment. The accelerometer readings indicate a mix of low and high-intensity vibrations, with some segments experiencing higher variance, suggesting rougher ride conditions. The dataset does not show any significant anomalies or abrupt transitions, indicating a generally smooth ride. The overall trend suggests a steady, low-intensity vibration pattern, with occasional spikes in intensity.
```

---

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.