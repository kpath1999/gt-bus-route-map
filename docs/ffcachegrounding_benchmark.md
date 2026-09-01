# Plan: One-Shot Grounding Benchmark for FLASH_FUSION_CACHE

## Objective

Create a single benchmark workflow that quantifies how light-model size affects cache grounding reliability in FLASH_FUSION_CACHE.

Primary question:
- As light-model size changes, how often does cache grounding fail and trigger planner fallback?

Primary deliverables:
- New runner: flashfusion/eval/benchmark_grounding.py
- Raw run artifacts (per model, per run, per query)
- Aggregated metrics JSON/CSV
- Final visualization in results/primary_visualizations

## Hypothesis

- Baseline model ibm-granite/granite-4.1-8b is expected to be mid-spectrum.
- Smaller models should increase grounding loss.
- Larger models should reduce grounding loss or hold steady.

Historical anchor:
- Granite-8b previously observed grounding loss is about 7.3%.

## Scope

In scope:
- Only in-scope queries with light grounding behavior: direct + intermediate complexity.
- FLASH_FUSION_CACHE execution path.
- OpenRouter light-model swap while keeping main planner behavior fixed.
- N=3 repeated runs per model.

Out of scope:
- Predictive queries.
- Out-of-scope query rejection quality.
- End-to-end answer semantic scoring.

## Models Under Test

Run in increasing parameter size order:

1. meta-llama/llama-3.2-1b-instruct
2. meta-llama/llama-3.2-3b-instruct
3. qwen/qwen-2.5-7b-instruct
4. ibm-granite/granite-4.1-8b (reuse existing artifacts from flashfusion/results/ff_hybrid_cache/FLASH_FUSION_CACHE)
5. google/gemma-3-12b-it
6. qwen/qwen3-14b

## Grounding-Loss Definition

Use existing fallback marker emitted by the cache baseline:
- stage name: cache_miss_or_validation_failure
- source: flashfusion/baselines/flash_fusion_cache.py

For one model and one run:

Grounding loss (%) =
100 * (count of in-scope queries that contain cache_miss_or_validation_failure)
/ (total in-scope queries evaluated)

Interpretation:
- A hit of cache_miss_or_validation_failure means cache grounding did not safely complete and execution fell back to full Flash-Fusion planning.

## Query Set and Ground Truth Strategy

### Query selection

- Use query definitions from:
	- flashfusion/eval/queries.py
	- flashfusion/eval/queries_v2.py
	- flashfusion/eval/queries_v3.py
- Keep only complexity in {direct, intermediate}.
- Exclude out_of_scope and predictive.

Note:
- Existing docs may call intermediate "reasoning". In code, use intermediate.

### Ground truth unit

Important correction:
- The existing eval ground-truth JSON files are answer-level references, not operator-plan ground truth.

For grounding analysis, the structural ground truth artifact is:
- the validated typed plan (version + ordered steps) where each operator has the concrete parameters filled in.
- this is exactly the "TYPED PLAN (validated, ready for execution)" artifact shown in trace output.

Canonical extraction path:
- use flashfusion/eval/trace_query.py with --cache and capture the printed typed plan block.
- source implementation for cache grounding and fallback marker remains flashfusion/baselines/flash_fusion_cache.py.

How this benchmark uses ground truth:
- primary metric (grounding loss) still uses only cache_miss_or_validation_failure on in-scope queries.
- typed-plan ground truth is used for qualitative/debug auditing of failures (for example: step count mismatch, wrong parameter fill, wrong operator argument shape), not as the denominator metric itself.

Optional deeper analysis fields:
- expected_operator_contract_hash (if available) to track strict contract mismatch paths.
- typed_plan_signature (optional hash over validated steps) for failure clustering.

## Experimental Controls

Hold constant across model runs:
- dataset
- query IDs included
- query wording policy per run
- planner model (--model)
- cache registry and semantic registry paths
- environment and API provider

Vary only:
- light grounding model

## Run Design (N=3)

Use this repeat policy for better robustness:
- Run 1 uses v1 queries
- Run 2 uses v2 queries
- Run 3 uses v3 queries

This mirrors existing cache benchmark practice and introduces paraphrase variance while preserving intent equivalence by query ID.

## Implementation Blueprint

Create flashfusion/eval/benchmark_grounding.py with the following behavior.

### CLI contract

Required args:
- --dataset with choices from SUPPORTED_DATASETS

Optional args:
- --model (fixed primary planner model; default from config)
- --models as comma-separated stage12 candidate list (default to six-model list above)
- --runs default 3
- --query-versions default v1,v2,v3
- --cache-path optional override
- --semantic-cache-path optional override
- --data optional dataset path override
- --output-dir default flashfusion/results/ff_hybrid_cache/grounding_benchmark
- --reuse-existing-granite-results flag (default on)
- --existing-granite-root default flashfusion/results/ff_hybrid_cache/FLASH_FUSION_CACHE
- --save-traces flag (persist per-query payload)
- --max-rows optional pass-through for mit_ecg

### Execution pattern per model

For each model m in models:
1. For run_index in 1..N:
2. Pick query version by run_index mapping (v1/v2/v3).
3. Enumerate in-scope query IDs for that version.
4. For each query ID, execute cache baseline via trace path equivalent:
	 - Use BaselineRunner mode FLASH_FUSION_CACHE or direct cache runner parity with trace_query flow.
	 - Ensure result object captures:
		 - stages_run
		 - plan_source
		 - execution_path
		 - deterministic_fallback_reason
		 - stage_latency_s
		 - query_id, dataset, query_version, run_index, model
5. Mark failure_for_grounding_loss = true if stages_run contains cache_miss_or_validation_failure.
6. Aggregate run metrics.

### Per-query record schema

Persist one row per query execution with at least:
- dataset
- query_id
- query_version
- complexity
- run_index
- stage12_model
- planner_model
- execution_path
- plan_source
- stages_run
- failure_for_grounding_loss
- deterministic_fallback_reason
- cache_grounding_latency_s
- cache_lookup_latency_s
- cache_validation_latency_s
- typed_exec_latency_s
- total_latency_s

Optional (recommended) audit fields:
- cache_grounding_failure (if present on result payload)
- typed_plan_signature (hash/canonical string for validated steps)
- failure_stage_excerpt (first stage/reason that triggered cache_miss_or_validation_failure)

### Aggregate outputs

Write:
- grounding_benchmark_raw.jsonl
- grounding_benchmark_summary.json
- grounding_benchmark_summary.csv

In summary include:
- model_order
- per_model:
	- n_queries_total
	- n_failures
	- grounding_loss_mean_pct
	- grounding_loss_std_pct
	- grounding_loss_ci95_pct
	- per_run_loss_pct
	- failure_reason_breakdown
	- cache_plan_source_breakdown

Use:
- sample std for N=3
- 95% CI as 1.96 * std / sqrt(N)

## One-Shot Execution Commands

Single command after implementation:

python -m flashfusion.eval.benchmark_grounding \
	--dataset bus \
	--model ibm-granite/granite-4.1-8b \
	--models meta-llama/llama-3.2-1b-instruct,meta-llama/llama-3.2-3b-instruct,qwen/qwen-2.5-7b-instruct,ibm-granite/granite-4.1-8b,google/gemma-3-12b-it,qwen/qwen3-14b \
	--runs 3 \
	--query-versions v1,v2,v3 \
	--output-dir flashfusion/results/ff_hybrid_cache/grounding_benchmark \
	--reuse-existing-granite-results \
	--existing-granite-root flashfusion/results/ff_hybrid_cache/FLASH_FUSION_CACHE \
	--save-traces

If running all datasets, execute once per dataset and then merge summaries in a post-step table.

## Visualization Spec

Create bar chart at:
- results/primary_visualizations/grounding_loss_vs_model_size.png

Chart definition:
- x-axis: parameter-size notations in increasing order: 1b, 3b, 7b, 8b, 12b, 14b
- y-axis: grounding loss (%)
- bars: mean over N=3
- error bars: std or CI95 (pick one and label clearly)
- legend or annotation: map each size notation to full model id

Also export plotting table:
- results/primary_visualizations/grounding_loss_vs_model_size.csv

with columns:
- model
- params_b
- grounding_loss_mean_pct
- grounding_loss_std_pct
- grounding_loss_ci95_pct

## Acceptance Criteria

Benchmark is complete only if all are true:

1. Script runs end-to-end with one command and writes raw + summary artifacts.
2. In-scope filtering is explicit and reproducible.
3. Grounding loss is computed strictly from cache_miss_or_validation_failure.
4. N=3 variance is reported per model.
5. Model order is parameter-size ordered in output and plot.
6. Granite appears in the middle band and is directly comparable to smaller/larger choices.
7. No data path uses chat/data; only canonical data roots are used.

## File Touch Plan

Mandatory:
- flashfusion/eval/benchmark_grounding.py
- docs/ffcachegrounding_benchmark.md (this plan)

Likely optional helper:
- flashfusion/eval/visualize_grounding_benchmark.py

Output locations:
- flashfusion/results/ff_hybrid_cache/grounding_benchmark/
- results/primary_visualizations/

## Risks and Mitigations

Risk:
- Provider throttling introduces noisy latency and sporadic failures.
Mitigation:
- Keep latency secondary; grounding loss is primary metric. Preserve failure reasons per query.

Risk:
- Query-version differences change difficulty.
Mitigation:
- Fixed v1/v2/v3 mapping across all models, same query IDs by run.

Risk:
- Semantic cache registry mismatch across datasets.
Mitigation:
- Use dataset-specific default semantic registry paths from existing eval config.

## Next Immediate Build Order

1. Implement benchmark_grounding.py runner and summary writer.
2. Dry-run on one dataset and one model.
3. Run full six-model N=3 experiment.
4. Generate visualization and add one short interpretation note to docs.