# Flash-Fusion Benchmark Report

Generated: 2026-05-29T12:55:57+00:00

## Summary Table

| Baseline       |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:---------------|-------------------:|------------------:|-----------------:|
| FLASH_FUSION   |             0.8889 |            5.0277 |           0.0006 |
| AUTOIOT_PAPER  |             0.3889 |           96.1898 |           0.0159 |
| LLMSENSE_PAPER |             0.3333 |            2.2239 |           0.0005 |
| HARGPT_PAPER   |             0.1944 |            1.8885 |           0.0003 |

## Per-Query Results

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.