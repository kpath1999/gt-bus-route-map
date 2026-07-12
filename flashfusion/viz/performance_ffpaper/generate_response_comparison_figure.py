#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import textwrap
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIGURE_PDF = ROOT / "assets/figures/summarytableclusterversusraw.pdf"
FIGURE_PNG = ROOT / "assets/figures/summarytableclusterversusraw-1.png"


@dataclass(frozen=True)
class ExampleSpec:
    query_type: str
    dataset: str
    query_id: int
    query_text: str
    raw_results_path: Path
    metrics_path: Path
    ff_notes: tuple[str, str]
    autoiot_notes: tuple[str, str]
    ff_answer_override: str | None = None
    autoiot_answer_override: str | None = None


EXAMPLES = (
    ExampleSpec(
        query_type="Direct",
        dataset="WISDM",
        query_id=1,
        query_text="What is the maximum recorded x-acceleration for user 15?",
        raw_results_path=ROOT / "performance/data/run_all_remaining_20260528_215519/wisdm/benchmark/raw_results.jsonl",
        metrics_path=ROOT / "performance/data/run_all_remaining_20260528_215519/wisdm/benchmark/metrics.csv",
        ff_notes=(
            "Correct answer from a single dataframe lookup",
            "7.2x lower latency, 46.7x fewer input tokens",
        ),
        autoiot_notes=(
            "Gets the answer right, but pays full agent overhead",
            "21,927 input tokens for one max() query",
        ),
    ),
    ExampleSpec(
        query_type="Reasoning",
        dataset="Bus",
        query_id=5,
        query_text="Is the northern half of the route (latitude above median) rougher than the southern half, based on average acceleration variance?",
        raw_results_path=ROOT / "performance/data/run_all_remaining_20260528_215519/bus/benchmark/raw_results.jsonl",
        metrics_path=ROOT / "performance/data/run_all_remaining_20260528_215519/bus/benchmark/metrics.csv",
        ff_notes=(
            "Completes the median-split aggregation correctly",
            "Correct in 6.6s",
        ),
        autoiot_notes=(
            "Multi-step aggregation collapses into execution failure",
            "13.0x slower and still fails",
        ),
    ),
    ExampleSpec(
        query_type="Out-of-Scope",
        dataset="WISDM",
        query_id=10,
        query_text="Based on the acceleration data, predict the exact geographic location where user 10 was jogging.",
        raw_results_path=ROOT / "performance/data/run_all_remaining_20260528_215519/wisdm/benchmark/raw_results.jsonl",
        metrics_path=ROOT / "performance/data/run_all_remaining_20260528_215519/wisdm/benchmark/metrics.csv",
        ff_notes=(
            "Rejects from schema limits alone",
            "3.4s, 363 input tokens",
        ),
        autoiot_notes=(
            "Invents a GPS and map-matching workflow",
            "38.7x slower, 101.7x more input tokens",
        ),
        ff_answer_override=(
            "Query rejected. Exact geographic location is not derivable from the available columns."
        ),
        autoiot_answer_override=(
            "We would need GPS data and a map-matching algorithm. Final answer: Location: Unknown."
        ),
    ),
)


def _import_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, FancyBboxPatch

    return plt, Circle, FancyBboxPatch


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _load_raw_answer(path: Path, baseline: str, query_text: str, occurrence: int = 0) -> str:
    matches: list[dict[str, object]] = []
    target = _normalize_text(query_text)
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            candidate = _normalize_text(str(row.get("query", "")))
            if row.get("baseline") == baseline and (candidate == target or target in candidate or candidate in target):
                matches.append(row)
    if len(matches) <= occurrence:
        raise ValueError(f"Missing raw result for {baseline} / {query_text} in {path}")
    return str(matches[occurrence].get("answer", "")).strip()


def _load_metrics(path: Path, baseline: str, query_id: int, run_id: int = 1) -> dict[str, float | int | bool]:
    with path.open() as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row["baseline"] == baseline and int(row["query_id"]) == query_id and int(row["run_id"]) == run_id:
                return {
                    "latency_s": float(row["latency_s"]),
                    "input_tokens": int(row["input_tokens"]),
                    "output_tokens": int(row["output_tokens"]),
                    "cost_usd": float(row["cost_usd"]),
                    "executed": row["executed"] == "True",
                    "rejected": row["rejected"] == "True",
                }
    raise ValueError(f"Missing metrics row for {baseline} / q{query_id} in {path}")


def _wrap(text: str, width: int) -> str:
    return textwrap.fill(text.replace("**", ""), width=width)


def _badge_text(metrics: dict[str, float | int | bool]) -> str:
    mode = "Rejected" if metrics["rejected"] else "Executed"
    return (
        f"{mode} | {metrics['latency_s']:.1f}s | "
        f"{metrics['input_tokens']:,} in | {metrics['output_tokens']:,} out"
    )


def _ratio_text(num: float, den: float) -> str:
    return f"{num / den:.1f}x"


def _draw_status(ax, x: float, y: float, color: str, label: str, circle_cls) -> None:
    circle = circle_cls((x, y), 0.018, facecolor=color, edgecolor="none")
    ax.add_patch(circle)
    ax.text(x, y, label, ha="center", va="center", fontsize=16, color="white", fontweight="bold")


def _draw_card(ax, x: float, y: float, w: float, h: float, title: str, answer: str, badge: str, notes: tuple[str, str], accent: str, status_label: str, circle_cls, box_cls) -> None:
    ax.add_patch(
        box_cls(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.008,rounding_size=0.015",
            facecolor="white",
            edgecolor="#d7d7d7",
            linewidth=1.2,
        )
    )
    ax.text(x + 0.02, y + h - 0.045, title, ha="left", va="top", fontsize=11, color="#666666", fontweight="bold")

    badge_y = y + h - 0.095
    ax.add_patch(
        box_cls(
            (x + 0.02, badge_y),
            w - 0.04,
            0.042,
            boxstyle="round,pad=0.006,rounding_size=0.012",
            facecolor="#f5f5f5",
            edgecolor="none",
        )
    )
    ax.text(x + 0.035, badge_y + 0.021, badge, ha="left", va="center", fontsize=10.5, color="#333333")

    answer_box_y = y + 0.112
    answer_box_h = h - 0.175
    ax.add_patch(
        box_cls(
            (x + 0.02, answer_box_y),
            w - 0.04,
            answer_box_h,
            boxstyle="round,pad=0.006,rounding_size=0.012",
            facecolor="#fbfbfb",
            edgecolor="none",
        )
    )

    clean_answer = answer.replace("**", "")
    answer_font = 18
    wrap_width = 38
    if len(clean_answer) > 120:
        answer_font = 15
        wrap_width = 42
    if len(clean_answer) > 220:
        answer_font = 13
        wrap_width = 44
    ax.text(
        x + 0.02,
        answer_box_y + answer_box_h - 0.02,
        _wrap(clean_answer, wrap_width),
        ha="left",
        va="top",
        fontsize=answer_font,
        color="#111111",
        linespacing=1.25,
    )

    _draw_status(ax, x + 0.038, y + 0.055, accent, status_label, circle_cls)
    ax.text(x + 0.100, y + 0.074, _wrap(notes[0], 38), ha="left", va="top", fontsize=10.8, color="#202020", linespacing=1.15)
    ax.text(x + 0.100, y + 0.037, _wrap(notes[1], 38), ha="left", va="top", fontsize=10.8, color="#202020", linespacing=1.15)


def generate() -> None:
    plt, circle_cls, box_cls = _import_matplotlib()

    fig = plt.figure(figsize=(16, 11.5), dpi=180)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    green = "#49a942"
    red = "#e53e3e"
    left_gray = "#dadada"

    ax.text(0.12, 0.955, "Question", fontsize=38, fontweight="bold", ha="center")
    ax.text(0.50, 0.955, "Flash-Fusion", fontsize=38, fontweight="bold", ha="center")
    ax.text(0.84, 0.955, "LLM Only", fontsize=38, fontweight="bold", ha="center")
    ax.text(
        0.5,
        0.918,
        "One direct, one reasoning, and one out-of-scope query from the benchmark runs",
        fontsize=15,
        color="#5d5d5d",
        ha="center",
    )

    row_top = 0.82
    row_h = 0.23
    row_step = 0.25
    qx, qw = 0.03, 0.20
    fx, fw = 0.27, 0.30
    axx, aw = 0.61, 0.34

    for idx, spec in enumerate(EXAMPLES):
        y = row_top - idx * row_step - row_h
        ff_answer = spec.ff_answer_override or _load_raw_answer(spec.raw_results_path, "FLASH_FUSION", spec.query_text, occurrence=0)
        autoiot_answer = spec.autoiot_answer_override or _load_raw_answer(spec.raw_results_path, "AUTOIOT_PAPER", spec.query_text, occurrence=0)
        ff_metrics = _load_metrics(spec.metrics_path, "FLASH_FUSION", spec.query_id, run_id=1)
        ai_metrics = _load_metrics(spec.metrics_path, "AUTOIOT_PAPER", spec.query_id, run_id=1)

        ax.add_patch(
            box_cls(
                (qx, y + 0.02),
                qw,
                row_h - 0.04,
                boxstyle="round,pad=0.008,rounding_size=0.01",
                facecolor=left_gray,
                edgecolor="none",
            )
        )
        ax.text(qx + qw / 2, y + row_h - 0.03, f"{spec.query_type} | {spec.dataset}", ha="center", va="top", fontsize=13, color="#4d4d4d", fontweight="bold")
        question_font = 19
        question_width = 22
        if len(spec.query_text) > 80:
            question_font = 17
            question_width = 24
        ax.text(qx + qw / 2, y + 0.09, _wrap(spec.query_text, question_width), ha="center", va="center", fontsize=question_font, color="#111111", linespacing=1.28)

        ff_badge = _badge_text(ff_metrics)
        ai_badge = _badge_text(ai_metrics)
        ff_note_2 = spec.ff_notes[1]
        ai_note_2 = spec.autoiot_notes[1]

        if idx == 0:
            ff_note_2 = f"Same answer with {_ratio_text(float(ai_metrics['latency_s']), float(ff_metrics['latency_s']))} lower latency and {_ratio_text(float(ai_metrics['input_tokens']), float(ff_metrics['input_tokens']))} fewer input tokens"
        elif idx == 1:
            ai_note_2 = f"{_ratio_text(float(ai_metrics['latency_s']), float(ff_metrics['latency_s']))} slower and still ends with an execution error"
        elif idx == 2:
            ff_note_2 = f"Rejects in {ff_metrics['latency_s']:.1f}s with {ff_metrics['input_tokens']:,} input tokens"
            ai_note_2 = f"{_ratio_text(float(ai_metrics['latency_s']), float(ff_metrics['latency_s']))} slower with {_ratio_text(float(ai_metrics['input_tokens']), float(ff_metrics['input_tokens']))} more input tokens"

        _draw_card(
            ax,
            fx,
            y,
            fw,
            row_h,
            "Grounded answer" if not ff_metrics["rejected"] else "Guardrail rejection",
            ff_answer,
            ff_badge,
            (spec.ff_notes[0], ff_note_2),
            green,
            "OK",
            circle_cls,
            box_cls,
        )
        _draw_card(
            ax,
            axx,
            y,
            aw,
            row_h,
            "Agent output",
            autoiot_answer,
            ai_badge,
            (spec.autoiot_notes[0], ai_note_2),
            red,
            "X",
            circle_cls,
            box_cls,
        )

    fig.savefig(FIGURE_PDF, bbox_inches="tight")
    fig.savefig(FIGURE_PNG, bbox_inches="tight", dpi=220)


if __name__ == "__main__":
    generate()