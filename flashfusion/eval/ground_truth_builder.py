"""Build deterministic ground-truth JSON for supported benchmark datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

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
    q8_up_mean = float(df.loc[df["activity_lower"].isin({"upstairs", "stairs"}), "z"].mean())
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
            "reference_answer": "Reject: personalized workout recommendation is outside benchmark analytics scope.",
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

    q1_total_101 = int(df.loc[df["record_id"] == 101].shape[0])
    q2_max_mlii_105 = float(df.loc[df["record_id"] == 105, "MLII"].max())
    q3_mean_v1_234 = float(df.loc[df["record_id"] == 234, "V1"].mean())
    q4_ann_109 = int(df.loc[(df["record_id"] == 109) & (df["is_annotated"])].shape[0])

    ann_counts = (
        df.loc[df["is_annotated"]]
        .groupby("record_id")
        .size()
        .sort_values(ascending=False)
    )
    q5_record = int(ann_counts.index[0])
    q5_count = int(ann_counts.iloc[0])

    q6_abs_101 = float(df.loc[df["record_id"] == 101, "MLII"].abs().mean())
    q6_abs_234 = float(df.loc[df["record_id"] == 234, "MLII"].abs().mean())
    q6_diff = q6_abs_101 - q6_abs_234

    rec109 = df.loc[df["record_id"] == 109]
    q7_ann_mean = float(rec109.loc[rec109["is_annotated"], "MLII"].mean())
    q7_unann_mean = float(rec109.loc[~rec109["is_annotated"], "MLII"].mean())
    q7_diff = q7_ann_mean - q7_unann_mean

    v1_std = df.groupby("record_id")["V1"].std().sort_values(ascending=False)
    q8_record = int(v1_std.index[0])
    q8_std = float(v1_std.iloc[0])

    return [
        {
            "query_id": 1,
            "query_text": qmap[1],
            "reference_answer": f"Total samples for record_id 101: {q1_total_101}.",
            "expected_rejection": False,
        },
        {
            "query_id": 2,
            "query_text": qmap[2],
            "reference_answer": f"Maximum MLII for record_id 105 is {q2_max_mlii_105:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 3,
            "query_text": qmap[3],
            "reference_answer": f"Average V1 for record_id 234 is {q3_mean_v1_234:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 4,
            "query_text": qmap[4],
            "reference_answer": f"Annotated-beat count for record_id 109 is {q4_ann_109}.",
            "expected_rejection": False,
        },
        {
            "query_id": 5,
            "query_text": qmap[5],
            "reference_answer": (
                f"record_id {q5_record} has the highest annotated-beat count with {q5_count} samples."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 6,
            "query_text": qmap[6],
            "reference_answer": (
                f"Average |MLII| is {q6_abs_101:.4f} for record_id 101 and {q6_abs_234:.4f} for record_id 234; "
                f"difference (101-234) is {q6_diff:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 7,
            "query_text": qmap[7],
            "reference_answer": (
                f"For record_id 109, mean MLII during annotated samples is {q7_ann_mean:.4f} and "
                f"during unannotated samples is {q7_unann_mean:.4f}; difference (annotated-unannotated) is {q7_diff:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 8,
            "query_text": qmap[8],
            "reference_answer": (
                f"record_id {q8_record} has the highest V1 standard deviation at {q8_std:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 9,
            "query_text": qmap[9],
            "reference_answer": "Reject: patient age is unavailable in this ECG dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 10,
            "query_text": qmap[10],
            "reference_answer": "Reject: medication labels are unavailable in this ECG dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 11,
            "query_text": qmap[11],
            "reference_answer": "Reject: sex metadata is unavailable in this ECG dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 12,
            "query_text": qmap[12],
            "reference_answer": "Reject: geographic collection metadata is unavailable in this ECG dataset.",
            "expected_rejection": True,
        },
    ]


def build_ground_truth_bus(df: pd.DataFrame) -> list[dict]:
    """Build deterministic bus ground-truth entries for 12 queries."""
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna().reset_index(drop=True)

    qmap = {q["id"]: q["text"] for q in get_queries(DATASET_BUS)}

    q1_max_var = float(df["accel_variance"].max())
    q2_mean_accel = float(df["accel_mean"].mean())

    q3_idx = int(df["accel_stats_z_p99"].idxmax())
    q3_ts = df.loc[q3_idx, "timestamp"]

    q4_count = int((df["accel_variance"] > 0.20).sum())

    lat_median = float(df["latitude"].median())
    north_mask = df["latitude"] >= lat_median
    q5_north = float(df.loc[north_mask, "accel_variance"].mean())
    q5_south = float(df.loc[~north_mask, "accel_variance"].mean())
    q5_diff = q5_north - q5_south

    df["vertical_shock_proxy"] = df["accel_stats_z_p99"] - df["accel_stats_z_p10"]
    q6_idx = int(df["vertical_shock_proxy"].idxmax())
    q6_row = df.loc[q6_idx]

    q7_x = float(df["accel_stats_x_p99"].mean())
    q7_y = float(df["accel_stats_y_p99"].mean())
    q7_diff = q7_x - q7_y

    q8_q3 = float(df["accel_variance"].quantile(0.75))
    q8_z_median = float(df["accel_stats_z_p99"].median())
    q8_mask = (df["accel_variance"] >= q8_q3) & (df["accel_stats_z_p99"] > q8_z_median)
    q8_fraction = float(q8_mask.mean())

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
                "Highest accel_stats_z_p99 occurs at "
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
                f"North-half mean accel_variance is {q5_north:.4f}, south-half mean is {q5_south:.4f}, "
                f"difference (north-south) is {q5_diff:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 6,
            "query_text": qmap[6],
            "reference_answer": (
                f"Largest vertical shock proxy is {float(q6_row['vertical_shock_proxy']):.4f} at "
                f"({float(q6_row['latitude']):.6f}, {float(q6_row['longitude']):.6f}) "
                f"timestamp {q6_row['timestamp'].strftime('%Y-%m-%d %H:%M:%S' )}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 7,
            "query_text": qmap[7],
            "reference_answer": (
                f"Mean accel_stats_x_p99 is {q7_x:.4f}, mean accel_stats_y_p99 is {q7_y:.4f}, "
                f"difference (x-y) is {q7_diff:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 8,
            "query_text": qmap[8],
            "reference_answer": (
                f"Top-quartile accel_variance threshold is {q8_q3:.4f}, z_p99 median is {q8_z_median:.4f}, "
                f"and the qualifying fraction is {q8_fraction:.4f}."
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
        default="flashfusion/eval/ground_truth.json",
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
