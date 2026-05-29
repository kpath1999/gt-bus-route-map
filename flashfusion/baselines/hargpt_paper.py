"""
baselines/hargpt_paper.py — Paper-faithful HAR-GPT baseline.

This baseline follows the HAR-GPT paper framing:
- input raw IMU sequences (textualized x/y/z)
- use role-play + step-by-step reasoning prompts
- output a HAR class prediction

Scope policy in this repository:
- Execute only for HAR-classification-style queries on IMU-labeled data.
- Reject non-classification analytics prompts (aggregate/filter/rank/etc.)
- Reject datasets that are not suitable for HAR classification.
"""

from __future__ import annotations

import io
import re
from typing import Iterable

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.pipeline.runner import LLMClient, RunResult


_INSTRUCTION = (
	"You are an expert on analyzing human activities based on IMU recordings. "
	"Reason carefully from temporal patterns and provide one final activity label from the allowed list."
)

_ECG_INSTRUCTION = (
	"You are an expert ECG analyst. Given a tabular ECG signal excerpt, "
	"reason step by step about the signal characteristics and answer the user's question accurately."
)

_BUS_INSTRUCTION = (
	"You are an expert transport telematics analyst. Given bus telemetry data, "
	"reason step by step about the acceleration patterns and road quality to answer the user's question."
)

_NARRATIVE_QUESTION_TEMPLATE = """\
Dataset columns: {columns}

Data sample ({nrows} rows):
{data_sample}

User question: {query}

Reason step by step from the data above, then provide a concise final answer.
Final answer: <your answer>
"""

_QUESTION_TEMPLATE = """\
The IMU data is collected from a wearable/phone sensor stream and represented in the IMU coordinate frame.
The three-axis accelerations are shown below:
1. x-axis: {x_axis}
2. y-axis: {y_axis}
3. z-axis: {z_axis}

Candidate activity labels: {labels}
User question: {query}

Please make an analysis step by step, then finish with one line:
Final label: <one label from candidate activity labels>
"""


def _invoke(client: LLMClient, stage: str, payload: dict[str, str]) -> str:
	chain = (
		ChatPromptTemplate.from_messages(
			[("system", _INSTRUCTION), ("human", _QUESTION_TEMPLATE)]
		)
		| client.llm
		| StrOutputParser()
	)
	return client.invoke_chain(chain, payload, stage=stage)


def _infer_dataset(df) -> str:
	cols = set(df.columns)
	if {"MLII", "V1", "record_id"}.issubset(cols):
		return "ecg"
	if {"latitude", "longitude", "accel_mean"}.issubset(cols):
		return "bus"
	return "wisdm"


def _invoke_narrative(
	client: LLMClient,
	stage: str,
	instruction: str,
	df,
	query: str,
) -> str:
	buf = io.StringIO()
	df.head(40).to_csv(buf, index=False, float_format="%.4f")
	data_sample = buf.getvalue()
	cols = ", ".join(str(c) for c in df.columns)
	chain = (
		ChatPromptTemplate.from_messages(
			[("system", instruction), ("human", _NARRATIVE_QUESTION_TEMPLATE)]
		)
		| client.llm
		| StrOutputParser()
	)
	return client.invoke_chain(
		chain,
		{"columns": cols, "nrows": min(40, len(df)), "data_sample": data_sample, "query": query},
		stage=stage,
	)


def _parse_final_answer(response: str) -> str:
	m = re.search(r"final\s+answer\s*:\s*(.+)", response, flags=re.IGNORECASE | re.DOTALL)
	if m:
		return m.group(1).strip()
	lines = [line.strip() for line in response.splitlines() if line.strip()]
	return lines[-1] if lines else response.strip()


def _run_hargpt_narrative_fallback(
	query: str,
	df,
	client: LLMClient,
	r: RunResult,
	dataset: str,
) -> RunResult:
	"""Zero-shot step-by-step reasoning fallback for non-IMU datasets (ECG, BUS)."""
	instruction = _ECG_INSTRUCTION if dataset == "ecg" else _BUS_INSTRUCTION
	window_stage = f"hargpt_{dataset}_window"
	infer_stage = f"hargpt_{dataset}_infer"
	parse_stage = f"hargpt_{dataset}_parse"

	r.stages_run.append(window_stage)
	response = _invoke_narrative(client, infer_stage, instruction, df, query)
	r.stages_run.append(infer_stage)
	r.trace = response
	r.stages_run.append(parse_stage)

	r.answer = _parse_final_answer(response)
	r.executed = False
	r.rejected = False
	r.agent_tries = 1
	r.execution_attempts.append(
		{
			"stage": infer_stage,
			"rows_used": min(40, len(df)),
			"dataset": dataset,
		}
	)
	return r


def _normalize_label(text: str) -> str:
	return re.sub(r"\s+", " ", text.strip().lower())


def _classification_intent(query: str) -> bool:
	q = query.lower()
	positive = (
		"what activity",
		"what action",
		"classify",
		"classification",
		"recognize",
		"recognise",
		"activity is",
		"action is",
		"doing",
	)
	negative = (
		"maximum",
		"minimum",
		"average",
		"mean",
		"median",
		"count",
		"how many",
		"difference",
		"correlate",
		"correlation",
		"predict",
		"forecast",
		"duration",
		"total",
	)
	return any(k in q for k in positive) and not any(k in q for k in negative)


def _extract_user_id(query: str) -> int | None:
	m = re.search(r"\buser\s+(\d+)\b", query.lower())
	if not m:
		return None
	try:
		return int(m.group(1))
	except ValueError:
		return None


def _format_axis(vals: Iterable[float]) -> str:
	rounded = [f"{float(v):.2f}" for v in vals]
	return "[" + " ".join(rounded) + "]"


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


def _parse_final_label(response: str, labels: list[str]) -> str | None:
	if not response.strip() or not labels:
		return None

	normalized_to_original = {_normalize_label(lbl): lbl for lbl in labels}

	explicit = re.search(r"final\s*label\s*:\s*(.+)", response, flags=re.IGNORECASE)
	if explicit:
		candidate = explicit.group(1).strip().strip("`*.- ")
		norm = _normalize_label(candidate)
		if norm in normalized_to_original:
			return normalized_to_original[norm]

	lower_resp = _normalize_label(response)
	for norm, original in normalized_to_original.items():
		if re.search(rf"\b{re.escape(norm)}\b", lower_resp):
			return original

	return None


def run_hargpt_paper(query: str, df, client: LLMClient, r: RunResult) -> RunResult:
	"""Execute paper-faithful HAR-GPT classification baseline."""
	r.stages_run.append("hargpt_scope")

	dataset = _infer_dataset(df)
	if dataset != "wisdm":
		return _run_hargpt_narrative_fallback(query, df, client, r, dataset)

	required = {"x", "y", "z"}
	if not required.issubset(set(df.columns)):
		r.rejected = True
		r.executed = False
		r.rejection_reason = "HARGPT_PAPER requires IMU acceleration columns x, y, z."
		r.alignment_explanation = "Rejected before execution: dataset is not compatible with HAR-GPT IMU classification."
		return r

	labels = _candidate_labels(df)
	if not labels:
		r.rejected = True
		r.executed = False
		r.rejection_reason = "HARGPT_PAPER requires activity labels (activity_name/activity_label) for candidate classes."
		r.alignment_explanation = "Rejected before execution: no activity label space available for HAR classification."
		return r

	if not _classification_intent(query):
		r.rejected = True
		r.executed = False
		r.rejection_reason = "HARGPT_PAPER is paper-faithful and only handles HAR classification-style queries."
		r.alignment_explanation = (
			"Rejected before execution: query asks for analytics/aggregation outside HAR-GPT classification scope."
		)
		return r

	user_id = _extract_user_id(query)
	work_df = df
	if user_id is not None and "subject_id" in df.columns:
		narrowed = df[df["subject_id"] == user_id]
		if not narrowed.empty:
			work_df = narrowed

	r.stages_run.append("hargpt_window")
	window = work_df[["x", "y", "z"]].head(120)
	x_axis = _format_axis(window["x"].tolist())
	y_axis = _format_axis(window["y"].tolist())
	z_axis = _format_axis(window["z"].tolist())

	response = _invoke(
		client,
		"hargpt_infer",
		{
			"x_axis": x_axis,
			"y_axis": y_axis,
			"z_axis": z_axis,
			"labels": labels,
			"query": query,
		},
	)
	r.stages_run.append("hargpt_infer")
	r.trace = response
	r.stages_run.append("hargpt_parse")

	label = _parse_final_label(response, labels)
	if label is None:
		r.rejected = True
		r.executed = False
		r.rejection_reason = "Model response did not map to a single allowed activity label."
		r.alignment_explanation = "Rejected before execution: ambiguous HAR classification output."
		return r

	r.answer = f"Predicted activity: {label}"
	r.executed = False
	r.rejected = False
	r.agent_tries = 1
	r.execution_attempts.append(
		{
			"stage": "hargpt_infer",
			"rows_used": int(len(window)),
			"labels": labels,
			"predicted_label": label,
		}
	)
	return r
