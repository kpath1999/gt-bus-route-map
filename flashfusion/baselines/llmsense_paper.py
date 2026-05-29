"""LLMSENSE_PAPER baseline.

Implements a two-tier narration pipeline:
- Stage N: narrate short traces directly
- Stage S: summarize long traces in chunks
- Stage R: reason over narrative text only

This baseline never executes pandas code.
"""

from __future__ import annotations

import io

import pandas as pd
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.config import (
    LLMSENSE_HISTORY_HOURS,
    LLMSENSE_MAX_ROWS_DIRECT,
    LLMSENSE_SENSOR_HZ,
    LLMSENSE_SUMMARY_WINDOW_MIN,
)
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.prompts.llmsense_prompts import (
    NARRATION_OBJECTIVE,
    NARRATION_PROMPT_TEMPLATE,
    REASONING_PROMPT_TEMPLATE,
    SUMMARIZATION_PROMPT_TEMPLATE,
    DATASET_BUS,
    DATASET_MIT_ECG,
    DATASET_WISDM,
    build_dataset_context,
    get_narration_requirements,
    get_reasoning_dataset_label,
)


def _df_to_table_string(df: pd.DataFrame, max_rows: int = 200) -> str:
    """Render a dataframe excerpt as compact CSV text for prompt data slots."""
    sample = df.head(max_rows)
    buf = io.StringIO()
    sample.to_csv(buf, index=False, float_format="%.4f")
    return buf.getvalue()


def _infer_dataset_name(df: pd.DataFrame) -> str:
    columns = set(df.columns)
    if {"x", "y", "z", "activity_label"}.issubset(columns):
        return DATASET_WISDM
    if {"MLII", "V1", "record_id", "time_s"}.issubset(columns):
        return DATASET_MIT_ECG
    if {"latitude", "longitude", "accel_mean", "accel_variance"}.issubset(columns):
        return DATASET_BUS
    return DATASET_WISDM


def _build_context(dataset: str, hz: float = LLMSENSE_SENSOR_HZ) -> str:
    return build_dataset_context(dataset, hz)


def _needs_summarization(df: pd.DataFrame) -> bool:
    return len(df) > LLMSENSE_MAX_ROWS_DIRECT


def _subject_chunks(sub_df: pd.DataFrame) -> list[pd.DataFrame]:
    """Chunk per subject using configured summary window and history limit."""
    rows_per_window = max(1, int(LLMSENSE_SENSOR_HZ * 60 * LLMSENSE_SUMMARY_WINDOW_MIN))
    max_rows = max(rows_per_window, int(LLMSENSE_SENSOR_HZ * 3600 * LLMSENSE_HISTORY_HOURS))
    limited = sub_df.head(max_rows)
    return [limited.iloc[i : i + rows_per_window] for i in range(0, len(limited), rows_per_window)]


def _stage_narrate(df: pd.DataFrame, client: LLMClient, dataset: str) -> str:
    context = _build_context(dataset)
    requirements = get_narration_requirements(dataset)
    prompt_text = NARRATION_PROMPT_TEMPLATE.format(
        objective=NARRATION_OBJECTIVE,
        context=context,
        narration_requirements=requirements,
        data_table=_df_to_table_string(df),
    )
    chain = ChatPromptTemplate.from_template("{prompt}") | client.llm | StrOutputParser()
    return client.invoke_chain(chain, {"prompt": prompt_text}, stage="N_narrate")


def _stage_summarize(df: pd.DataFrame, client: LLMClient, dataset: str) -> str:
    context = _build_context(dataset)
    summaries: list[str] = []

    if "subject_id" in df.columns:
        group_key: str | None = "subject_id"
    elif "record_id" in df.columns:
        group_key = "record_id"
    else:
        group_key = None
    grouped = df.groupby(group_key, sort=True) if group_key else [("all", df)]

    sort_col = "timestamp" if "timestamp" in df.columns else ("time_s" if "time_s" in df.columns else None)
    for group_id, sub_df in grouped:
        ordered = sub_df.sort_values(sort_col) if sort_col else sub_df
        chunks = _subject_chunks(ordered)
        for idx, chunk in enumerate(chunks, start=1):
            duration_desc = (
                f"group {group_id}, chunk {idx}/{len(chunks)}, "
                f"window={LLMSENSE_SUMMARY_WINDOW_MIN}min, history={LLMSENSE_HISTORY_HOURS}h"
            )
            prompt_text = SUMMARIZATION_PROMPT_TEMPLATE.format(
                objective=NARRATION_OBJECTIVE,
                context=context,
                duration_desc=duration_desc,
                data_table=_df_to_table_string(chunk, max_rows=160),
            )
            chain = ChatPromptTemplate.from_template("{prompt}") | client.llm | StrOutputParser()
            summary = client.invoke_chain(
                chain,
                {"prompt": prompt_text},
                stage=f"S_summarize_group{group_id}_chunk{idx}",
            )
            summaries.append(f"[Group {group_id} - chunk {idx}] {summary}")

    return "\n\n".join(summaries)


def _stage_reason(
    narrative: str,
    query: str,
    context: str,
    dataset_label: str,
    client: LLMClient,
) -> str:
    prompt_text = REASONING_PROMPT_TEMPLATE.format(
        dataset_label=dataset_label,
        context=context,
        narrative=narrative,
        query=query,
    )
    chain = ChatPromptTemplate.from_template("{prompt}") | client.llm | StrOutputParser()
    return client.invoke_chain(chain, {"prompt": prompt_text}, stage="R_reason")


def run_llmsense_paper(
    query: str,
    df: pd.DataFrame,
    client: LLMClient,
    r: RunResult,
) -> RunResult:
    """Execute the LLMSENSE_PAPER baseline."""
    dataset = _infer_dataset_name(df)
    context = _build_context(dataset)
    dataset_label = get_reasoning_dataset_label(dataset)

    if _needs_summarization(df):
        narrative = _stage_summarize(df, client, dataset)
        r.stages_run.append("S_summarize")
    else:
        narrative = _stage_narrate(df, client, dataset)
        r.stages_run.append("N_narrate")

    r.answer = _stage_reason(narrative, query, context, dataset_label, client)
    r.stages_run.append("R_reason")
    r.trace = narrative
    r.executed = False
    r.rejected = False
    r.judge_verdict = {}
    return r
