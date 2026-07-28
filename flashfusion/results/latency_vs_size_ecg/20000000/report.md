# Latency vs Dataset Size (Safe Execution Segment)

## Study Configuration

- Dataset: mit_ecg
- Data path: data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt
- Sizes: 20,000,000
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
| 20,000,000 | 2.6465 | 2.6465 | 2.6465 | 0.2718 | 0.0198 | 2.3549 |

### Workload: light

- Mean total safe latency slope (seconds per 10x rows): nan
- Mean parent startup/transfer overhead slope (seconds per 10x rows): nan
- Mean worker copy slope (seconds per 10x rows): nan
- Mean worker pandas execution slope (seconds per 10x rows): nan

| Rows | Mean total (s) | Median total (s) | P95 total (s) | Mean copy (s) | Mean exec (s) | Mean startup/transfer overhead (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 20,000,000 | 2.5566 | 2.5566 | 2.5566 | 0.2664 | 0.0156 | 2.2746 |

### Workload: report_like

- Mean total safe latency slope (seconds per 10x rows): nan
- Mean parent startup/transfer overhead slope (seconds per 10x rows): nan
- Mean worker copy slope (seconds per 10x rows): nan
- Mean worker pandas execution slope (seconds per 10x rows): nan

| Rows | Mean total (s) | Median total (s) | P95 total (s) | Mean copy (s) | Mean exec (s) | Mean startup/transfer overhead (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 20,000,000 | 2.6272 | 2.6272 | 2.6272 | 0.3986 | 0.0342 | 2.1944 |

## Interpretation Guidance

- If startup/transfer overhead slope dominates, row count mainly hurts process/data movement.
- If worker copy slope dominates, cost is mostly from defensive df.copy() in the worker.
- If worker exec slope dominates, pandas compute is the main size-sensitive component.
- If all slopes are near zero, latency is largely size-insensitive in this segment.
