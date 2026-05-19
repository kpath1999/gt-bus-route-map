"""
eval/queries.py — WISDM benchmark query definitions.

DO NOT MODIFY without re-running the full benchmark and updating CLAUDE.md.

Each query is a dict with:
  id         (int)   1-indexed query identifier
  text       (str)   The exact natural language query fed to each baseline
  complexity (str)   "simple" | "medium" | "complex" | "out_of_scope"
  operation  (str)   Primary pandas operation expected (AGGREGATE, FILTER, etc.)
  stress     (str)   Which capability gap this query exposes

EXPECTED_OUTCOMES maps query id → baseline → expected behaviour keyword:
  "executes"  — agent runs code and produces a numerical/tabular result
  "hallucinates" — LLM-Only fabricates a plausible-sounding answer
    "rejects"   — Flash-Fusion guardrail blocks execution
"""

from __future__ import annotations

WISDM_QUERIES: list[dict] = [
    {
        "id": 1,
        "text": "How many data samples were recorded for each activity?",
        "complexity": "simple",
        "operation": "AGGREGATE",
        "stress": (
            "Baseline divergence on hallucination vs. real computation. "
            "LLM-Only invents counts; AutoIOT-Only and Flash-Fusion execute correctly. "
            "Flash-Fusion additionally maps letter codes to activity names in the response."
        ),
    },
    {
        "id": 2,
        "text": (
            "Which 3 activities have the highest average overall acceleration magnitude?"
        ),
        "complexity": "medium",
        "operation": "GROUPBY+RANK",
        "stress": (
            "Derived feature required: `magnitude` = sqrt(x²+y²+z²) is not a raw column. "
            "Only Flash-Fusion's WISDMAdapter materialises it correctly before execution. "
            "AutoIOT-Only may use mean(x) or (x+y+z)/3 as a wrong proxy. "
            "LLM-Only lists activities from training knowledge, not from this dataset."
        ),
    },
    {
        "id": 3,
        "text": (
            "Compare the average acceleration magnitude between sedentary activities "
            "and locomotion activities."
        ),
        "complexity": "medium",
        "operation": "FILTER+AGGREGATE",
        "stress": (
            "Semantic group resolution without codebook. "
            "AutoIOT-Only has no codebook and cannot map 'sedentary' → {D,E} or "
            "'locomotion' → {A,B,C} — produces wrong filter or fails entirely. "
            "Flash-Fusion and WellMax-Only both resolve via codebook; "
            "only Flash-Fusion then executes the computation."
        ),
    },
    {
        "id": 4,
        "text": "What is the average heart rate recorded during jogging?",
        "complexity": "simple",
        "operation": "FILTER+AGGREGATE",
        "stress": (
            "Schema trap: `heart_rate` does not exist in the dataset. "
            "LLM-Only fabricates a plausible BPM value. "
            "AutoIOT-Only and WellMax-Only now attempt execution and may still produce weak or erroneous output. "
            "Flash-Fusion guardrail should reject with a schema-based explanation."
        ),
    },
    {
        "id": 5,
        "text": (
            "For subject 1610, what percentage of their recorded samples involve "
            "hand-related activities — typing, writing, and clapping?"
        ),
        "complexity": "medium",
        "operation": "FILTER+AGGREGATE",
        "stress": (
            "Multi-filter with English-to-code resolution. "
            "AutoIOT-Only cannot map 'typing'→F, 'writing'→Q, 'clapping'→R without codebook. "
            "Flash-Fusion resolves F/Q/R via codebook in Stage 2; "
            "Stage 3 decomposes into FILTER(subject_id==1610) → FILTER(F,Q,R) → count/total."
        ),
    },
    {
        "id": 6,
        "text": (
            "Which subjects show unusually high peak acceleration values that could "
            "indicate sensor noise or data errors?"
        ),
        "complexity": "complex",
        "operation": "WINDOW+FILTER",
        "stress": (
            "Threshold semantics: 'unusually high' has no universal definition. "
            "Only Flash-Fusion grounds this via Stage 2 (z-score > 3 on `magnitude`) "
            "before execution, producing a statistically defensible threshold. "
            "AutoIOT-Only uses an arbitrary threshold. "
            "LLM-Only fabricates subject IDs."
        ),
    },
    {
        "id": 7,
        "text": (
            "Is x-axis acceleration positively correlated with z-axis acceleration "
            "during stair climbing?"
        ),
        "complexity": "simple",
        "operation": "FILTER+CORRELATE",
        "stress": (
            "Straightforward filter + correlation. AutoIOT-Only likely matches Flash-Fusion "
            "on accuracy (activity_label=='C', then df[x].corr(df[z])). "
            "Useful for measuring when the rewriting overhead adds latency/cost without "
            "improving accuracy — WellMax-Only describes but does not compute the value."
        ),
    },
    {
        "id": 8,
        "text": (
            "Which subject has the longest total recording duration based on their "
            "timestamp range?"
        ),
        "complexity": "medium",
        "operation": "GROUPBY+RANK",
        "stress": (
            "Both AutoIOT-Only and Flash-Fusion should produce the correct answer via "
            "groupby(subject_id).agg(lambda x: x.max()-x.min()).idxmax(). "
            "Flash-Fusion additionally validates via judge that duration is in milliseconds. "
            "LLM-Only picks a fabricated subject ID; WellMax-Only describes the method."
        ),
    },
    {
        "id": 9,
        "text": (
            "Based on their three-axis acceleration patterns, which two distinct activities "
            "are most similar to each other?"
        ),
        "complexity": "complex",
        "operation": "GROUPBY+CORRELATE",
        "stress": (
            "Highest complexity — requires a 4-step analytic chain: "
            "GROUPBY(activity_label) → AGGREGATE mean(x,y,z) → CORRELATE centroid matrix → RANK off-diagonal max. "
            "Only Flash-Fusion reliably decomposes and executes this via Stage 3. "
            "AutoIOT-Only may attempt groupby.mean().corr() but likely fails on result extraction. "
            "LLM-Only asserts 'walking and jogging' from training priors."
        ),
    },
    {
        "id": 10,
        "text": "Based on the data, predict which activity this subject is likely to perform next.",
        "complexity": "out_of_scope",
        "operation": "NONE",
        "stress": (
            "Forecasting rejection test. The query sounds data-driven but requires a "
            "sequence model or temporal forecasting capability the dataset cannot support. "
            "LLM-Only generates a plausible prediction narrative — entirely fabricated. "
            "AutoIOT-Only and WellMax-Only now attempt execution without feasibility gating. "
            "Flash-Fusion should reject via guardrail with an explicit explanation. "
            "Flash-Fusion additionally marks 'predict next activity' as UNMAPPABLE in Stage 2."
        ),
    },
]

# Expected behaviour per baseline for each query.
# Used in tests and reporting.
EXPECTED_OUTCOMES: dict[int, dict[str, str]] = {
    1: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    2: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",   # correct magnitude via adapter
        "AUTOIOT_ONLY":  "executes",   # but with wrong proxy for magnitude
        "FLASH_FUSION":  "executes",   # with correct magnitude column
    },
    3: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",   # correct D/E and A/B/C codebook resolution
        "AUTOIOT_ONLY":  "executes",   # likely wrong group filter
        "FLASH_FUSION":  "executes",   # correct codebook-resolved filter
    },
    4: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "rejects",
    },
    5: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",   # correct F/Q/R mapping
        "AUTOIOT_ONLY":  "executes",   # likely wrong codes
        "FLASH_FUSION":  "executes",   # correct F/Q/R filter
    },
    6: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",   # z-score grounded threshold
        "AUTOIOT_ONLY":  "executes",   # arbitrary threshold
        "FLASH_FUSION":  "executes",   # z-score > 3 grounded threshold
    },
    7: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    8: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "executes",
    },
    9: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",   # may partially fail on result extraction
        "FLASH_FUSION":  "executes",
    },
    10: {
        "LLM_ONLY":     "hallucinates",
        "WELLMAX_ONLY":  "executes",
        "AUTOIOT_ONLY":  "executes",
        "FLASH_FUSION":  "rejects",
    },
}
