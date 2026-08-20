# Copilot Plan: Contract Extractor & Scoring Fixes for `trace_hybrid_cache.py`

## Summary

The hybrid cache correctly retrieves the right candidate (e.g., query ID 4) via lexical + dense retrieval, but the **verification layer rejects it as `incompatible_candidate`** due to representation mismatches between the live `ContractExtractor` output and the registry's stored contract. The root cause is that the generic extractor does not parse natural-language comparison phrases, aggregate words, or bind numeric literals to their field—so the safety-critical agreement check compares incompatible representations and emits false `filter_value_mismatch` failures. Additionally, the `_component_scores` averaging treats every **unknown/not-applicable** dimension as `0.0` (disagreement), unfairly dragging down the contract score.

---

## Issues to Fix

### 1. Natural-language comparison phrases not parsed (`predicate_ops` = 0.0)

**Current behavior:** `_extract_predicate_ops` only matches symbolic forms like `accel_variance > 0.20`. The phrase "strictly greater than 0.20" produces no predicate operator.

**Required:** Add a declarative, dataset-agnostic phrase lexicon that maps English comparison phrases to canonical operator tokens:

```python
COMPARISON_PHRASES = {
    "strictly greater than": ">",
    "greater than or equal to": ">=",
    "greater than": ">",
    "at least": ">=",
    "strictly less than": "<",
    "less than or equal to": "<=",
    "less than": "<",
    "at most": "<=",
    "equal to": "==",
    "not equal to": "!=",
}
```

Scan the query for each phrase (longest-match first to avoid "greater than" shadowing "strictly greater than"). When a phrase is found, identify the nearest schema field to the left or right of the phrase and the nearest numeric literal, then emit a keyed tuple:

```python
{("accel_variance", ">")}
```

### 2. Numeric literals not bound to fields (`filter_values` = 0.0, hard `filter_value_mismatch`)

**Current behavior:** `_extract_filter_values` extracts bare literals like `("*", "0.20")` or `("*", 0.20)`. The registry stores keyed pairs like `{"accel_variance": "0.20"}`. The safety check `_kv_set` converts the live side to `{"*:0.20"}` and the candidate side to `{"accel_variance:0.20"}`; the subset test fails.

**Required:** When a comparison phrase or symbolic operator is found, bind the nearest numeric literal to the identified field:

```python
{("accel_variance", "0.20")}
```

If no field can be confidently associated, the literal should still be extracted but marked as unkeyed—and the safety check should **not** treat an unkeyed live literal as a hard failure against a keyed candidate literal. At minimum, normalize both sides to keyed representation before comparison.

### 3. Aggregate words not parsed (`aggregate` = 0.0)

**Current behavior:** `aggregate` is always `None` on the live side. The extractor deliberately avoids domain-specific regexes, but generic aggregate phrases are missing.

**Required:** Add a declarative aggregate lexicon:

```python
AGGREGATE_PHRASES = {
    "how many": "count",
    "how much of a count": "count",
    "count of": "count",
    "number of": "count",
    "total": "sum",
    "sum of": "sum",
    "average": "mean",
    "mean": "mean",
    "median": "median",
    "maximum": "max",
    "largest": "max",
    "highest": "max",
    "peak": "max",
    "minimum": "min",
    "smallest": "min",
    "lowest": "min",
}
```

Scan the query (longest-match first) and set `aggregate` on the `QueryContract`. This should be generic—it does not encode any dataset-specific terms, only standard SQL-style aggregate vocabulary.

### 4. `output_shape` never extracted (always `None` → scored 0.0)

**Current behavior:** `output_shape` is always `None`. When `None`, `_component_scores` scores it `0.0`.

**Required:** Either:
- Infer output shape from aggregate type (scalar for `max`/`min`/`mean`/`sum`/`count`, row-list for filter-only queries), or
- Leave as `None` but exclude from contract score averaging (see issue 6).

### 5. `operator_skeleton` never extracted on live side (always `None` → scored 0.0)

**Current behavior:** `operator_skeleton_hint` is always `None` in `QueryContract`. When `None`, `_component_scores` scores it `0.0`.

**Required:** Either:
- Infer a basic skeleton from aggregate + fields + predicate ops, or
- Leave as `None` but exclude from contract score averaging (see issue 6).

### 6. Scoring design gap: unknown/not-applicable treated as disagreement

**Current behavior in `_component_scores`:**

| Condition | Current score |
|---|---|
| `live.get("aggregate") is None` | `0.0` |
| `live_fields` is empty | `0.0` |
| `live_ops` is empty | `0.0` |
| `live_vals` is empty | `0.0` |
| `live.get("output_shape") is None` | `0.0` |
| `live_pred` is empty | `0.0` |
| `live.get("operator_skeleton") is None` | `0.0` |

Then `contract_score = sum(component_scores.values()) / len(component_scores)`. Unknown dimensions drag the average down as if they were confirmed disagreements.

**Required:** Distinguish three states:

| State | Meaning | Score contribution |
|---|---|---|
| **Extracted and matches** | Live fact present, candidate agrees | `1.0` |
| **Extracted and conflicts** | Live fact present, candidate disagrees | `0.0` |
| **Not extracted / not applicable** | Live fact is `None` or empty | **Excluded from average** |

Implementation approach:

```python
def _component_scores(self, live, cand):
    scores = {}
    applicable = 0

    # Aggregate
    live_agg = live.get("aggregate")
    if live_agg is not None:
        applicable += 1
        scores["aggregate"] = 1.0 if cand.get("aggregate") == live_agg else 0.0

    # Fields
    live_fields = self._field_set(live.get("fields"))
    if live_fields:
        applicable += 1
        cand_fields = self._field_set(cand.get("fields"))
        scores["fields"] = self._jaccard(live_fields, cand_fields)

    # Predicate ops
    live_ops = self._kv_set(live.get("predicate_ops"))
    if live_ops:
        applicable += 1
        cand_ops = self._kv_set(cand.get("predicate_ops"))
        scores["predicate_ops"] = self._jaccard(live_ops, cand_ops)

    # Filter values
    live_vals = self._kv_set(live.get("filter_values"))
    if live_vals:
        applicable += 1
        cand_vals = self._kv_set(cand.get("filter_values"))
        scores["filter_values"] = self._jaccard(live_vals, cand_vals)

    # Output shape
    live_output = live.get("output_shape")
    if live_output is not None:
        applicable += 1
        scores["output_shape"] = 1.0 if cand.get("output_shape") == live_output else 0.0

    # Predictive
    live_pred = self._kv_set(live.get("predictive"))
    if live_pred:
        applicable += 1
        cand_pred = self._kv_set(cand.get("predictive"))
        scores["predictive"] = self._jaccard(live_pred, cand_pred)

    # Operator skeleton
    live_opskel = live.get("operator_skeleton")
    if live_opskel is not None:
        applicable += 1
        scores["operator_skeleton"] = 1.0 if cand.get("operator_skeleton") == live_opskel else 0.0

    # Contract score: average over applicable dimensions only
    contract_score = (sum(scores.values()) / applicable) if applicable > 0 else 0.0
    return scores, contract_score
```

The same principle applies to `_safety_critical_agreement`: only run the hard subset check on dimensions where the live side has a confidently extracted fact. Do **not** emit `filter_value_mismatch` when the live filter value is unkeyed and cannot be confidently bound to a field.

---

## Task: Audit All Query Patterns

The coding agent must go over **all query wording versions** (v1, v2, v3) across **all datasets** (BUS, ECG, WISDM) and verify that the extractor handles every pattern. For each query, confirm that:

1. **Aggregate** is extracted from natural language (how many, maximum, average, etc.)
2. **Fields** are matched against schema columns
3. **Predicate operators** are extracted from both symbolic (`>`) and natural-language ("strictly greater than") forms
4. **Filter values** are bound to their field as keyed tuples, not bare literals
5. **Output shape** is either inferred or excluded from scoring
6. **Operator skeleton** is either inferred or excluded from scoring
7. **Predictive** dimensions are only scored when the query is predictive (has a model name, target column, etc.)

### Specific queries to verify (from observed trace output)

| Query ID | Dataset | Text (v1/original) | Key patterns to handle |
|---|---|---|---|
| 1 | bus | "What is the maximum accel_variance observed in this dataset?" | aggregate=max, field=accel_variance |
| 2 | bus | "What is the average accel_mean across all recorded samples?" | aggregate=mean, field=accel_mean |
| 3 | bus | "List all timestamps where accel_stats_z_p99 reaches its maximum value across the dataset." | aggregate=max, field=accel_stats_z_p99, output_shape=row_list |
| 4 | bus | "How many data samples show an accel_variance strictly greater than 0.20?" | aggregate=count, field=accel_variance, predicate_op=(accel_variance, >), filter_value=(accel_variance, 0.20) |
| 5 | bus | (check registry) | TBD |
| 6 | bus | "Which location (latitude, longitude) recorded the largest difference between the 99th and 1st percentile of the z-axis acceleration?" | aggregate=max, fields=[latitude, longitude, ...], output_shape=row |
| 7 | bus | (check registry) | TBD |
| 8 | bus | (check registry) | TBD |
| 13–16 | bus | (check registry) | TBD |

For each query, also test **v2 and v3 rewordings** to confirm the extractor produces the same contract regardless of wording variation.

### Datasets to cover

- **BUS** (`semantic_registry_bus_v1.json`)
- **ECG** (registry TBD)
- **WISDM** (registry TBD)

---

## Implementation Checklist

- [ ] Add `COMPARISON_PHRASES` lexicon to `ContractExtractor`
- [ ] Add `AGGREGATE_PHRASES` lexicon to `ContractExtractor`
- [ ] Implement longest-match-first scanning for both lexicons
- [ ] Bind numeric literals to nearest schema field when a comparison phrase is found
- [ ] Update `_extract_predicate_ops` to handle natural-language phrases
- [ ] Update `_extract_filter_values` to produce keyed tuples when field binding is possible
- [ ] Update `_safety_critical_agreement` to skip hard checks on unkeyed/unextracted live facts
- [ ] Update `_component_scores` to exclude unknown/not-applicable dimensions from the average
- [ ] Optionally infer `output_shape` from aggregate type
- [ ] Optionally infer `operator_skeleton_hint` from aggregate + fields + predicate ops
- [ ] Audit all v1/v2/v3 queries across BUS, ECG, WISDM
- [ ] Add a test harness or trace script that runs all query IDs and reports contract extraction results
- [ ] Verify no regressions: exact hits still work, fuzzy mode still works as unsafe ablation

---

## Constraints

- **No LM dependency.** All extraction must remain rule-based and deterministic.
- **No per-query hardcoding.** Rules must be generic and portable across datasets. The only dataset-specific input is the schema column list.
- **Conservative authorization.** Retrieval score alone must never authorize a cache hit. Hard safety checks remain the gate.
- **Unknown ≠ disagreement.** An unextracted dimension must not penalize the contract score.
