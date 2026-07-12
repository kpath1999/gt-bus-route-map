# Flash-Fusion Benchmark Report

Generated: 2026-05-29T11:47:02+00:00

## Summary Table

| Baseline       |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:---------------|-------------------:|------------------:|-----------------:|
| AUTOIOT_PAPER  |             0.3333 |          240.1926 |           0.0152 |
| FLASH_FUSION   |             0.3333 |           25.1179 |           0.0005 |
| LLMSENSE_PAPER |             0.3333 |          562.5772 |           0.1389 |
| HARGPT_PAPER   |             0.1389 |            1.4279 |           0.0002 |

## Per-Query Results

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.