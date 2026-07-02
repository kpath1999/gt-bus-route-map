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

# Row caps derived from 80% of the 128k context window (102,400 tokens) minus
# ~500 tokens of template + instruction overhead ≈ 101,900 tokens for data.
# Token density measured/estimated with the native (sentencepiece) tokenizer.
# 80% (not 95%) leaves enough headroom that prefill time stays under the API timeout.
_WISDM_MAX_ROWS = 5120   # ~20 tok/row → 5,120 × 20 = 102,400 tok (80.0% of 128k)
_ECG_MAX_ROWS   = 5700   # ~18 tok/row → 5,700 × 18 = 102,600 tok (80.2%) — empirically confirmed
_BUS_MAX_ROWS   = 1860   # ~55 tok/row → 1,860 × 55 = 102,300 tok (79.9%); total dataset is only 1,219 rows
_AXIS_MAX_VALUES = 32
_CSV_MAX_ROWS = 10000

# Calibrated from latencychunks observations: practical context saturation is
# reached around ~310 chunks at ~200 tokens/chunk for 128k-window models.
_CALIBRATION_CONTEXT_WINDOW_TOKENS = 128_000
_CALIBRATION_EXHAUSTION_CHUNKS = 310
_CALIBRATION_CHUNK_TOKEN_TARGET = 200
_CALIBRATION_ACTUAL_TOKEN_RATIO = 1.9
_CONTEXT_TARGET_SAFETY = 0.80
_BUDGET_PREFILTER_BUFFER_ROWS = 512


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


def _estimate_tokens(text: str) -> int:
	"""Approximate token count with a chars/4 heuristic."""
	if not text:
		return 0
	return max(1, len(text) // 4)


def _default_budget_tokens_est() -> int:
	"""Return prompt budget in heuristic (chars//4) units.

	Set to the full 95%-of-128k target so the hard per-dataset row caps
	(_ECG_MAX_ROWS, _WISDM_MAX_ROWS, _BUS_MAX_ROWS) are always the binding
	constraint, not this budget.  Previously the empirical knee from
	latencychunks (~32k heuristic tokens) was limiting ECG to ~4k rows (58%
	of context); removing that bottleneck allows the hard caps to take effect.
	"""
	return int(_CALIBRATION_CONTEXT_WINDOW_TOKENS * _CONTEXT_TARGET_SAFETY / _CALIBRATION_ACTUAL_TOKEN_RATIO)


def _estimate_prompt_overhead_tokens(dataset: str, query: str) -> int:
	"""Estimate template + instruction overhead using an empty table excerpt."""
	if dataset == "ecg":
		dummy = _ECG_CONTENT_TEMPLATE.format(
			mlii="[]",
			v1="[]",
			nrows=0,
			table="",
			query=query,
		)
		instruction = (
			"You are an expert on analyzing time-series biosignals from ECG recordings. "
			"Reason from the provided excerpt only."
		)
	elif dataset == "bus":
		dummy = _BUS_CONTENT_TEMPLATE.format(
			accel_mean="[]",
			accel_variance="[]",
			nrows=0,
			table="",
			query=query,
		)
		instruction = (
			"You are an expert on analyzing transport telemetry and road-quality signals. "
			"Reason from the provided excerpt only."
		)
	else:
		dummy = _WISDM_CONTENT_TEMPLATE.format(
			x_axis="[]",
			y_axis="[]",
			z_axis="[]",
			labels="[]",
			nrows=0,
			table="",
			query=query,
		)
		instruction = _INSTRUCTION

	return _estimate_tokens(instruction) + _estimate_tokens(dummy)


def _estimate_avg_row_tokens(df: pd.DataFrame, sample_size: int = 256) -> float:
	"""Estimate average tokens/row from a small prefix sample."""
	if df.empty:
		return 1.0
	sample = df.head(sample_size)
	csv_text = sample.to_csv(index=False, float_format="%.4f")
	lines = csv_text.splitlines()
	if len(lines) <= 1:
		return 1.0
	row_lines = lines[1:]
	row_token_total = sum(_estimate_tokens(line) for line in row_lines)
	return max(1.0, row_token_total / max(1, len(row_lines)))


def _budget_rows(
	df: pd.DataFrame,
	budget_tokens_est: int,
	overhead_tokens_est: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
	"""Select the largest contiguous prefix that fits the estimated prompt budget."""
	if df.empty:
		return df, {
			"prefilter_rows": 0,
			"available_data_tokens_est": 0,
			"data_tokens_est": 0,
			"est_prompt_tokens": overhead_tokens_est,
			"context_pct_window": 0.0,
			"context_pct_budget": 0.0,
			"budget_tokens_est": int(budget_tokens_est),
			"prefilter_applied": False,
		}

	available_data_tokens = max(1, budget_tokens_est - overhead_tokens_est)
	avg_row_tokens = _estimate_avg_row_tokens(df)
	rough_rows = int(available_data_tokens / avg_row_tokens) + _BUDGET_PREFILTER_BUFFER_ROWS
	rough_rows = max(1, min(len(df), rough_rows))

	pre_df = df.head(rough_rows)
	csv_text = pre_df.to_csv(index=False, float_format="%.4f")
	lines = csv_text.splitlines()
	if len(lines) <= 1:
		selected = pre_df.head(1)
		data_tokens_est = 0
	else:
		header_tokens = _estimate_tokens(lines[0])
		used_tokens = header_tokens
		selected_rows = 0
		for line in lines[1:]:
			line_tokens = _estimate_tokens(line)
			if selected_rows > 0 and used_tokens + line_tokens > available_data_tokens:
				break
			used_tokens += line_tokens
			selected_rows += 1
		selected_rows = max(1, selected_rows)
		selected = pre_df.head(selected_rows)
		data_tokens_est = used_tokens

	est_prompt_tokens = overhead_tokens_est + data_tokens_est
	context_pct_window = (est_prompt_tokens / _CALIBRATION_CONTEXT_WINDOW_TOKENS) * 100
	context_pct_budget = (est_prompt_tokens / max(1, budget_tokens_est)) * 100
	meta = {
		"prefilter_rows": int(rough_rows),
		"available_data_tokens_est": int(available_data_tokens),
		"data_tokens_est": int(data_tokens_est),
		"est_prompt_tokens": int(est_prompt_tokens),
		"context_pct_window": float(context_pct_window),
		"context_pct_budget": float(context_pct_budget),
		"budget_tokens_est": int(budget_tokens_est),
		"prefilter_applied": bool(rough_rows < len(df)),
	}
	return selected, meta


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
	legacy_max_rows = _WISDM_MAX_ROWS
	if dataset == "ecg":
		legacy_max_rows = _ECG_MAX_ROWS
	elif dataset == "bus":
		legacy_max_rows = _BUS_MAX_ROWS

	budget_tokens_est = _default_budget_tokens_est()
	overhead_tokens_est = _estimate_prompt_overhead_tokens(dataset, query)
	window, budget_meta = _budget_rows(
		ordered,
		budget_tokens_est=budget_tokens_est,
		overhead_tokens_est=overhead_tokens_est,
	)

	# Keep legacy row caps as a hard stop if calibration over-estimates.
	stride = 1
	if len(window) > legacy_max_rows:
		stride = max(1, len(window) // legacy_max_rows)
		window = window.iloc[::stride].head(legacy_max_rows)

	meta = {
		"rows_total": int(len(df)),
		"rows_after_target_filter": int(len(work_df)),
		"rows_used": int(len(window)),
		"truncated": bool(len(work_df) > len(window)),
		"target": target,
		"stride": int(stride),
		"budget_tokens_est": int(budget_meta["budget_tokens_est"]),
		"est_prompt_tokens": int(budget_meta["est_prompt_tokens"]),
		"context_pct_window": float(budget_meta["context_pct_window"]),
		"context_pct_budget": float(budget_meta["context_pct_budget"]),
		"prefilter_rows": int(budget_meta["prefilter_rows"]),
		"prefilter_applied": bool(budget_meta["prefilter_applied"]),
		"overhead_tokens_est": int(overhead_tokens_est),
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

	_rows_total = meta["rows_total"]
	_rows_used = meta["rows_used"]
	_pct_seen = 100.0 * _rows_used / _rows_total if _rows_total > 0 else 0.0
	print(
		f"[HARGPT] {dataset.upper()} rows_seen={_rows_used:,}/{_rows_total:,} ({_pct_seen:.4f}%)",
		flush=True,
	)

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
			"stride": meta["stride"],
			"budget_tokens_est": meta["budget_tokens_est"],
			"est_prompt_tokens": meta["est_prompt_tokens"],
			"context_pct_window": meta["context_pct_window"],
			"context_pct_budget": meta["context_pct_budget"],
			"prefilter_rows": meta["prefilter_rows"],
			"prefilter_applied": meta["prefilter_applied"],
			"overhead_tokens_est": meta["overhead_tokens_est"],
			"query_rewritten": True,
		}
	)
	return r
