"""HARGPT_PAPER baseline with query rewriting and demo-style prompting.

This implementation keeps changes scoped to HARGPT only and rewrites incoming
benchmark queries into an Instruction + Content format inspired by the upstream
HAR-GPT demos. It uses deterministic truncation so large datasets remain
prompt-feasible and returns best-effort, caveated answers for unsupported asks.
"""

from __future__ import annotations

import io
import re
from typing import Iterable

import pandas as pd

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.pipeline.runner import LLMClient, RunResult


_INSTRUCTION = "You are an expert on analyzing human activities based on IMU recordings."

_WISDM_MAX_ROWS = 120
_ECG_MAX_ROWS = 120
_BUS_MAX_ROWS = 120
_AXIS_MAX_VALUES = 60
_CSV_MAX_ROWS = 80


_WISDM_CONTENT_TEMPLATE = """\
The IMU data is collected from a mobile phone attached to the user's body with a sampling rate of 20Hz.
The IMU data is given in the IMU coordinate frame. The three-axis accelerations recording is given below.
1. x-axis: {x_axis}
2. y-axis: {y_axis}
3. z-axis: {z_axis}

Candidate activity categories in this dataset slice: {labels}

Tabular excerpt ({nrows} rows):
{table}

User question: {query}
Please make an analysis step by step based only on the provided data.
Final answer: <your concise answer>
"""


_ECG_CONTENT_TEMPLATE = """\
The ECG signal data is collected from clinical recordings and provided as a tabular time-series excerpt.
The channel recordings are summarized below.
1. MLII: {mlii}
2. V1: {v1}

Tabular excerpt ({nrows} rows):
{table}

User question: {query}
Please make an analysis step by step based only on the provided data.
If crucial metadata is missing, provide a best-effort answer with an explicit caveat.
Final answer: <your concise answer>
"""


_BUS_CONTENT_TEMPLATE = """\
The transport telemetry data is collected from a bus route and provided as time-ordered samples.
Relevant telemetry signals are summarized below.
1. accel_mean: {accel_mean}
2. accel_variance: {accel_variance}

Tabular excerpt ({nrows} rows):
{table}

User question: {query}
Please make an analysis step by step based only on the provided data.
If crucial metadata is missing, provide a best-effort answer with an explicit caveat.
Final answer: <your concise answer>
"""

def _invoke_rewritten(
	client: LLMClient,
	stage: str,
	instruction: str,
	content: str,
) -> str:
	chain = (
		ChatPromptTemplate.from_messages(
			[("system", "{instruction}"), ("human", "{content}")]
		)
		| client.llm
		| StrOutputParser()
	)
	return client.invoke_chain(
		chain,
		{"instruction": instruction, "content": content},
		stage=stage,
	)


def _infer_dataset(df) -> str:
	cols = set(df.columns)
	if {"MLII", "V1", "record_id"}.issubset(cols):
		return "ecg"
	if {"latitude", "longitude", "accel_mean"}.issubset(cols):
		return "bus"
	return "wisdm"


def _df_to_csv(df: pd.DataFrame, max_rows: int = _CSV_MAX_ROWS) -> str:
	buf = io.StringIO()
	df.head(max_rows).to_csv(buf, index=False, float_format="%.4f")
	return buf.getvalue()


def _parse_final_answer(response: str) -> str:
	m = re.search(r"final\s+answer\s*:\s*(.+)", response, flags=re.IGNORECASE | re.DOTALL)
	if m:
		return m.group(1).strip()
	lines = [line.strip() for line in response.splitlines() if line.strip()]
	return lines[-1] if lines else response.strip()


def _normalize_label(text: str) -> str:
	return re.sub(r"\s+", " ", text.strip().lower())


def _extract_user_id(query: str) -> int | None:
	m = re.search(r"\buser\s+(\d+)\b", query.lower())
	if not m:
		return None
	try:
		return int(m.group(1))
	except ValueError:
		return None


def _format_axis(vals: Iterable[float]) -> str:
	rounded = [f"{float(v):.2f}" for v in list(vals)[:_AXIS_MAX_VALUES]]
	return "[" + " ".join(rounded) + "]"


def _extract_record_id(query: str) -> int | None:
	m = re.search(r"\brecord[_\s-]*id\s+(\d+)\b", query.lower())
	if not m:
		return None
	try:
		return int(m.group(1))
	except ValueError:
		return None


def _sort_for_dataset(df: pd.DataFrame) -> pd.DataFrame:
	if "timestamp" in df.columns:
		return df.sort_values("timestamp")
	if "time_s" in df.columns:
		return df.sort_values("time_s")
	return df


def _truncate_for_query(df: pd.DataFrame, dataset: str, query: str) -> tuple[pd.DataFrame, dict[str, object]]:
	work_df = df
	target = None

	if dataset == "wisdm" and "subject_id" in df.columns:
		user_id = _extract_user_id(query)
		if user_id is not None:
			narrowed = df[df["subject_id"] == user_id]
			if not narrowed.empty:
				work_df = narrowed
				target = f"subject_id={user_id}"

	if dataset == "ecg" and "record_id" in df.columns:
		record_id = _extract_record_id(query)
		if record_id is not None:
			narrowed = df[df["record_id"].astype(str) == str(record_id)]
			if not narrowed.empty:
				work_df = narrowed
				target = f"record_id={record_id}"

	ordered = _sort_for_dataset(work_df)
	max_rows = _WISDM_MAX_ROWS
	if dataset == "ecg":
		max_rows = _ECG_MAX_ROWS
	elif dataset == "bus":
		max_rows = _BUS_MAX_ROWS

	window = ordered.head(max_rows)
	meta = {
		"rows_total": int(len(df)),
		"rows_after_target_filter": int(len(work_df)),
		"rows_used": int(len(window)),
		"truncated": bool(len(work_df) > len(window)),
		"target": target,
	}
	return window, meta


def _candidate_labels(df) -> list[str]:
	if "activity_name" in df.columns:
		raw = df["activity_name"].dropna().astype(str).tolist()
	elif "activity_label" in df.columns:
		raw = df["activity_label"].dropna().astype(str).tolist()
	else:
		return []

	labels: list[str] = []
	for item in raw:
		clean = item.strip()
		if clean and clean not in labels:
			labels.append(clean)
	return labels[:12]


def _build_wisdm_content(query: str, df: pd.DataFrame) -> str:
	x_axis = _format_axis(df["x"].tolist()) if "x" in df.columns else "[]"
	y_axis = _format_axis(df["y"].tolist()) if "y" in df.columns else "[]"
	z_axis = _format_axis(df["z"].tolist()) if "z" in df.columns else "[]"
	labels = _candidate_labels(df)
	table_cols = [
		c
		for c in ["subject_id", "timestamp", "activity_label", "activity_name", "x", "y", "z", "magnitude"]
		if c in df.columns
	]
	table = _df_to_csv(df[table_cols] if table_cols else df)
	return _WISDM_CONTENT_TEMPLATE.format(
		x_axis=x_axis,
		y_axis=y_axis,
		z_axis=z_axis,
		labels=labels or "[]",
		nrows=int(len(df)),
		table=table,
		query=query,
	)


def _build_ecg_content(query: str, df: pd.DataFrame) -> str:
	mlii = _format_axis(df["MLII"].tolist()) if "MLII" in df.columns else "[]"
	v1 = _format_axis(df["V1"].tolist()) if "V1" in df.columns else "[]"
	table_cols = [c for c in ["record_id", "time_s", "annotation", "MLII", "V1"] if c in df.columns]
	table = _df_to_csv(df[table_cols] if table_cols else df)
	return _ECG_CONTENT_TEMPLATE.format(
		mlii=mlii,
		v1=v1,
		nrows=int(len(df)),
		table=table,
		query=query,
	)


def _build_bus_content(query: str, df: pd.DataFrame) -> str:
	accel_mean = _format_axis(df["accel_mean"].tolist()) if "accel_mean" in df.columns else "[]"
	accel_variance = _format_axis(df["accel_variance"].tolist()) if "accel_variance" in df.columns else "[]"
	table_cols = [
		c
		for c in [
			"timestamp",
			"latitude",
			"longitude",
			"accel_mean",
			"accel_variance",
			"accel_stats_x_p99",
			"accel_stats_y_p99",
			"accel_stats_z_p99",
			"accel_stats_z_p1",
		]
		if c in df.columns
	]
	table = _df_to_csv(df[table_cols] if table_cols else df)
	return _BUS_CONTENT_TEMPLATE.format(
		accel_mean=accel_mean,
		accel_variance=accel_variance,
		nrows=int(len(df)),
		table=table,
		query=query,
	)


def _rewrite_prompt(query: str, dataset: str, df: pd.DataFrame) -> tuple[str, str]:
	instruction = _INSTRUCTION
	if dataset == "ecg":
		instruction = (
			"You are an expert on analyzing time-series biosignals from ECG recordings. "
			"Reason from the provided excerpt only."
		)
	if dataset == "bus":
		instruction = (
			"You are an expert on analyzing transport telemetry and road-quality signals. "
			"Reason from the provided excerpt only."
		)

	if dataset == "wisdm":
		return instruction, _build_wisdm_content(query, df)
	if dataset == "ecg":
		return instruction, _build_ecg_content(query, df)
	return instruction, _build_bus_content(query, df)


def run_hargpt_paper(query: str, df, client: LLMClient, r: RunResult) -> RunResult:
	"""Execute HARGPT baseline with template-style query rewriting."""
	r.stages_run.append("hargpt_scope")
	
	# DEBUG: Log what we received
	import sys
	print(f"[HARGPT DEBUG] query={query[:60]}...", file=sys.stderr, flush=True)
	print(f"[HARGPT DEBUG] df type={type(df)}, is None={df is None}", file=sys.stderr, flush=True)
	if df is not None:
		print(f"[HARGPT DEBUG] df len={len(df)}, cols={list(df.columns) if hasattr(df, 'columns') else 'N/A'}", file=sys.stderr, flush=True)
		if len(df) > 0:
			print(f"[HARGPT DEBUG] df head:\n{df.head(3)}", file=sys.stderr, flush=True)
	
	if df is None or len(df) == 0:
		r.rejected = True
		r.executed = False
		r.rejection_reason = "HARGPT_PAPER requires at least one data row to construct a prompt."
		r.alignment_explanation = "Rejected before execution: empty dataset excerpt."
		print(f"[HARGPT DEBUG] REJECTING: df is {'None' if df is None else 'empty'}", file=sys.stderr, flush=True)
		return r

	dataset = _infer_dataset(df)

	window, meta = _truncate_for_query(df, dataset, query)
	r.stages_run.append(f"hargpt_{dataset}_window")

	instruction, content = _rewrite_prompt(query, dataset, window)
	r.stages_run.append(f"hargpt_{dataset}_rewrite")

	infer_stage = f"hargpt_{dataset}_infer"
	response = _invoke_rewritten(
		client,
		infer_stage,
		instruction,
		content,
	)
	r.stages_run.append(infer_stage)
	r.stages_run.append(f"hargpt_{dataset}_parse")

	answer = _parse_final_answer(response)
	r.answer = answer if answer else response.strip()
	r.trace = f"=== Rewritten Prompt ===\n{content}\n\n=== Model Output ===\n{response}"
	r.executed = False
	r.rejected = False
	r.agent_tries = 1
	r.execution_attempts.append(
		{
			"stage": infer_stage,
			"dataset": dataset,
			"rows_total": meta["rows_total"],
			"rows_after_target_filter": meta["rows_after_target_filter"],
			"rows_used": meta["rows_used"],
			"truncated": meta["truncated"],
			"target": meta["target"],
			"query_rewritten": True,
		}
	)
	return r
