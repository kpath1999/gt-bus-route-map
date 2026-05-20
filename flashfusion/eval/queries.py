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
