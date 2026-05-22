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
    Print a summary LLM-accuracy/latency/cost table to stdout grouped by baseline.

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
    metric_cols = ["latency_s", "cost_usd"]
    if "gt_score" in df.columns:
        metric_cols.insert(0, "gt_score")
    summary = (
        df.groupby("baseline")[metric_cols]
        .mean()
        .reset_index()
    )
    summary = summary.rename(
        columns={
            "baseline": "Baseline",
            "gt_score": "Avg LLM Accuracy",
            "latency_s": "Avg Latency (s)",
            "cost_usd": "Avg Cost (USD)",
        }
    )
    sort_col = "Avg LLM Accuracy" if "Avg LLM Accuracy" in summary.columns else "Avg Latency (s)"
    summary = summary.sort_values(sort_col, ascending=False)
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


def save_markdown(
    results: list[RunResult],
    path: str,
    metrics_df: pd.DataFrame | None = None,
    query_defs: list[dict] | None = None,
) -> None:
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

    if metrics_df is None:
        metrics_df = aggregate_metrics(results, query_defs=query_defs)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = []
    lines.append("# Flash-Fusion Benchmark Report")
    lines.append("")
    lines.append(f"Generated: {timestamp}")
    lines.append("")
    lines.append("## Summary Table")
    lines.append("")
    if not metrics_df.empty:
        metric_cols = ["latency_s", "cost_usd"]
        if "gt_score" in metrics_df.columns:
            metric_cols.insert(0, "gt_score")
        summary = (
            metrics_df.groupby("baseline")[metric_cols]
            .mean()
            .reset_index()
        )
        summary = summary.rename(
            columns={
                "baseline": "Baseline",
                "gt_score": "Avg LLM Accuracy",
                "latency_s": "Avg Latency (s)",
                "cost_usd": "Avg Cost (USD)",
            }
        )
        sort_col = "Avg LLM Accuracy" if "Avg LLM Accuracy" in summary.columns else "Avg Latency (s)"
        summary = summary.sort_values(sort_col, ascending=False)
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

    if query_defs is None:
        query_defs = WISDM_QUERIES
    query_lookup = {q["text"]: q for q in query_defs}
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
            verdict = (
                r.judge_verdict.get("verdict", "N/A")
                if r.judge_verdict
                else "N/A"
            )
            verdict_label = (
                "Plan Judge"
                if "judge_plan" in r.stages_run or "judge_plan_retry" in r.stages_run
                else "Judge"
            )
            lines.append(f"#### {r.baseline}")
            lines.append("")
            lines.append(f"**Answer:** {ans}")
            lines.append("")
            lines.append(
                f"- Executed: {r.executed} | Rejected: {r.rejected} | "
                f"{verdict_label}: {verdict}"
            )
            lines.append(f"- Stages: {','.join(r.stages_run)}")
            if "S3_refine" in r.stages_run:
                lines.append("- Plan refinement: yes (one Stage-3 regeneration)")
            lines.append(f"- Latency: {r.latency_s:.2f}s | Cost: ${r.cost_usd:.5f}")
            lines.append("")

            # Stage 1 — Concept Extraction
            if r.s1_concepts:
                data_c = ", ".join(r.s1_concepts.get("DATA", [])) or "NONE"
                reasoning_c = ", ".join(r.s1_concepts.get("REASONING", [])) or "NONE"
                lines.append("**Stage 1 — Concept Extraction**")
                lines.append("")
                lines.append(f"- DATA: {data_c}")
                lines.append(f"- REASONING: {reasoning_c}")
                lines.append("")

            # Stage 2 — Schema Grounding (full raw LLM output)
            if r.s2_grounding:
                lines.append("**Stage 2 — Schema Grounding**")
                lines.append("")
                lines.append("```")
                lines.append(r.s2_grounding.strip())
                lines.append("```")
                lines.append("")

            # Stage 3 — Sub-query Generation
            if r.s3_sub_queries:
                lines.append("**Stage 3 — Sub-queries**")
                lines.append("")
                for i, sq in enumerate(r.s3_sub_queries, 1):
                    lines.append(f"{i}. {sq}")
                if r.s3_synthesis_hint:
                    lines.append("")
                    lines.append(f"*Synthesis hint: {r.s3_synthesis_hint}*")
                lines.append("")

            # Agent trace (AUTOIOT_ONLY / FLASH_FUSION when executed)
            if r.trace and r.trace.strip() and r.trace.strip() != "(no steps captured)":
                lines.append("**Agent Trace**")
                lines.append("")
                lines.append("```")
                lines.append(r.trace.strip())
                lines.append("```")
                lines.append("")

            # Final code
            if r.final_code:
                lines.append("**Final Code Executed**")
                lines.append("")
                lines.append("```python")
                lines.append(r.final_code.strip())
                lines.append("```")
                lines.append("")

            # Judge details
            if r.judge_verdict and verdict not in ("N/A", "UNKNOWN"):
                issue = r.judge_verdict.get("issue", "")
                suggestion = r.judge_verdict.get("suggestion", "")
                if issue or suggestion:
                    lines.append(f"**{verdict_label} Details**")
                    lines.append("")
                    if issue:
                        lines.append(f"- Issue: {issue}")
                    if suggestion:
                        lines.append(f"- Suggestion: {suggestion}")
                    lines.append("")

            if r.alignment_explanation:
                lines.append("**Alignment Explanation**")
                lines.append("")
                lines.append(r.alignment_explanation)
                lines.append("")

            if r.rejected and r.rejection_reason:
                lines.append("**Rejection Reasoning**")
                lines.append("")
                lines.append(f"- {r.rejection_reason}")
                lines.append("")

            lines.append("---")
            lines.append("")

    lines.append("## Baseline Comparison Notes")
    lines.append("")
    lines.append(
        "- **LLM-Only**: no schema grounding, no execution — may hallucinate."
    )
    lines.append(
        "- **WellMax-Only**: S1+S2+S3 grounding, then direct grounded execution (no guardrail, no judge)."
    )
    lines.append(
        "- **AutoIOT-Only**: raw-query pandas execution only (no guardrail, no codebook grounding)."
    )
    lines.append(
        "- **Flash-Fusion**: full grounding pipeline + guardrail + pre-agent plan judge (+ one refinement) + grounded execution."
    )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
