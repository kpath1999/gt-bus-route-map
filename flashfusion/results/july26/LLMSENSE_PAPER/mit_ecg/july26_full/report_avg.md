# Flash-Fusion Benchmark Report

Generated: 2026-07-03T14:57:58+00:00

## Summary Table

| Baseline       |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:---------------|-------------------:|------------------:|-----------------:|
| LLMSENSE_PAPER |             0.3333 |          170.7381 |           0.2048 |

## Per-Query Results

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.