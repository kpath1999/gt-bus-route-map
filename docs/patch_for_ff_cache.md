"""
Surgical patch for flashfusion/baselines/flash_fusion_cache.py

GOAL
----
Replace the single monolithic GROUNDING_SYSTEM_PROMPT with a routed
assembly: a small universal preamble (always sent) + only the operator-
specific rule blocks for operators actually present in the current
cached skeleton.

This patch does NOT touch:
- build_compact_operator_spec(skeleton)  (already skeleton-conditional)
- _grounding_prompt()                    (unchanged)
- OUT_OF_SCOPE_SYSTEM_PROMPT             (already small, unrelated)
- _apply_grounding_semantic_guards()     (post-hoc code guards, unchanged)

APPLY IN 4 EDITS
----------------
1) DELETE the existing `GROUNDING_SYSTEM_PROMPT = """..."""` block entirely
   and REPLACE it with the constants + builder function below (Section A).
2) In `_invoke_light_for_plan`, add a `skeleton: list[str]` parameter and
   swap the SystemMessage source (Section B).
3) In `_execute_grounded_cache_entry`, pass the skeleton into the call
   (Section C).
4) (Optional but recommended) add the verification snippet at the bottom
   to confirm token savings before/after on your own machine.
"""

# =============================================================================
# SECTION A — replaces the old `GROUNDING_SYSTEM_PROMPT = """..."""` constant
# =============================================================================

from functools import lru_cache  # add to the existing import block at top of file

_GROUNDING_PREAMBLE = """You ground values into a fixed Flash-Fusion typed
operator skeleton. Return exactly one JSON object and no markdown, prose, or
code fences.

You MUST preserve the supplied operator sequence exactly: same number of
steps, same operator names, and same order as the REQUIRED OUTPUT checklist
given below. Never emit fewer or more steps than that checklist lists. Each
step object MUST use ONLY the exact field names given for its operator in
OPERATOR FIELD SPEC below — no renamed, added, or extra fields (e.g. never
"filter_column"/"filter_op"/"filter_value"/"input" — those are not real
fields). Fill those fields using the QUESTION and the LIVE DATASET SCHEMA.
Do not invent columns. Do not add, remove, reorder, or rename operators. Do
not return an answer and do not write Python.

Strict JSON requirements:
- Return a single valid JSON object only; no trailing commas.
- The top-level keys are exactly: {"version":"1","steps":[...]}.
- Every item in `steps` must be a valid object with an `op` field.
- Do not include comments, markdown fences, or any text before/after the JSON.
- If a step cannot be grounded, emit exactly:
  {"cache_grounding_failed": true, "reason": "..."}

The returned plan will be parsed by Pydantic (extra fields are rejected),
checked against the live DataFrame schema, and executed deterministically.
If the requested structure cannot be grounded from the schema, return:
{"cache_grounding_failed": true, "reason": "..."}

Semantic grounding rules (must follow):"""

# Applies regardless of which operators are present -- always included.
_UNIVERSAL_SEMANTIC_RULE = """- Strict 1-to-1 checklist correspondence:
    - Step index `i` in your JSON output MUST correspond to step `i` of the checklist.
    - NEVER emit an operator more times than it appears in the REQUIRED OUTPUT checklist.
    - If the checklist has 1 DERIVE_DURATION_SECONDS step, emit EXACTLY 1 DERIVE_DURATION_SECONDS
      step regardless of how many categories or comparison groups the question mentions.
- Keep operator sequence fixed, but choose semantically correct field values."""

# Each entry: (frozenset of operators that trigger this block, rule text).
# A block is included if ANY of its trigger operators appear in the skeleton.
_OPERATOR_SEMANTIC_RULES: list[tuple[frozenset[str], str]] = [
    (
        frozenset({"FILTER_COMPARE"}),
        """- Bare entity/id mentions imply equality filters on that key:
    - Example: "record_id 106", "for record_id 106", "user 20" -> comparator must be
        "eq" with that value for the corresponding key column.
    - Do NOT weaken bare key mentions to ranges like gt/ge/lt/le.
- Explicit relational language must map to the matching comparator:
    - ">", "strictly greater", "greater than", "above" -> "gt"
    - ">=", "at least", "no less than" -> "gte"
    - "<", "strictly less", "below" -> "lt"
    - "<=", "at most", "no more than" -> "lte"
- FILTER_COMPARE.comparator accepts ONLY "eq","ne","gt","gte","lt","lte".
    Never emit "max", "min", "top", or a null value for comparator or value.""",
    ),
    (
        frozenset({"RANK_ROWS"}),
        """- To select the row(s) with the largest/smallest value of a derived column
    (e.g. "by the largest margin", "highest", "greatest"), use RANK_ROWS alone.
    Do NOT precede RANK_ROWS with a FILTER_COMPARE step whose purpose is
    extremum selection - that logic belongs to RANK_ROWS's direction field only.""",
    ),
    (
        frozenset({"SPLIT_BY_VALUES", "PARALLEL_AGGREGATE"}),
        """- Any step or branch object that sets non-empty filter_values MUST also set
    filter_column to the categorical column those values belong to (grounded
    from the LIVE DATASET SCHEMA, e.g. "activity_label"). Never leave
    filter_column null when filter_values is non-empty.""",
    ),
    (
        frozenset({"COUNT_ROWS"}),
        """- "how many", "number of samples/rows" means row counting semantics:
    - Use COUNT_ROWS / COUNT / group size when asking for sample counts.
    - Use nunique only when the question asks for unique entities.
- "highest/lowest total number of ... samples" means per-entity sample COUNT then rank.
    - Do NOT substitute sum of a sensor channel for sample counts.""",
    ),
    (
        frozenset({"DERIVE_BIN", "GROUP_AGGREGATE", "RANK_GROUPS"}),
        """- For a `DERIVE_BIN`, `GROUP_AGGREGATE`, `RANK_GROUPS` sequence answering
    "which N-second interval has the highest number/count of ...":
        - `GROUP_AGGREGATE.group_by` MUST be exactly the `DERIVE_BIN.result` column.
            Do not group by an entity key (such as `record_id`) already narrowed by a
            preceding filter; that produces one entity group, not interval groups.
        - Use `aggregate="count"` with `column=null` to count rows in each interval,
            then use `RANK_GROUPS.direction="max"`.""",
    ),
    (
        frozenset({"GROUP_AGGREGATE", "AGGREGATE_PARTITIONS"}),
        """- "average X" / "mean X" means mean aggregation of X.
    - Do NOT use variance unless the question explicitly asks for variance.""",
    ),
    (
        frozenset({"AGGREGATE_PARTITIONS", "COMPARE_PARTITIONS"}),
        """- Comparative roughness phrased on average variance must preserve average semantics.
    - If asked "rougher" with average/mean variance, compare mean variance values and use
        a comparison mode aligned to the question (difference/which higher), not ratio by default.""",
    ),
    (
        frozenset({"DERIVE_BINARY", "RANK_ROWS"}),
        """- "A exceeds/is greater than B by the largest margin" implies a SIGNED
    difference in the stated order (A - B), not abs_difference. Compute
    DERIVE_BINARY with operation="subtract" in that order, optionally gate
    with FILTER_COMPARE(comparator="gt", value=0) to enforce the stated
    direction, then RANK_ROWS on that signed column. Use abs_difference only
    when the query says "difference" or "contrast" with no stated direction.""",
    ),
    (
        frozenset({"DERIVE_BIN"}),
        """- DERIVE_BIN modes (mutually exclusive):
    - For numeric columns (e.g. float/int elapsed seconds like `time_s` or numeric values):
      use `kind="numeric"`, supply `width` (e.g. 60.0), and set `freq=null`, `epoch_unit=null`.
    - For datetime / calendar timestamps:
      use `kind="temporal"`, supply `freq` (e.g. "60s" or "1min"), and set `width=null`.
        - For a numeric epoch timestamp used with `kind="temporal"`, supply both
            `freq` and its explicit `epoch_unit` (one of `"s"`, `"ms"`, `"us"`, or
            `"ns"`), and set `width=null`. Infer the unit from the schema column
            name: `time_s` means `epoch_unit="s"`; suffixes `_ms`, `_us`, and `_ns`
            mean `"ms"`, `"us"`, and `"ns"` respectively. Never leave
            `epoch_unit` null for temporal binning of a numeric source.
    - NEVER mix `kind="temporal"` with `width` or `freq=null`.""",
    ),
    (
        frozenset({"DERIVE_DURATION_SECONDS"}),
        """- DERIVE_DURATION_SECONDS fields:
    - DERIVE_DURATION_SECONDS is materialized ONCE across the timeline:
      Emit a single DERIVE_DURATION_SECONDS step with a generic result column (e.g., "dt_s" or "duration_seconds").
      NEVER create multiple category-specific duration steps (e.g., "resting_duration_s" and "dynamic_duration_s").
      Category distinctions are handled entirely within downstream steps (e.g., PARALLEL_AGGREGATE branches filtering
      by activity_label and summing the single derived duration column).
    - `group_by` must be the entity key column only (e.g. ['subject_id'] or ['record_id']).
      Never include category/label columns (e.g. 'activity_label') in DERIVE_DURATION_SECONDS.group_by;
      category filtering is handled in downstream steps (e.g. PARALLEL_AGGREGATE branches).
    - `fill_first` MUST be a float number (default 0.0), NEVER null or omitted.
    - `clip_negative` MUST be a boolean (default true).
    - `result` is the derived column name (e.g. "dt_s" or "duration_seconds").""",
    ),
    (
        frozenset({"SPLIT_BY_VALUES", "AGGREGATE_PARTITIONS"}),
        """- When comparing two or more named groups of a categorical column (e.g.
    "compare X between label A and label B"), emit one SPLIT_BY_VALUES step
    PER GROUP, each with its own distinct label and its own values list.
    Never reuse the same label across steps and never merge multiple
    comparison groups' values into a single SPLIT_BY_VALUES call.
    AGGREGATE_PARTITIONS.partitions must then list every distinct label
    produced this way (minimum two), never a single repeated label.""",
    ),
    (
        frozenset({"PREDICTIVE_PIPELINE"}),
        """- Train split and holdout row are independent fields in predictive plans:
    - The query may mention both a training split (e.g., 'first 80%') and a holdout row
        position (e.g., 'first row in the holdout set'). These are independent.
        `train_fraction` controls the split; `holdout_row` must reflect only the phrase
        that describes which holdout row to predict. If the query says 'first row in the
        holdout set', `holdout_row` must be 'first'.
- PREDICTIVE_PIPELINE target semantics:
        - `target_column` MUST be the real DataFrame schema column being predicted (e.g.,
            "annotation"). It is a column reference, not a class label or a check name.
        - `target_from_non_empty` controls target construction. Set it to true when the
            question asks whether `target_column` is present, non-empty, or annotated; this
            converts the target into the binary classes 0/1 before training.
        - `target_label` is display text for the prediction sentence only. It is NOT a
            DataFrame column, is NOT used to construct `y`, and must never replace
            `target_column`. For a presence question, use `target_label="present"`.
        - For "whether an annotation is present" / "whether <column> is non-empty", emit
            `target_column="annotation"` (or the real named schema column),
            `target_from_non_empty=true`, and `target_label="present"`. Do not emit
            `target_from_non_empty=false`, which trains on raw annotation strings.""",
    ),
]


@lru_cache(maxsize=256)
def _build_grounding_system_prompt(skeleton: tuple[str, ...]) -> str:
    """Assemble the grounding system prompt for exactly this skeleton.

    Cached by skeleton so (a) repeated calls with the same skeleton are
    free after the first, and (b) the output is guaranteed byte-identical
    across every query that shares this skeleton -- a precondition for
    KV-cache prefix reuse on the serving side (e.g. Ollama prefix caching).
    Must be called with a tuple (hashable) rather than a list.
    """
    skeleton_set = set(skeleton)
    blocks = [_GROUNDING_PREAMBLE, _UNIVERSAL_SEMANTIC_RULE]
    for trigger_ops, text in _OPERATOR_SEMANTIC_RULES:
        if trigger_ops & skeleton_set:
            blocks.append(text)
    return "\n".join(blocks)


# =============================================================================
# SECTION B — edit _invoke_light_for_plan
# =============================================================================
#
# BEFORE:
#
# def _invoke_light_for_plan(
#     client: LLMClient, prompt: str, trace: CacheGroundingTrace | None = None
# ) -> dict[str, Any]:
#     ...
#     messages = [
#         SystemMessage(content=GROUNDING_SYSTEM_PROMPT),
#         HumanMessage(content=prompt),
#     ]
#
# AFTER:

def _invoke_light_for_plan(
    client: LLMClient,
    prompt: str,
    skeleton: list[str],
    trace: CacheGroundingTrace | None = None,
) -> dict[str, Any]:
    """Call only the configured light model and parse a single JSON object.

    Messages are passed directly rather than through a ``ChatPromptTemplate``:
    the system prompt contains a literal JSON output contract, whose braces a
    template would try to interpolate. Going through ``invoke_messages`` also
    records tokens, latency, and cost in the client's call_log — otherwise the
    cache baseline would report a free LLM call in the benchmark.
    """
    light = getattr(client, "light", None) or client
    if getattr(light, "llm", None) is None or not hasattr(light, "invoke_messages"):
        raise RuntimeError("cache grounding requires client.light.llm")
    system_prompt = _build_grounding_system_prompt(tuple(skeleton))
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ]
    started = time.perf_counter()
    raw = light.invoke_messages(messages, stage="cache_grounding")
    _record(
        trace,
        raw_light_output=raw,
        grounding_latency_s=time.perf_counter() - started,
    )
    repaired = _repair_light_json(raw)
    parsed = json.loads(repaired)
    if not isinstance(parsed, dict):
        raise ValueError("light model output must be a JSON object")
    if parsed.get("cache_grounding_failed") is True:
        raise ValueError(f"light model declined grounding: {parsed.get('reason', '')}")
    _record(trace, parsed_plan=parsed)
    return parsed


# =============================================================================
# SECTION C — edit the call site inside _execute_grounded_cache_entry
# =============================================================================
#
# BEFORE:
#
#     grounding_started = time.perf_counter()
#     try:
#         prompt = _grounding_prompt(query, entry, df)
#         _record(trace, prompt=prompt)
#         raw_plan = _invoke_light_for_plan(client, prompt, trace)
#     finally:
#         stage_latency["cache_grounding"] += _accounted_light_latency(client, grounding_started)
#
# AFTER (only the _invoke_light_for_plan call changes -- add skeleton arg):
#
#     grounding_started = time.perf_counter()
#     try:
#         prompt = _grounding_prompt(query, entry, df)
#         _record(trace, prompt=prompt)
#         raw_plan = _invoke_light_for_plan(client, prompt, entry["operator_skeleton"], trace)
#     finally:
#         stage_latency["cache_grounding"] += _accounted_light_latency(client, grounding_started)


# =============================================================================
# SECTION D — verification snippet (run standalone, not part of the module)
# =============================================================================
if __name__ == "__main__":
    mit_ecg_q7_skeleton = ["FILTER_COMPARE", "FILTER_NOT_EMPTY", "DERIVE_BIN", "GROUP_AGGREGATE", "RANK_GROUPS"]
    trimmed = _build_grounding_system_prompt(tuple(mit_ecg_q7_skeleton))
    print(f"Trimmed system prompt length: {len(trimmed)} chars (~{len(trimmed.split())} words)")
    print("---")
    print(trimmed)