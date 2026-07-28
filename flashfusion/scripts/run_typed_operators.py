"""
Run validated Flash-Fusion typed operators against an in-memory dataset.

Examples:
    python scripts/run_typed_operators.py \
      --data data/ecg.csv \
      --plans eval/typed_plans_ecg.jsonl \
      --output results/ecg_typed_operator_results.jsonl

    python scripts/run_typed_operators.py \
      --data data/wisdm.parquet \
      --plans eval/typed_plans_wisdm.jsonl \
      --output results/wisdm_typed_operator_results.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from flashfusion.pipeline.operators import (
    StructuralValidationError,
    TypedPlan,
    execute_plan,
    validate_plan_against_dataframe,
    PlanSchemaError,
)


def load_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix in {".pkl", ".pickle"}:
        return pd.read_pickle(path)

    raise ValueError(
        f"Unsupported input format: {suffix}. "
        "Use CSV, Parquet, or Pickle."
    )


def json_ready(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Series):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, pd.DataFrame):
        return [
            {str(key): json_ready(item) for key, item in row.items()}
            for row in value.to_dict(orient="records")
        ]
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if pd.isna(value):
        return None
    return value


def approximately_equal(actual: Any, expected: Any, tolerance: float) -> bool | None:
    if expected is None:
        return None

    if isinstance(actual, (int, float, np.number)) and isinstance(
        expected,
        (int, float, np.number),
    ):
        return bool(np.isclose(actual, expected, rtol=tolerance, atol=tolerance))

    return actual == expected


def run_plan(
    df: pd.DataFrame,
    record: dict[str, Any],
    tolerance: float,
) -> dict[str, Any]:
    started = time.perf_counter()

    # Same two gates the live pipeline uses: structural first, then schema.
    try:
        plan = TypedPlan.from_dict(record["plan"])
    except StructuralValidationError as exc:
        return _validation_failure(record, df, "structural", str(exc), started)

    try:
        validate_plan_against_dataframe(plan, df)
    except PlanSchemaError as exc:
        return _validation_failure(record, df, "schema", str(exc), started)

    execution = execute_plan(df, plan)
    latency_ms = (time.perf_counter() - started) * 1000

    actual = json_ready(execution.value)
    expected = record.get("expected")

    return {
        "id": record.get("id"),
        "query": record.get("query"),
        "expected": json_ready(expected),
        "actual": actual,
        "matches_expected": approximately_equal(
            actual,
            expected,
            tolerance=tolerance,
        ),
        "typed_plan": record["plan"],
        "ok": execution.ok,
        "error": execution.error,
        "plan_kind": execution.plan_kind,
        "plan_validation_stage_failed": "" if execution.ok else "execution",
        "operators_used": execution.operators_used,
        "latency_ms": round(latency_ms, 3),
        "rows_scanned": execution.rows_scanned,
        "rows_after_filter": execution.rows_after_filter,
        "retained_row_fraction": (
            round(execution.rows_after_filter / execution.rows_scanned, 8)
            if execution.rows_scanned and execution.rows_after_filter is not None
            else None
        ),
        "columns_used": list(execution.columns_used),
        "full_dataframe_columns": len(df.columns),
        "columns_avoided": max(0, len(df.columns) - len(execution.columns_used)),
    }


def _validation_failure(
    record: dict[str, Any],
    df: pd.DataFrame,
    stage: str,
    error: str,
    started: float,
) -> dict[str, Any]:
    """A plan that fails either gate never touches the data — record the gap."""
    return {
        "id": record.get("id"),
        "query": record.get("query"),
        "expected": json_ready(record.get("expected")),
        "actual": None,
        "matches_expected": False if record.get("expected") is not None else None,
        "typed_plan": record["plan"],
        "ok": False,
        "error": error,
        "plan_kind": "",
        "plan_validation_stage_failed": stage,
        "operators_used": [],
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "rows_scanned": 0,
        "rows_after_filter": None,
        "retained_row_fraction": None,
        "columns_used": [],
        "full_dataframe_columns": len(df.columns),
        "columns_avoided": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--plans", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tolerance", type=float, default=1e-8)
    args = parser.parse_args()

    df = load_dataframe(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    with args.plans.open(encoding="utf-8") as source:
        for line in source:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    results: list[dict[str, Any]] = []
    for record in records:
        results.append(run_plan(df, record, args.tolerance))

    with args.output.open("w", encoding="utf-8") as destination:
        for result in results:
            destination.write(json.dumps(result, default=json_ready) + "\n")

    completed = sum(result["ok"] for result in results)
    compared = [
        result for result in results
        if result["matches_expected"] is not None
    ]
    matched = sum(result["matches_expected"] is True for result in compared)

    print(
        json.dumps(
            {
                "plans": len(results),
                "executed": completed,
                "failed": len(results) - completed,
                "compared_to_expected": len(compared),
                "matched_expected": matched,
                "mean_latency_ms": round(
                    sum(result["latency_ms"] for result in results) / len(results),
                    3,
                ) if results else 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()