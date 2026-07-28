# Latency vs Dataset Size (Safe Execution Segment)

## Study Configuration

- Dataset: mit_ecg
- Data path: data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt
- Sizes: 200,000
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
| 200,000 | 0.5583 | 0.5583 | 0.5583 | 0.0022 | 0.0008 | 0.5552 |

### Workload: light

- Mean total safe latency slope (seconds per 10x rows): nan
- Mean parent startup/transfer overhead slope (seconds per 10x rows): nan
- Mean worker copy slope (seconds per 10x rows): nan
- Mean worker pandas execution slope (seconds per 10x rows): nan

| Rows | Mean total (s) | Median total (s) | P95 total (s) | Mean copy (s) | Mean exec (s) | Mean startup/transfer overhead (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 200,000 | 0.5755 | 0.5755 | 0.5755 | 0.0024 | 0.0005 | 0.5726 |

### Workload: report_like

- Mean total safe latency slope (seconds per 10x rows): nan
- Mean parent startup/transfer overhead slope (seconds per 10x rows): nan
- Mean worker copy slope (seconds per 10x rows): nan
- Mean worker pandas execution slope (seconds per 10x rows): nan

| Rows | Mean total (s) | Median total (s) | P95 total (s) | Mean copy (s) | Mean exec (s) | Mean startup/transfer overhead (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 200,000 | 0.5607 | 0.5607 | 0.5607 | 0.0023 | 0.0008 | 0.5576 |

## Interpretation Guidance

- If startup/transfer overhead slope dominates, row count mainly hurts process/data movement.
- If worker copy slope dominates, cost is mostly from defensive df.copy() in the worker.
- If worker exec slope dominates, pandas compute is the main size-sensitive component.
- If all slopes are near zero, latency is largely size-insensitive in this segment.
