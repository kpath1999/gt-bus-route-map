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

CODE_TO_NAME = {
    "a": "walking",
    "b": "jogging",
    "c": "stairs",
    "d": "sitting",
    "e": "standing",
    "f": "typing",
    "g": "brushing teeth",
    "h": "eating soup",
    "i": "eating chips",
    "j": "eating pasta",
    "k": "drinking",
    "l": "eating sandwich",
    "m": "kicking soccer ball",
    "o": "playing catch",
    "p": "dribbling basketball",
    "q": "writing",
    "r": "clapping",
    "s": "folding clothes",
}


def _canonical_activity_series(df: pd.DataFrame) -> pd.Series:
    labels = df["activity_label"].astype(str).str.strip().str.lower()
    return labels.map(lambda x: CODE_TO_NAME.get(x, x))

def build_ground_truth(df: pd.DataFrame) -> list[dict]:
    df = df.copy()
    df["activity_label"] = df["activity_label"].astype(str).str.strip()
    df["activity_norm"] = _canonical_activity_series(df)
    df["magnitude"] = (df["x"] ** 2 + df["y"] ** 2 + df["z"] ** 2) ** 0.5

    qmap = {q["id"]: q["text"] for q in WISDM_QUERIES}

    q1_max_x = float(df.loc[df["subject_id"] == 15, "x"].max())

    q2_walk_count = int(df.loc[df["activity_norm"] == "walking"].shape[0])

    q3_mask = (df["subject_id"] == 5) & (df["activity_norm"] == "sitting")
    q3_y_mean = float(df.loc[q3_mask, "y"].mean())

    per_user_count = df.groupby("subject_id").size().sort_values(ascending=False)
    q4_user = int(per_user_count.index[0])
    q4_count = int(per_user_count.iloc[0])

    dynamic_mask = df["activity_norm"].isin({"walking", "jogging"})
    resting_mask = df["activity_norm"].isin({"sitting", "standing"})
    q5_dynamic_mean = float(df.loc[dynamic_mask, "magnitude"].mean())
    q5_resting_mean = float(df.loc[resting_mask, "magnitude"].mean())
    q5_diff = q5_dynamic_mean - q5_resting_mean

    stationary_mask = df["activity_norm"].isin({"sitting", "standing"})
    locomotion_mask = df["activity_norm"].isin(
        {"walking", "jogging", "upstairs", "downstairs", "stairs"}
    )
    stationary_counts = df.loc[stationary_mask].groupby("subject_id").size()
    locomotion_counts = df.loc[locomotion_mask].groupby("subject_id").size()
    q6_compare = pd.DataFrame(
        {"stationary": stationary_counts, "locomotion": locomotion_counts}
    ).fillna(0)
    q6_compare["delta"] = q6_compare["stationary"] - q6_compare["locomotion"]
    q6_candidates = q6_compare[q6_compare["delta"] > 0].sort_values("delta", ascending=False)

    ascending_mask = df["activity_norm"].isin({"upstairs", "stairs"})
    q7_mask = (df["subject_id"] == 20) & ascending_mask
    q7_median_mag = float(df.loc[q7_mask, "magnitude"].median())

    q8_up_mean = float(df.loc[df["activity_norm"].isin({"upstairs", "stairs"}), "z"].mean())
    q8_down_mean = float(df.loc[df["activity_norm"] == "downstairs", "z"].mean())
    q8_diff = q8_up_mean - q8_down_mean

    entries = [
        {
            "query_id": 1,
            "query_text": qmap[1],
            "reference_answer": f"Maximum x-acceleration for user 15 is {q1_max_x:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 2,
            "query_text": qmap[2],
            "reference_answer": f"Total Walking samples in the dataset: {q2_walk_count}.",
            "expected_rejection": False,
        },
        {
            "query_id": 3,
            "query_text": qmap[3],
            "reference_answer": f"Average y-acceleration for user 5 during Sitting is {q3_y_mean:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 4,
            "query_text": qmap[4],
            "reference_answer": (
                f"User with the highest sample count is {q4_user} with {q4_count} samples."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 5,
            "query_text": qmap[5],
            "reference_answer": (
                f"Average magnitude for dynamic activities (walking,jogging) is {q5_dynamic_mean:.4f}; "
                f"resting activities (sitting,standing) is {q5_resting_mean:.4f}; "
                f"difference (dynamic-resting) is {q5_diff:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 6,
            "query_text": qmap[6],
            "reference_answer": (
                f"User {int(q6_candidates.index[0])} has the largest stationary-over-locomotion "
                f"duration proxy (sample delta={int(q6_candidates.iloc[0]['delta'])})."
                if not q6_candidates.empty
                else "No user has stationary duration proxy greater than locomotion duration proxy."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 7,
            "query_text": qmap[7],
            "reference_answer": f"Median net acceleration magnitude for user 20 while ascending steps is {q7_median_mag:.4f}.",
            "expected_rejection": False,
        },
        {
            "query_id": 8,
            "query_text": qmap[8],
            "reference_answer": (
                f"Average z for ascending elevation changes is {q8_up_mean:.4f}; "
                f"descending is {q8_down_mean:.4f}; difference (ascending-descending) is {q8_diff:.4f}."
            ),
            "expected_rejection": False,
        },
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
