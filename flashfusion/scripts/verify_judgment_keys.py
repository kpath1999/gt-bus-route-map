"""Verify result-to-LLM-judgment identity across benchmark run artifacts.

This script is read-only. It compares the ``(baseline, query_id)`` keys in
``metrics.csv`` with those in ``ground_truth_llm_judge/llm_judgments.csv`` and
reports whether raw result rows persist an explicit ``query_id``. It also shows
which query catalog version each raw query text matches, because the current
LLM judge resolves IDs through the v1 text lookup.

Typical use:
    python -m flashfusion.scripts.verify_judgment_keys

To fail with a nonzero exit code when key mismatches are found:
    python -m flashfusion.scripts.verify_judgment_keys --strict
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from flashfusion.eval import queries_v2, queries_v3
from flashfusion.eval.queries import get_queries


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_ROOT = (
    PROJECT_ROOT
    / "flashfusion"
    / "results"
    / "ff_and_react_qwen"
    / "FLASH_FUSION_CACHE"
)
QUERY_MODULES = {
    "v1": None,
    "v2": queries_v2,
    "v3": queries_v3,
}

Key = tuple[str, int]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required CSV file not found: {path}")
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required JSONL file not found: {path}")

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected a JSON object at {path}:{line_number}")
            rows.append(row)
    return rows


def _parse_key(row: dict[str, Any], *, source: Path) -> Key:
    baseline = str(row.get("baseline", ""))
    if not baseline:
        raise ValueError(f"Missing baseline in {source}: {row!r}")
    try:
        query_id = int(row["query_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid query_id in {source}: {row!r}") from exc
    return baseline, query_id


def _query_catalogs(dataset: str) -> dict[str, dict[str, int]]:
    catalogs: dict[str, dict[str, int]] = {}
    for version, module in QUERY_MODULES.items():
        queries = get_queries(dataset) if module is None else module.get_queries(dataset)
        catalogs[version] = {str(query["text"]): int(query["id"]) for query in queries}
    return catalogs


def _expected_version(run_name: str) -> str | None:
    suffix = run_name.removeprefix("run_")
    return f"v{suffix}" if suffix in {"1", "2", "3"} else None


def _format_keys(keys: set[Key]) -> str:
    if not keys:
        return "none"
    return ", ".join(
        f"{baseline}:{query_id}"
        for baseline, query_id in sorted(keys, key=lambda key: (key[0], key[1]))
    )


def audit_run(run_dir: Path, dataset: str) -> dict[str, Any]:
    """Return identity and catalog-match evidence for one run directory."""
    metrics_path = run_dir / "metrics.csv"
    judgments_path = run_dir / "ground_truth_llm_judge" / "llm_judgments.csv"
    raw_results_path = run_dir / "raw_results.jsonl"

    metrics = _read_csv(metrics_path)
    judgments = _read_csv(judgments_path)
    raw_results = _read_jsonl(raw_results_path)

    result_keys = {_parse_key(row, source=metrics_path) for row in metrics}
    judgment_keys = {_parse_key(row, source=judgments_path) for row in judgments}
    missing_judgment_keys = result_keys - judgment_keys
    unexpected_judgment_keys = judgment_keys - result_keys

    missing_score_keys = {
        _parse_key(row, source=metrics_path)
        for row in metrics
        if row.get("gt_method") == "llm_judge_score_missing"
    }
    fallback_keys = {
        _parse_key(row, source=metrics_path)
        for row in metrics
        if str(row.get("gt_method", "")).startswith("guardrail_skip_")
    }

    explicit_query_ids = [row.get("query_id") for row in raw_results]
    rows_with_query_id = sum(
        query_id is not None and str(query_id).strip() != ""
        for query_id in explicit_query_ids
    )

    catalogs = _query_catalogs(dataset)
    catalog_matches = {
        version: sum(str(row.get("query", "")) in lookup for row in raw_results)
        for version, lookup in catalogs.items()
    }
    expected_version = _expected_version(run_dir.name)

    return {
        "dataset": dataset,
        "run": run_dir.name,
        "raw_row_count": len(raw_results),
        "metrics_row_count": len(metrics),
        "judgment_row_count": len(judgments),
        "result_keys": result_keys,
        "judgment_keys": judgment_keys,
        "missing_judgment_keys": missing_judgment_keys,
        "unexpected_judgment_keys": unexpected_judgment_keys,
        "missing_score_keys": missing_score_keys,
        "fallback_keys": fallback_keys,
        "missing_labels_are_unjudged": missing_score_keys <= missing_judgment_keys,
        "rows_with_explicit_query_id": rows_with_query_id,
        "catalog_matches": catalog_matches,
        "expected_version": expected_version,
        "expected_version_matches": (
            catalog_matches.get(expected_version, 0) if expected_version else None
        ),
        "gt_method_counts": dict(Counter(row.get("gt_method", "") for row in metrics)),
    }


def _print_audit(audit: dict[str, Any]) -> None:
    label = f"{audit['dataset']}/{audit['run']}"
    print(f"\n{label}")
    print("-" * len(label))
    print(
        "rows: "
        f"raw={audit['raw_row_count']}, "
        f"metrics={audit['metrics_row_count']}, "
        f"judgments={audit['judgment_row_count']}"
    )
    print(
        "raw rows with explicit query_id: "
        f"{audit['rows_with_explicit_query_id']}/{audit['raw_row_count']}"
    )
    print(f"query text matches by catalog: {audit['catalog_matches']}")
    if audit["expected_version"]:
        print(
            f"expected catalog for {audit['run']}: {audit['expected_version']} "
            f"({audit['expected_version_matches']}/{audit['raw_row_count']} matches)"
        )
    print(f"gt_method counts: {audit['gt_method_counts']}")
    print(f"missing judgment keys: {_format_keys(audit['missing_judgment_keys'])}")
    print(f"unexpected judgment keys: {_format_keys(audit['unexpected_judgment_keys'])}")
    print(
        "llm_judge_score_missing keys are absent from judgments: "
        f"{audit['missing_labels_are_unjudged']}"
    )
    hidden_by_fallback = audit["fallback_keys"] & audit["missing_judgment_keys"]
    print(f"unjudged keys scored by guardrail fallback: {_format_keys(hidden_by_fallback)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=f"FLASH_FUSION_CACHE results directory (default: {DEFAULT_RESULTS_ROOT})",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=None,
        help="Datasets to inspect (default: every dataset directory under results root).",
    )
    parser.add_argument(
        "--runs",
        nargs="+",
        default=["run_2", "run_3"],
        help="Run directory names to inspect (default: run_2 run_3).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with status 1 if missing/unexpected judgment keys are detected.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results_root = args.results_root.resolve()
    if not results_root.is_dir():
        raise SystemExit(f"Results root not found: {results_root}")

    datasets = args.datasets or sorted(
        path.name for path in results_root.iterdir() if path.is_dir()
    )
    audits: list[dict[str, Any]] = []
    errors: list[str] = []

    for dataset in datasets:
        for run_name in args.runs:
            run_dir = results_root / dataset / run_name
            if not run_dir.is_dir():
                errors.append(f"Run directory not found: {run_dir}")
                continue
            try:
                audit = audit_run(run_dir, dataset)
            except (FileNotFoundError, ValueError, KeyError) as exc:
                errors.append(str(exc))
                continue
            audits.append(audit)
            _print_audit(audit)

    print("\nOverall diagnosis")
    print("-----------------")
    print(f"audited runs: {len(audits)}")
    print(
        "runs with no explicit raw query_id: "
        f"{sum(audit['rows_with_explicit_query_id'] == 0 for audit in audits)}"
    )
    print(
        "runs with missing judgment keys: "
        f"{sum(bool(audit['missing_judgment_keys']) for audit in audits)}"
    )
    print(
        "runs where every raw query matches its run-version catalog: "
        f"{sum(audit['expected_version_matches'] == audit['raw_row_count'] for audit in audits)}"
    )
    print(
        "runs where every llm_judge_score_missing label corresponds to an absent key: "
        f"{sum(audit['missing_labels_are_unjudged'] for audit in audits)}"
    )

    if errors:
        print("\nErrors")
        print("------")
        for error in errors:
            print(f"- {error}")

    has_key_mismatch = any(
        audit["missing_judgment_keys"] or audit["unexpected_judgment_keys"]
        for audit in audits
    )
    if errors or not audits or (args.strict and has_key_mismatch):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
