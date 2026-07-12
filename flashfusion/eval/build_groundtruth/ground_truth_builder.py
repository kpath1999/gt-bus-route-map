"""Build deterministic ground-truth JSON for supported benchmark datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, find_peaks
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.linear_model import LogisticRegression

try:
    import torch
    import torch.nn as nn
except ImportError as exc:  # pragma: no cover - explicit runtime guidance
    raise ImportError(
        "Torch is required for predictive ground-truth generation (WISDM Q14, MIT ECG Q16). "
        "Install with: pip install torch"
    ) from exc

from flashfusion.eval.queries import (
    DATASET_BUS,
    DATASET_MIT_ECG,
    DATASET_WISDM,
    get_queries,
)
from flashfusion.pipeline.loader import load_dataset_by_name

RANDOM_SEED = 42


def _set_seed() -> None:
    np.random.seed(RANDOM_SEED)
    torch.manual_seed(RANDOM_SEED)
    torch.use_deterministic_algorithms(True, warn_only=True)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_entries(entries: list[dict], qmap: dict[int, str], dataset: str) -> list[dict]:
    expected_ids = sorted(qmap.keys())
    got_ids = sorted(int(e["query_id"]) for e in entries)
    _require(
        got_ids == expected_ids,
        f"{dataset}: ground-truth ids mismatch. expected={expected_ids}, got={got_ids}",
    )
    seen: set[int] = set()
    for e in entries:
        qid = int(e["query_id"])
        _require(qid not in seen, f"{dataset}: duplicate query_id {qid}")
        seen.add(qid)
        _require(
            str(e["query_text"]) == qmap[qid],
            f"{dataset}: query text mismatch for query_id {qid}",
        )
    return entries


def _sliding_windows(
    n: int,
    window_size: int,
    step: int,
) -> list[tuple[int, int]]:
    if n < window_size:
        return []
    return [(s, s + window_size) for s in range(0, n - window_size + 1, step)]


def _majority_label(labels: np.ndarray) -> str:
    vals, counts = np.unique(labels.astype(str), return_counts=True)
    return str(vals[np.argmax(counts)])


def _wisdm_feature_windows(
    df: pd.DataFrame,
    user_ids: list[int],
    window_size: int,
    step: int,
    max_windows: int,
    label_filter: set[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    features: list[list[float]] = []
    labels: list[str] = []
    centers: list[int] = []

    for uid in user_ids:
        g = df.loc[df["subject_id"] == uid].sort_values("timestamp")
        if g.empty:
            continue
        x = g["x"].to_numpy(dtype=np.float64)
        y = g["y"].to_numpy(dtype=np.float64)
        z = g["z"].to_numpy(dtype=np.float64)
        ts = g["timestamp"].to_numpy(dtype=np.int64)
        lbl = g["activity_label"].astype(str).to_numpy()

        for s, e in _sliding_windows(len(g), window_size, step):
            win_labels = lbl[s:e]
            maj = _majority_label(win_labels)
            maj_lower = maj.lower()
            if label_filter is not None and maj_lower not in label_filter:
                continue

            xw = x[s:e]
            yw = y[s:e]
            zw = z[s:e]
            features.append(
                [
                    float(np.mean(xw)),
                    float(np.mean(yw)),
                    float(np.mean(zw)),
                    float(np.var(xw)),
                    float(np.var(yw)),
                    float(np.var(zw)),
                ]
            )
            labels.append(maj)
            centers.append(int(ts[s + (window_size // 2)]))

            if len(features) >= max_windows:
                return np.array(features), np.array(labels), np.array(centers, dtype=np.int64)

    return np.array(features), np.array(labels), np.array(centers, dtype=np.int64)


def _wisdm_raw_windows(
    df: pd.DataFrame,
    user_ids: list[int],
    window_size: int,
    step: int,
    max_windows: int,
    allowed_labels: set[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    windows: list[np.ndarray] = []
    labels: list[str] = []
    centers: list[int] = []

    for uid in user_ids:
        g = df.loc[df["subject_id"] == uid].sort_values("timestamp")
        if g.empty:
            continue
        xyz = g[["x", "y", "z"]].to_numpy(dtype=np.float32)
        ts = g["timestamp"].to_numpy(dtype=np.int64)
        lbl = g["activity_label"].astype(str).str.lower().to_numpy()

        for s, e in _sliding_windows(len(g), window_size, step):
            win_lbl = lbl[s:e]
            maj = _majority_label(win_lbl)
            if allowed_labels is not None and maj not in allowed_labels:
                continue
            windows.append(xyz[s:e].T)
            labels.append(maj)
            centers.append(int(ts[s + (window_size // 2)]))
            if len(windows) >= max_windows:
                return np.stack(windows), np.array(labels), np.array(centers, dtype=np.int64)

    if not windows:
        return np.empty((0, 3, window_size), dtype=np.float32), np.array([]), np.array([], dtype=np.int64)
    return np.stack(windows), np.array(labels), np.array(centers, dtype=np.int64)


class TinyCNN(nn.Module):
    def __init__(self, in_channels: int, out_classes: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(in_channels, 16, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.Conv1d(16, 32, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten(),
            nn.Linear(32, out_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def _train_tiny_cnn(
    x_train: np.ndarray,
    y_train: np.ndarray,
    num_classes: int,
    epochs: int,
) -> TinyCNN:
    _set_seed()
    model = TinyCNN(in_channels=x_train.shape[1], out_classes=num_classes)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    x_tensor = torch.tensor(x_train, dtype=torch.float32)
    y_tensor = torch.tensor(y_train, dtype=torch.long)

    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        logits = model(x_tensor)
        loss = criterion(logits, y_tensor)
        loss.backward()
        optimizer.step()

    return model


def build_ground_truth_wisdm(df: pd.DataFrame) -> list[dict]:
    df = df.copy()

    # Shared pre-processing
    df["activity_label"] = df["activity_label"].astype(str).str.strip()
    df["activity_lower"] = df["activity_label"].str.lower()
    df["magnitude"] = (df["x"] ** 2 + df["y"] ** 2 + df["z"] ** 2) ** 0.5

    qmap = {q["id"]: q["text"] for q in get_queries(DATASET_WISDM)}

    # Q1-Q8
    q1_max_x = float(df.loc[df["subject_id"] == 15, "x"].max())
    q2_walk_count = int(df.loc[df["activity_lower"] == "walking"].shape[0])
    q3_mask = (df["subject_id"] == 5) & (df["activity_lower"] == "sitting")
    q3_y_mean = float(df.loc[q3_mask, "y"].mean())

    per_user_count = df.groupby("subject_id").size().sort_values(ascending=False)
    q4_user = int(per_user_count.index[0])
    q4_count = int(per_user_count.iloc[0])

    dynamic_mask = df["activity_lower"].isin({"walking", "jogging", "upstairs", "downstairs"})
    resting_mask = df["activity_lower"].isin({"sitting", "standing"})
    q5_dynamic_mean = float(df.loc[dynamic_mask, "magnitude"].mean())
    q5_resting_mean = float(df.loc[resting_mask, "magnitude"].mean())
    q5_diff = q5_dynamic_mean - q5_resting_mean

    df_sorted = df.sort_values(["subject_id", "timestamp"]).copy()
    dt_ns = df_sorted.groupby("subject_id")["timestamp"].diff()
    df_sorted["dt_s"] = (dt_ns.clip(lower=0).fillna(0) / 1_000_000_000).astype(float)

    stationary_mask = df_sorted["activity_lower"].isin({"sitting", "standing"})
    locomotion_mask = df_sorted["activity_lower"].isin({"walking", "jogging", "upstairs", "downstairs"})
    stationary_duration_s = df_sorted.loc[stationary_mask].groupby("subject_id")["dt_s"].sum()
    locomotion_duration_s = df_sorted.loc[locomotion_mask].groupby("subject_id")["dt_s"].sum()

    q6_compare = pd.DataFrame(
        {"stationary_s": stationary_duration_s, "locomotion_s": locomotion_duration_s}
    ).fillna(0.0)
    q6_compare["delta_s"] = q6_compare["stationary_s"] - q6_compare["locomotion_s"]
    q6_candidates = q6_compare[q6_compare["delta_s"] > 0].sort_values("delta_s", ascending=False)
    q6_qualifying_users = [int(uid) for uid in q6_candidates.index.tolist()]

    q7_median_net_vec = float(
        df.loc[(df["subject_id"] == 20) & (df["activity_label"] == "Upstairs"), "magnitude"].median()
    )
    q8_up_mean = float(df.loc[df["activity_lower"] == "upstairs", "z"].mean())
    q8_down_mean = float(df.loc[df["activity_lower"] == "downstairs", "z"].mean())
    q8_diff = q8_up_mean - q8_down_mean

    # Q13: RandomForest on engineered windows
    train_feat, train_lbl, _ = _wisdm_feature_windows(
        df=df,
        user_ids=list(range(1, 21)),
        window_size=40,
        step=20,
        max_windows=50000,
    )
    _require(len(train_feat) > 0, "wisdm Q13: no training windows produced for users 1-20")
    rf = RandomForestClassifier(n_estimators=300, random_state=RANDOM_SEED, n_jobs=-1)
    rf.fit(train_feat, train_lbl)

    pred_feat, _, pred_centers = _wisdm_feature_windows(
        df=df,
        user_ids=[33],
        window_size=40,
        step=20,
        max_windows=15000,
    )
    _require(len(pred_feat) > 0, "wisdm Q13: no candidate windows produced for user 33")
    target_ts = 49105962326000
    nearest = int(np.argmin(np.abs(pred_centers - target_ts)))
    q13_pred = str(rf.predict(pred_feat[nearest].reshape(1, -1))[0])

    # Q14: Torch 1D-CNN binary Jogging vs Walking
    allowed = {"jogging", "walking"}
    x_train_raw, y_train_raw, _ = _wisdm_raw_windows(
        df=df,
        user_ids=list(range(1, 21)),
        window_size=40,
        step=20,
        max_windows=20000,
        allowed_labels=allowed,
    )
    _require(len(x_train_raw) > 0, "wisdm Q14: no train windows for Jogging/Walking")
    label_to_idx = {"jogging": 0, "walking": 1}
    idx_to_label = {0: "Jogging", 1: "Walking"}
    y_train_idx = np.array([label_to_idx[str(v)] for v in y_train_raw], dtype=np.int64)
    cnn_wisdm = _train_tiny_cnn(x_train_raw, y_train_idx, num_classes=2, epochs=8)

    x_pred_raw, _, pred_centers_raw = _wisdm_raw_windows(
        df=df,
        user_ids=[33],
        window_size=40,
        step=20,
        max_windows=5000,
        allowed_labels=allowed,
    )
    _require(len(x_pred_raw) > 0, "wisdm Q14: no prediction windows for user 33 Jogging/Walking")
    nearest_raw = int(np.argmin(np.abs(pred_centers_raw - target_ts)))
    cnn_wisdm.eval()
    with torch.no_grad():
        logits = cnn_wisdm(torch.tensor(x_pred_raw[nearest_raw:nearest_raw + 1], dtype=torch.float32))
        q14_idx = int(torch.argmax(logits, dim=1).item())
        q14_pred = idx_to_label[q14_idx]

    # Q15: low-pass z-axis + variance state transition count
    user20 = df.loc[df["subject_id"] == 20].sort_values("timestamp").copy()
    _require(not user20.empty, "wisdm Q15: user 20 not found")
    fs = 20.0
    b, a = butter(3, 3.0 / (fs / 2), btype="low")
    z_filt = filtfilt(b, a, user20["z"].to_numpy(dtype=np.float64))
    labels20 = user20["activity_lower"].to_numpy()
    static_set = {"sitting", "standing"}

    vars20: list[float] = []
    true_state: list[int] = []
    for s, e in _sliding_windows(len(user20), window_size=20, step=1):
        vars20.append(float(np.var(z_filt[s:e])))
        maj = _majority_label(labels20[s:e])
        true_state.append(0 if maj in static_set else 1)  # 0 static, 1 dynamic

    _require(len(vars20) > 0, "wisdm Q15: no windows available for user 20")
    vars20_np = np.array(vars20)
    state_np = np.array(true_state)
    static_mean = float(np.mean(vars20_np[state_np == 0])) if np.any(state_np == 0) else float(np.mean(vars20_np))
    dynamic_mean = float(np.mean(vars20_np[state_np == 1])) if np.any(state_np == 1) else float(np.mean(vars20_np))
    thr = (static_mean + dynamic_mean) / 2.0
    pred_state = (vars20_np > thr).astype(int)
    q15_transitions = int(np.sum(pred_state[1:] != pred_state[:-1]))

    # Q16: IsolationForest trained on user 20 Sitting, evaluated on user 20 full set
    train_sitting = user20.loc[user20["activity_lower"] == "sitting", ["x", "y", "z"]]
    _require(len(train_sitting) >= 100, "wisdm Q16: insufficient Sitting samples for user 20")
    iso_wisdm = IsolationForest(contamination=0.05, random_state=RANDOM_SEED)
    iso_wisdm.fit(train_sitting.to_numpy(dtype=np.float64))
    full_xyz = user20[["x", "y", "z"]].to_numpy(dtype=np.float64)
    pred_iso = iso_wisdm.predict(full_xyz)
    jogging_mask = user20["activity_lower"].to_numpy() == "jogging"
    q16_jogging_anom = int(np.sum((pred_iso == -1) & jogging_mask))

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
            "reference_answer": f"User with the highest sample count is {q4_user} with {q4_count} samples.",
            "expected_rejection": False,
        },
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
        {
            "query_id": 7,
            "query_text": qmap[7],
            "reference_answer": f"Median net acceleration vector length for user 20 while ascending steps is {q7_median_net_vec:.4f}.",
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
            "reference_answer": "Reject: future MVPA-guideline compliance cannot be predicted from this dataset and the WHO guideline threshold is not represented in the schema.",
            "expected_rejection": True,
        },
        {
            "query_id": 13,
            "query_text": qmap[13],
            "reference_answer": (
                f"Random Forest prediction for user 33 at timestamp 49105962326000 is '{q13_pred}' "
                "using 40-sample mean/variance window features over x,y,z."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 14,
            "query_text": qmap[14],
            "reference_answer": (
                f"1D-CNN binary prediction for user 33 at timestamp 49105962326000 is '{q14_pred}' "
                "for Jogging vs Walking."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 15,
            "query_text": qmap[15],
            "reference_answer": (
                f"Predicted number of static/dynamic state transitions for user 20 is {q15_transitions} "
                "after low-pass filtering z-axis acceleration and using 20-sample variance windows."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 16,
            "query_text": qmap[16],
            "reference_answer": (
                f"Isolation Forest (trained on user 20 Sitting samples) flags {q16_jogging_anom} "
                "Jogging samples as anomalies on user 20's full dataset."
            ),
            "expected_rejection": False,
        },
    ]
    return _validate_entries(entries, qmap, DATASET_WISDM)


def _ecg_window_features(
    df: pd.DataFrame,
    signal_col: str,
    time_col: str,
    ann_col: str,
    window_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vals = df[signal_col].to_numpy(dtype=np.float64)
    t = df[time_col].to_numpy(dtype=np.float64)
    anns = df[ann_col].astype(str).str.strip().to_numpy()

    starts = np.arange(float(t.min()), float(t.max()) - window_s + 1e-9, window_s)
    feats: list[list[float]] = []
    labels: list[int] = []
    start_times: list[float] = []

    for st in starts:
        en = st + window_s
        mask = (t >= st) & (t < en)
        if not np.any(mask):
            continue
        seg = vals[mask]
        ann_seg = anns[mask]
        feats.append(
            [
                float(np.mean(seg)),
                float(np.std(seg)),
                float(np.max(seg)),
                float(np.min(seg)),
                float(np.sqrt(np.mean(seg**2))),
            ]
        )
        labels.append(int(np.any(ann_seg != "")))
        start_times.append(float(st))

    return np.array(feats), np.array(labels, dtype=np.int64), np.array(start_times)


def _ecg_tumbling_raw(
    df: pd.DataFrame,
    channels: list[str],
    window_s: float,
    ann_col: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    t = df["time_s"].to_numpy(dtype=np.float64)
    starts = np.arange(float(t.min()), float(t.max()) - window_s + 1e-9, window_s)

    windows: list[np.ndarray] = []
    labels: list[int] = []
    out_starts: list[float] = []
    for st in starts:
        en = st + window_s
        m = (t >= st) & (t < en)
        if int(np.sum(m)) < 32:
            continue
        seg = df.loc[m, channels].to_numpy(dtype=np.float32).T
        ann = df.loc[m, ann_col].astype(str).str.strip().to_numpy()
        windows.append(seg)
        labels.append(int(np.any(ann != "")))
        out_starts.append(float(st))

    if not windows:
        return np.empty((0, len(channels), 0), dtype=np.float32), np.array([], dtype=np.int64), np.array([])

    min_len = min(w.shape[1] for w in windows)
    windows = [w[:, :min_len] for w in windows]
    return np.stack(windows), np.array(labels, dtype=np.int64), np.array(out_starts)


def build_ground_truth_mit_ecg(df: pd.DataFrame) -> list[dict]:
    """Build deterministic MIT ECG ground-truth entries for 16 queries."""
    df = df.copy()
    df["annotation"] = df["annotation"].astype(str).fillna("")
    df["is_annotated"] = df["annotation"].str.strip() != ""

    qmap = {q["id"]: q["text"] for q in get_queries(DATASET_MIT_ECG)}

    q1_min_mlii_101 = float(df.loc[df["record_id"] == 101, "MLII"].min())
    q2_max_time_234 = float(df.loc[df["record_id"] == 234, "time_s"].max())
    q3_count_106 = int(df.loc[(df["record_id"] == 106) & (df["MLII"] > 0)].shape[0])
    rec221_ann = df.loc[(df["record_id"] == 221) & (df["is_annotated"]), "time_s"]
    q4_last_ann_221 = float(rec221_ann.max())

    rec208 = df.loc[df["record_id"] == 208]
    q5_ann_count_208 = int(rec208.loc[rec208["is_annotated"]].shape[0])
    q5_max_time_208 = float(rec208["time_s"].max())
    q5_hr_208 = (q5_ann_count_208 / q5_max_time_208) * 60.0

    mlii_range = df.groupby("record_id")["MLII"].agg(lambda x: x.max() - x.min()).sort_values(ascending=False)
    q6_record = int(mlii_range.index[0])
    q6_range = float(mlii_range.iloc[0])

    rec101 = df.loc[df["record_id"] == 101]
    rec101_ann = rec101.loc[rec101["is_annotated"]].copy()
    if rec101_ann.empty:
        q7_interval_start, q7_interval_end, q7_interval_count = 0, 10, 0
    else:
        rec101_ann["interval_10s"] = (rec101_ann["time_s"] // 10).astype(int)
        interval_counts = rec101_ann.groupby("interval_10s").size().sort_values(ascending=False)
        q7_interval = int(interval_counts.index[0])
        q7_interval_start = q7_interval * 10
        q7_interval_end = q7_interval_start + 10
        q7_interval_count = int(interval_counts.iloc[0])

    mlii_106 = df.loc[df["record_id"] == 106, "MLII"]
    q8_rms_106 = float(np.sqrt((mlii_106**2).mean()))

    # Q13 Pan-Tompkins style peak detection
    rec101_mlii = rec101["MLII"].to_numpy(dtype=np.float64)
    _require(len(rec101_mlii) > 1000, "mit_ecg Q13: record_id 101 does not have enough samples")
    fs = 360.0
    b_bp, a_bp = butter(1, [5.0, 15.0], btype="bandpass", fs=fs)
    filt = filtfilt(b_bp, a_bp, rec101_mlii)
    diff = np.diff(filt, prepend=filt[0])
    sq = diff**2
    mwi_n = int(0.150 * fs)
    integ = np.convolve(sq, np.ones(mwi_n) / mwi_n, mode="same")
    peaks, _ = find_peaks(integ, distance=int(0.2 * fs), height=np.percentile(integ, 95))
    q13_peak_count = int(len(peaks))

    # Q14 supervised 5s-window annotation occurrence model on record 208
    rec208s = rec208.sort_values("time_s").copy()
    feat208, lbl208, st208 = _ecg_window_features(
        rec208s,
        signal_col="V1",
        time_col="time_s",
        ann_col="annotation",
        window_s=5.0,
    )
    _require(len(feat208) > 20, "mit_ecg Q14: insufficient 5-second windows for record_id 208")
    train_mask = st208 < 600.0
    final_start = float(rec208s["time_s"].max()) - 120.0
    test_mask = st208 >= final_start
    _require(np.any(train_mask), "mit_ecg Q14: no train windows in first 10 minutes")
    _require(np.any(test_mask), "mit_ecg Q14: no test windows in final 2 minutes")

    train_classes = np.unique(lbl208[train_mask])
    if len(train_classes) < 2:
        fallback_class = int(train_classes[0])
        q14_pred_count = int(np.sum(np.full(np.sum(test_mask), fallback_class, dtype=np.int64)))
        q14_method = (
            "single-class fallback (all train windows had one class); "
            f"predicted class={fallback_class} for all final windows"
        )
    else:
        lr_ecg = LogisticRegression(random_state=RANDOM_SEED, max_iter=2000)
        lr_ecg.fit(feat208[train_mask], lbl208[train_mask])
        q14_pred_count = int(np.sum(lr_ecg.predict(feat208[test_mask])))
        q14_method = "logistic regression"

    # Q15 frequency features + KMeans(k=2) on record 101
    rec101s = rec101.sort_values("time_s").copy()
    ts = rec101s["time_s"].to_numpy(dtype=np.float64)
    sig = rec101s["MLII"].to_numpy(dtype=np.float64)
    starts = np.arange(float(ts.min()), float(ts.max()) - 10.0 + 1e-9, 10.0)
    ffeat: list[list[float]] = []
    fstart: list[float] = []
    for st in starts:
        en = st + 10.0
        m = (ts >= st) & (ts < en)
        if np.sum(m) < 32:
            continue
        seg = sig[m]
        fft_mag = np.abs(np.fft.rfft(seg))
        freqs = np.fft.rfftfreq(len(seg), d=1.0 / fs)
        dom_idx = int(np.argmax(fft_mag[1:]) + 1) if len(fft_mag) > 1 else 0
        dom_freq = float(freqs[dom_idx])
        power = float(np.mean(fft_mag**2))
        bw = float(np.sqrt(np.sum(((freqs - dom_freq) ** 2) * fft_mag) / (np.sum(fft_mag) + 1e-12)))
        ffeat.append([dom_freq, power, bw])
        fstart.append(float(st))

    _require(len(ffeat) >= 3, "mit_ecg Q15: insufficient windows for KMeans clustering")
    km_ecg = KMeans(n_clusters=2, random_state=RANDOM_SEED, n_init=10)
    klabels = km_ecg.fit_predict(np.array(ffeat, dtype=np.float64))
    uniq, counts = np.unique(klabels, return_counts=True)
    minority = int(uniq[np.argmin(counts)])
    first_min_idx = int(np.where(klabels == minority)[0][0])
    q15_first_time = float(fstart[first_min_idx])

    # Q16 Torch 1D-CNN train record 101 test record 208 (10s windows)
    train_raw, train_y, _ = _ecg_tumbling_raw(
        rec101s,
        channels=["MLII", "V1"],
        window_s=10.0,
        ann_col="annotation",
    )
    test_raw, test_y, _ = _ecg_tumbling_raw(
        rec208s,
        channels=["MLII", "V1"],
        window_s=10.0,
        ann_col="annotation",
    )
    _require(len(train_raw) > 0, "mit_ecg Q16: no train windows for record_id 101")
    _require(len(test_raw) > 0, "mit_ecg Q16: no test windows for record_id 208")

    cnn_ecg = _train_tiny_cnn(train_raw, train_y, num_classes=2, epochs=20)
    cnn_ecg.eval()
    with torch.no_grad():
        logits_test = cnn_ecg(torch.tensor(test_raw, dtype=torch.float32))
        pred_test = torch.argmax(logits_test, dim=1).cpu().numpy().astype(np.int64)
    q16_acc = float(np.mean(pred_test == test_y))

    entries = [
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
        {
            "query_id": 13,
            "query_text": qmap[13],
            "reference_answer": (
                "Pan-Tompkins-style processing on record_id 101 MLII detects "
                f"{q13_peak_count} R-peaks."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 14,
            "query_text": qmap[14],
            "reference_answer": (
                f"Predicted annotation count in the final 2 minutes of record_id 208 is {q14_pred_count} "
                f"using {q14_method} trained on the first 10 minutes with 5-second windows."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 15,
            "query_text": qmap[15],
            "reference_answer": (
                "The first minority-cluster window start time is "
                f"time_s={q15_first_time:.3f} using 10-second FFT-feature windows and K-Means(k=2)."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 16,
            "query_text": qmap[16],
            "reference_answer": (
                "1D-CNN classification accuracy on record_id 208 is "
                f"{q16_acc:.4f} when trained on record_id 101 using 10-second MLII+V1 windows."
            ),
            "expected_rejection": False,
        },
    ]
    return _validate_entries(entries, qmap, DATASET_MIT_ECG)


def build_ground_truth_bus(df: pd.DataFrame) -> list[dict]:
    """Build deterministic bus ground-truth entries for 16 queries."""
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

    df["vertical_shock"] = df["accel_stats_z_p99"] - df["accel_stats_z_p1"]
    q6_idx = int(df["vertical_shock"].idxmax())
    q6_row = df.loc[q6_idx]

    df["peak_magnitude"] = np.sqrt(
        df["accel_stats_x_p99"] ** 2
        + df["accel_stats_y_p99"] ** 2
        + df["accel_stats_z_p99"] ** 2
    )
    q7_mean_magnitude = float(df["peak_magnitude"].mean())

    df["minute_bin"] = df["timestamp"].dt.floor("min")
    variance_by_minute = df.groupby("minute_bin")["accel_variance"].sum().sort_values(ascending=False)
    q8_bin = variance_by_minute.index[0]
    q8_total = float(variance_by_minute.iloc[0])

    # Q13 isolation forest split 500/rest
    _require(len(df) > 500, "bus Q13: dataset must have >500 rows for first-500/remainder split")
    train_13 = df.loc[:499, ["accel_stats_z_p99", "accel_variance"]].to_numpy(dtype=np.float64)
    test_13 = df.loc[500:, ["accel_stats_z_p99", "accel_variance"]].to_numpy(dtype=np.float64)
    _require(len(test_13) > 0, "bus Q13: no remainder rows after first 500")
    iso_bus = IsolationForest(contamination=0.03, random_state=RANDOM_SEED)
    iso_bus.fit(train_13)
    scores = iso_bus.score_samples(test_13)
    worst_rel = int(np.argmin(scores))
    worst_abs = 500 + worst_rel
    q13_ts = df.loc[worst_abs, "timestamp"]

    # Q14 one-step forecast from first 100 to 101st
    _require(len(df) >= 101, "bus Q14: dataset must have at least 101 rows")
    y = df["accel_mean"].to_numpy(dtype=np.float64)
    x_train = y[:99]
    y_train = y[1:100]
    _require(len(x_train) > 1, "bus Q14: insufficient accel_mean history")
    # Simple AR(1): y_t = a*x_{t-1} + b
    A = np.vstack([x_train, np.ones_like(x_train)]).T
    a, b = np.linalg.lstsq(A, y_train, rcond=None)[0]
    q14_pred = float(a * y[99] + b)

    # Q15 logistic classification at fixed timestamp
    feat_cols = [
        "accel_stats_x_p1", "accel_stats_x_p10", "accel_stats_x_p90", "accel_stats_x_p99",
        "accel_stats_y_p1", "accel_stats_y_p10", "accel_stats_y_p90", "accel_stats_y_p99",
        "accel_stats_z_p1", "accel_stats_z_p10", "accel_stats_z_p90", "accel_stats_z_p99",
    ]
    x_all = df[feat_cols].to_numpy(dtype=np.float64)
    y_all = (df["accel_variance"].to_numpy(dtype=np.float64) > 0.20).astype(int)
    idx = np.arange(len(df))
    rng = np.random.default_rng(RANDOM_SEED)
    rng.shuffle(idx)
    cut = int(0.8 * len(df))
    train_idx = idx[:cut]
    lr_bus = LogisticRegression(random_state=RANDOM_SEED, max_iter=2000)
    lr_bus.fit(x_all[train_idx], y_all[train_idx])

    target_ts = pd.Timestamp("2025-06-06 16:01:25")
    hit = df.loc[df["timestamp"] == target_ts]
    _require(not hit.empty, "bus Q15: timestamp 2025-06-06 16:01:25 not found")
    q15_label = int(lr_bus.predict(hit[feat_cols].to_numpy(dtype=np.float64))[0])
    q15_name = "rough" if q15_label == 1 else "not_rough"

    # Q16 kmeans k=3 on accel_variance + accel_mean
    km_bus = KMeans(n_clusters=3, random_state=RANDOM_SEED, n_init=10)
    clusters = km_bus.fit_predict(df[["accel_variance", "accel_mean"]].to_numpy(dtype=np.float64))
    tmp = df.copy()
    tmp["cluster"] = clusters
    by_cluster = tmp.groupby("cluster")["accel_variance"].mean().sort_values(ascending=False)
    rough_cluster = int(by_cluster.index[0])
    q16_count = int((tmp["cluster"] == rough_cluster).sum())

    entries = [
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
                f"Highest accel_stats_z_p99 occurs at {q3_ts.strftime('%Y-%m-%d %H:%M:%S')} "
                f"with value {float(df.loc[q3_idx, 'accel_stats_z_p99']):.4f}."
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
                "Average 3D peak acceleration magnitude "
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
        {
            "query_id": 13,
            "query_text": qmap[13],
            "reference_answer": (
                "Most anomalous timestamp in rows after the first 500 is "
                f"{q13_ts.strftime('%Y-%m-%d %H:%M:%S')} using Isolation Forest on "
                "[accel_stats_z_p99, accel_variance]."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 14,
            "query_text": qmap[14],
            "reference_answer": (
                "Forecasted accel_mean for the 101st timestamp is "
                f"{q14_pred:.3f} using an AR(1)-style one-step model trained on the first 100 rows."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 15,
            "query_text": qmap[15],
            "reference_answer": (
                "Predicted class at timestamp 2025-06-06 16:01:25 is "
                f"'{q15_name}' (threshold-defined rough label: accel_variance > 0.20)."
            ),
            "expected_rejection": False,
        },
        {
            "query_id": 16,
            "query_text": qmap[16],
            "reference_answer": (
                "Number of samples in the highest-roughness cluster (highest mean accel_variance) "
                f"is {q16_count} from K-Means(k=3)."
            ),
            "expected_rejection": False,
        },
    ]
    return _validate_entries(entries, qmap, DATASET_BUS)


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

    _set_seed()
    df = load_dataset_by_name(args.data, args.dataset)
    entries = build_ground_truth(df, args.dataset)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=True, indent=2)
    print(f"Wrote {len(entries)} entries to {out}")


if __name__ == "__main__":
    main()
