"""
eval/queries.py — WISDM benchmark query definitions.

DO NOT MODIFY without re-running the full benchmark and updating CLAUDE.md.

Each query is a dict with:
  id         (int)   1-indexed query identifier
  text       (str)   The exact natural language query fed to each baseline
    complexity (str)   "direct" | "intermediate" | "out_of_scope"
  operation  (str)   Primary pandas operation expected (AGGREGATE, FILTER, etc.)
  stress     (str)   Which capability gap this query exposes

EXPECTED_OUTCOMES maps query id → baseline → expected behaviour keyword:
  "executes"  — agent runs code and produces a numerical/tabular result
    "rejects"   — baseline declines execution with an explicit rationale
"""

from __future__ import annotations

DATASET_WISDM = "wisdm"
DATASET_MIT_ECG = "mit_ecg"
DATASET_BUS = "bus"

SUPPORTED_DATASETS = (DATASET_WISDM, DATASET_MIT_ECG, DATASET_BUS)

WISDM_QUERIES: list[dict] = [
    {
        "id": 1,
        "text": "What is the maximum recorded x-acceleration for user 15?",
        "complexity": "direct",
        "operation": "FILTER+AGGREGATE",
        "stress": (
            "direct scalar lookup baseline. All tool-using baselines should execute. "
            "Used to confirm that differences on later queries are due to reasoning depth, "
            "not basic filtering or aggregation failures."
        ),
    },
    {
        "id": 2,
        "text": "How many total samples in the dataset are classified as the Walking activity?",
        "complexity": "direct",
        "operation": "FILTER+COUNT",
        "stress": (
            "Category counting with activity-label normalization. "
            "WellMax-Only and Flash-Fusion should align when labels are normalized; "
            "AutoIOT-Only can drift if it misreads label variants."
        ),
    },
    {
        "id": 3,
        "text": "What is the average y-accel value for user 5 during the Sitting activity?",
        "complexity": "direct",
        "operation": "FILTER+AGGREGATE",
        "stress": (
            "Conjunctive filter (subject + activity) with numeric aggregation. "
            "This separates robust structured filtering from single-column lookups."
        ),
    },
    {
        "id": 4,
        "text": "Which user has the highest total number of recorded data samples?",
        "complexity": "direct",
        "operation": "GROUPBY+RANK",
        "stress": (
            "Groupby+argmax control query. "
            "Expected to execute across all three tool-using baselines and validate "
            "ranking correctness before harder reasoning prompts."
        ),
    },
    {
        "id": 5,
        "text": (
            "Compare the overall acceleration magnitude between dynamic movements, "
            "such as walking and jogging, and resting states like sitting."
        ),
        "complexity": "intermediate",
        "operation": "FILTER+AGGREGATE",
        "stress": (
            "Requires derived feature reasoning (`magnitude`) and semantic grouping of "
            "dynamic vs resting activities. Flash-Fusion should consistently ground this "
            "before execution; AutoIOT-Only and WellMax-Only may collapse to weaker proxies."
        ),
    },
    {
        "id": 6,
        "text": (
            "Identify the user whose total recorded duration of stationary activities "
            "exceeds their duration of active locomotion."
        ),
        "complexity": "intermediate",
        "operation": "FILTER+GROUPBY+COMPARE",
        "stress": (
            "Multi-step decomposition query: map stationary/locomotion sets, aggregate per user, "
            "then compare totals. Flash-Fusion should execute a faithful analytic chain; "
            "AutoIOT-Only and WellMax-Only may simplify intent or mishandle category mapping."
        ),
    },
    {
        "id": 7,
        "text": (
            "What is the median net acceleration vector length for user 20 while ascending steps?"
        ),
        "complexity": "intermediate",
        "operation": "FILTER+DERIVE+AGGREGATE",
        "stress": (
            "Derived vector magnitude plus median aggregation under activity synonym handling "
            "(ascending steps/upstairs/stairs). Flash-Fusion is expected to preserve the full "
            "intent path; weaker baselines may use raw-axis shortcuts."
        ),
    },
    {
        "id": 8,
        "text": (
            "Calculate the difference in average z-axis acceleration between ascending "
            "and descending elevation changes for all users."
        ),
        "complexity": "intermediate",
        "operation": "FILTER+AGGREGATE+DIFF",
        "stress": (
            "Comparative aggregation across two activity families. "
            "Flash-Fusion should return a computed signed difference; AutoIOT-Only and "
            "WellMax-Only may answer descriptively without robust calculation."
        ),
    },
    {
        "id": 9,
        "text": (
            "How does the average walking speed in miles per hour correlate with the age of the users?"
        ),
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": (
            "Out-of-scope feature request: speed and age are unavailable in WISDM schema. "
            "Flash-Fusion should reject with schema-grounded rationale; AutoIOT-Only and "
            "WellMax-Only are expected to attempt execution anyway."
        ),
    },
    {
        "id": 10,
        "text": "Based on the acceleration data, predict the exact geographic location where user 10 was jogging.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": (
            "Out-of-scope inference: geolocation is not represented in this dataset. "
            "Flash-Fusion should reject; AutoIOT-Only and WellMax-Only are expected to "
            "proceed despite missing required signals."
        ),
    },
    {
        "id": 11,
        "text": "Are female subjects more likely to have a higher cadence during stair climbing than male subjects?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": (
            "Out-of-scope demographic + cadence analysis. Sex and cadence fields are absent. "
            "Flash-Fusion should reject on schema/feasibility grounds while AutoIOT-Only "
            "and WellMax-Only are expected to attempt execution."
        ),
    },
    {
        "id": 12,
        "text": "Recommend a personalized daily workout routine for user 3 based on their most frequent physical activities.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": (
            "Out-of-scope recommendation task requiring prescriptive planning beyond dataset-backed analytics. "
            "Flash-Fusion should reject as non-analytic/prescriptive intent; AutoIOT-Only and "
            "WellMax-Only are expected to attempt response generation."
        ),
    },
]

MIT_ECG_QUERIES: list[dict] = [
    {
        "id": 1,
        "text": "How many total samples belong to record_id 101?",
        "complexity": "direct",
        "operation": "FILTER+COUNT",
        "stress": "Direct count over one ECG record.",
    },
    {
        "id": 2,
        "text": "What is the maximum MLII value for record_id 105?",
        "complexity": "direct",
        "operation": "FILTER+AGGREGATE",
        "stress": "Single-column max over one record.",
    },
    {
        "id": 3,
        "text": "What is the average V1 value for record_id 234?",
        "complexity": "direct",
        "operation": "FILTER+AGGREGATE",
        "stress": "Single-column mean over one record.",
    },
    {
        "id": 4,
        "text": "How many beats are annotated (annotation is non-empty) for record_id 109?",
        "complexity": "direct",
        "operation": "FILTER+COUNT",
        "stress": "Annotation-presence filtering.",
    },
    {
        "id": 5,
        "text": "Which record_id has the highest count of annotated beats?",
        "complexity": "intermediate",
        "operation": "FILTER+GROUPBY+RANK",
        "stress": "Multi-record ranking by annotation count.",
    },
    {
        "id": 6,
        "text": "Compare the average absolute MLII signal between record_id 101 and record_id 234.",
        "complexity": "intermediate",
        "operation": "FILTER+DERIVE+AGGREGATE",
        "stress": "Derived metric with two-record comparison.",
    },
    {
        "id": 7,
        "text": "For record_id 109, what is the difference between mean MLII during annotated versus unannotated samples?",
        "complexity": "intermediate",
        "operation": "FILTER+AGGREGATE+DIFF",
        "stress": "Conditional aggregation with signed difference.",
    },
    {
        "id": 8,
        "text": "Which record_id shows the highest variability (standard deviation) in V1?",
        "complexity": "intermediate",
        "operation": "GROUPBY+AGGREGATE+RANK",
        "stress": "Groupwise variability ranking.",
    },
    {
        "id": 9,
        "text": "How does arrhythmia burden correlate with patient age?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Age is unavailable in this ECG schema.",
    },
    {
        "id": 10,
        "text": "Predict each patient's medication regimen from the ECG waveform.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Medication labels are unavailable.",
    },
    {
        "id": 11,
        "text": "Are female patients more likely than male patients to have ventricular beats?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Sex metadata is unavailable.",
    },
    {
        "id": 12,
        "text": "Estimate where each recording was collected geographically.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Location data is unavailable.",
    },
]

BUS_QUERIES: list[dict] = [
    {
        "id": 1,
        "text": "What is the maximum accel_variance observed in this dataset?",
        "complexity": "direct",
        "operation": "AGGREGATE",
        "stress": "Single-column max over route segments.",
    },
    {
        "id": 2,
        "text": "What is the average accel_mean across all samples?",
        "complexity": "direct",
        "operation": "AGGREGATE",
        "stress": "Dataset-wide scalar mean.",
    },
    {
        "id": 3,
        "text": "At which timestamp was the highest accel_stats_z_p99 recorded?",
        "complexity": "direct",
        "operation": "RANK+SELECT",
        "stress": "Argmax lookup on one percentile signal.",
    },
    {
        "id": 4,
        "text": "How many samples have accel_variance greater than 0.20?",
        "complexity": "direct",
        "operation": "FILTER+COUNT",
        "stress": "Threshold filter with deterministic count.",
    },
    {
        "id": 5,
        "text": "Compare average accel_variance between the northern half and southern half of the route (split by median latitude).",
        "complexity": "intermediate",
        "operation": "FILTER+AGGREGATE+COMPARE",
        "stress": "Median split then group comparison.",
    },
    {
        "id": 6,
        "text": "Which coordinate has the largest vertical shock proxy defined as (accel_stats_z_p99 - accel_stats_z_p10)?",
        "complexity": "intermediate",
        "operation": "DERIVE+RANK+SELECT",
        "stress": "Derived feature ranking with coordinate retrieval.",
    },
    {
        "id": 7,
        "text": "What is the difference between mean accel_stats_x_p99 and mean accel_stats_y_p99?",
        "complexity": "intermediate",
        "operation": "AGGREGATE+DIFF",
        "stress": "Cross-axis aggregate comparison.",
    },
    {
        "id": 8,
        "text": "What fraction of samples are in the top quartile of accel_variance and also have accel_stats_z_p99 above its median?",
        "complexity": "intermediate",
        "operation": "FILTER+QUANTILE+RATE",
        "stress": "Compound quantile gating and proportion computation.",
    },
    {
        "id": 9,
        "text": "How does passenger occupancy vary with road roughness in this trip?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Passenger occupancy is unavailable.",
    },
    {
        "id": 10,
        "text": "Did rainy weather cause the roughest segments in this route?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Weather metadata is unavailable.",
    },
    {
        "id": 11,
        "text": "Which bus driver generated the smoothest driving profile?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Driver identity metadata is unavailable.",
    },
    {
        "id": 12,
        "text": "Predict next week's pothole repairs for the road segments in this dataset.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Future maintenance labels are unavailable.",
    },
]

# Expected behaviour per baseline for each query.
# Used in tests and reporting.
EXPECTED_OUTCOMES: dict[int, dict[str, str]] = {
    1: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    2: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    3: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    4: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    5: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    6: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    7: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    8: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    9: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "rejects",
    },
    10: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "rejects",
    },
    11: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "rejects",
    },
    12: {
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "rejects",
    },
}

EXPECTED_OUTCOMES_MIT_ECG: dict[int, dict[str, str]] = {
    1: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    2: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    3: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    4: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    5: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    6: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    7: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    8: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    9: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "rejects"},
    10: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "rejects"},
    11: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "rejects"},
    12: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "rejects"},
}

EXPECTED_OUTCOMES_BUS: dict[int, dict[str, str]] = {
    1: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    2: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    3: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    4: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    5: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    6: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    7: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    8: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "executes"},
    9: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "rejects"},
    10: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "rejects"},
    11: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "rejects"},
    12: {"WELLMAX_ONLY": "executes", "AUTOIOT_ONLY": "executes", "FLASH_FUSION": "rejects"},
}


def get_queries(dataset: str) -> list[dict]:
    if dataset == DATASET_WISDM:
        return WISDM_QUERIES
    if dataset == DATASET_MIT_ECG:
        return MIT_ECG_QUERIES
    if dataset == DATASET_BUS:
        return BUS_QUERIES
    raise ValueError(f"Unsupported dataset {dataset!r}. Supported: {SUPPORTED_DATASETS}")


def get_expected_outcomes(dataset: str) -> dict[int, dict[str, str]]:
    if dataset == DATASET_WISDM:
        return EXPECTED_OUTCOMES
    if dataset == DATASET_MIT_ECG:
        return EXPECTED_OUTCOMES_MIT_ECG
    if dataset == DATASET_BUS:
        return EXPECTED_OUTCOMES_BUS
    raise ValueError(f"Unsupported dataset {dataset!r}. Supported: {SUPPORTED_DATASETS}")
