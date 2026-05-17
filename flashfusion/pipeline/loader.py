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

import pandas as pd


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


def build_column_metadata(df: pd.DataFrame) -> dict:
    """
    Compute descriptive metadata for every column in the DataFrame.

    Returns a dict mapping column name → metadata dict. Each metadata dict
    always contains:
        dtype         (str)  — string representation of the pandas dtype
        n_unique      (int)  — number of unique non-null values
        sample_values (list) — up to 5 unique values (arbitrary order)

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
        sample = non_null.unique()[:5].tolist() if not non_null.empty else []
        entry: dict = {
            "dtype": str(series.dtype),
            "n_unique": int(non_null.nunique()),
            "sample_values": sample,
        }
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
        "{col} ({dtype}): n_unique={n_unique} | sample={sample_values}"

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
        sample = info.get("sample_values", [])
        line = (
            f"{col} ({dtype}): n_unique={n_unique} | sample={sample}"
        )
        if "min" in info and "max" in info:
            line += (
                f" | min={info['min']:.3f} max={info['max']:.3f}"
                f" mean={info['mean']:.3f} std={info['std']:.3f}"
            )
        lines.append(line)
    return "\n".join(lines)
