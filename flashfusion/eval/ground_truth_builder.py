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


def _series_to_compact_kv(s: pd.Series, limit: int = 10) -> str:
    items = [f"{k}: {v}" for k, v in s.head(limit).items()]
    if len(s) > limit:
        items.append("...")
    return "; ".join(items)


def build_ground_truth(df: pd.DataFrame) -> list[dict]:
    df = df.copy()
    df["activity_label"] = df["activity_label"].astype(str).str.strip()
    df["magnitude"] = (df["x"] ** 2 + df["y"] ** 2 + df["z"] ** 2) ** 0.5

    labels_norm = df["activity_label"].str.lower()

    qmap = {q["id"]: q["text"] for q in WISDM_QUERIES}

    q1 = df.groupby("activity_label").size().sort_values(ascending=False)
    q2 = (
        df.groupby("activity_label")["magnitude"]
        .mean()
        .sort_values(ascending=False)
        .head(3)
    )

    sedentary_mask = labels_norm.isin({"d", "e", "sitting", "standing"})
    locomotion_mask = labels_norm.isin(
        {"a", "b", "c", "walking", "jogging", "upstairs", "downstairs", "stairs"}
    )
    sedentary = df.loc[sedentary_mask, "magnitude"].mean()
    locomotion = df.loc[locomotion_mask, "magnitude"].mean()

    sub_1610 = df[df["subject_id"] == 1610]
    hand = sub_1610[sub_1610["activity_label"].isin(["F", "Q", "R"])]
    q5_pct = (len(hand) / len(sub_1610) * 100.0) if len(sub_1610) else 0.0

    mu = df["magnitude"].mean()
    sigma = df["magnitude"].std(ddof=0)
    z = (df["magnitude"] - mu) / (sigma if sigma else 1.0)
    q6_subjects = sorted(df.loc[z > 3.0, "subject_id"].astype(int).unique().tolist())

    stairs = df.loc[labels_norm.isin({"c", "stairs", "upstairs", "downstairs"})]
    q7_corr = stairs["x"].corr(stairs["z"]) if not stairs.empty else float("nan")

    ranges = df.groupby("subject_id")["timestamp"].agg(lambda s: int(s.max() - s.min()))
    q8_subject = int(ranges.idxmax())
    q8_duration = int(ranges.max())

    centroids = df.groupby("activity_label")[["x", "y", "z"]].mean()
    cmat = centroids.T.corr()
    best_pair: tuple[str, str] | None = None
    best_val = -2.0
    cols = list(cmat.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            val = float(cmat.iloc[i, j])
            if val > best_val:
                best_val = val
                best_pair = (cols[i], cols[j])

    entries = [
        {
            "query_id": 1,
            "query_text": qmap[1],
            "reference_answer": f"Sample counts by activity label: {_series_to_compact_kv(q1)}",
            "expected_rejection": False,
        },
        {
            "query_id": 2,
            "query_text": qmap[2],
            "reference_answer": "Top 3 activities by mean magnitude: " + ", ".join(
                f"{k} ({v:.4f})" for k, v in q2.items()
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 3,
            "query_text": qmap[3],
            "reference_answer": (
                f"Average magnitude - sedentary (D,E): {sedentary:.4f}; "
                f"locomotion (A,B,C): {locomotion:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 4,
            "query_text": qmap[4],
            "reference_answer": "Reject: heart_rate is not present in this dataset.",
            "expected_rejection": True,
        },
        {
            "query_id": 5,
            "query_text": qmap[5],
            "reference_answer": (
                f"For subject 1610, hand-related samples (F,Q,R) are {q5_pct:.4f}% of all samples."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 6,
            "query_text": qmap[6],
            "reference_answer": (
                "Subjects with unusually high peak acceleration (z-score > 3 on magnitude): "
                + ", ".join(str(s) for s in q6_subjects)
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 7,
            "query_text": qmap[7],
            "reference_answer": (
                f"For stair climbing (C), Pearson correlation between x and z is {q7_corr:.4f}."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 8,
            "query_text": qmap[8],
            "reference_answer": (
                f"Subject with longest duration is {q8_subject} with {q8_duration} ms range."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 9,
            "query_text": qmap[9],
            "reference_answer": (
                f"Most similar activity pair by centroid correlation is {best_pair[0]} and {best_pair[1]} "
                f"(corr={best_val:.4f})."
            ) if best_pair else "Unable to determine pair.",
            "expected_rejection": False,
        },
        {
            "query_id": 10,
            "query_text": qmap[10],
            "reference_answer": "Reject: next-activity prediction is out of scope for this benchmark.",
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
