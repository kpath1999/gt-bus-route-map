"""Mini-experiment: Flash-Fusion's latency across the stages.

I see there is a S1, S2, S3, guardrail and agent stage.
It'd be interesting to see how much time gets spent in each stage.
Average across the three query types - direct, reasoning, oos.
Ultimatately, I want to create a visualization that looks like three separate swimlanes,
and latency (in ms/s) is plotted on the x-axis.
Query type is on the y-axis.
And you can see horizontal stacked bars that visually show you how much time is spent in each stage.
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
STAGE_KEYS: tuple[str, ...] = ("s1", "s2", "s3", "guardrail", "agent")
QUERY_TYPE_ORDER: tuple[str, ...] = ("direct", "reasoning", "oos")
QUERY_TYPE_LABELS: dict[str, str] = {
	"direct": "Direct",
	"reasoning": "Reasoning",
	"oos": "OOS",
}
STAGE_PLOT_LABELS: dict[str, str] = {
	"s1": "S1",
	"s2": "S2",
	"s3": "S3",
	"guardrail": "Guardrail",
	"agent": "Agent",
}
STAGE_COLORS: dict[str, str] = {
	"s1": "#2c8c4a",
	"s2": "#2f6ad9",
	"s3": "#f28e2b",
	"guardrail": "#d62728",
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


def _stage_latency_s(result: RunResult) -> dict[str, float]:
	src = result.stage_latency_s if isinstance(result.stage_latency_s, dict) else {}
	return {k: float(src.get(k, 0.0) or 0.0) for k in STAGE_KEYS}


def _run_single_query(query_text: str, df_full: pd.DataFrame, model: str, api_key: str) -> RunResult:
	client = LLMClient(model_name=model, api_key=api_key)
	runner = BaselineRunner(mode="FLASH_FUSION", df=df_full, client=client)
	try:
		return runner.run(query_text)
	except Exception as exc:
		r = RunResult(
			baseline="FLASH_FUSION",
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
		return r


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
	with path.open("w", encoding="utf-8") as fh:
		for row in rows:
			fh.write(json.dumps(row, ensure_ascii=True) + "\n")


def _aggregate_query_rows(query_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
	if query_df.empty:
		return pd.DataFrame(), pd.DataFrame()

	agg_fields: dict[str, tuple[str, str]] = {
		"avg_total_latency_s": ("latency_s", "mean"),
		"execution_rate": ("executed", "mean"),
		"rejection_rate": ("rejected", "mean"),
		"query_count": ("query_id", "count"),
	}
	for stage in STAGE_KEYS:
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
	summary["stacked_stage_latency_s"] = summary[[f"{s}_latency_s" for s in STAGE_KEYS]].sum(axis=1)
	summary["stacked_stage_latency_ms"] = summary["stacked_stage_latency_s"] * 1000.0

	per_dataset["stacked_stage_latency_s"] = per_dataset[[f"{s}_latency_s" for s in STAGE_KEYS]].sum(axis=1)
	per_dataset["stacked_stage_latency_ms"] = per_dataset["stacked_stage_latency_s"] * 1000.0
	return per_dataset, summary


def plot_stacked_swimlanes(summary_df: pd.DataFrame, output_dir: Path) -> None:
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
	ax.set_xlabel("Latency (s)")
	ax.set_ylabel("Query Type")
	ax.set_title("Flash-Fusion Stage Latency by Query Type")
	ax.grid(axis="x", alpha=0.25)
	ax.set_axisbelow(True)
	ax.legend(title="Stage", loc="upper right")

	fig.tight_layout()
	output_dir.mkdir(parents=True, exist_ok=True)
	fig.savefig(output_dir / "latency_by_stages.png", dpi=180, bbox_inches="tight")
	fig.savefig(output_dir / "latency_by_stages.pdf", bbox_inches="tight")
	plt.close(fig)


def run_experiment(
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

		print(f"[{dataset}] loading dataset: {data_file}", flush=True)
		df_full = load_dataset_by_name(str(data_file), dataset)
		if df_full.empty:
			raise ValueError(f"Dataset loaded empty for {dataset}: {data_file}")

		for qid in active_ids:
			q = query_map[qid]
			query_text = str(q["text"])
			print(f"[{dataset}] Q{qid}: {query_text[:72]}...", flush=True)
			rr = _run_single_query(query_text=query_text, df_full=df_full, model=model, api_key=api_key)
			stage_s = _stage_latency_s(rr)

			row = {
				"dataset": dataset,
				"query_id": int(qid),
				"query_type": _normalize_query_type(str(q.get("complexity", ""))),
				"complexity": str(q.get("complexity", "")),
				"query": query_text,
				"baseline": rr.baseline,
				"model": rr.model,
				"executed": bool(rr.executed),
				"rejected": bool(rr.rejected),
				"latency_s": float(rr.latency_s),
				"latency_ms": float(rr.latency_s) * 1000.0,
				"cost_usd": float(rr.cost_usd),
				"input_tokens": int(rr.input_tokens),
				"output_tokens": int(rr.output_tokens),
				"stages_run": ",".join(rr.stages_run),
			}
			for stage in STAGE_KEYS:
				row[f"{stage}_latency_s"] = stage_s[stage]
				row[f"{stage}_latency_ms"] = stage_s[stage] * 1000.0
			row["stacked_stage_latency_s"] = sum(stage_s.values())
			row["stacked_stage_latency_ms"] = row["stacked_stage_latency_s"] * 1000.0
			query_rows.append(row)

			raw = dataclasses.asdict(rr)
			raw["dataset"] = dataset
			raw["query_id"] = int(qid)
			raw["query_type"] = row["query_type"]
			raw_rows.append(raw)

	query_df = pd.DataFrame(query_rows)
	per_dataset_df, summary_df = _aggregate_query_rows(query_df)

	query_df.to_csv(out / "latency_by_stages_query_metrics.csv", index=False)
	per_dataset_df.to_csv(out / "latency_by_stages_per_dataset_summary.csv", index=False)
	summary_df.to_csv(out / "latency_by_stages_summary.csv", index=False)
	_write_jsonl(out / "latency_by_stages_raw_results.jsonl", raw_rows)

	with (out / "run_metadata.json").open("w", encoding="utf-8") as fh:
		json.dump(
			{
				"datasets": datasets,
				"query_ids": query_ids,
				"model": model,
				"stage_keys": list(STAGE_KEYS),
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
	parser = argparse.ArgumentParser(description="Flash-Fusion stage latency mini-experiment")
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

	data_paths: dict[str, str | None] = {
		"wisdm": args.data_path_wisdm,
		"mit_ecg": args.data_path_mit_ecg,
		"bus": args.data_path_bus,
	}

	print(f"Model: {args.model}", flush=True)
	for ds in args.datasets:
		resolved = _resolve_dataset_file(ds, data_paths.get(ds))
		print(f"[{ds}] data path: {resolved}", flush=True)

	if args.dry_run_check_paths:
		print("Dry-run path/model checks passed.", flush=True)
		return

	query_df, per_dataset_df, summary_df = run_experiment(
		datasets=list(args.datasets),
		query_ids=args.query_ids,
		model=args.model,
		output_dir=args.output_dir,
		data_paths=data_paths,
	)
	if not args.no_plot:
		plot_stacked_swimlanes(summary_df, Path(args.output_dir))

	print(f"Wrote query rows: {len(query_df)}", flush=True)
	print(f"Wrote per-dataset rows: {len(per_dataset_df)}", flush=True)
	print(f"Wrote swimlane rows: {len(summary_df)}", flush=True)
	print(f"Outputs: {Path(args.output_dir).resolve()}", flush=True)


if __name__ == "__main__":
	main()