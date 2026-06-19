# LLM-Only vs Flash-Fusion — Scaling Comparison

Self-contained empirical study that characterizes the **limits of brute-force LLM
prompting** on raw sensor data, and contrasts it with the staged Flash-Fusion
pipeline. The headline question: *what happens when the dataset does not fit in
the context window?*

Three datasets, treated as a size axis:

| Dataset | Rows | Bytes | Token est. (full) | Fits 128k context? |
|---------|-----:|------:|------------------:|:------------------:|
| bus     | 1,219 | 189 KB | ~40k | yes |
| WISDM IMU | 1,098,210 | 48 MB | ~12M | no (~90× over) |
| MIT ECG | 20,800,000 | 841 MB | ~220M | no (~1700× over) |

For each dataset we run two baselines:

- **LLM_ONLY** — serialize the dataset to CSV, prepend a question, send in one
  API call. If the serialized prompt exceeds the model's context window, we
  char-truncate to fit and record `truncation_pct`. This is the naive
  brute-force baseline.
- **FLASH_FUSION** — the published staged pipeline (concept extraction →
  schema grounding → sub-query generation → grounded execution → judge). For
  WISDM the numbers come from `flashfusion/eval_results/runs/run_20260520_173222/`;
  for bus and ECG, FF is invoked fresh via the standard `BaselineRunner`.

For each (baseline, dataset, question) cell we run **3 reps**.

## What gets measured (per trial)

| Metric | Source |
|---|---|
| `prompt_tokens` / `completion_tokens` | real Groq-billed counts from `response.usage_metadata` |
| `latency_s` | wall-clock around the single API call |
| `cost_usd` | `flashfusion.config.MODEL_RATE_PER_1M_TOKENS` × real token counts |
| `truncation_pct` | (full_tokens − sent_tokens) / full_tokens |
| `accuracy_score` | post-hoc `judge_rows_with_llm` against the existing ground-truth JSONs (bus has no GT → null) |
| `api_error` | captured verbatim if the call fails |

## How to run

```bash
# Phase 1: dry-run — 1 dataset × 1 question × 1 rep end-to-end
bash flashfusion/llm_only/run_study.sh --phase=1

# Phase 2: full sweep (gated on user OK after reviewing Phase 1 output)
bash flashfusion/llm_only/run_study.sh --phase=2

# Post-run analysis (writes results/summary.csv and figures/)
python -m flashfusion.llm_only.analyze
python -m flashfusion.llm_only.plots
```

Requires `OPENROUTER_API_KEY` in the environment (`GROQ_API_KEY` accepted during transition). Hard cost cap: `$5.00`.

## Outputs

| File | Contents |
|---|---|
| `results/trials.csv` | one row per trial — both baselines, all datasets |
| `results/summary.csv` | aggregated mean ± std per (baseline, dataset) |
| `results/figures/fig_headline.png` | **money figure**: 4 panels × 3 datasets, LLM_ONLY vs FLASH_FUSION |
| `results/figures/fig_truncation.png` | truncation_pct by dataset size |
| `results/figures/fig_scaling.png` | log-x scaling overlay |
| `results/raw_responses/*.json` | per-trial archive (prompt, response, tokens) |
| `writeup.md` | 1–2 page draft for the paper's §2.2 figure |

## Framing

The point of this study is not "LLMs are bad" — it is an *empirical
characterization* of where naive raw-data prompting breaks down. The numbers
motivate why Flash-Fusion's staged retrieval architecture is necessary at scale
rather than a stylistic choice.
