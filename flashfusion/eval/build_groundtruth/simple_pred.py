"""Small deterministic prediction scripts for ECG, WISDM, and Bus datasets.

This module intentionally reuses the benchmark dataset-loading contract from
ground_truth_builder.py via load_dataset_by_name.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from flashfusion.eval.queries import DATASET_BUS, DATASET_MIT_ECG, DATASET_WISDM
from flashfusion.pipeline.loader import load_dataset_by_name

RANDOM_SEED = 42
BEHAVIOR_LABELS = ["calm", "moderate", "slightly_unstable", "aggressive", "very_aggressive"]
MODEL_ORDER = ["logreg", "rf", "1nn", "hgb"]


def _set_seed() -> None:
	np.random.seed(RANDOM_SEED)


def _progress(message: str) -> None:
	print(f"[simple_pred] {message}", flush=True)


def _require(condition: bool, message: str) -> None:
	if not condition:
		raise ValueError(message)


def _repo_root() -> Path:
	return Path(__file__).resolve().parents[3]


def _default_data_path(dataset: str) -> Path:
	root = _repo_root()
	if dataset == DATASET_WISDM:
		return root / "data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt"
	if dataset == DATASET_MIT_ECG:
		return root / "data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt"
	if dataset == DATASET_BUS:
		return root / "data/bus/bus_data.csv"
	raise ValueError(f"Unsupported dataset {dataset!r}")


def _validate_data_path(path: Path) -> None:
	normalized = str(path.resolve()).replace("\\", "/")
	_require("/chat/data/" not in normalized, "Dataset path must not use chat/data.")
	_require("/data/" in normalized, "Dataset path must be under the repository data/ root.")


def _parse_model_names(text: str) -> list[str]:
	model_names = [x.strip().lower() for x in text.split(",") if x.strip()]
	_require(model_names, "At least one model is required.")
	allowed = {"logreg", "rf", "hgb", "1nn"}
	unknown = sorted(set(model_names) - allowed)
	_require(not unknown, f"Unsupported models: {unknown}; allowed={sorted(allowed)}")
	return model_names


def _build_model(name: str):
	if name == "logreg":
		return make_pipeline(
			StandardScaler(),
			LogisticRegression(random_state=RANDOM_SEED, max_iter=2000),
		)
	if name == "rf":
		return RandomForestClassifier(n_estimators=300, random_state=RANDOM_SEED, n_jobs=-1)
	if name == "hgb":
		return HistGradientBoostingClassifier(
			max_iter=200,
            learning_rate=0.08,
            max_leaf_nodes=31,
            l2_regularization=1.0,
            random_state=RANDOM_SEED,
		)
	if name == "1nn":
		return make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=1))
	raise ValueError(f"Unsupported model {name!r}")


def _split_index(n_rows: int, train_fraction: float) -> int:
	_require(0.5 <= train_fraction < 1.0, "train_fraction must be in [0.5, 1.0).")
	split = int(np.floor(n_rows * train_fraction))
	split = max(split, 1)
	split = min(split, n_rows - 1)
	_require(split < n_rows, "Split must leave at least one test row (T+1).")
	return split


def _run_model_suite(
	x: np.ndarray,
	y: np.ndarray,
	split: int,
	model_names: list[str],
	t_plus_one_value: str,
) -> dict:
	x_train, y_train = x[:split], y[:split]
	x_test, y_test = x[split:], y[split:]
	_require(len(x_train) > 0 and len(x_test) > 0, "Need non-empty train and test partitions.")
	_progress(
		f"Model evaluation started: {len(x_train)} training rows, "
		f"{len(x_test)} test rows, T+1={t_plus_one_value}."
	)

	results: dict[str, dict] = {}
	for name in model_names:
		model_started = time.perf_counter()
		_progress(f"Fitting {name}...")
		# Guard against single-class training labels for linear models.
		train_classes = np.unique(y_train)
		if len(train_classes) == 1:
			pred = np.full(len(y_test), train_classes[0], dtype=y_train.dtype)
			method_note = f"single-class fallback ({str(train_classes[0])})"
		else:
			model = _build_model(name)
			model.fit(x_train, y_train)
			pred = model.predict(x_test)
			method_note = "fitted"

		acc = float(accuracy_score(y_test, pred))
		_progress(
			f"Finished {name}: T+1 prediction={str(pred[0])!r}, "
			f"holdout accuracy={acc:.4f}, elapsed={time.perf_counter() - model_started:.2f}s."
		)
		results[name] = {
			"method": method_note,
			"holdout_accuracy": acc,
			"t_plus_one": {
				"timestamp_or_time": t_plus_one_value,
				"true_label": str(y_test[0]),
				"pred_label": str(pred[0]),
				"correct": bool(pred[0] == y_test[0]),
			},
			"n_train": int(len(y_train)),
			"n_test": int(len(y_test)),
		}
	return results


def _enrich_bus_for_behavior(df: pd.DataFrame) -> pd.DataFrame:
	_progress(f"Preparing bus behavior features for {len(df)} rows.")
	out = df.copy()
	out["extreme_event_magnitude"] = np.sqrt(
		out["accel_stats_x_p99"] ** 2
		+ out["accel_stats_y_p99"] ** 2
		+ out["accel_stats_z_p99"] ** 2
	)
	out["instability_score"] = out["accel_variance"].astype(float)

	kx = out[["instability_score", "extreme_event_magnitude"]].to_numpy(dtype=np.float64)
	km = KMeans(n_clusters=5, random_state=RANDOM_SEED, n_init=10)
	_progress("Fitting bus K-Means with 5 clusters on instability and extreme-event features...")
	cluster_ids = km.fit_predict(kx)

	center_df = pd.DataFrame(
		{
			"cluster": np.arange(5, dtype=int),
			"instability_center": km.cluster_centers_[:, 0],
			"extreme_center": km.cluster_centers_[:, 1],
		}
	)
	risk = (
		(center_df["instability_center"] - center_df["instability_center"].mean())
		/ (center_df["instability_center"].std(ddof=0) + 1e-12)
		+ (center_df["extreme_center"] - center_df["extreme_center"].mean())
		/ (center_df["extreme_center"].std(ddof=0) + 1e-12)
	)
	center_df["risk"] = risk
	ranked = center_df.sort_values("risk")["cluster"].tolist()
	cluster_to_behavior = {int(c): BEHAVIOR_LABELS[i] for i, c in enumerate(ranked)}
	out["behavior"] = [cluster_to_behavior[int(c)] for c in cluster_ids]
	counts = out["behavior"].value_counts().reindex(BEHAVIOR_LABELS, fill_value=0).to_dict()
	_progress(f"Bus behavior labels assigned: {counts}.")

	return out


def _bus_behavior_prediction(
	df: pd.DataFrame,
	train_fraction: float,
	model_names: list[str],
	enriched_output_path: Path | None,
) -> dict:
	_progress("Starting bus behavior prediction.")
	bus = _enrich_bus_for_behavior(df)
	bus = bus.sort_values("timestamp").reset_index(drop=True)
	split = _split_index(len(bus), train_fraction)
	_progress(f"Bus rows sorted chronologically; train rows={split}, test rows={len(bus) - split}.")

	feature_cols = [
		"accel_mean",
		"accel_variance",
		"accel_stats_x_p1",
		"accel_stats_x_p10",
		"accel_stats_x_p90",
		"accel_stats_x_p99",
		"accel_stats_y_p1",
		"accel_stats_y_p10",
		"accel_stats_y_p90",
		"accel_stats_y_p99",
		"accel_stats_z_p1",
		"accel_stats_z_p10",
		"accel_stats_z_p90",
		"accel_stats_z_p99",
		"extreme_event_magnitude",
		"instability_score",
	]
	x = bus[feature_cols].to_numpy(dtype=np.float64)
	y = bus["behavior"].astype(str).to_numpy()

	t_plus_one = str(bus.loc[split, "timestamp"])
	model_results = _run_model_suite(x, y, split, model_names, t_plus_one)

	enriched_dataset_path = None
	if enriched_output_path is not None:
		enriched_output_path.parent.mkdir(parents=True, exist_ok=True)
		bus.to_csv(enriched_output_path, index=False)
		_progress(f"Wrote enriched bus dataset to {enriched_output_path}.")
		enriched_dataset_path = str(enriched_output_path)

	return {
		"task": "bus_behavior_prediction",
		"n_rows": int(len(bus)),
		"train_rows": int(split),
		"test_rows": int(len(bus) - split),
		"t_plus_one_timestamp": t_plus_one,
		"behavior_classes": BEHAVIOR_LABELS,
		"enriched_dataset_path": enriched_dataset_path,
		"added_fields": ["extreme_event_magnitude", "instability_score", "behavior"],
		"models": model_results,
	}


def run_prediction_suite(
	df: pd.DataFrame,
	dataset: str,
	*,
	train_fraction: float = 0.8,
	model_names: list[str] | None = None,
	record_id: int = 101,
	bus_enriched_output_path: Path | None = None,
) -> dict:
	"""Run deterministic predictive workflow for one dataset and model suite."""
	selected_models = model_names if model_names is not None else list(MODEL_ORDER)
	if dataset == DATASET_WISDM:
		return _wisdm_activity_prediction(df, train_fraction, selected_models)
	if dataset == DATASET_MIT_ECG:
		return _ecg_r_peak_and_prediction(df, train_fraction, selected_models, record_id)
	if dataset == DATASET_BUS:
		return _bus_behavior_prediction(df, train_fraction, selected_models, bus_enriched_output_path)
	raise ValueError(f"Unsupported dataset {dataset!r}")


def _wisdm_activity_prediction(df: pd.DataFrame, train_fraction: float, model_names: list[str]) -> dict:
	_progress(f"Starting WISDM activity classification with {len(df)} loaded rows.")
	wisdm = df.copy()
	_progress("Normalizing activity labels and computing acceleration magnitude...")
	wisdm["activity_label"] = wisdm["activity_label"].astype(str).str.strip()
	wisdm["magnitude"] = np.sqrt(wisdm["x"] ** 2 + wisdm["y"] ** 2 + wisdm["z"] ** 2)
	wisdm = wisdm.sort_values(["timestamp", "subject_id"]).reset_index(drop=True)

	split = _split_index(len(wisdm), train_fraction)
	_progress(
		f"WISDM rows sorted by timestamp; train rows={split}, "
		f"test rows={len(wisdm) - split}, T+1 timestamp={int(wisdm.loc[split, 'timestamp'])}."
	)
	x = wisdm[["x", "y", "z", "magnitude"]].to_numpy(dtype=np.float64)
	y = wisdm["activity_label"].astype(str).to_numpy()
	t_plus_one = str(int(wisdm.loc[split, "timestamp"]))
	model_results = _run_model_suite(x, y, split, model_names, t_plus_one)

	return {
		"task": "wisdm_activity_classification",
		"n_rows": int(len(wisdm)),
		"train_rows": int(split),
		"test_rows": int(len(wisdm) - split),
		"t_plus_one_timestamp": t_plus_one,
		"models": model_results,
	}


def _match_peaks_with_tolerance(pred: np.ndarray, truth: np.ndarray, tol: int) -> int:
	i = 0
	j = 0
	matched = 0
	while i < len(pred) and j < len(truth):
		d = int(pred[i]) - int(truth[j])
		if abs(d) <= tol:
			matched += 1
			i += 1
			j += 1
		elif d < -tol:
			i += 1
		else:
			j += 1
	return matched


def _detect_r_peaks(signal: np.ndarray, min_distance: int, threshold_q: float) -> np.ndarray:
	thr = float(np.percentile(signal, threshold_q))
	local = np.where((signal[1:-1] > signal[:-2]) & (signal[1:-1] >= signal[2:]) & (signal[1:-1] >= thr))[0] + 1
	if len(local) == 0:
		return np.array([], dtype=np.int64)

	kept: list[int] = [int(local[0])]
	for idx in local[1:]:
		i = int(idx)
		if i - kept[-1] >= min_distance:
			kept.append(i)
		elif signal[i] > signal[kept[-1]]:
			kept[-1] = i
	return np.array(kept, dtype=np.int64)


def _ecg_r_peak_and_prediction(
	df: pd.DataFrame,
	train_fraction: float,
	model_names: list[str],
	record_id: int,
) -> dict:
	_progress(f"Starting ECG R-peak task for record_id={record_id}.")
	ecg = df.loc[df["record_id"] == record_id].sort_values("time_s").reset_index(drop=True)
	_require(not ecg.empty, f"No rows found for record_id={record_id}")
	_progress(f"Loaded {len(ecg)} ECG rows for record_id={record_id}; detecting signal peaks...")

	signal = ecg["MLII"].to_numpy(dtype=np.float64)
	time_s = ecg["time_s"].to_numpy(dtype=np.float64)
	dt = np.diff(time_s)
	median_dt = float(np.median(dt[dt > 0])) if np.any(dt > 0) else (1.0 / 360.0)
	min_distance = max(1, int(round(0.2 / median_dt)))

	pred_peaks = _detect_r_peaks(signal, min_distance=min_distance, threshold_q=97.0)
	truth_peaks = np.where(ecg["annotation"].astype(str).str.strip().to_numpy() != "")[0]
	tol = max(1, int(round(0.05 / median_dt)))
	matched = _match_peaks_with_tolerance(pred_peaks, truth_peaks, tol=tol)
	precision = float(matched / len(pred_peaks)) if len(pred_peaks) > 0 else 0.0
	recall = float(matched / len(truth_peaks)) if len(truth_peaks) > 0 else 0.0
	f1 = float((2.0 * precision * recall) / (precision + recall + 1e-12))
	_progress(
		f"R-peak detection complete: detected={len(pred_peaks)}, annotated={len(truth_peaks)}, "
		f"precision={precision:.4f}, recall={recall:.4f}, F1={f1:.4f}."
	)

	ecg["is_annotated"] = ecg["annotation"].astype(str).str.strip() != ""
	ecg["d_mlii"] = ecg["MLII"].diff().fillna(0.0)
	ecg["rolling_std_mlii"] = ecg["MLII"].rolling(window=9, min_periods=1, center=True).std().fillna(0.0)

	split = _split_index(len(ecg), train_fraction)
	_progress(f"ECG rows split chronologically; train rows={split}, test rows={len(ecg) - split}.")
	x = ecg[["MLII", "V1", "d_mlii", "rolling_std_mlii"]].to_numpy(dtype=np.float64)
	y = ecg["is_annotated"].astype(int).to_numpy()
	t_plus_one = f"{float(ecg.loc[split, 'time_s']):.6f}"
	model_results = _run_model_suite(x, y, split, model_names, t_plus_one)

	return {
		"task": "ecg_r_peak_identification",
		"record_id": int(record_id),
		"n_rows": int(len(ecg)),
		"train_rows": int(split),
		"test_rows": int(len(ecg) - split),
		"t_plus_one_time_s": t_plus_one,
		"r_peak_detection": {
			"detected_count": int(len(pred_peaks)),
			"annotated_count": int(len(truth_peaks)),
			"matched_with_tolerance": int(matched),
			"precision": precision,
			"recall": recall,
			"f1": f1,
			"sample_tolerance_points": int(tol),
			"sample_refractory_points": int(min_distance),
		},
		"models": model_results,
	}


def _build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Simple deterministic prediction mini-scripts")
	parser.add_argument(
		"--dataset",
		required=True,
		choices=[DATASET_WISDM, DATASET_MIT_ECG, DATASET_BUS],
		help="Dataset profile to run",
	)
	parser.add_argument(
		"--data",
		default=None,
		help="Path to dataset file. If omitted, a deterministic repo default is used.",
	)
	parser.add_argument(
		"--models",
		default="logreg,rf,hgb,1nn",
		help="Comma-separated model set from {logreg,rf,hgb,1nn}",
	)
	parser.add_argument(
		"--train-fraction",
		type=float,
		default=0.8,
		help="Train split fraction; prediction focuses on deterministic T+1 at split boundary.",
	)
	parser.add_argument(
		"--record-id",
		type=int,
		default=101,
		help="ECG only: record_id used for peak detection and T+1 classification.",
	)
	parser.add_argument(
		"--bus-enriched-output",
		default=None,
		help="Bus only: output CSV path for enriched copy with extreme_event_magnitude, instability_score, behavior.",
	)
	parser.add_argument(
		"--output",
		default=None,
		help="Optional JSON output path for report payload.",
	)
	return parser


def main() -> None:
	parser = _build_parser()
	args = parser.parse_args()

	_set_seed()
	model_names = _parse_model_names(args.models)
	data_path = Path(args.data) if args.data else _default_data_path(args.dataset)
	_progress(f"Dataset selected: {args.dataset}; models: {', '.join(model_names)}.")
	_progress(f"Loading dataset from {data_path}...")
	_validate_data_path(data_path)
	_require(data_path.exists(), f"Dataset path not found: {data_path}")

	df = load_dataset_by_name(str(data_path), args.dataset)
	_require(not df.empty, f"Loaded dataset is empty: {data_path}")
	_progress(f"Loaded {len(df)} rows with columns: {', '.join(df.columns)}.")

	bus_enriched_path = None
	if args.dataset == DATASET_BUS:
		default_enriched = _repo_root() / "data/bus/bus_data_enriched_behavior.csv"
		bus_enriched_path = Path(args.bus_enriched_output) if args.bus_enriched_output else default_enriched

	report = run_prediction_suite(
		df,
		args.dataset,
		train_fraction=args.train_fraction,
		model_names=model_names,
		record_id=args.record_id,
		bus_enriched_output_path=bus_enriched_path,
	)

	report["dataset"] = args.dataset
	report["data_path"] = str(data_path)
	report["random_seed"] = RANDOM_SEED

	if args.output:
		_progress(f"Writing JSON report to {args.output}...")
		out = Path(args.output)
		out.parent.mkdir(parents=True, exist_ok=True)
		with out.open("w", encoding="utf-8") as fh:
			json.dump(report, fh, ensure_ascii=True, indent=2)
		print(f"Wrote report to {out}")

	_progress("Prediction workflow complete.")
	print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
	main()
