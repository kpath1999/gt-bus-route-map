#!/usr/bin/env python3
"""Summarize query accuracy by query type within each dataset."""

from __future__ import annotations

import argparse
from pathlib import Path

from measure import (
    BASELINE_ORDER,
    aggregate_accuracy_by_dataset_query_type,
    load_all_metrics,
)


def _resolve_user_path(raw_path: str | None, repo_root: Path) -> Path | None:
    """Resolve paths with repo-relative and cwd-relative semantics."""
    if raw_path is None:
        return None

    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve()

    if path.parts and path.parts[0] in {"flashfusion", "results"}:
        return (repo_root / path).resolve()

    return (Path.cwd() / path).resolve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize accuracy by dataset, baseline, and query type."
    )
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent
    parser.add_argument(
        "--results-root",
        default=str(repo_root / "results" / "with_slm_predictive"),
        help="Root folder containing dataset-level metrics.csv files.",
    )
    parser.add_argument(
        "--output",
        default=str(repo_root / "results" / "primary_visualizations" / "accuracy_by_dataset_query_type_summary.csv"),
        help="CSV path for the dataset/query-type summary.",
    )
    parser.add_argument(
        "--baselines",
        default=",".join(BASELINE_ORDER),
        help="Comma-separated baseline codes to include.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent

    baselines = [value.strip().upper() for value in args.baselines.split(",") if value.strip()]
    results_root = _resolve_user_path(args.results_root, repo_root)
    if results_root is None:
        raise ValueError("--results-root is required")

    df = load_all_metrics(results_root, baselines=baselines)
    summary = aggregate_accuracy_by_dataset_query_type(df, baselines=baselines)

    output = _resolve_user_path(args.output, repo_root)
    if output is None:
        raise ValueError("--output is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output, index=False)
    print(summary.to_string(index=False))
    print(f"\nWrote {output}")


if __name__ == "__main__":
    main()