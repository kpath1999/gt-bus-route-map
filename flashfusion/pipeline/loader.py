"""
pipeline/loader.py — WISDM data loading and column metadata utilities.

Functions:
  load_wisdm(path)              → pd.DataFrame
  build_column_metadata(df)     → dict
  meta_to_str(metadata)         → str

Implementation notes (see CLAUDE.md §pipeline/loader.py for full algorithm):
  - WISDM raw file has no header; rows end with semicolons.
  - activity_label values have leading whitespace — always .strip().
  - Malformed lines (< 6 fields, non-numeric x/y/z) are silently skipped.
"""

from __future__ import annotations

import re

import pandas as pd

from flashfusion.eval.queries import DATASET_BUS, DATASET_MIT_ECG, DATASET_WISDM


def load_wisdm(path: str) -> pd.DataFrame:
    """
    Load the WISDM raw accelerometer file into a clean DataFrame.

    The file has no header row. Each line has the format:
        subject_id,activity_label,timestamp,x,y,z;

    Columns returned:
        subject_id (int), activity_label (str), timestamp (int64),
        x (float64), y (float64), z (float64)

    Malformed lines are silently skipped.

    Args:
        path: Absolute or relative path to WISDM_ar_v1.1_raw.txt.

    Returns:
        Clean DataFrame with 6 typed columns, no index reset needed.

    Implementation steps:
        1. Open file, iterate lines.
        2. Strip trailing whitespace and trailing ";".
        3. Skip blank lines and lines with < 6 comma-separated fields.
        4. Parse: subject_id=int, activity_label=str.strip(),
           timestamp=int, x=float, y=float, z=float.
        5. On any ValueError or IndexError: skip the line.
        6. Build DataFrame from collected rows.
        7. Cast dtypes: subject_id→int32, timestamp→int64, x/y/z→float64.
    """
    rows: list[list] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip().rstrip(";").strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                subject_id = int(parts[0].strip())
                activity_label = parts[1].strip()
                timestamp = int(parts[2].strip())
                x = float(parts[3].strip())
                y = float(parts[4].strip())
                z = float(parts[5].strip())
            except (ValueError, IndexError):
                continue
            rows.append([subject_id, activity_label, timestamp, x, y, z])

    df = pd.DataFrame(
        rows,
        columns=["subject_id", "activity_label", "timestamp", "x", "y", "z"],
    )
    if not df.empty:
        df["subject_id"] = df["subject_id"].astype("int32")
        df["activity_label"] = df["activity_label"].astype(str)
        df["timestamp"] = df["timestamp"].astype("int64")
        df["x"] = df["x"].astype("float64")
        df["y"] = df["y"].astype("float64")
        df["z"] = df["z"].astype("float64")
    return df


def load_mit_arrythmia(path: str) -> pd.DataFrame:
    """
    Load consolidated MIT arrhythmia text into a clean DataFrame.

    Expected line format (headerless, semicolon-terminated):
        sample_idx,time_s,MLII,V1,record_id,annotation;
    """
    rows: list[list] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip().rstrip(";").strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            if len(parts) > 6:
                parts = parts[:5] + [",".join(parts[5:]).strip()]
            try:
                sample_idx = int(parts[0])
                time_s = float(parts[1])
                mlii = float(parts[2])
                v1 = float(parts[3])
                record_id = int(parts[4])
                annotation = parts[5]
                if annotation.lower() in {"nan", "none"}:
                    annotation = ""
            except (ValueError, IndexError):
                continue
            rows.append([sample_idx, time_s, mlii, v1, record_id, annotation])

    df = pd.DataFrame(
        rows,
        columns=["sample_idx", "time_s", "MLII", "V1", "record_id", "annotation"],
    )
    if not df.empty:
        df["sample_idx"] = df["sample_idx"].astype("int64")
        df["time_s"] = df["time_s"].astype("float64")
        df["MLII"] = df["MLII"].astype("float64")
        df["V1"] = df["V1"].astype("float64")
        df["record_id"] = df["record_id"].astype("int32")
        df["annotation"] = df["annotation"].astype(str)
    return df


def load_bus_data(path: str) -> pd.DataFrame:
    """Load bus telemetry CSV into a normalized DataFrame."""
    required_columns = [
        "timestamp",
        "latitude",
        "longitude",
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
    ]
    df = pd.read_csv(path)
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise ValueError(f"Bus dataset missing required columns: {missing}")

    df = df[required_columns].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    numeric_columns = [c for c in required_columns if c != "timestamp"]
    for col in numeric_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required_columns).reset_index(drop=True)
    if not df.empty:
        for col in numeric_columns:
            df[col] = df[col].astype("float64")
    return df


def load_dataset_by_name(path: str, dataset: str) -> pd.DataFrame:
    """Load a supported benchmark dataset by identifier."""
    if dataset == DATASET_WISDM:
        return load_wisdm(path)
    if dataset == DATASET_MIT_ECG:
        return load_mit_arrythmia(path)
    if dataset == DATASET_BUS:
        return load_bus_data(path)
    raise ValueError(f"Unsupported dataset {dataset!r}")


# Maximum number of unique values to include in full for non-numeric columns.
# Columns with n_unique <= this threshold get an "all_values" key (complete
# enumeration); columns above the threshold get a "sample_values" key (5 items).
MAX_CAT_VALUES: int = 30

_ANNOTATION_LIKE_PATTERN = re.compile(r"(annotation|label|tag|event)", re.IGNORECASE)


def _semantic_note_for_column(col: str, series: pd.Series, non_null: pd.Series) -> str | None:
    if pd.api.types.is_numeric_dtype(series):
        return None
    if not _ANNOTATION_LIKE_PATTERN.search(col):
        return None
    if non_null.empty:
        return None
    values = non_null.astype(str).str.strip()
    empty_count = int((values == "").sum())
    if empty_count > 0:
        return (
            "Empty string ('') indicates missing annotation; "
            f"filter df[df['{col}'] != ''] before counting."
        )
    return (
        "Check for empty strings indicating missing annotations; "
        f"filter df[df['{col}'] != ''] before counting."
    )


def build_column_metadata(df: pd.DataFrame) -> dict:
    """
    Compute descriptive metadata for every column in the DataFrame.

    Returns a dict mapping column name → metadata dict. Each metadata dict
    always contains:
        dtype         (str)  — string representation of the pandas dtype
        n_unique      (int)  — number of unique non-null values

    For non-numeric columns:
        all_values    (list) — ALL unique values when n_unique <= MAX_CAT_VALUES
        sample_values (list) — up to 5 unique values when n_unique > MAX_CAT_VALUES
        semantic_note (str)  — optional hint for schema semantics (when applicable)

    For numeric columns, additionally contains:
        min   (float)
        max   (float)
        mean  (float)
        std   (float)

    Args:
        df: Any pandas DataFrame (used on WISDM DataFrames but generic).

    Returns:
        dict[str, dict] — one entry per column.
    """
    metadata: dict[str, dict] = {}
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        n_unique = int(non_null.nunique())
        entry: dict = {
            "dtype": str(series.dtype),
            "n_unique": n_unique,
        }
        if not pd.api.types.is_numeric_dtype(series):
            unique_vals = non_null.unique().tolist() if not non_null.empty else []
            if n_unique <= MAX_CAT_VALUES:
                entry["all_values"] = unique_vals
            else:
                entry["sample_values"] = unique_vals[:5]
            semantic_note = _semantic_note_for_column(col, series, non_null)
            if semantic_note:
                entry["semantic_note"] = semantic_note
        if pd.api.types.is_numeric_dtype(series) and not non_null.empty:
            entry["min"] = float(non_null.min())
            entry["max"] = float(non_null.max())
            entry["mean"] = float(non_null.mean())
            std = non_null.std()
            entry["std"] = float(std) if pd.notna(std) else 0.0
        metadata[col] = entry
    return metadata


def meta_to_str(metadata: dict) -> str:
    """
    Format column metadata as a compact multi-line string for prompt injection.

    One line per column:
        "{col} ({dtype}): n_unique={n_unique} | values={all_values}"   (complete list)
        "{col} ({dtype}): n_unique={n_unique} | sample={sample_values}"  (partial list)

    The key distinction:
        ``values=`` — the list is COMPLETE (all unique values present).
        ``sample=`` — the list is a partial sample only.

    Numeric columns append:
        " | min={min:.3f} max={max:.3f} mean={mean:.3f} std={std:.3f}"

    Args:
        metadata: Dict as returned by build_column_metadata().

    Returns:
        Multi-line string, one line per column.
    """
    lines: list[str] = []
    for col, info in metadata.items():
        dtype = info.get("dtype", "")
        n_unique = info.get("n_unique", 0)
        if "all_values" in info:
            line = f"{col} ({dtype}): n_unique={n_unique} | values={info['all_values']}"
        else:
            sample = info.get("sample_values", [])
            line = f"{col} ({dtype}): n_unique={n_unique} | sample={sample}"
        if "min" in info and "max" in info:
            line += (
                f" | min={info['min']:.3f} max={info['max']:.3f}"
                f" mean={info['mean']:.3f} std={info['std']:.3f}"
            )
        if "semantic_note" in info:
            line += f" | note={info['semantic_note']}"
        lines.append(line)
    return "\n".join(lines)
