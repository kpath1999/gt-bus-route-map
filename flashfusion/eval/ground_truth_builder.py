"""Build deterministic ground-truth JSON for supported benchmark datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import numpy as np

from flashfusion.eval.queries import (
    DATASET_BUS,
    DATASET_MIT_ECG,
    DATASET_WISDM,
    get_queries,
)
from flashfusion.pipeline.loader import load_dataset_by_name


def build_ground_truth_wisdm(df: pd.DataFrame) -> list[dict]:
    df = df.copy()

    # ---------------------------------------------------------------------------
    # Shared pre-processing
    # ---------------------------------------------------------------------------
    df["activity_label"] = df["activity_label"].astype(str).str.strip()
    df["activity_lower"] = df["activity_label"].str.lower()
    df["magnitude"] = (df["x"] ** 2 + df["y"] ** 2 + df["z"] ** 2) ** 0.5

    qmap = {q["id"]: q["text"] for q in get_queries(DATASET_WISDM)}

    # ---------------------------------------------------------------------------
    # Q1 — Maximum x-acceleration for user 15
    # ---------------------------------------------------------------------------
    q1_max_x = float(df.loc[df["subject_id"] == 15, "x"].max())

    # ---------------------------------------------------------------------------
    # Q2 — Total sample count for Walking activity
    # ---------------------------------------------------------------------------
    q2_walk_count = int(df.loc[df["activity_lower"] == "walking"].shape[0])

    # ---------------------------------------------------------------------------
    # Q3 — Average y-acceleration for user 5 during Sitting
    # ---------------------------------------------------------------------------
    q3_mask = (df["subject_id"] == 5) & (df["activity_lower"] == "sitting")
    q3_y_mean = float(df.loc[q3_mask, "y"].mean())

    # ---------------------------------------------------------------------------
    # Q4 — User with the highest total sample count
    # ---------------------------------------------------------------------------
    per_user_count = df.groupby("subject_id").size().sort_values(ascending=False)
    q4_user = int(per_user_count.index[0])
    q4_count = int(per_user_count.iloc[0])

    # ---------------------------------------------------------------------------
    # Q5 — Mean acceleration magnitude: dynamic vs resting states
    #
    # dynamic: walking, jogging, upstairs, downstairs
    # resting: sitting, standing
    #
    # Method: compute mean magnitude across all rows in each group, then diff.
    # ---------------------------------------------------------------------------
    dynamic_mask = df["activity_lower"].isin(
        {"walking", "jogging", "upstairs", "downstairs"}
    )
    resting_mask = df["activity_lower"].isin({"sitting", "standing"})

    q5_dynamic_mean = float(df.loc[dynamic_mask, "magnitude"].mean())
    q5_resting_mean = float(df.loc[resting_mask, "magnitude"].mean())
    q5_diff = q5_dynamic_mean - q5_resting_mean

    # ---------------------------------------------------------------------------
    # Q6 — User whose stationary duration exceeds locomotion duration
    #
    # Duration proxy: sum of inter-sample timestamp deltas (nanoseconds → seconds).
    # Timestamps are per-subject monotonic; sort by (subject_id, timestamp) first.
    # stationary: sitting, standing
    # locomotion: walking, jogging, upstairs, downstairs, stairs
    # ---------------------------------------------------------------------------
    df_sorted = df.sort_values(["subject_id", "timestamp"]).copy()
    dt_ns = df_sorted.groupby("subject_id")["timestamp"].diff()
    # First row per subject has no predecessor; treat its interval as 0.
    df_sorted["dt_s"] = (dt_ns.clip(lower=0).fillna(0) / 1_000_000_000).astype(float)

    stationary_mask = df_sorted["activity_lower"].isin({"sitting", "standing"})
    locomotion_mask = df_sorted["activity_lower"].isin(
        {"walking", "jogging", "upstairs", "downstairs"}
    )
    stationary_duration_s = df_sorted.loc[stationary_mask].groupby("subject_id")["dt_s"].sum()
    locomotion_duration_s = df_sorted.loc[locomotion_mask].groupby("subject_id")["dt_s"].sum()

    q6_compare = pd.DataFrame(
        {"stationary_s": stationary_duration_s, "locomotion_s": locomotion_duration_s}
    ).fillna(0.0)
    q6_compare["delta_s"] = q6_compare["stationary_s"] - q6_compare["locomotion_s"]
    q6_candidates = q6_compare[q6_compare["delta_s"] > 0].sort_values(
        "delta_s", ascending=False
    )
    # All qualifying user IDs (stationary_s > locomotion_s), ordered largest delta first.
    q6_qualifying_users = [int(uid) for uid in q6_candidates.index.tolist()]

    # ---------------------------------------------------------------------------
    # Q7 — Median net acceleration vector length for user 20 ascending steps
    #
    # Net vector length = distance between consecutive (x,y,z) samples:
    #   sqrt((x_t - x_{t-1})^2 + (y_t - y_{t-1})^2 + (z_t - z_{t-1})^2)
    # ascending steps: activity_label in {Upstairs, Stairs}
    # ---------------------------------------------------------------------------
    q7_median_net_vec = df[(df['subject_id'] == 20) & (df['activity_label'] == 'Upstairs')]['magnitude'].median()

    # ---------------------------------------------------------------------------
    # Q8 — Difference in average z-axis acceleration: ascending vs descending
    #
    # ascending: Upstairs / Stairs
    # descending: Downstairs
    # ---------------------------------------------------------------------------
    q8_up_mean = float(df.loc[df["activity_lower"].isin({"upstairs"}), "z"].mean())
    q8_down_mean = float(df.loc[df["activity_lower"] == "downstairs", "z"].mean())
    q8_diff = q8_up_mean - q8_down_mean

    # ---------------------------------------------------------------------------
    # Assemble entries
    # ---------------------------------------------------------------------------
    entries = [
        # --- Q1: Max x-acceleration for user 15 ----------------------------------
        {
            "query_id": 1,
            "query_text": qmap[1],
            "reference_answer": f"Maximum x-acceleration for user 15 is {q1_max_x:.4f}.",
            "expected_rejection": False,
        },
        # --- Q2: Total Walking sample count --------------------------------------
        {
            "query_id": 2,
            "query_text": qmap[2],
            "reference_answer": f"Total Walking samples in the dataset: {q2_walk_count}.",
            "expected_rejection": False,
        },
        # --- Q3: Average y-accel for user 5 during Sitting -----------------------
        {
            "query_id": 3,
            "query_text": qmap[3],
            "reference_answer": f"Average y-acceleration for user 5 during Sitting is {q3_y_mean:.4f}.",
            "expected_rejection": False,
        },
        # --- Q4: User with the highest sample count ------------------------------
        {
            "query_id": 4,
            "query_text": qmap[4],
            "reference_answer": (
                f"User with the highest sample count is {q4_user} with {q4_count} samples."
            ),
            "expected_rejection": False,
        },
        # --- Q5: Mean magnitude — dynamic vs resting -----------------------------
        {
            "query_id": 5,
            "query_text": qmap[5],
            "reference_answer": (
                f"Average magnitude for dynamic activities (walking,jogging,upstairs,downstairs) is {q5_dynamic_mean:.4f}; "
                f"resting activities (sitting,standing) is {q5_resting_mean:.4f}; "
                f"difference (dynamic-resting) is {q5_diff:.4f}."
            ),
            "expected_rejection": False,
        },
        # --- Q6: User with stationary duration exceeding locomotion duration -----
        {
            "query_id": 6,
            "query_text": qmap[6],
            "reference_answer": (
                f"Users {q6_qualifying_users} have total stationary duration "
                f"(sitting, standing) exceeding locomotion duration "
                f"(walking, jogging, upstairs, downstairs). "
                f"User {q6_qualifying_users[0]} has the largest margin "
                f"(delta={q6_candidates.iloc[0]['delta_s']:.2f}s)."
                if q6_qualifying_users
                else "No user has stationary duration greater than locomotion duration."
            ),
            "expected_rejection": False,
        },
        # --- Q7: Median net acceleration vector length for user 20 ascending -----
        {
            "query_id": 7,
            "query_text": qmap[7],
            "reference_answer": f"Median net acceleration vector length for user 20 while ascending steps is {q7_median_net_vec:.4f}.",
            "expected_rejection": False,
        },
        # --- Q8: Δ average z-axis acceleration: ascending vs descending ----------
        {
            "query_id": 8,
            "query_text": qmap[8],
            "reference_answer": (
                f"Average z for ascending elevation changes is {q8_up_mean:.4f}; "
                f"descending is {q8_down_mean:.4f}; difference (ascending-descending) is {q8_diff:.4f}."
            ),
            "expected_rejection": False,
        },
        # --- Q9–Q12: Out-of-scope — expected rejections --------------------------
        {
            "query_id": 9,
            "query_text": qmap[9],
            "reference_answer": "Reject: speed in mph and user age are not available in this dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 10,
            "query_text": qmap[10],
            "reference_answer": "Reject: acceleration records do not contain geographic location signals.",
            "expected_rejection": True,
        },
        {
            "query_id": 11,
            "query_text": qmap[11],
            "reference_answer": "Reject: sex and cadence attributes are unavailable in this dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 12,
            "query_text": qmap[12],
            "reference_answer": "Reject: future MVPA-guideline compliance cannot be predicted from this dataset and the WHO guideline threshold is not represented in the schema.",
            "expected_rejection": True,
        },
    ]
    return entries


def build_ground_truth_mit_ecg(df: pd.DataFrame) -> list[dict]:
    """Build deterministic MIT ECG ground-truth entries for 12 queries."""
    df = df.copy()
    df["annotation"] = df["annotation"].astype(str).fillna("")
    df["is_annotated"] = df["annotation"].str.strip() != ""

    qmap = {q["id"]: q["text"] for q in get_queries(DATASET_MIT_ECG)}

    # --- DIRECT (Q1–Q4) ---

    # Q1: Min MLII for record_id 101
    q1_min_mlii_101 = float(df.loc[df["record_id"] == 101, "MLII"].min())

    # Q2: Max time_s (total duration) for record_id 234
    q2_max_time_234 = float(df.loc[df["record_id"] == 234, "time_s"].max())

    # Q3: Count of samples with MLII > 0 for record_id 106
    q3_count_106 = int(
        df.loc[(df["record_id"] == 106) & (df["MLII"] > 0)].shape[0]
    )

    # Q4: Timestamp of last annotated beat in record_id 221
    rec221_ann = df.loc[(df["record_id"] == 221) & (df["is_annotated"]), "time_s"]
    q4_last_ann_221 = float(rec221_ann.max())

    # --- INTERMEDIATE (Q5–Q8) ---

    # Q5: Estimated average HR in BPM for record_id 208
    #     HR (BPM) = (annotation_count / max_time_s) * 60
    rec208 = df.loc[df["record_id"] == 208]
    q5_ann_count_208 = int(rec208.loc[rec208["is_annotated"]].shape[0])
    q5_max_time_208 = float(rec208["time_s"].max())
    q5_hr_208 = (q5_ann_count_208 / q5_max_time_208) * 60.0

    # Q6: Record with largest peak-to-peak MLII amplitude (max - min)
    mlii_range = (
        df.groupby("record_id")["MLII"]
        .agg(lambda x: x.max() - x.min())
        .sort_values(ascending=False)
    )
    q6_record = int(mlii_range.index[0])
    q6_range = float(mlii_range.iloc[0])

    # Q7: 10-second interval with highest annotated beat count for record_id 101
    rec101 = df.loc[df["record_id"] == 101]
    rec101_ann = rec101.loc[rec101["is_annotated"]].copy()
    if rec101_ann.empty:
        q7_interval = 0
        q7_interval_start = 0
        q7_interval_end = 10
        q7_interval_count = 0
    else:
        rec101_ann["interval_10s"] = (rec101_ann["time_s"] // 10).astype(int)
        interval_counts = (
            rec101_ann.groupby("interval_10s").size().sort_values(ascending=False)
        )
        q7_interval = int(interval_counts.index[0])
        q7_interval_start = q7_interval * 10
        q7_interval_end = q7_interval_start + 10
        q7_interval_count = int(interval_counts.iloc[0])

    # Q8: RMS of MLII for record_id 106
    #     RMS = sqrt(mean(x^2))
    mlii_106 = df.loc[df["record_id"] == 106, "MLII"]
    q8_rms_106 = float(np.sqrt((mlii_106 ** 2).mean()))

    return [
        {
            "query_id": 1,
            "query_text": qmap[1],
            "reference_answer": f"Minimum MLII for record_id 101 is {q1_min_mlii_101:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 2,
            "query_text": qmap[2],
            "reference_answer": f"Total recording duration for record_id 234 is {q2_max_time_234:.4f} seconds.",
            "expected_rejection": False,
        },
        {
            "query_id": 3,
            "query_text": qmap[3],
            "reference_answer": f"record_id 106 has {q3_count_106} samples with MLII > 0.",
            "expected_rejection": False,
        },
        {
            "query_id": 4,
            "query_text": qmap[4],
            "reference_answer": f"The last annotated beat in record_id 221 occurs at time_s = {q4_last_ann_221:.6f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 5,
            "query_text": qmap[5],
            "reference_answer": (
                f"record_id 208 has {q5_ann_count_208} annotated beats over {q5_max_time_208:.4f} seconds, "
                f"giving an estimated average heart rate of {q5_hr_208:.2f} BPM."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 6,
            "query_text": qmap[6],
            "reference_answer": f"record_id {q6_record} has the largest peak-to-peak MLII amplitude at {q6_range:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 7,
            "query_text": qmap[7],
            "reference_answer": (
                f"The 10-second interval [{q7_interval_start}s, {q7_interval_end}s) "
                f"contains the highest number of annotated beats for record_id 101 with {q7_interval_count} beats."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 8,
            "query_text": qmap[8],
            "reference_answer": f"The RMS of the MLII signal for record_id 106 is {q8_rms_106:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 9,
            "query_text": qmap[9],
            "reference_answer": "Reject: patient outcome and mortality data are unavailable in this ECG dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 10,
            "query_text": qmap[10],
            "reference_answer": "Reject: BMI and anthropometric metadata are unavailable in this ECG dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 11,
            "query_text": qmap[11],
            "reference_answer": "Reject: family medical history is unavailable in this ECG dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 12,
            "query_text": qmap[12],
            "reference_answer": "Reject: hemodynamic variables such as blood pressure are unavailable in this ECG dataset.",
            "expected_rejection": True,
        },
    ]


def build_ground_truth_bus(df: pd.DataFrame) -> list[dict]:
    """Build deterministic bus ground-truth entries for 12 queries."""
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna().reset_index(drop=True)

    qmap = {q["id"]: q["text"] for q in get_queries(DATASET_BUS)}

    # --- DIRECT (Q1–Q4) --- unchanged ---

    q1_max_var = float(df["accel_variance"].max())
    q2_mean_accel = float(df["accel_mean"].mean())

    q3_idx = int(df["accel_stats_z_p99"].idxmax())
    q3_ts = df.loc[q3_idx, "timestamp"]

    q4_count = int((df["accel_variance"] > 0.20).sum())

    # --- INTERMEDIATE (Q5–Q8) ---

    # Q5: unchanged — north/south median-latitude split on accel_variance
    lat_median = float(df["latitude"].median())
    north_mask = df["latitude"] >= lat_median
    q5_north = float(df.loc[north_mask, "accel_variance"].mean())
    q5_south = float(df.loc[~north_mask, "accel_variance"].mean())
    q5_diff = q5_north - q5_south

    # Q6: UPDATED — vertical shock is now z_p99 - z_p1 (not z_p10)
    df["vertical_shock"] = df["accel_stats_z_p99"] - df["accel_stats_z_p1"]
    q6_idx = int(df["vertical_shock"].idxmax())
    q6_row = df.loc[q6_idx]

    # Q7: UPDATED — 3D peak magnitude: mean of sqrt(x_p99^2 + y_p99^2 + z_p99^2)
    df["peak_magnitude"] = np.sqrt(
        df["accel_stats_x_p99"] ** 2
        + df["accel_stats_y_p99"] ** 2
        + df["accel_stats_z_p99"] ** 2
    )
    q7_mean_magnitude = float(df["peak_magnitude"].mean())

    # Q8: unchanged — 1-minute interval with highest total accel_variance
    df["minute_bin"] = df["timestamp"].dt.floor("min")
    variance_by_minute = df.groupby("minute_bin")["accel_variance"].sum().sort_values(ascending=False)
    q8_bin = variance_by_minute.index[0]
    q8_total = float(variance_by_minute.iloc[0])

    return [
        {
            "query_id": 1,
            "query_text": qmap[1],
            "reference_answer": f"Maximum accel_variance is {q1_max_var:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 2,
            "query_text": qmap[2],
            "reference_answer": f"Average accel_mean across all samples is {q2_mean_accel:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 3,
            "query_text": qmap[3],
            "reference_answer": (
                f"Highest accel_stats_z_p99 occurs at "
                f"{q3_ts.strftime('%Y-%m-%d %H:%M:%S')} with value {float(df.loc[q3_idx, 'accel_stats_z_p99']):.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 4,
            "query_text": qmap[4],
            "reference_answer": f"Samples with accel_variance > 0.20: {q4_count}.",
            "expected_rejection": False,
        },
        {
            "query_id": 5,
            "query_text": qmap[5],
            "reference_answer": (
                f"Median latitude split is {lat_median:.6f}. "
                f"North-half mean accel_variance is {q5_north:.4f}, south-half mean is {q5_south:.4f}; "
                f"the northern half is {'rougher' if q5_diff > 0 else 'smoother'} by {abs(q5_diff):.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 6,
            "query_text": qmap[6],
            "reference_answer": (
                f"Largest vertical shock (z_p99 - z_p1) is {float(q6_row['vertical_shock']):.4f} at "
                f"({float(q6_row['latitude']):.6f}, {float(q6_row['longitude']):.6f}), "
                f"timestamp {q6_row['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 7,
            "query_text": qmap[7],
            "reference_answer": (
                f"Average 3D peak acceleration magnitude "
                f"[sqrt(x_p99^2 + y_p99^2 + z_p99^2)] across all samples is {q7_mean_magnitude:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 8,
            "query_text": qmap[8],
            "reference_answer": (
                f"The 1-minute window starting at {q8_bin.strftime('%Y-%m-%d %H:%M:%S')} "
                f"had the highest total accel_variance of {q8_total:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 9,
            "query_text": qmap[9],
            "reference_answer": "Reject: passenger occupancy data is unavailable in this bus dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 10,
            "query_text": qmap[10],
            "reference_answer": "Reject: weather metadata is unavailable in this bus dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 11,
            "query_text": qmap[11],
            "reference_answer": "Reject: driver identity metadata is unavailable in this bus dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 12,
            "query_text": qmap[12],
            "reference_answer": "Reject: future road maintenance labels are unavailable in this bus dataset.",
            "expected_rejection": True,
        },
    ]


def build_ground_truth(df: pd.DataFrame, dataset: str) -> list[dict]:
    if dataset == DATASET_WISDM:
        return build_ground_truth_wisdm(df)
    if dataset == DATASET_MIT_ECG:
        return build_ground_truth_mit_ecg(df)
    if dataset == DATASET_BUS:
        return build_ground_truth_bus(df)
    raise ValueError(f"Unsupported dataset {dataset!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Flash-Fusion ground truth JSON")
    parser.add_argument("--data", required=True, help="Path to benchmark dataset file")
    parser.add_argument(
        "--dataset",
        default=DATASET_WISDM,
        choices=[DATASET_WISDM, DATASET_MIT_ECG, DATASET_BUS],
        help="Dataset profile for deterministic ground-truth generation",
    )
    parser.add_argument(
        "--output",
        default="flashfusion/eval/ground_truth/ground_truth_wisdm.json",
        help="Output ground-truth JSON path",
    )
    args = parser.parse_args()

    df = load_dataset_by_name(args.data, args.dataset)
    entries = build_ground_truth(df, args.dataset)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=True, indent=2)
    print(f"Wrote {len(entries)} entries to {out}")


if __name__ == "__main__":
    main()
