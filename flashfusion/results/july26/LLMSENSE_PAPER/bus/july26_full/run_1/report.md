# Flash-Fusion Benchmark Report

Generated: 2026-07-03T15:07:48+00:00

## Summary Table

| Baseline       |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:---------------|-------------------:|------------------:|-----------------:|
| LLMSENSE_PAPER |             0.3333 |           44.7068 |           0.0674 |

## Per-Query Results

### Q1: What is the maximum accel_variance observed in this dataset?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 57.62s | Cost: $0.06734

**Agent Trace**

```
[Group all - chunk 1] The bus experienced a relatively stable ride with minimal vibrations, lasting approximately 30 minutes. The ride was characterized by low acceleration variance, indicating a smooth journey. The bus traveled at a consistent speed, with occasional minor deviations. The ride was generally free of significant bumps or jolts, with the exception of a few minor jolts around 16:22:00. The bus's trajectory remained relatively consistent, with minor changes in direction. The ride ended with a slight increase in acceleration variance, but overall, the journey was uneventful.
```

---

### Q2: What is the average accel_mean across all recorded samples?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 43.51s | Cost: $0.06731

**Agent Trace**

```
[Group all - chunk 1] The bus experienced a relatively stable ride with minimal vibrations, lasting approximately 30 minutes. The ride was characterized by low acceleration variance, indicating a smooth journey. The bus traveled at a consistent speed, with occasional minor deviations. The ride was generally free of abrupt transitions or anomalies, with the exception of a few instances of increased acceleration variance. Overall, the ride was uneventful, with no significant events or notable changes in the bus's movement.
```

---

### Q3: At what exact timestamp was the highest accel_stats_z_p99 recorded?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 45.38s | Cost: $0.06737

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The acceleration values range from -0.7660 to 2.1450, with 1.0730 being the most frequent value. The dataset also includes percentile summaries, such as the 1st, 10th, 90th, and 99th percentiles, which provide additional insight into the distribution of acceleration values. The data does not show any significant anomalies or abrupt transitions, suggesting a smooth and consistent ride.
```

---

### Q4: How many data samples show an accel_variance strictly greater than 0.20?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 51.04s | Cost: $0.06731

**Agent Trace**

```
[Group all - chunk 1] The data shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100, indicating a relatively smooth ride. The ride is mostly stable with minor fluctuations, with a few instances of increased acceleration variance. The overall duration of the ride is approximately 30 minutes, with a few brief periods of increased intensity. There are no significant anomalies or abrupt transitions observed during this period. The ride remains relatively consistent throughout, with no major changes in acceleration or variance.
```

---

### Q5: Is the northern half of the route (latitude above median) rougher than the southern half, based on average acceleration variance?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 48.31s | Cost: $0.06739

**Agent Trace**

```
[Group all - chunk 1] The bus experienced a mix of smooth and rough ride segments, with varying durations and intensities. The ride was generally smooth, with some segments showing higher acceleration and deceleration. There were periods of low acceleration and deceleration, as well as periods of high acceleration and deceleration. The ride was mostly consistent, with some minor variations in acceleration and deceleration. The bus traveled at a relatively constant speed, with some minor fluctuations. The ride was not extremely rough, but there were some segments with higher acceleration and deceleration. The bus maintained a consistent speed, with minor variations in acceleration and deceleration. The ride was generally consistent, with some minor variations in acceleration and deceleration.
```

---

### Q6: Which location (latitude, longitude) recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 58.44s | Cost: $0.06737

**Agent Trace**

```
[Group all - chunk 1] The bus experienced a relatively stable ride with minimal variance in acceleration, with a mean acceleration of 9.2690 and a standard deviation of 0.2100. The ride was characterized by a low level of vibration, with a variance of 0.1030. The bus traveled at a relatively constant speed, with a mean speed of 9.2690 and a standard deviation of 0.2100. The ride was smooth, with minimal jolts or bumps, and the bus maintained a consistent course. The bus's acceleration and speed remained relatively constant throughout the period, with no significant changes or anomalies observed.
```

---

### Q7: Calculate the average overall magnitude of peak acceleration using the 99th percentiles of the X, Y, and Z axes.

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 24.04s | Cost: $0.06738

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The acceleration values range from -0.7660 to 2.1450, with 1.0730 being the most frequent value. The dataset also includes percentile summaries, such as the 1st, 10th, 90th, and 99th percentiles, which provide additional insights into the distribution of acceleration values. The data does not show any significant anomalies or abrupt transitions, suggesting a smooth ride with minimal vibrations.
```

---

### Q8: If we group the data into 1-minute intervals, which time window experienced the most sustained turbulence?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 48.49s | Cost: $0.06738

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of low-intensity vibrations with a mean acceleration of 9.2690 and a variance of 0.2100. The data spans a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The ride is characterized by a low level of roughness, with the highest intensity of vibrations observed at 0.7660, 1.0730, and 1.9920. The dataset does not exhibit any significant anomalies or abrupt transitions, suggesting a smooth and uneventful ride. The bus traveled at a relatively constant speed, with minor variations in acceleration and vibration intensity. The overall ride quality can be described as smooth, with minimal turbulence or roughness.
```

---

### Q9: How does passenger occupancy correlate with road roughness during this trip?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 43.82s | Cost: $0.06735

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of acceleration with a mean of 9.2690 and variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The ride is characterized by low-intensity vibrations, with the highest intensity observed at 0.7660. The dataset does not exhibit any significant anomalies or abrupt transitions, suggesting a smooth and consistent ride. The acceleration patterns are consistent throughout the dataset, with no notable changes or spikes in intensity. The overall trend of the data indicates a steady and stable ride, with minimal fluctuations in acceleration.
```

---

### Q10: Did rainy weather cause the roughest segments in this route?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 14.99s | Cost: $0.06743

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of acceleration with a mean of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The acceleration values range from -0.7660 to 2.1450, with percentile summaries at 1%, 10%, 90%, and 99%. The dataset has a total of 2025-06-06 11:08:35 to 2025-06-06 11:21:08, with 2025-06-06 11:08:35 being the start time and 2025-06-06 11:21:08 being the end time. The ride is generally smooth, with some minor fluctuations in acceleration. There are no significant anomalies or abrupt transitions observed during this period.
```

---

### Q11: Which bus driver generated the smoothest driving profile?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 12.00s | Cost: $0.06739

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a consistent pattern of vibrations with a mean acceleration of 9.2690 and a variance of 0.2100. The data is collected over a 30-minute window with a history of 6 hours, indicating a relatively stable ride. The ride is characterized by low-intensity vibrations, with the highest intensity observed at 9.9600. The dataset does not show any significant anomalies or abrupt transitions, suggesting a smooth and consistent ride. The bus's location is tracked over time, with latitude and longitude values ranging from 33.7731 to 33.7789 and -84.3887 to -84.4027, respectively. The data is collected at 3-second intervals, providing a detailed picture of the bus's movement and vibration patterns.
```

---

### Q12: Predict next week's pothole repairs for the road segments in this dataset.

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 88.85s | Cost: $0.06796

**Agent Trace**

```
[Group all - chunk 1] The bus telemetry vibration dataset shows a mix of low and high-intensity vibrations, with a dominant frequency of around 1-2 Hz. The data is collected over a 30-minute window, with a history of 6 hours. The accelerometer readings indicate a range of acceleration values, with a mean of 9.2690 and a variance of 0.2100. The data also shows a range of percentile values, including the 1st, 10th, 90th, and 99th percentiles. The overall pattern of the data suggests a relatively stable and consistent vibration pattern, with some minor fluctuations. However, there are some instances of higher-intensity vibrations, particularly between 16:04:06 and 16:04:09, which may indicate a rougher ride segment. The data also shows a gradual increase in intensity over time, with a peak intensity observed around 16:01:13. 

The data does not show any significant anomalies or abrupt transitions, except for a brief period of higher-intensity vibrations between 16:04:06 and 16:04:09. The rest of the data is relatively consistent, with minor fluctuations in acceleration and vibration patterns. The overall trend of the data suggests a stable and consistent vibration pattern, with some minor variations over time. 

The dataset covers a period of 30 minutes, with a total of 2025-06-06 11:08:35 to 2025-06-06 11:08:38, and 2025-06-06 11:08:41 to 2025-06-06 11:08:44, and other similar time intervals. The data is collected at a frequency of 1-2 Hz, with a mean acceleration of 9.2690 and a variance of 0.2100. The percentile values of the data range from -0.7660 to 2.1450, with the 1st percentile at -0.7660, the 10th percentile at 0.0000, the 90th percentile at 1.2260, and the 99th percentile at 2.1450. 

The dataset also includes other time intervals, such as 2025-06-06 11:08:47 to 2025-06-06 11:08:50, and 2025-06-06 11:08:53 to 2025-06-06 11:08:56, with similar patterns and trends. The data is relatively consistent, with minor fluctuations in acceleration and vibration patterns, and does not show any significant anomalies or abrupt transitions, except for the brief period of higher-intensity vibrations between 16:04:06 and 16:04:09.
```

---

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.