# Replace the light-LM fast path with a deterministic, bucket-exclusion operator router

## Context

`flashfusion/baselines/flash_fusion.py` currently runs a light-LM "fast path" before
falling back to the full planner. `flashfusion/pipeline/stages.py` contains a related
fast path for column-concept resolution (skip an LLM call when every concept matches
the schema exactly/fuzzily; otherwise issue one corrective retry). `flashfusion/pipeline/operators.py`
defines `OPERATOR_VOCABULARY_SPEC`, `OPERATOR_VOCABULARY_VERSION`, and
`FLASH_FUSION_PLANNER_PREFIX`, which is built by concatenating the (large, static)
vocabulary spec with a plan-contract prefix so that cache breakpoints
(`_planner_prefix_message`) can be reused across queries.

The light LM is being removed because it produces semantic false positives: it
accepts a cached/common plan that is structurally valid but answers the wrong
question. We are replacing it with a **deterministic, zero-LM-call operator router**
that narrows `OPERATOR_VOCABULARY_SPEC` down to a smaller **canonical vocabulary
bucket** before the (unchanged) full planner LM call. This removes one LM round-trip
and shrinks planner input tokens, at the cost of the current single global
cache-prefix hit rate — that tradeoff is accepted.

## Design principle: exclude buckets, don't just select one

The router must be framed as **elimination-first, not selection-first**:

- Start from the set of ALL canonical buckets (see below) as candidates.
- Apply deterministic negative-evidence rules that DISQUALIFY a bucket only when
  there is no lexical/structural signal in the query that the bucket's operator
  families are needed.
- Never disqualify a bucket based on weak or absent evidence alone — disqualification
  requires an explicit, named rule to fire. Absence of a rule firing is NOT evidence
  of exclusion.
- If elimination leaves zero buckets, or the query trips an "ambiguous" detector,
  fall back to the `full` bucket (the entire `OPERATOR_VOCABULARY_SPEC`, i.e. current
  behavior with no narrowing).
- Log which buckets were excluded and why (rule name), so every planner failure can
  be traced back to a specific exclusion decision.

This is the opposite failure mode from the light LM: false positives there came from
committing to a specific wrong plan. Here, the only failure we must avoid is
excluding a bucket the gold plan actually needed (an operator-recall miss). Being
overly inclusive only costs tokens; being overly exclusive can make a correct plan
impossible. Bias every rule threshold toward inclusion.

## Canonical buckets to implement

Define these in a new module `flashfusion/pipeline/operator_router.py`:

```python
BUCKET_DIRECT = frozenset({
    "SELECT_COLUMN", "FILTER_COMPARE", "FILTER_IN", "FILTER_NOT_EMPTY",
    "FILTER_EQ_AGGREGATE", "AGGREGATE_COLUMN", "COUNT_ROWS", "COUNT_DISTINCT",
})
BUCKET_GROUP_RANK = BUCKET_DIRECT | frozenset({
    "GROUP_AGGREGATE", "AGGREGATE_GROUPS", "RANK_GROUPS", "RANK_ROWS",
})
BUCKET_PARTITION_COMPARE = BUCKET_DIRECT | frozenset({
    "SPLIT_BY_THRESHOLD", "SPLIT_BY_VALUES", "AGGREGATE_PARTITIONS",
    "COMPARE_PARTITIONS", "COMPARE_VALUES",
})
BUCKET_DERIVE = BUCKET_DIRECT | frozenset({
    "DERIVE_BINARY", "DERIVE_VECTOR_MAGNITUDE", "DERIVE_BIN", "DERIVE_DURATION_SECONDS",
})
BUCKET_CORRELATION = BUCKET_DIRECT | frozenset({"CORRELATE_COLUMNS"})
BUCKET_PARALLEL = BUCKET_GROUP_RANK | frozenset({"PARALLEL_AGGREGATE"})
BUCKET_PREDICTIVE = frozenset({"PREDICTIVE_PIPELINE"})  # opt-in only, see rules
BUCKET_FULL = frozenset(ALL_OPERATOR_NAMES)  # fallback, current behavior
```

Buckets overlap by design (`BUCKET_DIRECT` is a subset of most others) since
`SelectColumn`/filters/aggregates are near-universal terminal operators. The final
candidate set sent to the planner is the **union of all buckets that survive
elimination**, deduplicated.

## Deterministic elimination rules

Implement pure-Python, dependency-free (no network, no LM call) rule functions.
Each rule inspects normalized query text (and optionally the resolved schema/column
list from the existing concept-resolution stage in `stages.py`) and returns one of:
`REQUIRE(bucket)`, `EXCLUDE(bucket)`, or `NO_OPINION`.

```python
@dataclass(frozen=True)
class RuleResult:
    bucket: str
    verdict: Literal["require", "exclude", "no_opinion"]
    rule_name: str
```

Elimination logic:

1. Run every rule against the normalized query.
2. A bucket is **excluded** only if at least one `exclude` verdict fires for it AND
   no `require` verdict fires for it (require always overrides exclude for the same
   bucket).
3. `BUCKET_DIRECT` is never excludable — it is a required baseline in every route
   except `full`.
4. `BUCKET_PREDICTIVE` is excluded by default (`exclude: default_predictive`) and is
   only required when an explicit predictive cue is present (`predict`, `forecast`,
   `classify`, `train`, `train a model`, `predictive accuracy`). This is the one
   bucket that flips the default from "include unless excluded" to "exclude unless
   required," because unlike the analytical buckets, predictive language is rare and
   unambiguous when present.
5. Example exclusion rules (adapt names/terms to the project's existing synonym maps
   in `stages.py`):
   - `exclude(BUCKET_GROUP_RANK)` when the query contains no grouping cue
     (`by `, `per `, `each `, `group`, `across subjects`, `across activities`) AND no
     ranking cue (`most`, `least`, `top`, `bottom`, `highest`, `lowest`).
   - `exclude(BUCKET_PARTITION_COMPARE)` when there is no comparison cue
     (`compare`, `versus`, `vs`, `difference between`, `split by`) AND no threshold
     cue (`above`, `below`, `greater than`, `less than`).
   - `exclude(BUCKET_DERIVE)` when there is no derived-feature cue (`magnitude`,
     `bin`, `bucket`, `ratio`, `duration`, `how long`, `difference of`).
   - `exclude(BUCKET_CORRELATION)` when there is no correlation cue (`correlation`,
     `relationship`, `associated with`, `correlate`).
   - `exclude(BUCKET_PARALLEL)` when there is no multi-branch cue (`for each of`,
     `separately for`, `broken down by`) in addition to `BUCKET_GROUP_RANK` being
     excluded.
6. **Ambiguity override**: if the normalized query matches an "unparseable/rare
   verb" detector (unknown analytical verb, negation combined with aggregation,
   multi-clause query with `and`/`or` joining two different intents, or query length
   above a configurable token threshold), skip elimination entirely and route to
   `BUCKET_FULL`.

## Required function signature

```python
def route_operator_bucket(query: str, schema_columns: Sequence[str] | None = None) -> OperatorRoute:
    """Deterministic, no-LM operator router.

    Returns an OperatorRoute with:
      - candidate_ops: frozenset[str]      # union of surviving buckets
      - excluded_buckets: tuple[str, ...]   # bucket names removed
      - matched_rules: tuple[str, ...]      # rule_name for every require/exclude that fired
      - used_full_fallback: bool
    Must be a pure function: no I/O, no LLM client, no network calls.
    """
```

## Integration points

1. In `flashfusion/baselines/flash_fusion.py`:
   - Remove the light-LM fast-path call (the code path currently gated by
     `FF_FALLBACK_GROUNDING` / the one-round-trip common-plan shortcut).
   - Before building `_planner_prefix_message`, call
     `route_operator_bucket(query, schema_columns)`.
   - Build the planner prefix from a **filtered vocabulary spec**: add a helper
     `build_vocabulary_spec(candidate_ops: frozenset[str]) -> str` in
     `operators.py` that renders only the operator entries in `candidate_ops`
     (fall back to the full `OPERATOR_VOCABULARY_SPEC` string when
     `used_full_fallback` is True or `candidate_ops == BUCKET_FULL`).
   - Keep the existing cache-breakpoint marker logic, but note in a comment that
     cache hit rate will now vary by bucket rather than being global — this is an
     accepted, intentional tradeoff.
   - Log `excluded_buckets` and `matched_rules` on every planner call (structured
     log or a new column on `RunResult`), so failures are attributable to a specific
     exclusion.

2. In `flashfusion/pipeline/stages.py`:
   - Leave the existing column-concept fast path untouched — it is a separate,
     already-safe no-LM shortcut and is not part of this change.

3. In `flashfusion/pipeline/operators.py`:
   - Add `build_vocabulary_spec()` as described above.
   - Bump `OPERATOR_VOCABULARY_VERSION` since the planner prefix construction
     changes.

4. Update `flashfusion/tests/test_planner_prefix.py` and add a new
   `flashfusion/tests/test_operator_router.py` covering:
   - Every canonical bucket is reachable by at least one representative query.
   - `BUCKET_PREDICTIVE` is excluded for generic observational queries (e.g.
     "which activity occurred most frequently") and required only for explicit
     predictive queries (e.g. "predict the next activity").
   - Ambiguity override routes to `BUCKET_FULL`.
   - **Operator-recall regression test**: for every existing labeled query/gold-plan
     pair in the eval fixtures, assert
     `gold_plan_operators <= route_operator_bucket(query).candidate_ops`
     (100% recall requirement — this is the primary correctness gate, more important
     than bucket size).

## Acceptance criteria

- No LM/network call inside `route_operator_bucket`.
- Zero regressions in operator recall on the BUS, ECG, and WISDM labeled eval sets
  (i.e. the union of surviving buckets must always be a superset of the operators
  the previous full-vocabulary planner actually used to answer each query correctly).
- Report, per dataset, the average number of operators sent to the planner before
  vs. after, and confirm end-to-end accuracy does not regress relative to the
  full-vocabulary planner (not the light-LM fast path being replaced).
- Any bucket exclusion tied to a planner failure must be traceable via the logged
  `rule_name`.