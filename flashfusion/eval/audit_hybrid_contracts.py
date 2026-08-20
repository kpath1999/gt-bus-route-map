"""Audit hybrid contract extraction across datasets and query rewordings.

This script evaluates the rule-based ContractExtractor and compatibility checks
used by trace_hybrid_cache.py against all reusable registry entries for v1/v2/v3
query wording variants.

It does not run dense retrieval and does not call any language model.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from flashfusion.eval.trace_hybrid_cache import HybridMatcher, load_config, resolve_query

REGISTRY_BY_DATASET = {
    "bus": Path("flashfusion/eval/cache/semantic_registry_bus_v1.json"),
    "wisdm": Path("flashfusion/eval/cache/semantic_registry_wisdm_v1.json"),
    "mit_ecg": Path("flashfusion/eval/cache/semantic_registry_mit_ecg_v1.json"),
}

VERSIONS = ("v1", "v2", "v3")


def _infer_schema_columns(entries: list[dict[str, Any]]) -> list[str]:
    cols: set[str] = set()
    for entry in entries:
        sig = entry.get("semantic_signature")
        if not isinstance(sig, dict):
            continue

        for field in sig.get("fields") or []:
            if isinstance(field, str) and field.strip():
                cols.add(field.strip())

        pred_ops = sig.get("predicate_ops")
        if isinstance(pred_ops, dict):
            for key in pred_ops:
                if isinstance(key, str) and key.strip():
                    cols.add(key.strip())

        filter_values = sig.get("filter_values")
        if isinstance(filter_values, dict):
            for key in filter_values:
                if isinstance(key, str) and key.strip() and "__" not in key:
                    cols.add(key.strip())

        predictive = sig.get("predictive")
        if isinstance(predictive, dict):
            target_col = predictive.get("target_column")
            if isinstance(target_col, str) and target_col.strip():
                cols.add(target_col.strip())

    return sorted(cols)


def _matcher_for_dataset(dataset: str, entries: list[dict[str, Any]]) -> HybridMatcher:
    schema_columns = _infer_schema_columns(entries)
    config = load_config(Path("flashfusion/eval/cache/hybrid_match_config.json"))
    return HybridMatcher(
        entries=entries,
        config=config,
        dataset=dataset,
        schema_columns=schema_columns,
        schema_fingerprint=None,
        device="cpu",
        no_warmup=True,
        mode="fuzzy",
        dense_top_k_override=1,
        lexical_top_k_override=1,
    )


def _entries_by_query_id(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("status") != "reusable":
            continue
        query_id = entry.get("query_id")
        if query_id is None:
            continue
        out[str(query_id)] = entry
    return out


def _reusable_entries(entries: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    reusable: list[tuple[str, dict[str, Any]]] = []
    for entry in entries:
        if entry.get("status") != "reusable" or entry.get("query_id") is None:
            continue
        reusable.append((str(entry["query_id"]), entry))
    return sorted(reusable, key=lambda item: int(item[0]))


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _discrimination_metrics(counts: dict[str, int]) -> dict[str, int | float | None]:
    true_positives = counts["true_positives"]
    false_positives = counts["false_positives"]
    true_negatives = counts["true_negatives"]
    false_negatives = counts["false_negatives"]
    return {
        **counts,
        "precision": _rate(true_positives, true_positives + false_positives),
        "recall": _rate(true_positives, true_positives + false_negatives),
        "false_positive_rate": _rate(false_positives, false_positives + true_negatives),
    }


def audit_dataset(dataset: str, path: Path) -> dict[str, Any]:
    entries = HybridMatcher.load_registry(path)
    matcher = _matcher_for_dataset(dataset, entries)
    by_id = _entries_by_query_id(matcher.entries)
    candidates = _reusable_entries(matcher.entries)

    dataset_rows: list[dict[str, Any]] = []
    counts = Counter({
        "true_positives": 0,
        "false_positives": 0,
        "true_negatives": 0,
        "false_negatives": 0,
    })
    rejection_reasons: Counter[str] = Counter()
    ambiguity_count = 0

    for query_id in sorted(by_id, key=int):
        for version in VERSIONS:
            query_text = resolve_query(query_id=query_id, version=version, dataset=dataset, override=None)
            live_contract = matcher.extractor.extract(query_text)
            live = matcher._contract_from_live(live_contract)
            candidate_matrix: list[dict[str, Any]] = []
            compatible_candidate_ids: list[str] = []
            true_id_passes = False

            for candidate_index, (candidate_id, candidate_entry) in enumerate(candidates, start=1):
                candidate_contract = matcher._contract_from_entry(candidate_entry)
                component_scores, contract_score = matcher._component_scores(live, candidate_contract)
                compatibility_ok, compatibility_failures = matcher._compatibility(
                    candidate_entry,
                    expected_contract_hash=None,
                )
                safety_ok, safety_failures = matcher._safety_critical_agreement(live, candidate_contract)
                compatible = compatibility_ok and safety_ok
                rejection_reasons.update(compatibility_failures)
                rejection_reasons.update(safety_failures)

                candidate_matrix.append(
                    {
                        "candidate_registry_id": matcher._candidate_id(candidate_entry, candidate_index),
                        "candidate_query_id": candidate_id,
                        "passes": compatible,
                        "rejection_reasons": compatibility_failures + safety_failures,
                        "contract_score": round(float(contract_score), 6),
                        "component_scores": component_scores,
                    }
                )

                is_true_id = candidate_id == query_id
                if compatible:
                    compatible_candidate_ids.append(candidate_id)
                    counts["true_positives" if is_true_id else "false_positives"] += 1
                else:
                    counts["false_negatives" if is_true_id else "true_negatives"] += 1
                if is_true_id:
                    true_id_passes = compatible

            wrong_compatible_ids = [candidate_id for candidate_id in compatible_candidate_ids if candidate_id != query_id]
            potential_ambiguity = len(compatible_candidate_ids) >= 2
            if potential_ambiguity:
                ambiguity_count += 1

            row = {
                "dataset": dataset,
                "query_id": query_id,
                "version": version,
                "admissibility": live_contract.admissibility,
                "confidence": round(float(live_contract.confidence), 6),
                "live_contract": live,
                "true_id_passes": true_id_passes,
                "compatible_candidate_ids": compatible_candidate_ids,
                "wrong_compatible_ids": wrong_compatible_ids,
                "wrong_compatible_count": len(wrong_compatible_ids),
                "potential_ambiguity": potential_ambiguity,
                "candidate_matrix": candidate_matrix,
            }
            dataset_rows.append(row)

    return {
        "dataset": dataset,
        "registry": str(path),
        "total_live_queries": len(dataset_rows),
        "candidate_pairs": sum(counts.values()),
        "potential_ambiguities": ambiguity_count,
        "metrics": _discrimination_metrics(dict(counts)),
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "rows": dataset_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit hybrid contract extraction across query versions.")
    parser.add_argument("--dataset", choices=("all", "bus", "wisdm", "mit_ecg"), default="all")
    parser.add_argument("--output", type=Path, default=None, help="Optional output JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    datasets = tuple(REGISTRY_BY_DATASET.keys()) if args.dataset == "all" else (args.dataset,)

    reports = []
    for dataset in datasets:
        reports.append(audit_dataset(dataset=dataset, path=REGISTRY_BY_DATASET[dataset]))

    summary_counts = {
        metric: sum(int(report["metrics"][metric]) for report in reports)
        for metric in ("true_positives", "false_positives", "true_negatives", "false_negatives")
    }
    summary = {
        "datasets": [r["dataset"] for r in reports],
        "total_live_queries": sum(int(report["total_live_queries"]) for report in reports),
        "candidate_pairs": sum(int(report["candidate_pairs"]) for report in reports),
        "potential_ambiguities": sum(int(report["potential_ambiguities"]) for report in reports),
        "metrics": _discrimination_metrics(summary_counts),
        "rejection_reasons": dict(
            sorted(
                sum((Counter(report["rejection_reasons"]) for report in reports), Counter()).items()
            )
        ),
    }

    payload = {
        "summary": summary,
        "reports": reports,
    }

    encoded = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
