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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize accuracy by dataset, baseline, and query type."
    )
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--results-root",
        default=str(script_dir.parent / "results" / "with_slm_predictive"),
        help="Root folder containing dataset-level metrics.csv files.",
    )
    parser.add_argument(
        "--output",
        default=str(script_dir / "results" / "primary_visualizations" / "accuracy_by_dataset_query_type_summary.csv"),
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
    baselines = [value.strip().upper() for value in args.baselines.split(",") if value.strip()]
    df = load_all_metrics(Path(args.results_root).resolve(), baselines=baselines)
    summary = aggregate_accuracy_by_dataset_query_type(df, baselines=baselines)

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output, index=False)
    print(summary.to_string(index=False))
    print(f"\nWrote {output}")


if __name__ == "__main__":
    main()