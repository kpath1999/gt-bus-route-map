"""Evaluate semantic-cache matching of reworded benchmark queries.

The evaluator builds an in-memory v1 semantic registry from the exact cache,
then sends each v2/v3 query through the same schema-aware intent extractor and
hard-gate matcher used by ``run_flash_fusion_cache``. A match is correct only
when the selected registry entry has the query's preserved ground-truth ID.

Typical use:
	python -m flashfusion.scripts.cache_match_eval --output results/cache_match_eval.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from flashfusion.baselines.flash_fusion_cache import (
	DEFAULT_CACHE_PATH,
	_find_semantic_entry,
	prewarm_hybrid_cache_runtime,
)
from flashfusion.eval import queries_v2, queries_v3
from flashfusion.eval.benchmark import DEFAULT_DATA_PATHS
from flashfusion.eval.build_semantic_registry import build_semantic_registry
from flashfusion.eval.queries import SUPPORTED_DATASETS
from flashfusion.pipeline.loader import load_dataset_by_name


QUERY_MODULES = {"v2": queries_v2, "v3": queries_v3}


def _debug(enabled: bool, message: str) -> None:
	if enabled:
		print(f"[cache-match-debug] {message}", flush=True)


def _evaluate_query(
	*,
	query: dict[str, Any],
	entries: list[dict[str, Any]],
	dataset: str,
	df: Any,
	runs: int,
	debug: bool,
) -> dict[str, Any]:
	expected_query_id = str(query["id"])
	attempts: list[dict[str, Any]] = []
	started = time.perf_counter()
	_debug(debug, f"{dataset} query_id={expected_query_id}: matching started")

	for run in range(1, runs + 1):
		entry, status, evidence = _find_semantic_entry(
			entries,
			str(query["text"]),
			dataset,
			df,
			expected_operator_contract_hash=None,
		)
		matched_query_id = str(entry.get("query_id")) if entry is not None else None
		attempts.append(
			{
				"run": run,
				"status": status,
				"matched_query_id": matched_query_id,
				"correct": matched_query_id == expected_query_id,
				"complexity": str(query.get("complexity") or ""),
				"outcome_class": _classify_outcome(
					complexity=str(query.get("complexity") or ""),
					status=status,
					matched_query_id=matched_query_id,
					expected_query_id=expected_query_id,
				),
				"evidence": evidence,
			}
		)

	outcomes = Counter(
		(attempt["status"], attempt["matched_query_id"], attempt["correct"])
		for attempt in attempts
	)
	_debug(
		debug,
		f"{dataset} query_id={expected_query_id}: matching finished in "
		f"{time.perf_counter() - started:.2f}s",
	)
	return {
		"query_id": expected_query_id,
		"query_text": query["text"],
		"runs": attempts,
		"stable": len(outcomes) == 1,
		"correct_runs": sum(bool(attempt["correct"]) for attempt in attempts),
		"total_runs": runs,
	}


def _classify_outcome(
	*,
	complexity: str,
	status: str,
	matched_query_id: str | None,
	expected_query_id: str,
) -> str:
	is_oos = complexity == "out_of_scope"
	has_hit = matched_query_id is not None
	if has_hit:
		if matched_query_id == expected_query_id:
			return "correct_template_hit"
		return "false_positive_hit"
	if status.startswith("admissibility_out_of_scope"):
		if is_oos:
			return "correct_out_of_scope_rejection"
		return "safe_abstention"
	if is_oos:
		return "safe_abstention"
	return "false_negative_in_scope_abstention"


def evaluate_dataset(
	*,
	dataset: str,
	query_version: str,
	data_path: str,
	cache_path: Path,
	runs: int,
	max_rows: int | None = 1,
	debug: bool = False,
) -> dict[str, Any]:
	"""Build v1 reference intents and score one rewritten query version.

	Intent extraction and hard-gate matching require only schema headers and
	dtypes, so a bounded ECG sample avoids scanning the full waveform export.
	"""
	_debug(debug, f"{dataset} {query_version}: loading dataset from {data_path}")
	load_started = time.perf_counter()
	df = load_dataset_by_name(data_path, dataset, max_rows=max_rows)
	_debug(
		debug,
		f"{dataset} {query_version}: loaded {len(df):,} rows in "
		f"{time.perf_counter() - load_started:.2f}s",
	)
	_debug(debug, f"{dataset} {query_version}: building v1 semantic registry")
	registry_started = time.perf_counter()
	registry = build_semantic_registry(
		dataset=dataset,
		query_version="v1",
		data_path=data_path,
		cache_path=cache_path,
		df=df,
	)
	_debug(
		debug,
		f"{dataset} {query_version}: built registry in "
		f"{time.perf_counter() - registry_started:.2f}s",
	)
	entries = list(registry["entries"].values())

	# Prewarm the hybrid matcher runtime once per dataset so that embedding
	# model load and warm-up are not charged to per-query matching.
	prewarm_hybrid_cache_runtime(
		df=df,
		dataset=dataset,
		cache_path=cache_path,
		semantic_cache_path=None,
	)

	queries = QUERY_MODULES[query_version].get_queries(dataset)
	_debug(
		debug,
		f"{dataset} {query_version}: matching {len(queries)} queries against "
		f"{len(entries)} registry entries",
	)
	evaluations = [
		_evaluate_query(
			query=query,
			entries=entries,
			dataset=dataset,
			df=df,
			runs=runs,
			debug=debug,
		)
		for query in queries
	]
	outcome_counts = Counter(
		attempt["outcome_class"]
		for item in evaluations
		for attempt in item["runs"]
	)
	semantic_hits = outcome_counts["correct_template_hit"] + outcome_counts["false_positive_hit"]
	false_positive_hits = outcome_counts["false_positive_hit"]
	correct_runs = sum(item["correct_runs"] for item in evaluations)
	total_runs = sum(item["total_runs"] for item in evaluations)
	matched_runs = sum(
		attempt["matched_query_id"] is not None
		for item in evaluations
		for attempt in item["runs"]
	)
	return {
		"dataset": dataset,
		"query_version": query_version,
		"reference_registry_entries": len(entries),
		"queries": evaluations,
		"summary": {
			"queries": len(evaluations),
			"runs": total_runs,
			"matched_runs": matched_runs,
			"correct_runs": correct_runs,
			"match_rate": correct_runs / total_runs if total_runs else 0.0,
			"stable_queries": sum(item["stable"] for item in evaluations),
			"outcome_counts": dict(outcome_counts),
			"semantic_hits": semantic_hits,
			"false_positive_hits": false_positive_hits,
			"false_positive_rate_among_hits": (
				false_positive_hits / semantic_hits if semantic_hits else 0.0
			),
		},
	}


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--datasets",
		nargs="+",
		choices=SUPPORTED_DATASETS,
		default=list(SUPPORTED_DATASETS),
		help="Datasets to evaluate (default: all).",
	)
	parser.add_argument(
		"--query-versions",
		nargs="+",
		choices=tuple(QUERY_MODULES),
		default=list(QUERY_MODULES),
		help="Reworded query versions to evaluate (default: v2 v3).",
	)
	parser.add_argument(
		"--data",
		default=None,
		help="Override data path; only valid when evaluating one dataset.",
	)
	parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
	parser.add_argument(
		"--runs",
		type=int,
		default=1,
		help="Repeated extraction/matching attempts per query (default: 1).",
	)
	parser.add_argument(
		"--max-rows",
		type=int,
		default=1,
		help="Maximum ECG rows to load for schema extraction (default: 1; use a positive value).",
	)
	parser.add_argument("--output", type=Path, default=None, help="Optional JSON results path.")
	parser.add_argument(
		"--debug",
		action="store_true",
		help="Print flushed phase timings and per-query matching progress.",
	)
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	if args.runs < 1:
		raise ValueError("--runs must be at least 1")
	if args.max_rows < 1:
		raise ValueError("--max-rows must be at least 1")
	if args.data is not None and len(args.datasets) != 1:
		raise ValueError("--data requires exactly one selected dataset")

	cache_path = Path(args.cache_path)
	results = []
	for dataset in args.datasets:
		data_path = args.data or DEFAULT_DATA_PATHS[dataset]
		for query_version in args.query_versions:
			_debug(args.debug, f"starting {dataset} {query_version}")
			result = evaluate_dataset(
				dataset=dataset,
				query_version=query_version,
				data_path=data_path,
				cache_path=cache_path,
				runs=args.runs,
				max_rows=args.max_rows,
				debug=args.debug,
			)
			results.append(result)
			summary = result["summary"]
			print(
				f"{dataset} {query_version}: {summary['correct_runs']}/{summary['runs']} "
				f"correct ({summary['match_rate']:.1%}); "
				f"matched={summary['matched_runs']}/{summary['runs']}",
				flush=True,
			)

	overall_correct = sum(result["summary"]["correct_runs"] for result in results)
	overall_runs = sum(result["summary"]["runs"] for result in results)
	overall_outcomes = Counter()
	for result in results:
		overall_outcomes.update(result["summary"].get("outcome_counts") or {})
	overall_hits = overall_outcomes["correct_template_hit"] + overall_outcomes["false_positive_hit"]
	overall_fp = overall_outcomes["false_positive_hit"]
	payload = {
		"runs_per_query": args.runs,
		"results": results,
		"summary": {
			"correct_runs": overall_correct,
			"runs": overall_runs,
			"match_rate": overall_correct / overall_runs if overall_runs else 0.0,
			"outcome_counts": dict(overall_outcomes),
			"semantic_hits": overall_hits,
			"false_positive_hits": overall_fp,
			"false_positive_rate_among_hits": (
				overall_fp / overall_hits if overall_hits else 0.0
			),
		},
	}
	print(f"overall: {overall_correct}/{overall_runs} correct ({payload['summary']['match_rate']:.1%})")
	print(
		"overall outcomes: "
		+ ", ".join(f"{k}={v}" for k, v in sorted(payload["summary"]["outcome_counts"].items()))
	)
	print(
		f"false_positive_rate_among_hits="
		f"{payload['summary']['false_positive_rate_among_hits']:.1%} "
		f"({payload['summary']['false_positive_hits']}/{payload['summary']['semantic_hits']})"
	)

	if args.output is not None:
		args.output.parent.mkdir(parents=True, exist_ok=True)
		args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
		print(f"Wrote detailed results to {args.output}")


if __name__ == "__main__":
	main()
