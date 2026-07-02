"""Mini-experiment: semantic stage latency across baselines.

Creates per-query and aggregated latency telemetry grouped into four semantically
aligned stages so different baselines can be compared consistently:
	(i) Grounding
	(ii) Validation
	(iii) Planning
	(iv) Execution

For Flash-Fusion these map to S1+S2, guardrail, S3, and agent.
For AutoIOT, ReAct-only, and HARGPT, the mapping is baseline-specific.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# Ensure flashfusion is importable when run from subdirectories.
_script_dir = Path(__file__).resolve().parent
_repo_root = _script_dir.parents[1]
if str(_repo_root) not in sys.path:
	sys.path.insert(0, str(_repo_root))

from flashfusion.config import DEFAULT_MODEL, MODEL_RATE_PER_1M_TOKENS
from flashfusion.eval.queries import SUPPORTED_DATASETS, get_queries
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import BaselineRunner, LLMClient, RunResult

DEFAULT_DATA_PATHS: dict[str, str] = {
	"wisdm": "data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt",
	"mit_ecg": "data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt",
	"bus": "data/bus/bus_data.csv",
}

DEFAULT_OUTPUT_DIR = "flashfusion/miniexp/results/latencystages"
BASELINE_MODES: tuple[str, ...] = (
	"FLASH_FUSION",
	"AUTOIOT_PAPER",
	"REACT_ONLY",
	"HARGPT_PAPER",
)
COMPARE_BASELINE_MODES: tuple[str, ...] = ("FLASH_FUSION", "AUTOIOT_PAPER", "REACT_ONLY")
BASELINE_LABELS: dict[str, str] = {
	"FLASH_FUSION": "Flash-Fusion",
	"AUTOIOT_PAPER": "AutoIOT",
	"REACT_ONLY": "ReAct-only",
	"HARGPT_PAPER": "HARGPT",
}
# Common semantic stages used for cross-baseline comparison.
STAGE_KEYS: tuple[str, ...] = ("grounding", "validation", "planning", "execution")
FF_STAGE_KEYS: tuple[str, ...] = ("s1", "s2", "guardrail", "s3", "agent")
QUERY_TYPE_ORDER: tuple[str, ...] = ("direct", "reasoning", "oos")
QUERY_TYPE_LABELS: dict[str, str] = {
	"direct": "Direct",
	"reasoning": "Reasoning",
	"oos": "OOS",
}
STAGE_PLOT_LABELS: dict[str, str] = {
	"grounding": "Grounding",
	"validation": "Validation",
	"planning": "Planning",
	"execution": "Execution",
}
STAGE_COLORS: dict[str, str] = {
	"grounding": "#2c8c4a",
	"validation": "#d62728",
	"planning": "#2f6ad9",
	"execution": "#f28e2b",
}
FF_STAGE_PLOT_LABELS: dict[str, str] = {
	"s1": "S1",
	"s2": "S2",
	"guardrail": "Guardrail",
	"s3": "S3",
	"agent": "Agent",
}
FF_STAGE_COLORS: dict[str, str] = {
	"s1": "#2c8c4a",
	"s2": "#2f6ad9",
	"guardrail": "#d62728",
	"s3": "#f28e2b",
	"agent": "#9467bd",
}


def _is_forbidden_chat_data_path(path: str) -> bool:
	normalized = os.path.normpath(path).replace("\\", "/")
	if normalized == "chat/data" or normalized.startswith("chat/data/"):
		return True
	return "/chat/data/" in normalized


def _resolve_dataset_file(dataset: str, data_path: str | None) -> Path:
	raw = data_path or DEFAULT_DATA_PATHS[dataset]
	if _is_forbidden_chat_data_path(raw):
		raise ValueError(f"Path {raw!r} is under chat/data, which is not allowed.")

	p = Path(raw)
	if not p.is_absolute():
		p = (_repo_root / p).resolve()

	if p.is_dir():
		suffix = {
			"wisdm": "WISDM_ar_v1.1_raw.txt",
			"mit_ecg": "MIT_arrythmia_v1.txt",
			"bus": "bus_data.csv",
		}[dataset]
		p = p / suffix

	if not p.exists():
		raise FileNotFoundError(f"Dataset path not found for {dataset}: {p}")

	normalized = p.as_posix()
	if "/data/" not in normalized and not normalized.startswith("data/"):
		raise ValueError(f"Dataset path must be under data/: {p}")
	return p


def _normalize_query_type(complexity: str) -> str:
	raw = (complexity or "").strip().lower()
	if raw == "direct":
		return "direct"
	if raw == "intermediate":
		return "reasoning"
	if raw == "out_of_scope":
		return "oos"
	return "reasoning"


def _validate_model(model: str) -> None:
	if model not in MODEL_RATE_PER_1M_TOKENS:
		raise ValueError(
			f"Unknown model {model!r}. Add rates in flashfusion/config.py or pass a supported model ID."
		)


def _sum_call_stage_latency(call_log: list[Any], *, exact: set[str] | None = None, prefix: tuple[str, ...] = ()) -> float:
	total = 0.0
	exact = exact or set()
	for call in call_log:
		stage = str(getattr(call, "stage", "") or "")
		lat_s = float(getattr(call, "latency_s", 0.0) or 0.0)
		if stage in exact or any(stage.startswith(p) for p in prefix):
			total += lat_s
	return total


def map_semantic_stage_latency_s(result: RunResult, call_log: list[Any]) -> dict[str, float]:
	"""Map a baseline run to the four semantic latency stages.

	Returned keys are exactly: grounding, validation, planning, execution.
	"""
	total_latency_s = float(result.latency_s or 0.0)
	src = result.stage_latency_s if isinstance(result.stage_latency_s, dict) else {}
	baseline = str(result.baseline or "").upper()
	out: dict[str, float] = {k: 0.0 for k in STAGE_KEYS}

	# If semantic telemetry is already present, trust it.
	if any(k in src for k in STAGE_KEYS):
		return {k: float(src.get(k, 0.0) or 0.0) for k in STAGE_KEYS}

	if baseline == "FLASH_FUSION":
		out["grounding"] = float(src.get("s1", 0.0) or 0.0) + float(src.get("s2", 0.0) or 0.0)
		out["validation"] = float(src.get("guardrail", 0.0) or 0.0)
		out["planning"] = float(src.get("s3", 0.0) or 0.0)
		out["execution"] = float(src.get("agent", 0.0) or 0.0)
		if sum(out.values()) <= 0.0 and total_latency_s > 0.0:
			out["execution"] = total_latency_s
		return out

	if baseline == "AUTOIOT_PAPER":
		grounding_llm_s = _sum_call_stage_latency(
			call_log,
			exact={"autoiot_terms", "autoiot_search_queries"},
		)
		planning_llm_s = _sum_call_stage_latency(
			call_log,
			exact={"autoiot_design_high", "autoiot_design_detail", "autoiot_code_integration"},
			prefix=("autoiot_module_gen_",),
		)

		# Retrieval includes external Tavily requests and execution includes agent rounds;
		# these are not fully represented in call_log, so assign residual wall time.
		known_s = grounding_llm_s + planning_llm_s
		execution_s = max(0.0, total_latency_s - known_s)

		out["grounding"] = grounding_llm_s
		out["validation"] = 0.0
		out["planning"] = planning_llm_s
		out["execution"] = execution_s
		return out

	if baseline == "REACT_ONLY":
		out["execution"] = total_latency_s
		return out

	if baseline == "HARGPT_PAPER":
		out["execution"] = total_latency_s
		return out

	# Fallback for unknown baselines.
	out["execution"] = total_latency_s
	return out


def _flash_fusion_native_stage_latency_s(result: RunResult) -> dict[str, float]:
	src = result.stage_latency_s if isinstance(result.stage_latency_s, dict) else {}
	return {k: float(src.get(k, 0.0) or 0.0) for k in FF_STAGE_KEYS}


def _run_single_query(
	query_text: str,
	df_full: pd.DataFrame,
	model: str,
	api_key: str,
	baseline_mode: str,
) -> tuple[RunResult, list[Any]]:
	client = LLMClient(model_name=model, api_key=api_key)
	runner = BaselineRunner(mode=baseline_mode, df=df_full, client=client)
	try:
		result = runner.run(query_text)
		return result, list(client.call_log)
	except Exception as exc:
		r = RunResult(
			baseline=baseline_mode,
			model=model,
			query=query_text,
			answer=f"[ERROR] {type(exc).__name__}: {exc}",
			executed=False,
			rejected=False,
		)
		r.input_tokens = client.total_input_tokens()
		r.output_tokens = client.total_output_tokens()
		r.cost_usd = client.total_cost_usd()
		r.stage_latency_s = {k: 0.0 for k in STAGE_KEYS}
		return r, list(client.call_log)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
	with path.open("w", encoding="utf-8") as fh:
		for row in rows:
			fh.write(json.dumps(row, ensure_ascii=True) + "\n")


def _aggregate_query_rows(query_df: pd.DataFrame, stage_keys: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
	if query_df.empty:
		return pd.DataFrame(), pd.DataFrame()

	agg_fields: dict[str, tuple[str, str]] = {
		"avg_total_latency_s": ("latency_s", "mean"),
		"execution_rate": ("executed", "mean"),
		"rejection_rate": ("rejected", "mean"),
		"query_count": ("query_id", "count"),
	}
	for stage in stage_keys:
		agg_fields[f"{stage}_latency_s"] = (f"{stage}_latency_s", "mean")
		agg_fields[f"{stage}_latency_ms"] = (f"{stage}_latency_ms", "mean")

	per_dataset = (
		query_df.groupby(["dataset", "query_type"], as_index=False)
		.agg(**agg_fields)
		.sort_values(["dataset", "query_type"])
		.reset_index(drop=True)
	)

	summary = (
		per_dataset.groupby("query_type", as_index=False)
		.agg(**agg_fields)
		.sort_values("query_type")
		.reset_index(drop=True)
	)

	summary["query_type"] = pd.Categorical(summary["query_type"], categories=list(QUERY_TYPE_ORDER), ordered=True)
	summary = summary.sort_values("query_type").reset_index(drop=True)
	summary["query_type_label"] = summary["query_type"].map(QUERY_TYPE_LABELS)
	summary["stacked_stage_latency_s"] = summary[[f"{s}_latency_s" for s in stage_keys]].sum(axis=1)
	summary["stacked_stage_latency_ms"] = summary["stacked_stage_latency_s"] * 1000.0

	per_dataset["stacked_stage_latency_s"] = per_dataset[[f"{s}_latency_s" for s in stage_keys]].sum(axis=1)
	per_dataset["stacked_stage_latency_ms"] = per_dataset["stacked_stage_latency_s"] * 1000.0
	return per_dataset, summary


def plot_stacked_swimlanes(
	summary_df: pd.DataFrame,
	output_dir: Path,
	*,
	stage_keys: tuple[str, ...],
	stage_plot_labels: dict[str, str],
	stage_colors: dict[str, str],
	title: str,
	output_stem: str,
) -> None:
	if summary_df.empty:
		print("No summary rows to plot.", flush=True)
		return

	import matplotlib.pyplot as plt

	plt.rcParams.update(
		{
			"font.family": "DejaVu Sans",
			"figure.facecolor": "#ffffff",
			"axes.edgecolor": "#222222",
			"axes.linewidth": 1.5,
			"axes.titlesize": 17,
			"axes.titleweight": "bold",
			"axes.labelsize": 13,
			"xtick.labelsize": 11,
			"ytick.labelsize": 12,
			"legend.fontsize": 10,
		}
	)

	labels = [QUERY_TYPE_LABELS.get(str(q), str(q)) for q in summary_df["query_type"].tolist()]
	y_positions = list(range(len(labels)))
	left = [0.0 for _ in y_positions]

	fig, ax = plt.subplots(figsize=(11, 4.8))
	for stage in stage_keys:
		values = summary_df[f"{stage}_latency_s"].astype(float).tolist()
		ax.barh(
			y_positions,
			values,
			left=left,
			color=stage_colors[stage],
			edgecolor="white",
			linewidth=0.8,
			label=stage_plot_labels[stage],
			height=0.56,
		)
		left = [l + v for l, v in zip(left, values)]

	ax.set_yticks(y_positions)
	ax.set_yticklabels(labels)
	ax.set_xlabel("Latency (s)")
	ax.set_ylabel("Query Type")
	ax.set_title(title)
	ax.grid(axis="x", alpha=0.25)
	ax.set_axisbelow(True)
	ax.legend(title="Stage", loc="upper right")

	fig.tight_layout()
	output_dir.mkdir(parents=True, exist_ok=True)
	fig.savefig(output_dir / f"{output_stem}.png", dpi=180, bbox_inches="tight")
	fig.savefig(output_dir / f"{output_stem}.pdf", bbox_inches="tight")
	plt.close(fig)


def plot_semantic_baseline_comparison(summary_df: pd.DataFrame, output_dir: Path) -> None:
	if summary_df.empty:
		print("No semantic baseline summary rows to plot.", flush=True)
		return

	import matplotlib.pyplot as plt

	plt.rcParams.update(
		{
			"font.family": "DejaVu Sans",
			"figure.facecolor": "#ffffff",
			"axes.edgecolor": "#222222",
			"axes.linewidth": 1.5,
			"axes.titlesize": 17,
			"axes.titleweight": "bold",
			"axes.labelsize": 13,
			"xtick.labelsize": 11,
			"ytick.labelsize": 12,
			"legend.fontsize": 10,
		}
	)

	labels = [BASELINE_LABELS.get(str(b), str(b)) for b in summary_df["baseline"].tolist()]
	y_positions = list(range(len(labels)))
	left = [0.0 for _ in y_positions]

	fig, ax = plt.subplots(figsize=(11, 4.8))
	for stage in STAGE_KEYS:
		values = summary_df[f"{stage}_latency_s"].astype(float).tolist()
		ax.barh(
			y_positions,
			values,
			left=left,
			color=STAGE_COLORS[stage],
			edgecolor="white",
			linewidth=0.8,
			label=STAGE_PLOT_LABELS[stage],
			height=0.56,
		)
		left = [l + v for l, v in zip(left, values)]

	ax.set_yticks(y_positions)
	ax.set_yticklabels(labels)
	ax.set_xlabel("Average Latency (s)")
	ax.set_ylabel("Baseline")
	ax.set_title("Cross-Baseline Semantic Stage Latency (FF vs AutoIOT vs ReAct)")
	ax.grid(axis="x", alpha=0.25)
	ax.set_axisbelow(True)
	ax.legend(title="Semantic Stage", loc="upper right")

	fig.tight_layout()
	output_dir.mkdir(parents=True, exist_ok=True)
	fig.savefig(output_dir / "semantic_stage_compare_baselines.png", dpi=180, bbox_inches="tight")
	fig.savefig(output_dir / "semantic_stage_compare_baselines.pdf", bbox_inches="tight")
	plt.close(fig)


def run_flash_fusion_native_experiment(
	datasets: list[str],
	query_ids: list[int] | None,
	model: str,
	output_dir: str,
	data_paths: dict[str, str | None],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
	api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
	if not api_key:
		raise ValueError("OPENROUTER_API_KEY or GROQ_API_KEY environment variable required")

	out = Path(output_dir)
	out.mkdir(parents=True, exist_ok=True)

	query_rows: list[dict[str, Any]] = []
	raw_rows: list[dict[str, Any]] = []

	for dataset in datasets:
		data_file = _resolve_dataset_file(dataset, data_paths.get(dataset))
		query_defs = get_queries(dataset)
		query_map = {q["id"]: q for q in query_defs}
		valid_ids = set(query_map.keys())

		if query_ids is None:
			active_ids = sorted(valid_ids)
		else:
			invalid = [qid for qid in query_ids if qid not in valid_ids]
			if invalid:
				raise ValueError(f"Invalid query IDs for {dataset}: {invalid}")
			active_ids = list(query_ids)

		print(f"[FF native][{dataset}] loading dataset: {data_file}", flush=True)
		df_full = load_dataset_by_name(str(data_file), dataset)
		if df_full.empty:
			raise ValueError(f"Dataset loaded empty for {dataset}: {data_file}")

		for qid in active_ids:
			q = query_map[qid]
			query_text = str(q["text"])
			print(f"[FF native][{dataset}] Q{qid}: {query_text[:72]}...", flush=True)
			rr, _ = _run_single_query(
				query_text=query_text,
				df_full=df_full,
				model=model,
				api_key=api_key,
				baseline_mode="FLASH_FUSION",
			)
			stage_s = _flash_fusion_native_stage_latency_s(rr)

			total_latency_s = float(rr.latency_s)
			stacked = sum(stage_s.values())
			latency_delta_s = total_latency_s - stacked

			row = {
				"dataset": dataset,
				"query_id": int(qid),
				"query_type": _normalize_query_type(str(q.get("complexity", ""))),
				"complexity": str(q.get("complexity", "")),
				"query": query_text,
				"baseline": "FLASH_FUSION",
				"model": rr.model,
				"executed": bool(rr.executed),
				"rejected": bool(rr.rejected),
				"latency_s": total_latency_s,
				"latency_ms": total_latency_s * 1000.0,
				"cost_usd": float(rr.cost_usd),
				"input_tokens": int(rr.input_tokens),
				"output_tokens": int(rr.output_tokens),
				"stages_run": ",".join(rr.stages_run),
				"stacked_stage_latency_s": stacked,
				"stacked_stage_latency_ms": stacked * 1000.0,
				"latency_delta_s": latency_delta_s,
			}
			for stage in FF_STAGE_KEYS:
				row[f"{stage}_latency_s"] = stage_s[stage]
				row[f"{stage}_latency_ms"] = stage_s[stage] * 1000.0
			query_rows.append(row)

			raw = dataclasses.asdict(rr)
			raw["dataset"] = dataset
			raw["query_id"] = int(qid)
			raw["query_type"] = row["query_type"]
			raw_rows.append(raw)

	query_df = pd.DataFrame(query_rows)
	per_dataset_df, summary_df = _aggregate_query_rows(query_df, FF_STAGE_KEYS)

	query_df.to_csv(out / "ff_native_latency_by_stages_query_metrics.csv", index=False)
	per_dataset_df.to_csv(out / "ff_native_latency_by_stages_per_dataset_summary.csv", index=False)
	summary_df.to_csv(out / "ff_native_latency_by_stages_summary.csv", index=False)
	_write_jsonl(out / "ff_native_latency_by_stages_raw_results.jsonl", raw_rows)

	with (out / "ff_native_run_metadata.json").open("w", encoding="utf-8") as fh:
		json.dump(
			{
				"baseline": "FLASH_FUSION",
				"datasets": datasets,
				"query_ids": query_ids,
				"model": model,
				"stage_keys": list(FF_STAGE_KEYS),
			},
			fh,
			ensure_ascii=True,
			indent=2,
		)

	return query_df, per_dataset_df, summary_df


def run_semantic_comparison_experiment(
	datasets: list[str],
	query_ids: list[int] | None,
	model: str,
	output_dir: str,
	data_paths: dict[str, str | None],
	baselines: tuple[str, ...] = COMPARE_BASELINE_MODES,
) -> tuple[pd.DataFrame, pd.DataFrame]:
	out = Path(output_dir)
	out.mkdir(parents=True, exist_ok=True)

	all_query_dfs: list[pd.DataFrame] = []
	for baseline in baselines:
		baseline_out = out / "per_baseline" / baseline.lower()
		query_df, _, _ = run_experiment(
			datasets=datasets,
			query_ids=query_ids,
			model=model,
			output_dir=str(baseline_out),
			data_paths=data_paths,
			baseline_mode=baseline,
		)
		all_query_dfs.append(query_df)

	if not all_query_dfs:
		return pd.DataFrame(), pd.DataFrame()

	combined_df = pd.concat(all_query_dfs, ignore_index=True)

	agg_fields: dict[str, tuple[str, str]] = {
		"avg_total_latency_s": ("latency_s", "mean"),
		"execution_rate": ("executed", "mean"),
		"rejection_rate": ("rejected", "mean"),
		"query_count": ("query_id", "count"),
	}
	for stage in STAGE_KEYS:
		agg_fields[f"{stage}_latency_s"] = (f"{stage}_latency_s", "mean")
		agg_fields[f"{stage}_latency_ms"] = (f"{stage}_latency_ms", "mean")

	summary_df = (
		combined_df.groupby("baseline", as_index=False)
		.agg(**agg_fields)
		.reset_index(drop=True)
	)
	summary_df["baseline"] = pd.Categorical(summary_df["baseline"], categories=list(baselines), ordered=True)
	summary_df = summary_df.sort_values("baseline").reset_index(drop=True)
	summary_df["stacked_stage_latency_s"] = summary_df[[f"{s}_latency_s" for s in STAGE_KEYS]].sum(axis=1)
	summary_df["stacked_stage_latency_ms"] = summary_df["stacked_stage_latency_s"] * 1000.0

	combined_df.to_csv(out / "semantic_compare_query_metrics.csv", index=False)
	summary_df.to_csv(out / "semantic_compare_summary_by_baseline.csv", index=False)

	with (out / "semantic_compare_metadata.json").open("w", encoding="utf-8") as fh:
		json.dump(
			{
				"baselines": list(baselines),
				"datasets": datasets,
				"query_ids": query_ids,
				"model": model,
				"stage_keys": list(STAGE_KEYS),
			},
			fh,
			ensure_ascii=True,
			indent=2,
		)

	return combined_df, summary_df


def run_experiment(
	datasets: list[str],
	query_ids: list[int] | None,
	model: str,
	output_dir: str,
	data_paths: dict[str, str | None],
	baseline_mode: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
	api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
	if not api_key:
		raise ValueError("OPENROUTER_API_KEY or GROQ_API_KEY environment variable required")

	out = Path(output_dir)
	out.mkdir(parents=True, exist_ok=True)

	query_rows: list[dict[str, Any]] = []
	raw_rows: list[dict[str, Any]] = []

	for dataset in datasets:
		data_file = _resolve_dataset_file(dataset, data_paths.get(dataset))
		query_defs = get_queries(dataset)
		query_map = {q["id"]: q for q in query_defs}
		valid_ids = set(query_map.keys())

		if query_ids is None:
			active_ids = sorted(valid_ids)
		else:
			invalid = [qid for qid in query_ids if qid not in valid_ids]
			if invalid:
				raise ValueError(f"Invalid query IDs for {dataset}: {invalid}")
			active_ids = list(query_ids)

		print(f"[{dataset}] loading dataset: {data_file}", flush=True)
		df_full = load_dataset_by_name(str(data_file), dataset)
		if df_full.empty:
			raise ValueError(f"Dataset loaded empty for {dataset}: {data_file}")

		for qid in active_ids:
			q = query_map[qid]
			query_text = str(q["text"])
			print(f"[{dataset}] Q{qid}: {query_text[:72]}...", flush=True)
			rr, call_log = _run_single_query(
				query_text=query_text,
				df_full=df_full,
				model=model,
				api_key=api_key,
				baseline_mode=baseline_mode,
			)
			stage_s = map_semantic_stage_latency_s(rr, call_log)

			total_latency_s = float(rr.latency_s)
			stacked = sum(stage_s.values())
			latency_delta_s = total_latency_s - stacked
			if stacked > total_latency_s * 1.05:
				print(
					f"[SANITY WARNING] Q{qid} ({dataset}): stacked_stage_latency_s={stacked:.3f}s "
					f"> rr.latency_s={total_latency_s:.3f}s — possible double-counting "
					f"(delta={latency_delta_s:.3f}s)",
					flush=True,
				)
			else:
				print(
					f"[SANITY OK]  Q{qid} ({dataset}): rr.latency_s={total_latency_s:.3f}s, "
					f"stacked={stacked:.3f}s, overhead={latency_delta_s:.3f}s",
					flush=True,
				)

			row = {
				"dataset": dataset,
				"query_id": int(qid),
				"query_type": _normalize_query_type(str(q.get("complexity", ""))),
				"complexity": str(q.get("complexity", "")),
				"query": query_text,
				"baseline": baseline_mode,
				"model": rr.model,
				"executed": bool(rr.executed),
				"rejected": bool(rr.rejected),
				"latency_s": total_latency_s,
				"latency_ms": total_latency_s * 1000.0,
				"cost_usd": float(rr.cost_usd),
				"input_tokens": int(rr.input_tokens),
				"output_tokens": int(rr.output_tokens),
				"stages_run": ",".join(rr.stages_run),
				"stacked_stage_latency_s": stacked,
				"stacked_stage_latency_ms": stacked * 1000.0,
				"latency_delta_s": latency_delta_s,
			}
			for stage in STAGE_KEYS:
				row[f"{stage}_latency_s"] = stage_s[stage]
				row[f"{stage}_latency_ms"] = stage_s[stage] * 1000.0
			query_rows.append(row)

			raw = dataclasses.asdict(rr)
			raw["dataset"] = dataset
			raw["query_id"] = int(qid)
			raw["query_type"] = row["query_type"]
			raw_rows.append(raw)

	query_df = pd.DataFrame(query_rows)

	# Sanity summary: stacked stage latency vs wall-clock latency_s
	if not query_df.empty:
		n_warn = int((query_df["stacked_stage_latency_s"] > query_df["latency_s"] * 1.05).sum())
		mean_delta = query_df["latency_delta_s"].mean()
		max_delta = query_df["latency_delta_s"].max()
		min_delta = query_df["latency_delta_s"].min()
		print(
			f"[SANITY SUMMARY] queries={len(query_df)}, double-count warnings={n_warn}, "
			f"mean_overhead={mean_delta:.3f}s, max_overhead={max_delta:.3f}s, min_overhead={min_delta:.3f}s",
			flush=True,
		)

	per_dataset_df, summary_df = _aggregate_query_rows(query_df, STAGE_KEYS)

	query_df.to_csv(out / "latency_by_stages_query_metrics.csv", index=False)
	per_dataset_df.to_csv(out / "latency_by_stages_per_dataset_summary.csv", index=False)
	summary_df.to_csv(out / "latency_by_stages_summary.csv", index=False)
	_write_jsonl(out / "latency_by_stages_raw_results.jsonl", raw_rows)

	with (out / "run_metadata.json").open("w", encoding="utf-8") as fh:
		json.dump(
			{
				"baseline": baseline_mode,
				"datasets": datasets,
				"query_ids": query_ids,
				"model": model,
				"stage_keys": list(STAGE_KEYS),
				"semantic_stage_mapping": {
					"grounding": {
						"FLASH_FUSION": "S1 + S2",
						"AUTOIOT_PAPER": "retrieval-oriented setup (terms/search + retrieval residual)",
						"REACT_ONLY": "none",
						"HARGPT_PAPER": "none",
					},
					"validation": {
						"FLASH_FUSION": "guardrail",
						"AUTOIOT_PAPER": "none",
						"REACT_ONLY": "none",
						"HARGPT_PAPER": "none",
					},
					"planning": {
						"FLASH_FUSION": "S3",
						"AUTOIOT_PAPER": "planning + module generation + integration",
						"REACT_ONLY": "none",
						"HARGPT_PAPER": "none",
					},
					"execution": {
						"FLASH_FUSION": "agent",
						"AUTOIOT_PAPER": "iterative execution/refinement + selection (residual wall time)",
						"REACT_ONLY": "react_agent",
						"HARGPT_PAPER": "single rewritten inference",
					},
				},
				"query_type_mapping": {
					"direct": "direct",
					"intermediate": "reasoning",
					"out_of_scope": "oos",
				},
			},
			fh,
			ensure_ascii=True,
			indent=2,
		)

	return query_df, per_dataset_df, summary_df


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Semantic stage latency mini-experiment")
	parser.add_argument(
		"--mode",
		default="both",
		choices=["both", "ff_only", "semantic_single", "semantic_compare"],
		help="Run mode: FF native swimlane, semantic comparison, or both.",
	)
	parser.add_argument(
		"--baseline",
		default="FLASH_FUSION",
		choices=list(BASELINE_MODES),
		help="Baseline mode to evaluate (used in semantic_single mode).",
	)
	parser.add_argument(
		"--datasets",
		nargs="+",
		default=list(SUPPORTED_DATASETS),
		choices=list(SUPPORTED_DATASETS),
		help="Datasets to evaluate (default: all supported).",
	)
	parser.add_argument(
		"--query-ids",
		nargs="*",
		type=int,
		default=None,
		help="Optional query IDs to run (default: all for each dataset).",
	)
	parser.add_argument("--model", default=DEFAULT_MODEL, help="Model ID to use for Flash-Fusion runs.")
	parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory.")
	parser.add_argument("--data-path-wisdm", default=None, help="Override WISDM data path.")
	parser.add_argument("--data-path-mit-ecg", default=None, help="Override MIT ECG data path.")
	parser.add_argument("--data-path-bus", default=None, help="Override BUS data path.")
	parser.add_argument(
		"--dry-run-check-paths",
		action="store_true",
		help="Validate model and dataset paths, then exit.",
	)
	parser.add_argument(
		"--no-plot",
		action="store_true",
		help="Skip figure generation and only write CSV/JSON artifacts.",
	)
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	_validate_model(args.model)
	baseline_mode = str(args.baseline).upper()
	if baseline_mode not in BASELINE_MODES:
		raise ValueError(f"Unsupported baseline mode: {baseline_mode}")

	data_paths: dict[str, str | None] = {
		"wisdm": args.data_path_wisdm,
		"mit_ecg": args.data_path_mit_ecg,
		"bus": args.data_path_bus,
	}

	print(f"Mode: {args.mode}", flush=True)
	print(f"Baseline: {baseline_mode}", flush=True)
	print(f"Model: {args.model}", flush=True)
	for ds in args.datasets:
		resolved = _resolve_dataset_file(ds, data_paths.get(ds))
		print(f"[{ds}] data path: {resolved}", flush=True)

	if args.dry_run_check_paths:
		print("Dry-run path/model checks passed.", flush=True)
		return

	root_output = Path(args.output_dir)
	root_output.mkdir(parents=True, exist_ok=True)

	if args.mode in {"ff_only", "both"}:
		ff_out = root_output / "flash_fusion_native"
		ff_query_df, ff_per_dataset_df, ff_summary_df = run_flash_fusion_native_experiment(
			datasets=list(args.datasets),
			query_ids=args.query_ids,
			model=args.model,
			output_dir=str(ff_out),
			data_paths=data_paths,
		)
		if not args.no_plot:
			plot_stacked_swimlanes(
				ff_summary_df,
				ff_out,
				stage_keys=FF_STAGE_KEYS,
				stage_plot_labels=FF_STAGE_PLOT_LABELS,
				stage_colors=FF_STAGE_COLORS,
				title="Flash-Fusion Stage Latency by Query Type",
				output_stem="ff_native_latency_by_stages",
			)
		print(f"[FF native] Wrote query rows: {len(ff_query_df)}", flush=True)
		print(f"[FF native] Wrote per-dataset rows: {len(ff_per_dataset_df)}", flush=True)
		print(f"[FF native] Wrote swimlane rows: {len(ff_summary_df)}", flush=True)

	if args.mode == "semantic_single":
		semantic_out = root_output / "semantic_single" / baseline_mode.lower()
		query_df, per_dataset_df, summary_df = run_experiment(
			datasets=list(args.datasets),
			query_ids=args.query_ids,
			model=args.model,
			output_dir=str(semantic_out),
			data_paths=data_paths,
			baseline_mode=baseline_mode,
		)
		if not args.no_plot:
			plot_stacked_swimlanes(
				summary_df,
				semantic_out,
				stage_keys=STAGE_KEYS,
				stage_plot_labels=STAGE_PLOT_LABELS,
				stage_colors=STAGE_COLORS,
				title=f"{BASELINE_LABELS.get(baseline_mode, baseline_mode)}: Semantic Stage Latency by Query Type",
				output_stem="latency_by_stages",
			)
		print(f"[Semantic single] Wrote query rows: {len(query_df)}", flush=True)
		print(f"[Semantic single] Wrote per-dataset rows: {len(per_dataset_df)}", flush=True)
		print(f"[Semantic single] Wrote swimlane rows: {len(summary_df)}", flush=True)

	if args.mode in {"semantic_compare", "both"}:
		compare_out = root_output / "semantic_compare"
		combined_df, compare_summary_df = run_semantic_comparison_experiment(
			datasets=list(args.datasets),
			query_ids=args.query_ids,
			model=args.model,
			output_dir=str(compare_out),
			data_paths=data_paths,
			baselines=COMPARE_BASELINE_MODES,
		)
		if not args.no_plot:
			plot_semantic_baseline_comparison(compare_summary_df, compare_out)
		print(f"[Semantic compare] Wrote query rows: {len(combined_df)}", flush=True)
		print(f"[Semantic compare] Wrote baseline rows: {len(compare_summary_df)}", flush=True)

	print(f"Outputs: {root_output.resolve()}", flush=True)


if __name__ == "__main__":
	main()