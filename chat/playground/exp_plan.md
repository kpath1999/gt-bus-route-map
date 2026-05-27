# Flash-Fusion: Development & Baseline Guide

## baseline architectures

**Agent** — NL → code synthesis → local execution → result. Generates Python that runs locally; never ingests data into the LLM. Domain-aware and format-aware (you declare sensor type upfront). Makes no attempt to handle qualitative concepts — it solves "write me a heartbeat detector," not "was it a bumpy ride?"

**LLaSA** — fine-tuned multimodal model: raw IMU embeddings + NL question → free-text answer, end-to-end. Requires training on domain-specific QA pairs (OpenSQA). Locked to IMU and human activity recognition. No decomposition, no grounding, no sub-queries. Answers well within its training distribution; cannot generalize to a new sensor type without retraining.

**WellMax** — query augmentation, not query decomposition. Enriches the user query with sensor-derived context (stress, activity, sleep) before handing off to a general-purpose LLM. Works on fixed wearable profiles. Summarizes and injects context; does not execute analytical operations. No sub-queries, no schema grounding, no execution.

---

## build system first, then ablate

Build Flash-Fusion (B4) completely first, then derive every baseline by stripping layers out. Never build baselines independently — you'll end up with five divergent codebases, non-comparable results, and a fragmented eval harness.

### ablation ladder

| Baseline | What's present | What it tests |
|---|---|---|
| **B0** — Raw Prompt | Raw CSV + question → LLM | Cost/quality floor; naïve baseline |
| **B1** — Schema-Aware | Column metadata + question → LLM | Whether naming the schema alone is enough |
| **B2** — + Concept Extraction | B1 + Stage 1 (DATA/REASONING split) | Whether classification orients the LLM without grounding |
| **B3** — + Schema Grounding | B2 + Stage 2 (mappings) → single LLM call | Whether proxies suffice without decomposition |
| **B4** — Full Flash-Fusion | B3 + Stage 3 + Tavily | Complete pipeline |
| **B4a** — No Tavily | B4 with `TAVILY_API_KEY` unset | Isolates the contribution of web-retrieved jargon definitions |

Each baseline is a strict prefix subtraction of B4. Every component's marginal contribution is measurable.

### step-by-step build order

**1. Shared foundation first** — before any pipeline or baseline code, build the infrastructure all of them share:
- `data_loader.py` — format detection (CSV, JSON-lines, Parquet) → DataFrame + format tag. Nobody calls `pd.read_csv` directly anywhere else.
- `column_metadata.py` — extract `build_column_metadata` into its own module; add `validate_column_refs(mappings, df)` to catch hallucinated column names after Stage 2.
- `llm_client.py` — thin wrapper that logs input tokens, output tokens, latency, and raw prompt on every invocation.
- `eval_harness.py` — write this before any pipeline code. Define the 15–20 query set (factual / qualitative single-concept / qualitative jargon), scoring functions (correctness, executability, hallucination rate, token cost), and a `run_baseline(baseline_fn, query, df)` wrapper that calls any baseline uniformly.

**2. Build B4 stage by stage, contract-first** — define the exact input/output schema for each stage before implementing it, then write a unit test at the boundary before moving on.

- *Stage 1 contract:* `query: str` → `{"DATA": List[str], "REASONING": List[str]}`
- *Stage 1.5 contract:* `reasoning_concepts: List[str]` → `Dict[str, str]` (empty if all COMMON)
- *Stage 2 contract:* concepts + metadata + definitions → `{"mappings": List[str], "unmappable": List[str]}`. Immediately call `validate_column_refs` post-parse; flag invalid column references as `INVALID` rather than passing them silently downstream.
- *Stage 3 contract:* query + grounding + metadata → `{"sub_queries": List[Dict], "synthesis_hint": str}`. Each sub-query dict must carry `"operation"` (one of FILTER / AGGREGATE / WINDOW / CORRELATE / RANK) and `"question"`. This eliminates ambiguity the Pandas agent would otherwise have to re-infer.

**3. Build the Pandas execution layer** — `eval.py` must handle two modes: (a) typed sub-query list from B3/B4 (run each, collect results, synthesize), and (b) single NL prompt from B0/B1/B2 (pass directly, return answer). Log an executability flag (did the Pandas agent succeed without raising?) for every run.

**4. Ablate downward** — each baseline is a short-circuit of the existing pipeline, not new code:
- B3: run Stages 1–2, then pass grounding directly to LLM in one shot — no Stage 3.
- B2: run Stage 1 only, pass concept lists + metadata to LLM in one shot.
- B1: skip all stages, pass column metadata + question to LLM directly.
- B0: serialize raw DataFrame (truncated to context window), append question, send.

Implement as a `BaselineRunner` class with a mode flag. Zero divergence in infrastructure.

**5. Add B4a** — already almost free. B4 run with `TAVILY_API_KEY` unset. Add an explicit log entry for concepts that would have triggered Tavily, so you can report "Tavily was skipped for N JARGON concepts" in the ablation results.

**6. Run the full eval matrix** — six systems × 20 queries. Score answer correctness last (requires human judgment) to avoid anchoring bias. Everything else is auto-logged.

| Baseline | Answer Correctness | Hallucination Rate | Sub-query Executability | Avg Tokens | Avg Latency |
|---|---|---|---|---|---|
| B0 | | | | | |
| B1 | | | | | |
| B2 | | | | | |
| B3 | | | | | |
| B4a | | | | | |
| B4 | | | | | |

### one rule throughout

Every prompt template change requires a full eval matrix re-run. Prompt changes are not cosmetic — they change every baseline that uses that stage. Treat prompt engineering as system development, not a separate activity.