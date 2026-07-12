# Flash-Fusion Benchmark Report

Generated: 2026-05-29T03:17:37+00:00

## Summary Table

| Baseline       |   Avg LLM Accuracy |   Avg Latency (s) |   Avg Cost (USD) |
|:---------------|-------------------:|------------------:|-----------------:|
| FLASH_FUSION   |             1.0000 |            4.9082 |           0.0006 |
| AUTOIOT_PAPER  |             0.3333 |           79.2433 |           0.0243 |
| HARGPT_PAPER   |             0.3333 |            0.0901 |           0.0000 |
| LLMSENSE_PAPER |             0.3333 |           51.4634 |           0.0120 |

## Per-Query Results

## Baseline Comparison Notes

- **LLM-Only**: no schema grounding, no execution — may hallucinate.
- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge).
- **Agent-Only**: raw-query pandas execution only (no guardrail, no codebook grounding).
- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution.