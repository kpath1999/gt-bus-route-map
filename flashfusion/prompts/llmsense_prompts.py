"""LLMSense-style prompt templates.

Paper equation:
Prompt = Objective + Context + Data + Format
"""

from __future__ import annotations


DATASET_WISDM = "wisdm"
DATASET_MIT_ECG = "mit_ecg"
DATASET_BUS = "bus"
DEFAULT_DATASET = DATASET_WISDM


NARRATION_OBJECTIVE = (
    "You are a sensor-data analyst. Your task is to convert a structured "
    "table of wearable accelerometer readings into a concise, factual "
    "natural-language summary suitable for a downstream reasoning model."
)

WISDM_CONTEXT = (
    "Data source: WISDM (Wireless Sensor Data Mining) accelerometer dataset. "
    "Each row is one sensor sample at {hz} Hz from a wrist-worn device. "
    "Columns: subject_id (integer user ID), timestamp (nanoseconds since epoch), "
    "x / y / z (acceleration in m/s^2), activity_label (one of: "
    "Walking=A, Jogging=B, Upstairs=C, Downstairs=D, Sitting=E, Standing=F). "
    "magnitude = sqrt(x^2+y^2+z^2) is pre-computed. "
    "dt_s = inter-sample time in seconds within each subject."
)

MIT_ECG_CONTEXT = (
    "Data source: MIT-BIH arrhythmia style ECG table. "
    "Columns: sample_idx (sample number), time_s (seconds from start), "
    "MLII and V1 (ECG lead amplitudes), record_id (record identifier), "
    "annotation (beat/event label; empty string means missing annotation). "
    "Rows are ordered signal samples and should be interpreted as time-series data."
)

BUS_CONTEXT = (
    "Data source: bus telemetry vibration dataset. "
    "Columns include timestamp, latitude, longitude, accel_mean, accel_variance, "
    "and axis percentile summaries (accel_stats_x_*, accel_stats_y_*, accel_stats_z_*). "
    "Each row is a geo-temporal telemetry sample; higher accel_variance indicates rougher ride segments."
)

NARRATION_REQUIREMENTS = {
    DATASET_WISDM: (
        "1. Which subjects appear and how many rows each has.\n"
        "2. Which activities occur and their approximate duration (sum of dt_s per group).\n"
        "3. Key statistics for each activity group: mean and max magnitude, mean x/y/z.\n"
        "4. Any notable transitions between activity states."
    ),
    DATASET_MIT_ECG: (
        "1. Which record_id values appear and how many rows each has.\n"
        "2. Duration coverage per record using time_s range and sample density hints.\n"
        "3. Key signal statistics: min/max/mean of MLII and V1 by record or relevant segment.\n"
        "4. Annotation density, notable bursts, and any abrupt morphology/intensity shifts."
    ),
    DATASET_BUS: (
        "1. Time span and spatial coverage (latitude/longitude ranges).\n"
        "2. Roughness patterns using accel_variance and accel_mean trends over time.\n"
        "3. Extremes and contrasts in percentile acceleration columns (x/y/z p1/p99 where relevant).\n"
        "4. Notable route segments or windows with sustained turbulence transitions."
    ),
}

REASONING_DATASET_LABEL = {
    DATASET_WISDM: "WISDM accelerometer",
    DATASET_MIT_ECG: "MIT ECG",
    DATASET_BUS: "bus telemetry",
}


def normalize_dataset_name(dataset: str) -> str:
    value = (dataset or "").strip().lower()
    aliases = {
        "ecg": DATASET_MIT_ECG,
        "mit-ecg": DATASET_MIT_ECG,
        "mit_ecg": DATASET_MIT_ECG,
        "mit": DATASET_MIT_ECG,
    }
    if value in aliases:
        return aliases[value]
    if value in {DATASET_WISDM, DATASET_MIT_ECG, DATASET_BUS}:
        return value
    return DEFAULT_DATASET


def build_dataset_context(dataset: str, hz: float) -> str:
    normalized = normalize_dataset_name(dataset)
    if normalized == DATASET_MIT_ECG:
        return MIT_ECG_CONTEXT
    if normalized == DATASET_BUS:
        return BUS_CONTEXT
    return WISDM_CONTEXT.format(hz=hz)


def get_narration_requirements(dataset: str) -> str:
    normalized = normalize_dataset_name(dataset)
    return NARRATION_REQUIREMENTS.get(normalized, NARRATION_REQUIREMENTS[DEFAULT_DATASET])


def get_reasoning_dataset_label(dataset: str) -> str:
    normalized = normalize_dataset_name(dataset)
    return REASONING_DATASET_LABEL.get(normalized, REASONING_DATASET_LABEL[DEFAULT_DATASET])

NARRATION_PROMPT_TEMPLATE = """{objective}

{context}

Below is a structured excerpt of sensor data:
{data_table}

Convert this into a concise paragraph that states:
{narration_requirements}

Respond with only the narrative paragraph - no headings, no lists.
"""

SUMMARIZATION_PROMPT_TEMPLATE = """{objective}

{context}

The following is a long sensor trace covering {duration_desc}:
{data_table}

Step 1: Summarize the dominant activity patterns, durations, and intensity
trends observed during this period into 3-5 concise sentences.
Step 2: Identify any anomalies or abrupt transitions.

Respond with only the summary - no headings.
"""

REASONING_PROMPT_TEMPLATE = """You are an expert analyst answering questions about
the {dataset_label} dataset.

Dataset context:
{context}

Sensor narrative (pre-computed summary of the data):
{narrative}

User query:
{query}

Instructions:
- Answer the query directly using only information present in the narrative above.
- If the narrative does not contain sufficient information to answer the query,
  state exactly: "The available data does not contain enough information to answer this query."
- Do not hallucinate statistics. Do not reference activity patterns not mentioned.
- Format numbers to two decimal places.

Answer:
"""
