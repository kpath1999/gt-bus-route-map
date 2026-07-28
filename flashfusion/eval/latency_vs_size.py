from __future__ import annotations

"""
latency_vs_size.py

Study goal:
    Isolate the dataset-size effect inside the safe execution segment that sits
    between LLM code generation and LLM answer synthesis in ExecutionLayer:

      process spawn + data transfer + worker-side df.copy() + pandas execution

This script does not call any LLMs. It reuses the safe execution mechanics from
pipeline/executor.py (spawned process, isolated worker, df copy) and runs
deterministic code snippets over controlled row-count slices.

Example:
    python -m flashfusion.eval.latency_vs_size \
      --dataset mit_ecg \
      --data data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt \
      --sizes 2000,20000,200000,2000000,10000000,20000000 \
      --repeats 7 \
      --workload all \
      --output flashfusion/results/latency_vs_size_ecg
"""

import argparse
import contextlib
import io
import math
import multiprocessing as mp
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from queue import Empty
from typing import Any

import pandas as pd

from flashfusion.eval.queries import DATASET_BUS, DATASET_MIT_ECG, DATASET_WISDM
from flashfusion.pipeline.loader import load_dataset_by_name


DEFAULT_DATA_PATHS = {
    DATASET_WISDM: "data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt",
    DATASET_MIT_ECG: "data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt",
    DATASET_BUS: "data/bus/bus_data_enriched_behavior.csv",
}


WORKLOADS: dict[str, dict[str, str]] = {
    DATASET_MIT_ECG: {
        "light": "result = int((df['record_id'] == 106).sum())",
        "report_like": "result = int((df.loc[df['record_id'] == 106, 'MLII'] > 0).sum())",
        "heavy": (
            "import numpy as np\n"
            "s = df.loc[df['record_id'] == 106, 'MLII']\n"
            "result = float(np.sqrt(np.mean(s ** 2)))"
        ),
    },
    DATASET_WISDM: {
        "light": "result = int((df['subject_id'] == 15).sum())",
        "report_like": "result = float(df[(df['subject_id'] == 5) & (df['activity_label'] == 'Sitting')]['y'].mean())",
        "heavy": (
            "import numpy as np\n"
            "dynamic = ['Jogging', 'Walking', 'Upstairs', 'Downstairs']\n"
            "rest = ['Sitting', 'Standing']\n"
            "mag = np.sqrt(df['x'] ** 2 + df['y'] ** 2 + df['z'] ** 2)\n"
            "dynamic_mean = float(mag[df['activity_label'].isin(dynamic)].mean())\n"
            "rest_mean = float(mag[df['activity_label'].isin(rest)].mean())\n"
            "result = dynamic_mean - rest_mean"
        ),
    },
    DATASET_BUS: {
        "light": "result = int(len(df))",
        "report_like": "result = float(df['instability_score'].mean())",
        "heavy": (
            "tmp = df.groupby('behavior')['instability_score'].mean().sort_values(ascending=False)\n"
            "result = tmp.index[0]"
        ),
    },
}


@dataclass
class TrialRecord:
    dataset: str
    workload: str
    target_rows: int
    actual_rows: int
    trial: int
    ok: bool
    output: str
    total_safe_segment_s: float
    parent_startup_transfer_overhead_s: float
    worker_total_s: float
    worker_copy_s: float
    worker_exec_s: float
    worker_output_marshalling_s: float


def _pctl(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = (len(ordered) - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return ordered[lo]
    weight = idx - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _safe_exec_worker_profiled(code: str, df: pd.DataFrame, output_queue) -> None:
    """Execute generated code in a subprocess and emit timing components."""
    t_worker_0 = time.time()

    t_copy_0 = time.time()
    local_df = df.copy()
    t_copy_1 = time.time()

    local_ns: dict[str, Any] = {"df": local_df, "pd": pd, "result": None}
    stdout_buffer = io.StringIO()

    try:
        t_exec_0 = time.time()
        with contextlib.redirect_stdout(stdout_buffer):
            exec(code, {"__builtins__": __builtins__}, local_ns)
        t_exec_1 = time.time()

        result_obj = local_ns.get("result")
        std_out = stdout_buffer.getvalue().strip()
        if result_obj is None:
            rendered = std_out if std_out else "(no result produced)"
        else:
            rendered = str(result_obj)

        t_put_0 = time.time()
        output_queue.put(
            {
                "ok": True,
                "output": rendered,
                "worker_total_s": time.time() - t_worker_0,
                "worker_copy_s": t_copy_1 - t_copy_0,
                "worker_exec_s": t_exec_1 - t_exec_0,
                "worker_output_marshalling_s": time.time() - t_put_0,
            }
        )
    except Exception as exc:
        t_exec_1 = time.time()
        t_put_0 = time.time()
        output_queue.put(
            {
                "ok": False,
                "output": f"{type(exc).__name__}: {exc}",
                "worker_total_s": time.time() - t_worker_0,
                "worker_copy_s": t_copy_1 - t_copy_0,
                "worker_exec_s": t_exec_1 - t_copy_1,
                "worker_output_marshalling_s": time.time() - t_put_0,
            }
        )


def _run_safe_segment_profiled(df: pd.DataFrame, code: str, timeout_s: float, trial_label: str = "") -> dict[str, Any]:
    """
    Mirror ExecutionLayer._run_safe_code and profile timing components.

    total_safe_segment_s includes everything between code availability and
    receiving execution output in the parent process.
    """
    t0 = time.time()
    if trial_label:
        print(f"  {trial_label}: spawning worker process...", flush=True)
    ctx = mp.get_context("spawn")
    output_queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(
        target=_safe_exec_worker_profiled,
        args=(code, df, output_queue),
        daemon=True,
    )

    try:
        proc.start()
        proc.join(timeout=timeout_s)

        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=1.0)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=1.0)
            return {
                "ok": False,
                "output": (
                    "TimeoutError: Safe code execution exceeded "
                    f"{timeout_s:.1f}s and was terminated"
                ),
                "total_safe_segment_s": time.time() - t0,
                "worker_total_s": 0.0,
                "worker_copy_s": 0.0,
                "worker_exec_s": 0.0,
                "worker_output_marshalling_s": 0.0,
                "parent_startup_transfer_overhead_s": time.time() - t0,
            }

        try:
            payload = output_queue.get_nowait()
        except Empty:
            return {
                "ok": False,
                "output": (
                    "RuntimeError: Safe execution process exited without output "
                    f"(exit_code={proc.exitcode})"
                ),
                "total_safe_segment_s": time.time() - t0,
                "worker_total_s": 0.0,
                "worker_copy_s": 0.0,
                "worker_exec_s": 0.0,
                "worker_output_marshalling_s": 0.0,
                "parent_startup_transfer_overhead_s": time.time() - t0,
            }

        total_s = time.time() - t0
        worker_total_s = float(payload.get("worker_total_s", 0.0))
        parent_overhead_s = max(total_s - worker_total_s, 0.0)

        if trial_label and payload["ok"]:
            print(
                f"  {trial_label}: completed in {total_s:.3f}s "
                f"(overhead={parent_overhead_s:.3f}s, copy={payload.get('worker_copy_s', 0.0):.3f}s, exec={payload.get('worker_exec_s', 0.0):.3f}s)",
                flush=True,
            )
        elif trial_label and not payload["ok"]:
            print(f"  {trial_label}: FAILED - {payload['output'][:80]}", flush=True)

        return {
            "ok": bool(payload.get("ok", False)),
            "output": str(payload.get("output", "")),
            "total_safe_segment_s": total_s,
            "worker_total_s": worker_total_s,
            "worker_copy_s": float(payload.get("worker_copy_s", 0.0)),
            "worker_exec_s": float(payload.get("worker_exec_s", 0.0)),
            "worker_output_marshalling_s": float(
                payload.get("worker_output_marshalling_s", 0.0)
            ),
            "parent_startup_transfer_overhead_s": parent_overhead_s,
        }
    finally:
        output_queue.close()


def _parse_sizes(raw: str) -> list[int]:
    sizes = [int(x.strip()) for x in raw.split(",") if x.strip()]
    if not sizes:
        raise ValueError("--sizes must contain at least one integer")
    if any(x <= 0 for x in sizes):
        raise ValueError("All --sizes values must be positive")
    return sorted(set(sizes))


def _prepare_dataset(
    *,
    dataset: str,
    data_path: str,
    max_rows_needed: int,
) -> pd.DataFrame:
    if dataset == DATASET_MIT_ECG:
        return load_dataset_by_name(data_path, dataset, max_rows=max_rows_needed)
    df = load_dataset_by_name(data_path, dataset)
    if len(df) < max_rows_needed:
        raise ValueError(
            f"Dataset {dataset} has only {len(df):,} rows, but max requested size is {max_rows_needed:,}."
        )
    return df


def _slice_df(df: pd.DataFrame, n_rows: int, sample_mode: str, seed: int) -> pd.DataFrame:
    if n_rows >= len(df):
        return df.copy()
    if sample_mode == "random":
        return df.sample(n=n_rows, random_state=seed, replace=False).reset_index(drop=True)
    return df.iloc[:n_rows].copy()


def _summarize_trials(df_trials: pd.DataFrame) -> pd.DataFrame:
    grouped_rows: list[dict[str, Any]] = []
    metrics = [
        "total_safe_segment_s",
        "parent_startup_transfer_overhead_s",
        "worker_total_s",
        "worker_copy_s",
        "worker_exec_s",
    ]

    for (dataset, workload, target_rows), group in df_trials.groupby(
        ["dataset", "workload", "target_rows"], sort=True
    ):
        row: dict[str, Any] = {
            "dataset": dataset,
            "workload": workload,
            "target_rows": int(target_rows),
            "actual_rows": int(group["actual_rows"].iloc[0]),
            "n_trials": int(len(group)),
            "n_success": int(group["ok"].sum()),
            "success_rate": float(group["ok"].mean()),
        }
        for m in metrics:
            values = group[m].astype(float).tolist()
            row[f"{m}_mean"] = float(sum(values) / len(values)) if values else float("nan")
            row[f"{m}_median"] = _pctl(values, 0.50)
            row[f"{m}_p95"] = _pctl(values, 0.95)
            row[f"{m}_std"] = float(pd.Series(values).std(ddof=1)) if len(values) > 1 else 0.0
        grouped_rows.append(row)

    return pd.DataFrame(grouped_rows).sort_values(
        ["dataset", "workload", "target_rows"]
    ).reset_index(drop=True)


def _slope_seconds_per_10x(rows: list[int], values: list[float]) -> float:
    if len(rows) < 2 or len(values) < 2:
        return float("nan")
    x = [math.log10(float(r)) for r in rows]
    y = [float(v) for v in values]

    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    denom = sum((xi - x_mean) ** 2 for xi in x)
    if denom == 0:
        return float("nan")
    num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    return num / denom


def _build_markdown_report(
    *,
    dataset: str,
    data_path: str,
    sample_mode: str,
    repeats: int,
    timeout_s: float,
    sizes: list[int],
    summary_df: pd.DataFrame,
) -> str:
    lines: list[str] = []
    lines.append("# Latency vs Dataset Size (Safe Execution Segment)")
    lines.append("")
    lines.append("## Study Configuration")
    lines.append("")
    lines.append(f"- Dataset: {dataset}")
    lines.append(f"- Data path: {data_path}")
    lines.append(f"- Sizes: {', '.join(f'{s:,}' for s in sizes)}")
    lines.append(f"- Repeats per size: {repeats}")
    lines.append(f"- Sample mode: {sample_mode}")
    lines.append(f"- Timeout per trial: {timeout_s:.1f}s")
    lines.append("")
    lines.append("## Segment Definition")
    lines.append("")
    lines.append(
        "Measured latency is the safe execution segment between LLM code generation and "
        "LLM answer synthesis in ExecutionLayer: process spawn + transfer + worker df.copy() + pandas execution."
    )
    lines.append("")
    lines.append("## Per-Workload Size Scaling")
    lines.append("")

    for workload, group in summary_df.groupby("workload", sort=True):
        group = group.sort_values("target_rows")
        rows = group["target_rows"].astype(int).tolist()
        mean_total = group["total_safe_segment_s_mean"].astype(float).tolist()
        mean_overhead = group["parent_startup_transfer_overhead_s_mean"].astype(float).tolist()
        mean_copy = group["worker_copy_s_mean"].astype(float).tolist()
        mean_exec = group["worker_exec_s_mean"].astype(float).tolist()

        slope_total = _slope_seconds_per_10x(rows, mean_total)
        slope_overhead = _slope_seconds_per_10x(rows, mean_overhead)
        slope_copy = _slope_seconds_per_10x(rows, mean_copy)
        slope_exec = _slope_seconds_per_10x(rows, mean_exec)

        lines.append(f"### Workload: {workload}")
        lines.append("")
        lines.append(
            "- Mean total safe latency slope (seconds per 10x rows): "
            f"{slope_total:.4f}"
        )
        lines.append(
            "- Mean parent startup/transfer overhead slope (seconds per 10x rows): "
            f"{slope_overhead:.4f}"
        )
        lines.append(
            "- Mean worker copy slope (seconds per 10x rows): "
            f"{slope_copy:.4f}"
        )
        lines.append(
            "- Mean worker pandas execution slope (seconds per 10x rows): "
            f"{slope_exec:.4f}"
        )
        lines.append("")
        lines.append("| Rows | Mean total (s) | Median total (s) | P95 total (s) | Mean copy (s) | Mean exec (s) | Mean startup/transfer overhead (s) |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in group.iterrows():
            lines.append(
                "| "
                f"{int(r['target_rows']):,} | "
                f"{float(r['total_safe_segment_s_mean']):.4f} | "
                f"{float(r['total_safe_segment_s_median']):.4f} | "
                f"{float(r['total_safe_segment_s_p95']):.4f} | "
                f"{float(r['worker_copy_s_mean']):.4f} | "
                f"{float(r['worker_exec_s_mean']):.4f} | "
                f"{float(r['parent_startup_transfer_overhead_s_mean']):.4f} |"
            )
        lines.append("")

    lines.append("## Interpretation Guidance")
    lines.append("")
    lines.append("- If startup/transfer overhead slope dominates, row count mainly hurts process/data movement.")
    lines.append("- If worker copy slope dominates, cost is mostly from defensive df.copy() in the worker.")
    lines.append("- If worker exec slope dominates, pandas compute is the main size-sensitive component.")
    lines.append("- If all slopes are near zero, latency is largely size-insensitive in this segment.")
    lines.append("")
    return "\n".join(lines)


def _write_size_artifacts(
    *,
    output_dir: Path,
    dataset: str,
    data_path: str,
    sample_mode: str,
    repeats: int,
    timeout_s: float,
    sizes: list[int],
    trials_df: pd.DataFrame,
    summary_df: pd.DataFrame,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    trials_df.to_csv(output_dir / "safe_segment_trials.csv", index=False)
    summary_df.to_csv(output_dir / "safe_segment_summary.csv", index=False)
    report = _build_markdown_report(
        dataset=dataset,
        data_path=data_path,
        sample_mode=sample_mode,
        repeats=repeats,
        timeout_s=timeout_s,
        sizes=sizes,
        summary_df=summary_df,
    )
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def run_study(
    args: argparse.Namespace, output_dir: Path | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    print(f"\n{'='*70}", flush=True)
    print(f"Latency vs Dataset Size Study", flush=True)
    print(f"{'='*70}\n", flush=True)
    
    sizes = _parse_sizes(args.sizes)
    data_path = args.data or DEFAULT_DATA_PATHS[args.dataset]
    
    print(f"[1/3] Loading dataset: {args.dataset}", flush=True)
    print(f"      Data path: {data_path}", flush=True)
    print(f"      Sample mode: {args.sample_mode}", flush=True)

    df_base = _prepare_dataset(
        dataset=args.dataset,
        data_path=data_path,
        max_rows_needed=max(sizes),
    )
    print(f"      ✓ Loaded {len(df_base):,} rows, {len(df_base.columns)} columns\n", flush=True)

    workload_bank = WORKLOADS[args.dataset]
    if args.workload == "all":
        workload_names = ["light", "report_like", "heavy"]
    else:
        workload_names = [args.workload]

    print(f"[2/3] Configured workloads: {', '.join(workload_names)}", flush=True)
    print(f"      Sizes: {', '.join(f'{s:,}' for s in sizes)}", flush=True)
    print(f"      Repeats per (workload, size): {args.repeats}", flush=True)
    print(f"      Total trials to run: {len(workload_names) * len(sizes) * args.repeats}\n", flush=True)
    
    print(f"[3/3] Starting trials...\n", flush=True)

    trial_records: list[TrialRecord] = []
    total_trials_completed = 0
    total_trials_expected = len(workload_names) * len(sizes) * args.repeats

    for target_rows in sizes:
        df_slice = _slice_df(
            df_base,
            n_rows=target_rows,
            sample_mode=args.sample_mode,
            seed=args.seed,
        )
        actual_rows = len(df_slice)
        if actual_rows == 0:
            raise ValueError(f"Slice for target_rows={target_rows} is empty")

        print(f"Size: {target_rows:,} rows", flush=True)
        size_trial_records: list[TrialRecord] = []
        for workload_name in workload_names:
            code = workload_bank[workload_name]
            print(f"  Workload: {workload_name}", flush=True)
            for trial in range(1, args.repeats + 1):
                trial_label = f"Trial {trial}/{args.repeats}"
                payload = _run_safe_segment_profiled(
                    df=df_slice,
                    code=code,
                    timeout_s=args.timeout_s,
                    trial_label=trial_label,
                )
                total_trials_completed += 1
                record = TrialRecord(
                    dataset=args.dataset,
                    workload=workload_name,
                    target_rows=target_rows,
                    actual_rows=actual_rows,
                    trial=trial,
                    ok=bool(payload["ok"]),
                    output=str(payload["output"]),
                    total_safe_segment_s=float(payload["total_safe_segment_s"]),
                    parent_startup_transfer_overhead_s=float(
                        payload["parent_startup_transfer_overhead_s"]
                    ),
                    worker_total_s=float(payload["worker_total_s"]),
                    worker_copy_s=float(payload["worker_copy_s"]),
                    worker_exec_s=float(payload["worker_exec_s"]),
                    worker_output_marshalling_s=float(
                        payload["worker_output_marshalling_s"]
                    ),
                )
                trial_records.append(record)
                size_trial_records.append(record)

        if output_dir is not None:
            size_trials_df = pd.DataFrame(
                [asdict(record) for record in size_trial_records]
            )
            _write_size_artifacts(
                output_dir=output_dir / str(target_rows),
                dataset=args.dataset,
                data_path=args.data or DEFAULT_DATA_PATHS[args.dataset],
                sample_mode=args.sample_mode,
                repeats=args.repeats,
                timeout_s=args.timeout_s,
                sizes=[target_rows],
                trials_df=size_trials_df,
                summary_df=_summarize_trials(size_trials_df),
            )
            print(
                f"  Wrote completed size artifacts to {output_dir / str(target_rows)}",
                flush=True,
            )

    print(f"\n{'='*70}", flush=True)
    print(f"Trial execution complete: {total_trials_completed}/{total_trials_expected} trials", flush=True)
    print(f"{'='*70}\n", flush=True)
    
    print("Aggregating results...", flush=True)
    trials_df = pd.DataFrame([asdict(r) for r in trial_records])
    n_success = int(trials_df["ok"].sum())
    n_total = len(trials_df)
    print(f"  ✓ Success rate: {n_success}/{n_total} ({100*n_success/n_total:.1f}%)\n", flush=True)
    
    summary_df = _summarize_trials(trials_df)
    return trials_df, summary_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark dataset-size effects for the safe execution segment "
            "between LLM code generation and LLM answer synthesis."
        )
    )
    parser.add_argument(
        "--dataset",
        choices=[DATASET_BUS, DATASET_WISDM, DATASET_MIT_ECG],
        default=DATASET_MIT_ECG,
        help="Dataset loader to use.",
    )
    parser.add_argument(
        "--data",
        default="",
        help="Path to dataset file. Defaults to the project's standard path for --dataset.",
    )
    parser.add_argument(
        "--sizes",
        default="2000,20000,200000,2000000,10000000,20000000",
        help="Comma-separated row counts to benchmark.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=7,
        help="Number of cold-start trials per (workload, size).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=120.0,
        help="Per-trial timeout in seconds.",
    )
    parser.add_argument(
        "--sample-mode",
        choices=["head", "random"],
        default="head",
        help="How to construct row-count slices from the base dataframe.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed used when --sample-mode=random.",
    )
    parser.add_argument(
        "--workload",
        choices=["all", "light", "report_like", "heavy"],
        default="all",
        help="Deterministic code snippet profile(s) to execute.",
    )
    parser.add_argument(
        "--output",
        default="flashfusion/results/latency_vs_size",
        help="Directory where raw results and summary artifacts are written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    trials_df, summary_df = run_study(args, output_dir=output_dir)

    raw_csv = output_dir / "safe_segment_trials.csv"
    summary_csv = output_dir / "safe_segment_summary.csv"
    report_md = output_dir / "report.md"

    print("Writing artifacts...", flush=True)
    _write_size_artifacts(
        output_dir=output_dir,
        dataset=args.dataset,
        data_path=args.data or DEFAULT_DATA_PATHS[args.dataset],
        sample_mode=args.sample_mode,
        repeats=args.repeats,
        timeout_s=args.timeout_s,
        sizes=_parse_sizes(args.sizes),
        trials_df=trials_df,
        summary_df=summary_df,
    )
    print(f"  ✓ {raw_csv}", flush=True)
    print(f"  ✓ {summary_csv}", flush=True)
    print(f"  ✓ {report_md}", flush=True)
    
    print(f"\n{'='*70}", flush=True)
    print("✓ Study complete!", flush=True)
    print(f"{'='*70}\n", flush=True)


if __name__ == "__main__":
    main()