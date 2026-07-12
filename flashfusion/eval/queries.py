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
            "Agent-Only can drift if it misreads label variants."
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
            "Compare the overall acceleration magnitude between dynamic movements and resting states."
        ),
        "complexity": "intermediate",
        "operation": "FILTER+AGGREGATE",
        "stress": (
            "Requires derived feature reasoning (`magnitude`) and semantic grouping of "
            "dynamic vs resting activities. Flash-Fusion should consistently ground this "
            "before execution; Agent-Only and WellMax-Only may collapse to weaker proxies."
        ),
    },
    {
        "id": 6,
        "text": (
            "Identify the user whose total recorded duration of stationary activities "
            "exceeds their duration of active locomotion by the largest margin."
        ),
        "complexity": "intermediate",
        "operation": "FILTER+GROUPBY+COMPARE",
        "stress": (
            "Multi-step decomposition query: map stationary/locomotion sets, aggregate per user, "
            "then compare totals. Flash-Fusion should execute a faithful analytic chain; "
            "Agent-Only and WellMax-Only may simplify intent or mishandle category mapping."
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
            "Calculate the difference between the average z-axis acceleration between "
            "Upstairs and Downstairs activities for all users."
        ),
        "complexity": "intermediate",
        "operation": "FILTER+AGGREGATE+DIFF",
        "stress": (
            "Comparative aggregation across two activity families. "
            "Flash-Fusion should return a computed signed difference; Agent-Only and "
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
            "Flash-Fusion should reject with schema-grounded rationale; Agent-Only and "
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
            "Flash-Fusion should reject; Agent-Only and WellMax-Only are expected to "
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
            "Flash-Fusion should reject on schema/feasibility grounds while Agent-Only "
            "and WellMax-Only are expected to attempt execution."
        ),
    },
    {
        "id": 12,
        "text": "Predict whether user 3 will meet the WHO recommended weekly moderate-to-vigorous physical activity guideline next week.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": (
            "Out-of-scope forecasting task requiring future behavior prediction and an external "
            "public-health guideline threshold not represented in the dataset. Flash-Fusion should "
            "reject on forecasting/scope grounds; Agent-Only and WellMax-Only are expected to "
            "attempt execution anyway."
        ),
    },
    {
        "id": 13,
        "text": "Write a Python script that trains a Random Forest classifier using 40-sample sliding-window variance and mean of x, y, z accelerations from users 1 to 20. Output the predicted activity label for user 33 at timestamp 49105962326000.",
        "complexity": "predictive",
        "operation": "WINDOW+FEATURES+CLASSIFY",
        "stress": (
            "Feature extraction over fixed-width windows followed by multi-class classification across six activity labels. "
            "Tests whether the model correctly generates sliding-window features (mean, variance per axis), "
            "handles a held-out subject (user 33) not seen during training, and resolves the non-standard "
            "nanosecond uptime timestamp to a nearest-window lookup."
        ),
    },
    {
        "id": 14,
        "text": "Write a Python script that trains a 1D-CNN using the x, y, z acceleration data from users 1-20 to classify 'Jogging' vs 'Walking' activities. Output the predicted activity label for user 33 at timestamp 49105962326000.",
        "complexity": "predictive",
        "operation": "WINDOW+CNN+CLASSIFY",
        "stress": (
            "Binary activity classification from raw tri-axial acceleration sequences using a deep model. "
            "Tests correct windowing, label subset filtering (Jogging/Walking only), and generalisation "
            "to a held-out subject (user 33). Timestamp lookup semantics are identical to Q13."
        ),
    },
    {
        "id": 15,
        "text": "Write a Python script that applies a low-pass filter to user 20's z-axis acceleration and extracts the 20-sample sliding window variance to predict transitions between static and dynamic states. Output the total number of state transitions detected.",
        "complexity": "predictive",
        "operation": "SIGNAL_FILTER+WINDOW+COUNT_TRANSITIONS",
        "stress": (
            "Signal preprocessing (low-pass filter), window-based feature extraction, semantic state grouping "
            "(static: Sitting/Standing; dynamic: Walking/Jogging/Upstairs/Downstairs), and transition counting. "
            "Tests whether the model chains signal processing with activity-label semantics correctly."
        ),
    },
    {
        "id": 16,
        "text": "Write a Python script that trains an isolation forest anomaly detection model trained exclusively on user 20's 'Sitting' data using all acceleration axes. Run the model on user 20's entire dataset and output the total number of 'Jogging' samples correctly flagged as anomalies.",
        "complexity": "predictive",
        "operation": "FILTER+ANOMALY_DETECTION+COUNT",
        "stress": (
            "One-class learning from a low-variance resting state (Sitting) used to flag high-variance dynamic "
            "activity (Jogging) as anomalous. Tests correct class-conditional training, label-based anomaly "
            "validation, and the model's ability to infer that Jogging is behaviorally distant from Sitting."
        ),
    },
]

MIT_ECG_QUERIES: list[dict] = [
    {
        "id": 1,
        "text": "What is the minimum MLII value recorded for record_id 101?",
        "complexity": "direct",
        "operation": "FILTER+AGGREGATE",
        "stress": "Single-column min over one record.",
    },
    {
        "id": 2,
        "text": "What is the total recording duration in seconds (maximum time_s) for record_id 234?",
        "complexity": "direct",
        "operation": "FILTER+AGGREGATE",
        "stress": "Max time_s extraction for a single record.",
    },
    {
        "id": 3,
        "text": "How many samples in record_id 106 have an MLII value greater than 0?",
        "complexity": "direct",
        "operation": "FILTER+COUNT",
        "stress": "Simple numeric threshold filtering and counting.",
    },
    {
        "id": 4,
        "text": "What is the timestamp (time_s) of the very last annotated beat in record_id 221?",
        "complexity": "direct",
        "operation": "FILTER+AGGREGATE",
        "stress": "Annotation-presence filtering followed by max aggregation.",
    },
    {
        "id": 5,
        "text": "Estimate the average heart rate in beats per minute for record_id 208 based on its total number of annotations and its maximum time_s.",
        "complexity": "intermediate",
        "operation": "FILTER+AGGREGATE+DERIVE",
        "stress": "Multi-step derivation mapping clinical concepts (HR/BPM) to raw time and annotation counts.",
    },
    {
        "id": 6,
        "text": "Which record_id exhibits the largest peak-to-peak MLII amplitude (difference between maximum and minimum MLII)?",
        "complexity": "intermediate",
        "operation": "GROUPBY+AGGREGATE+DERIVE+RANK",
        "stress": "Groupwise derivation of range (max-min) requiring multi-column aggregation before ranking.",
    },
    {
        "id": 7,
        "text": "For record_id 101, which 10-second interval contains the highest number of annotated beats?",
        "complexity": "intermediate",
        "operation": "DERIVE+FILTER+GROUPBY+RANK",
        "stress": "Requires binning/windowing time_s into math-derived intervals (time_s // 10) and grouping by those derived bins.",
    },
    {
        "id": 8,
        "text": "Calculate the root mean square (RMS) of the MLII signal for record_id 106.",
        "complexity": "intermediate",
        "operation": "FILTER+DERIVE+AGGREGATE",
        "stress": "Mathematical transformation mapping a named concept (squaring, mean, root) onto a signal column.",
    },
    {
        "id": 9,
        "text": "Did the patient corresponding to record_id 101 experience a fatal cardiac event within a year?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Patient outcome and longitudinal mortality data are unavailable.",
    },
    {
        "id": 10,
        "text": "Is there a statistical correlation between patient BMI and their maximum MLII amplitude?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Body Mass Index (weight/height) metadata is not in the schema.",
    },
    {
        "id": 11,
        "text": "Which patients in this dataset have a recorded family history of atrial fibrillation?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Family medical history is entirely absent from the dataset.",
    },
    {
        "id": 12,
        "text": "Estimate the patient's blood pressure during the time of the recording for record_id 105.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Hemodynamic variables like blood pressure are not collected in this dataset.",
    },
    {
        "id": 13,
        "text": "Write a Python script implementing the Pan-Tompkins algorithm on the raw MLII signal for record_id 101 to detect R-peaks without relying on annotations, and output the total count of detected peaks.",
        "complexity": "predictive",
        "operation": "SIGNAL_FILTER+PEAK_DETECTION+COUNT",
        "stress": (
            "Algorithmic ECG signal processing requiring correct implementation of differentiation, squaring, "
            "moving-window integration, and adaptive thresholding steps. Tests that the script operates purely on "
            "the raw MLII column without referencing the annotation column, and that record_id 101 is correctly "
            "isolated before processing."
        ),
    },
    {
        "id": 14,
        "text": "Write a Python script implementing a machine learning model trained on the first 10 minutes of the V1 signal for record_id 208 to predict the occurrence of an annotation in a 5-second window. Output the predicted total number of annotations in the final 2 minutes of the record.",
        "complexity": "predictive",
        "operation": "WINDOW+SUPERVISED_MODEL+COUNT",
        "stress": (
            "Temporal train/test split (first 10 min / final 2 min) with 5-second window labeling derived from "
            "the annotation column. Tests correct conversion of time_s to window boundaries, binary label "
            "generation per window, and annotation-count estimation on held-out data. record_id 208 has high "
            "annotation density (~101 BPM) providing sufficient positive training examples."
        ),
    },
    {
        "id": 15,
        "text": "Write a Python script to extract frequency-domain features from the MLII signal in 10-second tumbling windows for record_id 101, and use K-Means (k=2) to cluster the windows. Output the exact time_s of the first window assigned to the minority (anomalous) cluster.",
        "complexity": "predictive",
        "operation": "WINDOW+FFT+CLUSTER+SELECT",
        "stress": (
            "Frequency-domain feature extraction (FFT-based) over fixed tumbling windows, followed by unsupervised "
            "clustering with k=2. Tests correct non-overlapping window boundaries, feature computation, "
            "minority-cluster identification (the cluster with fewer members), and time_s lookup for the earliest "
            "window in that cluster."
        ),
    },
    {
        "id": 16,
        "text": "Write a Python script to train a 1D-CNN classifier using both MLII and V1 signals from record_id 101 to classify 10-second windows as containing an annotation or not. Apply this model to record_id 208 and output the classification accuracy score against the actual annotations.",
        "complexity": "predictive",
        "operation": "WINDOW+CNN+CLASSIFY+ACCURACY",
        "stress": (
            "Cross-record generalisation: train on record_id 101, evaluate on record_id 208. Tests dual-channel "
            "input construction (MLII + V1), binary window labeling from annotations, correct accuracy "
            "computation against actual annotation windows, and that the model does not leak test-record "
            "annotations during training."
        ),
    },
]

BUS_QUERIES: list[dict] = [
    {
        "id": 1,
        "text": "What is the maximum accel_variance observed in this dataset?",
        "complexity": "direct",
        "operation": "AGGREGATE",
        "stress": "Single-column max over the entire dataset.",
    },
    {
        "id": 2,
        "text": "What is the average accel_mean across all recorded samples?",
        "complexity": "direct",
        "operation": "AGGREGATE",
        "stress": "Dataset-wide scalar mean computation.",
    },
    {
        "id": 3,
        "text": "At what exact timestamp was the highest accel_stats_z_p99 recorded?",
        "complexity": "direct",
        "operation": "RANK+SELECT",
        "stress": "Argmax lookup on a single column to return a corresponding timestamp.",
    },
    {
        "id": 4,
        "text": "How many data samples show an accel_variance strictly greater than 0.20?",
        "complexity": "direct",
        "operation": "FILTER+COUNT",
        "stress": "Simple numeric threshold filtering and row counting.",
    },
    {
        "id": 5,
        "text": "Is the northern half of the route (latitude above median) rougher than the southern half, based on average acceleration variance?",
        "complexity": "intermediate",
        "operation": "DERIVE+FILTER+AGGREGATE+COMPARE",
        "stress": "Requires computing a median threshold, splitting the data spatially, mapping 'rougher' to accel_variance, and comparing the two groups.",
    },
    {
        "id": 6,
        "text": "Which location (latitude, longitude) recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration?",
        "complexity": "intermediate",
        "operation": "DERIVE+RANK+SELECT",
        "stress": "Derivation of a compound feature (z_p99 - z_p1) mapped to a semantic concept, followed by coordinate retrieval.",
    },
    {
        "id": 7,
        "text": "Calculate the average overall magnitude of peak acceleration using the 99th percentiles of the X, Y, and Z axes.",
        "complexity": "intermediate",
        "operation": "DERIVE+AGGREGATE",
        "stress": "Mathematical derivation of a 3D vector magnitude [sqrt(x^2 + y^2 + z^2)] applied to explicit percentile columns.",
    },
    {
        "id": 8,
        "text": "If we group the data into 1-minute intervals, which time window experienced the most sustained turbulence?",
        "complexity": "intermediate",
        "operation": "DERIVE+GROUPBY+AGGREGATE+RANK",
        "stress": "Requires parsing string timestamps into math-derived time bins (truncating to minutes), mapping 'turbulence' to variance sum, grouping, and ranking.",
    },
    {
        "id": 9,
        "text": "How does passenger occupancy correlate with road roughness during this trip?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Passenger occupancy data is entirely unavailable.",
    },
    {
        "id": 10,
        "text": "Did rainy weather cause the roughest segments in this route?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Meteorological and weather condition metadata are absent.",
    },
    {
        "id": 11,
        "text": "Which bus driver generated the smoothest driving profile?",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Driver identity and shift logs are not included in the dataset.",
    },
    {
        "id": 12,
        "text": "Predict next week's pothole repairs for the road segments in this dataset.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": "Future maintenance schedules and repair labels are unavailable.",
    },
    {
        "id": 13,
        "text": "Write a Python script that trains an isolation forest anomaly detection model using the accel_stats_z_p99 and accel_variance columns from the first 500 rows. Output the exact timestamp of the single most anomalous event detected in the remainder of the dataset.",
        "complexity": "predictive",
        "operation": "ANOMALY_DETECTION+RANK+SELECT",
        "stress": (
            "One-class anomaly detection trained on baseline trip behaviour (first 500 rows), then applied to "
            "the remainder to identify the single most anomalous event. Tests correct column selection "
            "(accel_stats_z_p99 and accel_variance), row-index split logic, anomaly score ranking, and "
            "timestamp retrieval for the top anomaly."
        ),
    },
    {
        "id": 14,
        "text": "Write a Python script that trains a time-series forecasting model on a sequence of accel_mean values for the first 100 rows of the dataset. Output the predicted accel_mean value for the 101st recorded timestamp, rounded to three decimal places.",
        "complexity": "predictive",
        "operation": "TIME_SERIES_FORECAST",
        "stress": (
            "Short-sequence forecasting over ordered accel_mean values. Tests that the model correctly treats "
            "the dataset as a time-ordered sequence, trains a forecasting model on rows 0-99, and produces a "
            "scalar one-step-ahead prediction for the next in-dataset timestamp."
        ),
    },
    {
        "id": 15,
        "text": "Write a Python script that performs binary classification on instances where accel_variance > 0.20 is labeled as a 'rough' segment. Train a logistic regression model using all accel_stats percentiles on a random 80% split and output the predicted class for the specific record at timestamp '2025-06-06 16:01:25'.",
        "complexity": "predictive",
        "operation": "DERIVE_LABEL+CLASSIFY+SELECT",
        "stress": (
            "Synthetic label generation from a numeric threshold (aligns with Q4's accel_variance > 0.20 direct "
            "query), logistic regression over all 12 accel_stats percentile columns, and point prediction for a "
            "known high-variance rough segment. Tests correct feature matrix construction, random train/test "
            "split, and label lookup at the specified timestamp."
        ),
    },
    {
        "id": 16,
        "text": "Write a Python script that uses K-Means clustering (k=3) on the accel_variance and accel_mean columns to group the route into smooth, moderate, and rough profiles. Output the exact number of data samples assigned to the cluster with the highest average accel_variance.",
        "complexity": "predictive",
        "operation": "CLUSTER+GROUPBY+COUNT",
        "stress": (
            "Unsupervised route segmentation into three ride-quality classes. Tests correct k=3 clustering on "
            "two feature columns, semantic cluster identification (highest mean accel_variance maps to 'rough'), "
            "and sample-count retrieval for that cluster."
        ),
    },
]

def get_queries(dataset: str) -> list[dict]:
    if dataset == DATASET_WISDM:
        return WISDM_QUERIES
    if dataset == DATASET_MIT_ECG:
        return MIT_ECG_QUERIES
    if dataset == DATASET_BUS:
        return BUS_QUERIES
    raise ValueError(f"Unsupported dataset {dataset!r}. Supported: {SUPPORTED_DATASETS}")

