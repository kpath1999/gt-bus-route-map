# Flash-Fusion Benchmark Report

Generated: 2026-05-26T14:47:30+00:00

## Summary Table

| Baseline     |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:-------------|-------------------:|------------------:|-----------------:|
| FLASH_FUSION |             0.7778 |           15.3543 |           0.0005 |
| AUTOIOT_ONLY |             0.6111 |           10.7101 |           0.0002 |

## Per-Query Results

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **AutoIOT-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.