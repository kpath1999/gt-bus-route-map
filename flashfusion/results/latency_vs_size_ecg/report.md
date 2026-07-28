# Latency vs Dataset Size (Safe Execution Segment)

## Study Configuration

- Dataset: mit_ecg
- Data path: data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt
- Sizes: 2,000,000
- Repeats per size: 1
- Sample mode: head
- Timeout per trial: 120.0s

## Segment Definition

Measured latency is the safe execution segment between LLM code generation and LLM answer synthesis in ExecutionLayer: process spawn + transfer + worker df.copy() + pandas execution.

## Per-Workload Size Scaling

### Workload: heavy

- Mean total safe latency slope (seconds per 10x rows): nan
- Mean parent startup/transfer overhead slope (seconds per 10x rows): nan
- Mean worker copy slope (seconds per 10x rows): nan
- Mean worker pandas execution slope (seconds per 10x rows): nan

| Rows | Mean total (s) | Median total (s) | P95 total (s) | Mean copy (s) | Mean exec (s) | Mean startup/transfer overhead (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 2,000,000 | 0.7252 | 0.7252 | 0.7252 | 0.0280 | 0.0075 | 0.6897 |

### Workload: light

- Mean total safe latency slope (seconds per 10x rows): nan
- Mean parent startup/transfer overhead slope (seconds per 10x rows): nan
- Mean worker copy slope (seconds per 10x rows): nan
- Mean worker pandas execution slope (seconds per 10x rows): nan

| Rows | Mean total (s) | Median total (s) | P95 total (s) | Mean copy (s) | Mean exec (s) | Mean startup/transfer overhead (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 2,000,000 | 0.7479 | 0.7479 | 0.7479 | 0.0221 | 0.0016 | 0.7241 |

### Workload: report_like

- Mean total safe latency slope (seconds per 10x rows): nan
- Mean parent startup/transfer overhead slope (seconds per 10x rows): nan
- Mean worker copy slope (seconds per 10x rows): nan
- Mean worker pandas execution slope (seconds per 10x rows): nan

| Rows | Mean total (s) | Median total (s) | P95 total (s) | Mean copy (s) | Mean exec (s) | Mean startup/transfer overhead (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 2,000,000 | 0.7390 | 0.7390 | 0.7390 | 0.0255 | 0.0071 | 0.7063 |

## Interpretation Guidance

- If startup/transfer overhead slope dominates, row count mainly hurts process/data movement.
- If worker copy slope dominates, cost is mostly from defensive df.copy() in the worker.
- If worker exec slope dominates, pandas compute is the main size-sensitive component.
- If all slopes are near zero, latency is largely size-insensitive in this segment.
