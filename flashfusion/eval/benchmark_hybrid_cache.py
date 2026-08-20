from __future__ import annotations

"""End-to-end benchmark for verified hybrid cache retrieval and authorization."""

"""
python -m flashfusion.eval.benchmark_hybrid_cache \
  --dataset bus \
  --output /tmp/hybrid_bus_benchmark.json \
  --report-k 1 3 5 \
  --verbose
"""

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import torch

from flashfusion.eval.trace_hybrid_cache import (
    HybridMatcher,
    load_config,
    resolve_query,
    schema_fingerprint,
)
from flashfusion.pipeline.loader import load_dataset_by_name

REGISTRY_BY_DATASET = {
    "bus": Path("flashfusion/eval/cache/semantic_registry_bus_v1.json"),
    "wisdm": Path("flashfusion/eval/cache/semantic_registry_wisdm_v1.json"),
    "mit_ecg": Path("flashfusion/eval/cache/semantic_registry_mit_ecg_v1.json"),
}
VERSIONS = ("v1", "v2", "v3")
DEFAULT_CONFIG_PATH = Path("flashfusion/eval/cache/hybrid_match_config.json")
DEFAULT_DATA_PATHS = {
    "bus": "data/bus/bus_data_enriched_behavior.csv",
    "wisdm": "data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt",
    "mit_ecg": "data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt",
}
AUTHORIZED_DECISIONS = {"exact_hit", "hybrid_hit"}
ABSTENTION_DECISIONS = {
    "low_confidence_candidate",
    "complete_miss",
    "incompatible_candidate",
    "out_of_scope_hit",
}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 6)


def _reusable_query_ids(entries: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(entry["query_id"])
            for entry in entries
            if entry.get("status", "reusable") == "reusable" and entry.get("query_id") is not None
        },
        key=int,
    )


def _parse_data_overrides(raw_overrides: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in raw_overrides:
        dataset, separator, path = item.partition("=")
        if not separator or dataset not in REGISTRY_BY_DATASET or not path.strip():
            raise ValueError("--data must have the form bus=PATH, wisdm=PATH, or mit_ecg=PATH.")
        overrides[dataset] = path.strip()
    return overrides


def _load_schema_frame(path: str, dataset: str) -> Any:
    """Load only the data required to establish the matcher schema."""
    max_rows = 1 if dataset == "mit_ecg" else None
    return load_dataset_by_name(path, dataset, max_rows=max_rows)


def _candidate_ids(candidates: list[dict[str, Any]], limit: int) -> list[str]:
    return [str(item["query_id"] or item["candidate_id"]) for item in candidates[:limit]]


def _top_candidate(candidates: list[dict[str, Any]], expected_id: str) -> dict[str, Any] | None:
    if not candidates:
        return None
    candidate = dict(candidates[0])
    candidate["is_expected"] = str(candidate["query_id"] or candidate["candidate_id"]) == expected_id
    return candidate


def _metric_summary(rows: list[dict[str, Any]], report_k: list[int]) -> dict[str, Any]:
    valid_rows = [row for row in rows if "error" not in row]
    total = len(valid_rows)
    tp = sum(bool(row["correct_authorized_hit"]) for row in valid_rows)
    fp = sum(bool(row["false_positive_reuse"]) for row in valid_rows)
    fn = total - tp
    timings = [float(row["elapsed_ms"].get("total_match_ms", 0.0)) for row in valid_rows]
    return {
        "total_live_queries": total,
        "error_count": len(rows) - total,
        "dense_top_1_accuracy": _rate(sum(bool(row["expected_in_dense_top_1"]) for row in valid_rows), total),
        "lexical_top_1_accuracy": _rate(sum(bool(row["expected_in_lexical_top_1"]) for row in valid_rows), total),
        "dense_recall_at_k": {
            str(k): _rate(sum(str(row["expected_query_id"]) in row["dense_top_k_ids_by_report_k"][str(k)] for row in valid_rows), total)
            for k in report_k
        },
        "lexical_recall_at_k": {
            str(k): _rate(sum(str(row["expected_query_id"]) in row["lexical_top_k_ids_by_report_k"][str(k)] for row in valid_rows), total)
            for k in report_k
        },
        "union_recall_at_k": {
            str(k): _rate(sum(str(row["expected_query_id"]) in row["union_ids_by_report_k"][str(k)] for row in valid_rows), total)
            for k in report_k
        },
        "authorized_hit_precision": _rate(tp, tp + fp),
        "authorized_hit_recall": _rate(tp, total),
        "authorization_rate": _rate(tp + fp, total),
        "false_positive_reuse_rate": _rate(fp, total),
        "abstention_rate": _rate(sum(bool(row["abstained"]) for row in valid_rows), total),
        "ambiguity_rate": _rate(sum(bool(row["ambiguous"]) for row in valid_rows), total),
        "potential_ambiguity_rate": _rate(sum(bool(row["potential_ambiguity"]) for row in valid_rows), total),
        "correct_but_abstained_rate": _rate(sum(bool(row["correct_but_abstained"]) for row in valid_rows), total),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "average_match_ms": round(sum(timings) / total, 6) if total else None,
        "p95_match_ms": _percentile(timings, 0.95),
    }


def _failure_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_rows = [row for row in rows if "error" not in row]
    return {
        "wrong_authorized_reuse": sum(bool(row["false_positive_reuse"]) for row in valid_rows),
        "abstention_decisions": dict(sorted(Counter(row["decision"] for row in valid_rows if row["abstained"]).items())),
        "ambiguous_decisions": sum(bool(row["ambiguous"]) for row in valid_rows),
        "expected_absent_from_retrieval_union": sum(not bool(row["expected_in_retrieval_union"]) for row in valid_rows),
        "expected_retrieved_but_rejected_incompatible": sum(bool(row["expected_retrieved_but_rejected_incompatible"]) for row in valid_rows),
        "expected_compatible_but_not_final_winner": sum(bool(row["expected_compatible_but_not_final_winner"]) for row in valid_rows),
        "expected_winner_below_acceptance_floor": sum(bool(row["expected_winner_below_acceptance_floor"]) for row in valid_rows),
        "errors": len(rows) - len(valid_rows),
    }


def _benchmark_row(
    matcher: HybridMatcher,
    dataset: str,
    query_id: str,
    version: str,
    report_k: list[int],
) -> dict[str, Any]:
    query = resolve_query(query_id=query_id, version=version, dataset=dataset, override=None)
    diagnostics = matcher.retrieve_diagnostics(
        query,
        top_k=max(*report_k, matcher.dense_top_k, matcher.lexical_top_k),
    )
    dense, lexical = diagnostics["dense"], diagnostics["lexical"]
    result = matcher.match(query)
    predicted = None if result.winner is None else (result.winner.query_id or result.winner.candidate_id)
    authorized_hit = result.decision in AUTHORIZED_DECISIONS
    correct_authorized_hit = authorized_hit and predicted == query_id
    compatibility_candidates = [candidate for candidate in result.candidates if candidate.compatibility]
    expected_candidates = [candidate for candidate in result.candidates if candidate.query_id == query_id]
    expected_compatible = any(candidate.compatibility for candidate in expected_candidates)
    matching_union_ids = diagnostics["matching_union_ids"]
    union_by_k = {
        str(k): sorted(set(_candidate_ids(dense, k)) | set(_candidate_ids(lexical, k)))
        for k in report_k
    }
    winner_margin = None
    if result.winner is not None and result.runner_up is not None:
        winner_margin = round(result.winner.final_score - result.runner_up.final_score, 6)
    acceptance_floor = float(matcher.thresholds.get("acceptance_floor", 0.75))
    is_winner_expected = predicted == query_id
    return {
        "dataset": dataset,
        "query_id": query_id,
        "version": version,
        "query": query,
        "expected_query_id": query_id,
        "dense_top_1": _top_candidate(dense, query_id),
        "lexical_top_1": _top_candidate(lexical, query_id),
        "dense_top_k_ids": _candidate_ids(dense, matcher.dense_top_k),
        "lexical_top_k_ids": _candidate_ids(lexical, matcher.lexical_top_k),
        "retrieval_union_ids": matching_union_ids,
        "dense_top_k_ids_by_report_k": {str(k): _candidate_ids(dense, k) for k in report_k},
        "lexical_top_k_ids_by_report_k": {str(k): _candidate_ids(lexical, k) for k in report_k},
        "union_ids_by_report_k": union_by_k,
        "expected_in_dense_top_1": query_id in _candidate_ids(dense, 1),
        "expected_in_dense_top_k": query_id in _candidate_ids(dense, matcher.dense_top_k),
        "expected_in_lexical_top_1": query_id in _candidate_ids(lexical, 1),
        "expected_in_lexical_top_k": query_id in _candidate_ids(lexical, matcher.lexical_top_k),
        "expected_in_retrieval_union": query_id in matching_union_ids,
        "decision": result.decision,
        "predicted_query_id": predicted,
        "final_winner_is_expected": is_winner_expected,
        "authorized_hit": authorized_hit,
        "correct_authorized_hit": correct_authorized_hit,
        "false_positive_reuse": authorized_hit and predicted != query_id,
        "abstained": result.decision in ABSTENTION_DECISIONS,
        "ambiguous": result.decision == "ambiguous_multi_candidate",
        "winner": asdict(result.winner) if result.winner else None,
        "runner_up": asdict(result.runner_up) if result.runner_up else None,
        "winner_runner_up_margin": winner_margin,
        "compatibility_candidate_ids": [candidate.query_id or candidate.candidate_id for candidate in compatibility_candidates],
        "compatible_candidate_count": len(compatibility_candidates),
        "expected_is_compatible": expected_compatible,
        "potential_ambiguity": len(compatibility_candidates) > 1,
        "acceptance_floor": acceptance_floor,
        "ambiguity_margin": float(matcher.thresholds.get("ambiguity_margin", 0.08)),
        "expected_retrieved_but_rejected_incompatible": query_id in matching_union_ids and bool(expected_candidates) and not expected_compatible,
        "expected_compatible_but_not_final_winner": expected_compatible and not is_winner_expected,
        "expected_winner_below_acceptance_floor": is_winner_expected and result.winner is not None and result.winner.final_score < acceptance_floor,
        "correct_but_abstained": (
            query_id in matching_union_ids and expected_compatible and not correct_authorized_hit
        ),
        "elapsed_ms": {**diagnostics["elapsed_ms"], **result.elapsed_ms},
    }


def _print_summary(summary: dict[str, Any], failures: dict[str, Any]) -> None:
    overall = summary["overall"]
    print("Hybrid cache benchmark")
    print(
        "overall: "
        f"n={overall['total_live_queries']} precision={overall['authorized_hit_precision']} "
        f"recall={overall['authorized_hit_recall']} fpr={overall['false_positive_reuse_rate']} "
        f"abstention={overall['abstention_rate']} ambiguity={overall['ambiguity_rate']}"
    )
    for label, metrics in (("dataset", summary["by_dataset"]), ("version", summary["by_version"])):
        print(f"by {label}:")
        for key, values in metrics.items():
            print(f"  {key}: n={values['total_live_queries']} precision={values['authorized_hit_precision']} recall={values['authorized_hit_recall']}")
    print("failures:")
    for key, value in failures.items():
        print(f"  {key}: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark end-to-end verified hybrid cache matching.")
    parser.add_argument("--dataset", choices=("all", *REGISTRY_BY_DATASET), default="all")
    parser.add_argument("--version", choices=("all", *VERSIONS), default="all")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dense-top-k", type=int, default=None)
    parser.add_argument("--lexical-top-k", type=int, default=None)
    parser.add_argument("--report-k", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    parser.add_argument("--device", default=("cuda" if torch.cuda.is_available() else "cpu"))
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument("--data", action="append", default=[], metavar="DATASET=PATH")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    datasets = tuple(REGISTRY_BY_DATASET) if args.dataset == "all" else (args.dataset,)
    versions = VERSIONS if args.version == "all" else (args.version,)
    overrides = _parse_data_overrides(args.data)
    config = load_config(args.config)
    all_rows: list[dict[str, Any]] = []
    initialization_ms: dict[str, float] = {}
    effective_report_k: dict[str, list[int]] = {}

    for dataset in datasets:
        setup_started = time.perf_counter()
        try:
            entries = HybridMatcher.load_registry(REGISTRY_BY_DATASET[dataset])
            df = _load_schema_frame(overrides.get(dataset, DEFAULT_DATA_PATHS[dataset]), dataset)
            matcher = HybridMatcher(
                entries=entries,
                config=config,
                dataset=dataset,
                schema_columns=[str(column) for column in df.columns],
                schema_fingerprint=schema_fingerprint(df),
                device=args.device,
                no_warmup=args.no_warmup,
                mode="hybrid",
                dense_top_k_override=args.dense_top_k,
                lexical_top_k_override=args.lexical_top_k,
            )
            if not args.no_warmup:
                matcher.warm_up()
            query_ids = _reusable_query_ids(matcher.entries)
            report_k = sorted({k for k in args.report_k if 0 < k <= len(matcher.entries)})
            if not report_k:
                raise ValueError("No --report-k value is valid for this registry's reusable candidate count.")
            effective_report_k[dataset] = report_k
            initialization_ms[dataset] = round((time.perf_counter() - setup_started) * 1000.0, 6)
        except Exception as exc:
            initialization_ms[dataset] = round((time.perf_counter() - setup_started) * 1000.0, 6)
            all_rows.append({"dataset": dataset, "error": {"stage": "initialization", "message": str(exc)}})
            continue

        for query_id in query_ids:
            for version in versions:
                try:
                    all_rows.append(_benchmark_row(matcher, dataset, query_id, version, report_k))
                except Exception as exc:
                    all_rows.append({
                        "dataset": dataset,
                        "query_id": query_id,
                        "version": version,
                        "expected_query_id": query_id,
                        "error": {"stage": "query", "message": str(exc)},
                    })

    all_effective_k = sorted({k for values in effective_report_k.values() for k in values})
    by_dataset = {dataset: _metric_summary([row for row in all_rows if row["dataset"] == dataset], all_effective_k) for dataset in datasets}
    by_version = {version: _metric_summary([row for row in all_rows if row.get("version") == version], all_effective_k) for version in versions}
    by_dataset_and_version = {
        f"{dataset}/{version}": _metric_summary(
            [row for row in all_rows if row["dataset"] == dataset and row.get("version") == version], all_effective_k
        )
        for dataset in datasets for version in versions
    }
    summary = {
        "total_live_queries": sum(1 for row in all_rows if "error" not in row),
        "initialization_ms_by_dataset": initialization_ms,
        "overall": _metric_summary(all_rows, all_effective_k),
        "by_dataset": by_dataset,
        "by_version": by_version,
        "by_dataset_and_version": by_dataset_and_version,
    }
    failures = _failure_breakdown(all_rows)
    payload = {
        "benchmark": {
            "name": "hybrid_cache_end_to_end",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "config_path": str(args.config),
            "config_version": config.get("version"),
            "datasets": list(datasets), "versions": list(versions), "mode": "hybrid",
            "dense_top_k": args.dense_top_k or config.get("retrieval", {}).get("dense_top_k", 20),
            "lexical_top_k": args.lexical_top_k or config.get("retrieval", {}).get("lexical_top_k", 20),
            "report_k": all_effective_k,
        },
        "summary": summary,
        "failure_breakdown": failures,
        "rows": all_rows,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    _print_summary(summary, failures)
    if args.verbose or args.failures_only:
        for row in all_rows:
            if args.verbose or "error" in row or not row.get("correct_authorized_hit"):
                print(json.dumps(row, sort_keys=True))


if __name__ == "__main__":
    main()