#!/usr/bin/env python3
"""Diagnose latency-segment attribution for FLASH_FUSION_CACHE runs.

Companion to `latencystages.py`. Where `latencystages.py` plots the primary
stage-latency figures, this script answers two narrower debugging questions:

1. Why do individual FLASH_FUSION_CACHE latency segments fail to tile
   cleanly against the row's total latency ("not cookie cuts of each
   other")?
2. Is a FLASH_FUSION_CACHE cache HIT ever slower than the full FLASH_FUSION
   planner (no cache) running the *same query on the same dataset*? A hit
   should always beat the full planner -- it skips S1/S2/S3 planning and the
   agent loop entirely, paying only cache_lookup + cache_grounding +
   cache_validation + typed_exec. If a hit is slower than the planner, that
   is a real regression worth investigating, unlike hit-vs-miss latency
   (which compares two structurally different code paths and is not
   expected to be symmetric).

Background (see `flashfusion/baselines/flash_fusion_cache.py` and
`flashfusion/pipeline/runner.py`):

- A FLASH_FUSION_CACHE HIT runs: exact/semantic_cache_hit ->
  cache_light_grounding -> cache_plan_validated -> typed_exec. Instrumented
  stages: `cache_lookup`, `cache_grounding`, `cache_validation`, `typed_exec`.
- A FLASH_FUSION_CACHE MISS runs: cache_miss_or_validation_failure ->
  guardrail_plan -> plan_validated -> typed_exec. Instrumented stages:
  `cache_lookup`, `guardrail`, `typed_exec` (no cache_grounding/validation).
- The full FLASH_FUSION planner (baseline="FLASH_FUSION", no cache lookup at
  all) runs the S1/S2/S3 typed-operator pipeline and/or the ReAct agent
  fallback. Instrumented stages: `s1`, `s2`, `s3`, `guardrail`, `agent`,
  `typed_exec`.

Because these are three different code branches, only (2) -- cache hit vs.
full planner -- is a fair "should always win" comparison. Hit vs. miss is
NOT a fair comparison (different stages fire), which is why this script
reports that asymmetry separately instead of treating it as a regression.

Usage:
    python -m flashfusion.viz.latencydebug \
        --cache-results-dir flashfusion/results/ff_hybrid_cache/FLASH_FUSION_CACHE \
        --planner-results-dir flashfusion/results/ff_and_react_qwen/FLASH_FUSION \
        --output-dir flashfusion/viz/latencydebug

    # or point directly at single metrics.csv files
    python -m flashfusion.viz.latencydebug \
        --cache-metrics-csv path/to/cache_metrics.csv \
        --planner-metrics-csv path/to/planner_metrics.csv
"""

from __future__ import annotations

import argparse
import glob
import os
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except ImportError:  # pragma: no cover
    HAVE_MPL = False

# Stage columns instrumented across flash_fusion_cache.py / runner.py / metrics.py.
STAGE_COLUMNS = [
    "guardrail_latency_s",
    "cache_lookup_latency_s",
    "cache_grounding_latency_s",
    "cache_validation_latency_s",
    "cache_rejection_latency_s",
    "typed_exec_latency_s",
    "agent_latency_s",
]

# Which stages are *expected* to be populated for each execution branch.
HIT_STAGES = {
    "cache_lookup_latency_s",
    "cache_grounding_latency_s",
    "cache_validation_latency_s",
    "typed_exec_latency_s",
}
MISS_STAGES = {
    "cache_lookup_latency_s",
    "guardrail_latency_s",
    "typed_exec_latency_s",
}
REJECT_STAGES = {
    "cache_lookup_latency_s",
    "cache_rejection_latency_s",
}
PLANNER_STAGES = {
    "guardrail_latency_s",
    "typed_exec_latency_s",
    "agent_latency_s",
}

_RUN_DIR_RE = re.compile(r"^run_\d+$")
RETRY_OVERHEAD_COL = "cache_retry_overhead_s"


def _infer_dataset(path: str) -> str:
    """Infer the dataset name (bus/mit_ecg/wisdm/...) from a metrics.csv path.

    Layout is <results_root>/<BASELINE>/<dataset>/metrics.csv or
    <results_root>/<BASELINE>/<dataset>/run_N/metrics.csv.
    """
    parts = os.path.normpath(path).split(os.sep)
    parts = parts[:-1]  # drop 'metrics.csv'
    if parts and _RUN_DIR_RE.match(parts[-1]):
        parts = parts[:-1]  # drop 'run_N'
    return parts[-1] if parts else "unknown"


def _find_metrics_csvs(results_dir: str) -> list[str]:
    """Collect metrics.csv files under results_dir, one per dataset.

    Prefers the dataset-level aggregated metrics.csv (which already spans
    all runs, with a `run_id` column) over the per-run metrics.csv files, to
    avoid double-counting the same rows twice.
    """
    if not os.path.isdir(results_dir):
        return []

    # Direct metrics.csv right under results_dir (single-dataset layout).
    direct = os.path.join(results_dir, "metrics.csv")
    if os.path.exists(direct):
        return [direct]

    paths: list[str] = []
    for entry in sorted(os.listdir(results_dir)):
        dataset_dir = os.path.join(results_dir, entry)
        if not os.path.isdir(dataset_dir):
            continue
        top_level = os.path.join(dataset_dir, "metrics.csv")
        if os.path.exists(top_level):
            paths.append(top_level)
            continue
        # Fall back to per-run files only if no aggregated file exists.
        per_run = sorted(glob.glob(os.path.join(dataset_dir, "run_*", "metrics.csv")))
        paths.extend(per_run)

    if not paths:
        # Last resort: recursive search in case the layout differs entirely.
        paths = sorted(glob.glob(os.path.join(results_dir, "**", "metrics.csv"), recursive=True))
    return paths

def _normalize_cache_outcome(row: pd.Series) -> str:
    """Derive outcome from execution semantics to avoid stale metrics.csv labels."""
    path = str(row.get("execution_path", "") or "")
    plan_source = str(row.get("plan_source", "") or "")
    baseline = str(row.get("baseline", "") or "")

    if baseline != "FLASH_FUSION_CACHE":
        raw = str(row.get("cache_outcome", "") or "").strip()
        return raw if raw else "not_applicable"

    # Highest-confidence signals first
    if path == "guardrail_reject":
        # cache lookup found an out-of-scope template, then rejected
        if "out_of_scope" in plan_source:
            return "hit_rejected"
        return "hit_rejected"

    if path == "typed_operator_cache":
        return "hit"

    if path == "typed_operator":
        return "miss"

    # Conservative fallback for cache baseline: unknown path is not a proven hit.
    return "miss"

def _apply_cache_outcome_normalization(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "cache_outcome" not in out.columns:
        out["cache_outcome"] = "not_applicable"
    out["cache_outcome_raw"] = out["cache_outcome"].astype(str)
    out["cache_outcome"] = out.apply(_normalize_cache_outcome, axis=1)
    out["cache_outcome_mismatch"] = out["cache_outcome_raw"] != out["cache_outcome"]
    return out

def load_metrics(results_dir: str | None, metrics_csv: str | None, label: str) -> pd.DataFrame:
    if metrics_csv:
        paths = [metrics_csv]
    else:
        if results_dir is None:
            raise ValueError(
                f"No {label} metrics path provided. Pass --{label}-results-dir or --{label}-metrics-csv."
            )
        paths = _find_metrics_csvs(results_dir)
    if not paths:
        raise FileNotFoundError(
            f"No metrics.csv found for {label} under {results_dir!r}. "
            f"Pass --{label}-metrics-csv explicitly."
        )

    frames = []
    for path in paths:
        df = pd.read_csv(path)
        df["source_file"] = path
        df["dataset"] = _infer_dataset(path)
        if "run_id" not in df.columns:
            df["run_id"] = 1
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)

    for col in STAGE_COLUMNS + ["latency_s"]:
        if col not in combined.columns:
            combined[col] = 0.0
        combined[col] = pd.to_numeric(combined[col], errors="coerce").fillna(0.0)
    if RETRY_OVERHEAD_COL not in combined.columns:
        combined[RETRY_OVERHEAD_COL] = 0.0
    combined[RETRY_OVERHEAD_COL] = pd.to_numeric(
        combined[RETRY_OVERHEAD_COL], errors="coerce"
    ).fillna(0.0)

    if "cache_outcome" not in combined.columns:
        combined["cache_outcome"] = "not_applicable"

    # Normalize stale labels in historic artifacts.
    combined = _apply_cache_outcome_normalization(combined)

    return combined


def reconcile_segments(df: pd.DataFrame) -> pd.DataFrame:
    """Compute stage-sum vs. total latency_s and flag rows where segments
    don't tile the total ("not cookie cuts")."""
    out = df.copy()
    out["stage_sum_s"] = out[STAGE_COLUMNS].sum(axis=1)
    out["unaccounted_s"] = out["latency_s"] - out["stage_sum_s"]
    out["unaccounted_pct"] = np.where(
        out["latency_s"] > 0,
        100.0 * out["unaccounted_s"] / out["latency_s"],
        0.0,
    )

    def _expected_stages(row) -> set[str]:
        path = str(row.get("execution_path", ""))
        outcome = str(row.get("cache_outcome", ""))
        if path == "guardrail_reject":
            return REJECT_STAGES
        if path == "typed_operator_cache" or outcome == "hit":
            return HIT_STAGES
        if outcome == "hit_rejected":
            return REJECT_STAGES
        if outcome in ("miss",):
            return MISS_STAGES
        return PLANNER_STAGES

    def _stray_stage_mass(row) -> float:
        expected = _expected_stages(row)
        stray_cols = [c for c in STAGE_COLUMNS if c not in expected]
        return float(sum(row[c] for c in stray_cols))

    out["expected_stage_set"] = out.apply(
        lambda r: ",".join(sorted(_expected_stages(r))), axis=1
    )
    out["stray_stage_latency_s"] = out.apply(_stray_stage_mass, axis=1)
    return out


def _effective_cache_latency_s(df: pd.DataFrame) -> pd.Series:
    """Fair comparison latency for cache rows, excluding provider retry overhead."""
    if RETRY_OVERHEAD_COL not in df.columns:
        return df["latency_s"].astype(float)
    adjusted = df["latency_s"].astype(float) - df[RETRY_OVERHEAD_COL].astype(float)
    return adjusted.clip(lower=0.0)


def hit_vs_miss_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-stage mean/median/std split by cache_outcome.

    NOTE: hit, hit_rejected, and miss walk different code branches with different
    instrumented stages, so this is context, not a "should match" claim.
    """
    subset = df[df["cache_outcome"].isin(["hit", "hit_rejected", "miss"])].copy()
    if subset.empty:
        return pd.DataFrame()
    agg_cols = STAGE_COLUMNS + ["latency_s", "stage_sum_s", "unaccounted_s"]
    grouped = subset.groupby("cache_outcome")[agg_cols].agg(["mean", "median", "std", "count"])
    grouped.columns = ["_".join(c) for c in grouped.columns]
    return grouped.reset_index()


def find_hit_slower_than_planner(cache_df: pd.DataFrame, planner_df: pd.DataFrame) -> pd.DataFrame:
    """Compare each FLASH_FUSION_CACHE HIT against the average full-planner
    (FLASH_FUSION, no cache) latency for the same (dataset, query_id).

    Unlike hit-vs-miss, a hit walking cache_lookup -> cache_grounding ->
    cache_validation -> typed_exec should *always* be faster than the full
    planner walking s1 -> s2 -> s3 -> (guardrail | agent) -> typed_exec,
    since the planner strictly does more work. Any row where a hit is
    slower is flagged with a stage-by-stage explanation.
    """
    cache_df = cache_df.copy()
    if RETRY_OVERHEAD_COL not in cache_df.columns:
        cache_df[RETRY_OVERHEAD_COL] = 0.0

    if "execution_path" in cache_df.columns:
        is_typed_cache = cache_df["execution_path"] == "typed_operator_cache"
    else:
        is_typed_cache = pd.Series(False, index=cache_df.index)
    hits = cache_df[(cache_df["cache_outcome"] == "hit") & is_typed_cache].copy()
    if hits.empty or "query_id" not in hits.columns:
        return pd.DataFrame()
    if "query_id" not in planner_df.columns:
        return pd.DataFrame()

    planner_avg = (
        planner_df.groupby(["dataset", "query_id"])["latency_s"]
        .mean()
        .rename("planner_avg_latency_s")
        .reset_index()
    )
    planner_stage_avg = (
        planner_df.groupby(["dataset", "query_id"])[
            ["guardrail_latency_s", "typed_exec_latency_s", "agent_latency_s"]
        ]
        .mean()
        .rename(columns=lambda c: f"planner_avg_{c}")
        .reset_index()
    )

    merged = hits.merge(planner_avg, on=["dataset", "query_id"], how="inner")
    merged = merged.merge(planner_stage_avg, on=["dataset", "query_id"], how="left")
    if merged.empty:
        return merged

    merged["effective_latency_s"] = _effective_cache_latency_s(merged)

    merged["hit_minus_planner_avg_s"] = merged["effective_latency_s"] - merged["planner_avg_latency_s"]
    merged["speedup_x"] = np.where(
        merged["effective_latency_s"] > 0,
        merged["planner_avg_latency_s"] / merged["effective_latency_s"],
        np.nan,
    )
    flagged = merged[merged["hit_minus_planner_avg_s"] > 0].copy()

    keep_cols = [
        "dataset", "run_id", "query_id",
        "latency_s", "effective_latency_s", RETRY_OVERHEAD_COL,
        "planner_avg_latency_s", "hit_minus_planner_avg_s", "speedup_x",
        "cache_lookup_latency_s", "cache_grounding_latency_s",
        "cache_validation_latency_s", "typed_exec_latency_s",
        "planner_avg_guardrail_latency_s", "planner_avg_agent_latency_s",
        "planner_avg_typed_exec_latency_s",
        "execution_path", "plan_source",
    ]
    keep_cols = [c for c in keep_cols if c in flagged.columns]
    return flagged[keep_cols].sort_values("hit_minus_planner_avg_s", ascending=False)


def cache_vs_planner_speedup_summary(cache_df: pd.DataFrame, planner_df: pd.DataFrame) -> pd.DataFrame:
    """Per-dataset average latency for cache hit / hit_rejected / cache miss / full planner,
    plus the hit-over-planner speedup factor."""
    cache_df = cache_df.copy()
    if RETRY_OVERHEAD_COL not in cache_df.columns:
        cache_df[RETRY_OVERHEAD_COL] = 0.0

    rows = []
    for dataset in sorted(set(cache_df["dataset"]) | set(planner_df["dataset"])):
        cache_ds = cache_df[cache_df["dataset"] == dataset].copy()
        cache_ds["effective_latency_s"] = _effective_cache_latency_s(cache_ds)

        hit_raw = cache_ds[cache_ds["cache_outcome"] == "hit"]["latency_s"]
        hit_fair = cache_ds[cache_ds["cache_outcome"] == "hit"]["effective_latency_s"]
        hit_rejected_raw = cache_ds[cache_ds["cache_outcome"] == "hit_rejected"]["latency_s"]
        hit_rejected_fair = cache_ds[cache_ds["cache_outcome"] == "hit_rejected"]["effective_latency_s"]
        miss_raw = cache_ds[cache_ds["cache_outcome"] == "miss"]["latency_s"]
        miss_fair = cache_ds[cache_ds["cache_outcome"] == "miss"]["effective_latency_s"]
        planner_lat = planner_df[planner_df["dataset"] == dataset]["latency_s"]
        hit_mean = float(hit_fair.mean()) if not hit_fair.empty else float("nan")
        planner_mean = float(planner_lat.mean()) if not planner_lat.empty else float("nan")
        rows.append({
            "dataset": dataset,
            "n_hit": int(hit_fair.count()),
            "n_hit_rejected": int(hit_rejected_fair.count()),
            "n_miss": int(miss_fair.count()),
            "n_planner": int(planner_lat.count()),
            "hit_mean_latency_s": hit_mean,
            "hit_mean_latency_s_raw": float(hit_raw.mean()) if not hit_raw.empty else float("nan"),
            "hit_rejected_mean_latency_s": float(hit_rejected_fair.mean()) if not hit_rejected_fair.empty else float("nan"),
            "hit_rejected_mean_latency_s_raw": float(hit_rejected_raw.mean()) if not hit_rejected_raw.empty else float("nan"),
            "miss_mean_latency_s": float(miss_fair.mean()) if not miss_fair.empty else float("nan"),
            "miss_mean_latency_s_raw": float(miss_raw.mean()) if not miss_raw.empty else float("nan"),
            "cache_retry_overhead_mean_s": float(cache_ds[RETRY_OVERHEAD_COL].mean()) if not cache_ds.empty else float("nan"),
            "planner_mean_latency_s": planner_mean,
            "hit_speedup_vs_planner_x": (planner_mean / hit_mean) if hit_mean and hit_mean > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


def plot_cache_vs_planner(cache_df: pd.DataFrame, planner_df: pd.DataFrame, output_dir: str) -> str | None:
    if not HAVE_MPL:
        return None
    summary = cache_vs_planner_speedup_summary(cache_df, planner_df)
    if summary.empty:
        return None

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(summary))
    width = 0.25
    ax.bar(x - width, summary["hit_mean_latency_s"], width, label="cache hit", color="#2ca02c")
    ax.bar(x, summary["miss_mean_latency_s"], width, label="cache miss", color="#ff7f0e")
    ax.bar(x + width, summary["planner_mean_latency_s"], width, label="full planner (FLASH_FUSION)", color="#1f77b4")

    ax.set_xticks(x)
    ax.set_xticklabels(summary["dataset"])
    ax.set_ylabel("Mean latency (s)")
    ax.set_title("FLASH_FUSION_CACHE (hit/miss) vs. full FLASH_FUSION planner")
    ax.legend()
    plt.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "latencydebug_cache_vs_planner.png")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


@dataclass
class DebugReport:
    reconciled: pd.DataFrame
    hit_vs_miss: pd.DataFrame
    hit_slower_than_planner: pd.DataFrame
    speedup_summary: pd.DataFrame


def run_debug(
    cache_results_dir: str | None,
    cache_metrics_csv: str | None,
    planner_results_dir: str | None,
    planner_metrics_csv: str | None,
    output_dir: str,
) -> tuple[DebugReport, str | None]:
    cache_df = load_metrics(cache_results_dir, cache_metrics_csv, label="cache")
    planner_df = load_metrics(planner_results_dir, planner_metrics_csv, label="planner")

    reconciled = reconcile_segments(cache_df)
    hvm = hit_vs_miss_summary(reconciled)
    flagged = find_hit_slower_than_planner(reconciled, planner_df)
    speedup = cache_vs_planner_speedup_summary(reconciled, planner_df)

    os.makedirs(output_dir, exist_ok=True)
    reconciled.to_csv(os.path.join(output_dir, "latencydebug_reconciled.csv"), index=False)
    if not hvm.empty:
        hvm.to_csv(os.path.join(output_dir, "latencydebug_hit_vs_miss_summary.csv"), index=False)
    if not flagged.empty:
        flagged.to_csv(os.path.join(output_dir, "latencydebug_hit_slower_than_planner.csv"), index=False)
    if not speedup.empty:
        speedup.to_csv(os.path.join(output_dir, "latencydebug_cache_vs_planner_speedup.csv"), index=False)

    plot_path = plot_cache_vs_planner(reconciled, planner_df, output_dir)

    return DebugReport(reconciled, hvm, flagged, speedup), plot_path


def _print_report(report: DebugReport, plot_path: str | None) -> None:
    df = report.reconciled
    print("=" * 80)
    print("LATENCY SEGMENT RECONCILIATION (FLASH_FUSION_CACHE)")
    print("=" * 80)
    bad = df[df["unaccounted_pct"].abs() > 5.0]
    print(f"Rows checked: {len(df)}  |  Rows with >5% unaccounted latency: {len(bad)}")
    if not bad.empty:
        cols = [c for c in ["dataset", "run_id", "query_id", "cache_outcome", "execution_path",
                             "latency_s", "stage_sum_s", "unaccounted_s", "unaccounted_pct"]
                if c in bad.columns]
        print(bad[cols].sort_values("unaccounted_pct", ascending=False).head(15).to_string(index=False))

    stray = df[df["stray_stage_latency_s"] > 1e-6]
    print(f"\nRows with latency in stages outside their expected branch set: {len(stray)}")
    if not stray.empty:
        cols = [c for c in ["dataset", "run_id", "query_id", "cache_outcome", "execution_path",
                             "expected_stage_set", "stray_stage_latency_s"]
                if c in stray.columns]
        print(stray[cols].sort_values("stray_stage_latency_s", ascending=False).head(10).to_string(index=False))

    print("\n" + "=" * 80)
    print("CACHE OUTCOME STAGE SUMMARY (context only -- different code paths, not expected to match)")
    print("=" * 80)
    if report.hit_vs_miss.empty:
        print("No hit/hit_rejected/miss rows found (cache_outcome column missing or empty).")
    else:
        print(report.hit_vs_miss.to_string(index=False))

    print("\n" + "=" * 80)
    print("CACHE-HIT vs FULL-PLANNER SPEEDUP (per dataset, retry-overhead-adjusted cache latency)")
    print("=" * 80)
    if report.speedup_summary.empty:
        print("No overlapping dataset/query data between cache and planner results.")
    else:
        print(report.speedup_summary.to_string(index=False))

    print("\n" + "=" * 80)
    print("CACHE HITS SLOWER THAN THE FULL PLANNER FOR THE SAME (dataset, query_id)")
    print("=" * 80)
    if report.hit_slower_than_planner.empty:
        print("None found: every cache hit beat the average full-planner latency for its query. Expected.")
    else:
        print("REGRESSION: the following cache hits were slower than the full planner they should beat:")
        print(report.hit_slower_than_planner.to_string(index=False))

    if "cache_outcome_mismatch" in df.columns:
        n_mismatch = int(df["cache_outcome_mismatch"].sum())
        print(f"\nNormalized cache_outcome mismatches vs raw CSV labels: {n_mismatch}")
        if n_mismatch > 0:
            cols = [c for c in [
                "dataset", "run_id", "query_id", "execution_path", "plan_source",
                "cache_outcome_raw", "cache_outcome"
            ] if c in df.columns]
            print(df[df["cache_outcome_mismatch"]][cols].head(15).to_string(index=False))

    if plot_path:
        print(f"\nCache-vs-planner comparison chart written to: {plot_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose FLASH_FUSION_CACHE latency-segment attribution and verify "
            "cache hits are faster than the full FLASH_FUSION planner."
        )
    )
    parser.add_argument(
        "--cache-results-dir",
        default="flashfusion/results/ff_hybrid_cache/FLASH_FUSION_CACHE",
        help="Directory containing FLASH_FUSION_CACHE metrics.csv (per-dataset or run_*/metrics.csv subdirs).",
    )
    parser.add_argument(
        "--cache-metrics-csv",
        default=None,
        help="Path to a single FLASH_FUSION_CACHE metrics.csv, overriding --cache-results-dir discovery.",
    )
    parser.add_argument(
        "--planner-results-dir",
        default="flashfusion/results/ff_and_react_qwen/FLASH_FUSION",
        help="Directory containing the full FLASH_FUSION planner's metrics.csv (per-dataset or run_*/metrics.csv subdirs).",
    )
    parser.add_argument(
        "--planner-metrics-csv",
        default=None,
        help="Path to a single full-planner metrics.csv, overriding --planner-results-dir discovery.",
    )
    parser.add_argument(
        "--output-dir",
        default="flashfusion/viz/data/latencydebug",
        help="Directory to write reconciled CSVs and the comparison chart.",
    )
    args = parser.parse_args()

    report, plot_path = run_debug(
        args.cache_results_dir,
        args.cache_metrics_csv,
        args.planner_results_dir,
        args.planner_metrics_csv,
        args.output_dir,
    )
    _print_report(report, plot_path)


if __name__ == "__main__":
    main()
