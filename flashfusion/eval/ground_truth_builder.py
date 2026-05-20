"""
eval/ground_truth_builder.py — Build deterministic WISDM ground truth JSON.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from flashfusion.eval.queries import WISDM_QUERIES
from flashfusion.pipeline.loader import load_wisdm

"""
DATASET details:

Number of examples: 1,098,207
Number of attributes: 6

raw.txt follows this format:
[user],[activity],[timestamp],[x-acceleration],[y-accel],[z-accel];

This line is a representative example:
33,Jogging,49105962326000,-0.6946377,12.680544,0.50395286;
"""

def build_ground_truth(df: pd.DataFrame) -> list[dict]:
    df = df.copy()

    # ---------------------------------------------------------------------------
    # Shared pre-processing
    # ---------------------------------------------------------------------------
    df["activity_label"] = df["activity_label"].astype(str).str.strip()
    df["activity_lower"] = df["activity_label"].str.lower()
    df["magnitude"] = (df["x"] ** 2 + df["y"] ** 2 + df["z"] ** 2) ** 0.5

    qmap = {q["id"]: q["text"] for q in WISDM_QUERIES}

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Flash-Fusion ground truth JSON")
    parser.add_argument("--data", required=True, help="Path to WISDM raw txt")
    parser.add_argument(
        "--output",
        default="flashfusion/eval/ground_truth.json",
        help="Output ground-truth JSON path",
    )
    args = parser.parse_args()

    df = load_wisdm(args.data)
    entries = build_ground_truth(df)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=True, indent=2)
    print(f"Wrote {len(entries)} entries to {out}")


if __name__ == "__main__":
    main()
