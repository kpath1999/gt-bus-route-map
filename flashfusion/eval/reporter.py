"""
eval/reporter.py — Output formatting and file writing for benchmark results.

Functions:
  print_table(df)              → None  (prints summary to stdout using tabulate)
  save_csv(df, path)           → None  (writes metrics DataFrame to CSV)
  save_markdown(results, path) → None  (writes human-readable report.md)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
from tabulate import tabulate

from flashfusion.pipeline.runner import RunResult


def print_table(df: pd.DataFrame) -> None:
    """
    Print a summary accuracy/latency/cost table to stdout grouped by baseline.

    Args:
        df: DataFrame as returned by aggregate_metrics().

    Implementation:
        summary = (
            df.groupby("baseline")[["accuracy_score", "latency_s", "cost_usd"]]
            .mean()
            .reset_index()
        )
        summary.columns = ["Baseline", "Avg Accuracy", "Avg Latency (s)", "Avg Cost (USD)"]
        summary = summary.sort_values("Avg Accuracy", ascending=False)
        print(tabulate(summary, headers="keys", tablefmt="github",
                       floatfmt=(".0s", ".4f", ".2f", ".5f"), showindex=False))
    """
    if df.empty:
        print("(no results to summarise)")
        return
    summary = (
        df.groupby("baseline")[["accuracy_score", "latency_s", "cost_usd"]]
        .mean()
        .reset_index()
    )
    summary.columns = [
        "Baseline",
        "Avg Accuracy",
        "Avg Latency (s)",
        "Avg Cost (USD)",
    ]
    summary = summary.sort_values("Avg Accuracy", ascending=False)
    print(
        tabulate(
            summary,
            headers="keys",
            tablefmt="github",
            floatfmt=".4f",
            showindex=False,
        )
    )


def save_csv(df: pd.DataFrame, path: str) -> None:
    """
    Write the aggregate metrics DataFrame to a CSV file.

    Args:
        df:   DataFrame as returned by aggregate_metrics().
        path: Absolute or relative file path (e.g. "eval_results/metrics.csv").

    Implementation:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_csv(path, index=False)
    """
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    df.to_csv(path, index=False)


def save_markdown(results: list[RunResult], path: str) -> None:
    """
    Write a comprehensive human-readable Markdown report.

    Report structure:
        # Flash-Fusion Benchmark Report
        Generated: <ISO timestamp>

        ## Summary Table
        <tabulate aggregate metrics, one row per baseline>

        ## Per-Query Results
        ### Q{id}: {query text}
        For each baseline:
          - **{baseline}**: {answer[:300]}...
          - Executed: {executed} | Rejected: {rejected} | Judge: {judge_verdict}
          - Stages: {stages_run}

        ## Baseline Comparison Notes
        <static text from CLAUDE.md explaining what each baseline shows>

    Args:
        results: List of RunResult objects from run_benchmark().
        path:    Absolute or relative file path (e.g. "eval_results/report.md").

    Implementation notes:
        - Group results by query text, then by baseline.
        - Truncate answer to 300 chars with "..." if longer.
        - Use tabulate for the summary table (tablefmt="pipe" for GitHub Markdown).
        - Write to path with os.makedirs for missing directories.
    """
    from flashfusion.eval.metrics import aggregate_metrics
    from flashfusion.eval.queries import WISDM_QUERIES

    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    metrics_df = aggregate_metrics(results)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = []
    lines.append("# Flash-Fusion Benchmark Report")
    lines.append("")
    lines.append(f"Generated: {timestamp}")
    lines.append("")
    lines.append("## Summary Table")
    lines.append("")
    if not metrics_df.empty:
        summary = (
            metrics_df.groupby("baseline")[
                ["accuracy_score", "latency_s", "cost_usd"]
            ]
            .mean()
            .reset_index()
            .sort_values("accuracy_score", ascending=False)
        )
        summary.columns = [
            "Baseline",
            "Avg Accuracy",
            "Avg Latency (s)",
            "Avg Cost (USD)",
        ]
        lines.append(
            tabulate(
                summary,
                headers="keys",
                tablefmt="pipe",
                floatfmt=".4f",
                showindex=False,
            )
        )
    else:
        lines.append("(no results)")
    lines.append("")

    query_lookup = {q["text"]: q for q in WISDM_QUERIES}
    grouped: dict[str, list[RunResult]] = {}
    for r in results:
        grouped.setdefault(r.query, []).append(r)

    lines.append("## Per-Query Results")
    lines.append("")
    for query_text, group in grouped.items():
        qdef = query_lookup.get(query_text)
        qid = qdef["id"] if qdef else "?"
        lines.append(f"### Q{qid}: {query_text}")
        lines.append("")
        for r in group:
            ans = (r.answer or "").strip()
            if len(ans) > 300:
                ans = ans[:300] + "..."
            verdict = (
                r.judge_verdict.get("verdict", "N/A")
                if r.judge_verdict
                else "N/A"
            )
            lines.append(f"- **{r.baseline}**: {ans}")
            lines.append(
                f"  - Executed: {r.executed} | Rejected: {r.rejected} | "
                f"Judge: {verdict}"
            )
            lines.append(f"  - Stages: {','.join(r.stages_run)}")
            lines.append("")

    lines.append("## Baseline Comparison Notes")
    lines.append("")
    lines.append(
        "- **LLM-Only**: no schema grounding, no execution — may hallucinate."
    )
    lines.append(
        "- **WellMax-Only**: full rewriting pipeline, describes computation; no execution."
    )
    lines.append(
        "- **AutoIOT-Only**: pandas execution but no codebook or derived features."
    )
    lines.append(
        "- **Flash-Fusion**: full pipeline + per-sub-query execution + judge."
    )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
