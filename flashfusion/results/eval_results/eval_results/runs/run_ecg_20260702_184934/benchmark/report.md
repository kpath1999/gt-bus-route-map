# Flash-Fusion Benchmark Report

Generated: 2026-07-02T22:54:29+00:00

## Summary Table

| Baseline       |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:---------------|-------------------:|------------------:|-----------------:|
| LLMSENSE_PAPER |             0.0000 |          220.0364 |           0.2026 |

## Per-Query Results

### Q1: What is the minimum MLII value recorded for record_id 101?

#### LLMSENSE_PAPER

**Answer:** The available data does not contain enough information to answer this query.

- Executed: False | Rejected: False | Judge: N/A
- Stages: S_summarize,R_reason
- Latency: 220.04s | Cost: $0.20264

**Agent Trace**

```
[Group 101 - chunk 1] The ECG signal shows a mix of normal and abnormal patterns, with periods of normal sinus rhythm and occasional premature ventricular contractions. The signal also exhibits periods of atrial fibrillation, characterized by rapid and irregular heartbeats. The duration of these patterns varies, with some lasting only a few seconds and others persisting for several minutes. The intensity of the signal remains relatively consistent, with some fluctuations in amplitude. Anomalies in the signal include premature ventricular contractions and episodes of atrial fibrillation, which can be triggered by various factors such as stress, exhaustion, or underlying medical conditions. Abrupt transitions in the signal occur when the heart switches from one rhythm to another, such as from normal sinus rhythm to atrial fibrillation. These transitions can be triggered by changes in the autonomic nervous system's control over the heart or by underlying cardiac conditions.

[Group 101 - chunk 2] The ECG signal exhibits a mix of normal and abnormal patterns, with a dominant frequency around 100 Hz and a secondary frequency around 50 Hz. The signal amplitude varies between -0.9 and 0.9 millivolts, with a general trend of increasing amplitude over time. The signal shows periods of high-frequency activity, followed by periods of low-frequency activity, with occasional spikes and dips in amplitude. The signal also exhibits periods of isoelectric inactivity, where the amplitude is near zero. The overall pattern is complex, with multiple frequency components and non-linear interactions. Abrupt transitions in the signal are observed, with sudden changes in frequency and amplitude. Anomalies in the signal include periods of high-frequency noise and occasional large spikes in amplitude. The signal also shows periods of low-frequency drift, where the frequency decreases over time.

[Group 101 - chunk 3] The ECG signal exhibits a mix of low-frequency and high-frequency components, with a dominant frequency around 0.5-1 Hz. The signal amplitude varies, with peaks reaching up to 1.5 mV and troughs as low as -0.5 mV. The signal duration is approximately 30 minutes, with a gradual increase in amplitude over the first 10 minutes, followed by a decrease. The signal intensity remains relatively consistent, with occasional spikes and dips. Anomalies include brief periods of high-frequency activity and abrupt transitions in signal amplitude. Abrupt transitions occur around 200.5, 200.9, 201.4, 201.9, 202.4, 202.9, 203.4, 203.9, 204.4, 204.9, 205.4, 205.9, 206.4, 206.9, 207.4, 207.9, 208.4, 208.9, 209.4, 209.9, 210.4, 210.9, 211.4, 211.9, 212.4, 212.9, 213.4, 213.9, 214.4, 214.9, 215.4, and 215.9 seconds, with signal amplitudes ranging from -0.1 to 1.6 mV.
```

---

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.