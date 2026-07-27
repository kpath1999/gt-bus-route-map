# Primary Visualization Scripts

This folder now has a clean, focused plotting flow for July26 baseline data.

## Data source

Scripts can read from one primary root and optionally override specific baselines
from alternate roots.

Default baseline layout:

- `flashfusion/results/<RUN_ROOT>/<BASELINE>/<DATASET>/july26_full/metrics.csv`

Supported baselines:

- `FLASH_FUSION`
- `AUTOIOT_PAPER`
- `REACT_ONLY`
- `HARGPT_PAPER`
- `LLMSENSE_PAPER`

Supported datasets:

- `bus`
- `wisdm`
- `mit_ecg` (normalized to `ecg` in plots)

## Scripts

- `measure.py`
  - Shared data loading and aggregation helpers.
  - No figure writing by default.

- `llamas.py`
  - Figure (i): `accuracy_vs_baselines_across_datasets.png`
  - Figure (ii): `accuracy_vs_baselines_across_query_types.png`
  - Also writes CSV summaries for both.

- `latencystages.py`
  - Figure (iii): `per_stage_latency_breakdown_across_query_types_n3.png`
  - Uses Flash-Fusion stage columns and explicitly reflects N=3 runs.
  - Also writes a CSV summary.

- `miniexp/latencystages.py`
  - Compatibility wrapper that forwards to `latencystages.py`.

## Output location

Primary figures and summaries are written to:

- `flashfusion/viz/results/primary_visualizations`

## Commands

From repository root:

```bash
python3 flashfusion/viz/llamas.py
python3 flashfusion/viz/latencystages.py
```

For the requested three-baseline comparison with mixed roots:

- Flash-Fusion and ReAct-Only from `flashfusion/results/ff_newlook_with_react`
- AutoIOT from `flashfusion/results/with_slm_predictive`
- Query types: Direct, Reasoning, Out-of-Scope, Predictive

```bash
python3 flashfusion/viz/llamas.py \
  --results-root flashfusion/results/ff_newlook_with_react \
  --autoiot-root flashfusion/results/with_slm_predictive \
  --baseline-set FLASH_FUSION,REACT_ONLY,AUTOIOT_PAPER \
  --query-types Direct,Reasoning,Out-of-Scope,Predictive \
  --output-dir flashfusion/viz/results/primary_visualizations

python3 flashfusion/viz/latencystages.py \
  --results-root flashfusion/results/ff_newlook_with_react \
  --autoiot-root flashfusion/results/with_slm_predictive \
  --baseline-set FLASH_FUSION,REACT_ONLY,AUTOIOT_PAPER \
  --query-types Direct,Reasoning,Out-of-Scope,Predictive \
  --output-dir flashfusion/viz/results/primary_visualizations
```
