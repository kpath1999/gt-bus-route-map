# LLM-Only at Scale: An Empirical Characterization

*Draft for the §2.2 figure of the Flash-Fusion paper. Numbers populated from
`results/summary.csv`; figures from `results/figures/`.*

## Motivation (§2.2 framing)

A natural baseline for natural-language analysis over sensor data is to
serialize the dataset, prepend a question, and feed the whole thing to a
long-context LLM in one call. This study quantifies what happens when that
baseline is taken seriously across three datasets that span four orders of
magnitude in row count:

| Dataset | Rows | Bytes | Token est. (full) |
|---------|-----:|------:|------------------:|
| bus (small)   | 1,219 | 189 KB | ~40k |
| WISDM (medium) | 1,098,210 | 48 MB | ~12M |
| ECG (large)    | 20,800,000 | 841 MB | ~220M |

With a 128k-token context window (Llama 3.3 70B on Groq), only the bus dataset
fits. WISDM is ~90× over budget; ECG is ~1700× over.

## Method

For each (dataset, baseline, question) cell we run 3 reps. The baseline is one
of:

- **LLM-Only**: single API call with the full dataset serialized to CSV. When
  the prompt exceeds the 128k context, we char-truncate to the largest
  row-aligned prefix that fits and record `truncation_pct`.
- **Flash-Fusion**: the published staged pipeline (concept extraction →
  schema grounding → sub-query generation → grounded execution → judge).

Tokens are read from Groq's billed counts (`response.usage_metadata`), not
estimated, so cost numbers are exact. Accuracy is graded post-hoc with
`judge_rows_with_llm` against the existing WISDM and ECG ground-truth JSONs;
bus has no ground truth and is excluded from accuracy plots.

## Headline finding

![headline figure](results/figures/fig_headline.png)

Four panels × three datasets:

- **Accuracy** — LLM-Only is competitive on bus (no truncation), but on WISDM
  and ECG accuracy drops sharply as truncation cuts away most of the data. FF
  stays approximately flat because it queries a small grounded extract rather
  than the whole dataset.
- **Latency** — LLM-Only spends most of its time on token decode of giant
  prompts; FF latency is dominated by a handful of small staged calls.
- **Input tokens** — LLM-Only hits the 128k ceiling on WISDM and ECG; FF stays
  well below 1k.
- **Cost** — Two to three orders of magnitude separation on WISDM and ECG.

## Truncation

![truncation figure](results/figures/fig_truncation.png)

On WISDM the LLM-Only baseline drops ~99% of rows; on ECG ~99.95%. Whatever
answers the LLM produces are computed against an information-impoverished
slice that the user never asked for.

## Scaling

![scaling figure](results/figures/fig_scaling.png)

Cost diverges across the three dataset sizes — LLM-Only saturates against the
context-window cap (the per-call cost plateau) while accuracy continues to
degrade because the *information* dropped grows even though the *token bill*
plateaus.

## Discussion

This is not a claim that LLMs are unsuitable for sensor-data Q&A. It is a
quantification of *where* the brute-force prompting regime breaks down. The
results motivate the central design choice in Flash-Fusion: rather than
feeding raw data into the model, ground the question against the schema and
let a small, executed extract answer it. The accuracy-vs-cost separation on
WISDM and ECG is the empirical justification for that choice.

## Limitations

- Single model (Llama 3.3 70B via Groq); the qualitative trend should hold
  across long-context LLMs but is not measured here.
- 4 executable questions per dataset; out-of-scope queries are excluded.
- LLM-Only token counts are real (Groq-billed); FF token counts use the
  pipeline's existing word-count estimator (~1.3× of true). Order-of-magnitude
  comparisons are robust to this difference.
- Bus has no ground truth, so accuracy is not reported for that dataset.
