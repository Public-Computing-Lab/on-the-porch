"""Deterministic offline aggregation for Stage 2 turn traces."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


def percentile(values: Sequence[float], quantile: float) -> float:
    """Return a linearly interpolated percentile for a finite numeric sequence."""
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return 0.0
    q = max(0.0, min(1.0, float(quantile)))
    if len(clean) == 1:
        return round(clean[0], 3)
    position = (len(clean) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(clean[lower], 3)
    weight = position - lower
    return round(clean[lower] * (1 - weight) + clean[upper] * weight, 3)


def distribution(values: Sequence[float]) -> Dict[str, Any]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(clean),
        "p50_ms": percentile(clean, 0.50),
        "p90_ms": percentile(clean, 0.90),
        "p95_ms": percentile(clean, 0.95),
        "p99_ms": percentile(clean, 0.99),
        "max_ms": round(max(clean), 3) if clean else 0.0,
    }


def load_traces(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    traces: List[Dict[str, Any]] = []
    stats = {"lines": 0, "malformed": 0, "ignored": 0}
    if not path.exists():
        return traces, stats
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stats["lines"] += 1
        line = raw_line.strip()
        if not line:
            stats["ignored"] += 1
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            stats["malformed"] += 1
            continue
        if not isinstance(value, dict) or value.get("event") != "turn_trace":
            stats["ignored"] += 1
            continue
        traces.append(value)
    return traces, stats


def _group_distribution(
    traces: Iterable[Mapping[str, Any]],
    field: str,
) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[float]] = defaultdict(list)
    successes: Dict[str, int] = defaultdict(int)
    counts: Dict[str, int] = defaultdict(int)
    for trace in traces:
        key = str(trace.get(field) or "unknown")
        counts[key] += 1
        if trace.get("completion_status") == "success":
            successes[key] += 1
        duration = trace.get("duration_ms")
        if isinstance(duration, (int, float)):
            groups[key].append(float(duration))
    output: Dict[str, Dict[str, Any]] = {}
    for key in sorted(counts):
        output[key] = {
            "turn_count": counts[key],
            "success_rate": round(successes[key] / counts[key], 4) if counts[key] else 0.0,
            **distribution(groups[key]),
        }
    return output


def aggregate_traces(
    traces: Sequence[Mapping[str, Any]],
    *,
    input_stats: Mapping[str, int] | None = None,
) -> Dict[str, Any]:
    durations = [
        float(trace["duration_ms"])
        for trace in traces
        if isinstance(trace.get("duration_ms"), (int, float))
    ]
    successes = sum(trace.get("completion_status") == "success" for trace in traces)
    timeouts = sum(bool(trace.get("timeout_category")) for trace in traces)
    failures = len(traces) - successes

    model_call_counts = [int(trace.get("model_call_count") or 0) for trace in traces]
    retry_counts = [int(trace.get("retry_count") or 0) for trace in traces]
    stage_values: Dict[str, List[float]] = defaultdict(list)
    model_values: Dict[str, List[float]] = defaultdict(list)
    failure_categories: Dict[str, int] = defaultdict(int)
    sse_disconnects = 0
    sse_fallbacks = 0

    for trace in traces:
        if trace.get("error_category"):
            failure_categories[str(trace["error_category"])] += 1
        sse_disconnects += int(bool(trace.get("sse_disconnect")))
        sse_fallbacks += int(bool(trace.get("sse_fallback")))
        for stage in trace.get("stages") or []:
            if not isinstance(stage, Mapping):
                continue
            value = stage.get("duration_ms")
            if isinstance(value, (int, float)):
                stage_values[str(stage.get("name") or "unknown")].append(float(value))
        for call in trace.get("model_calls") or []:
            if not isinstance(call, Mapping):
                continue
            value = call.get("duration_ms")
            if isinstance(value, (int, float)):
                model_values[str(call.get("model") or "unknown")].append(float(value))

    slowest = sorted(
        (
            {
                "trace_id": trace.get("trace_id"),
                "scenario_id": trace.get("scenario_id"),
                "route": trace.get("route"),
                "mode": trace.get("mode"),
                "duration_ms": trace.get("duration_ms"),
            }
            for trace in traces
            if isinstance(trace.get("duration_ms"), (int, float))
        ),
        key=lambda row: float(row["duration_ms"]),
        reverse=True,
    )[:20]

    return {
        "input": dict(input_stats or {}),
        "turn_count": len(traces),
        "success_count": successes,
        "failure_count": failures,
        "success_rate": round(successes / len(traces), 4) if traces else 0.0,
        "failure_rate": round(failures / len(traces), 4) if traces else 0.0,
        "timeout_count": timeouts,
        "timeout_rate": round(timeouts / len(traces), 4) if traces else 0.0,
        "latency": distribution(durations),
        "by_route": _group_distribution(traces, "route"),
        "by_mode": _group_distribution(traces, "mode"),
        "by_model": {
            model: distribution(values) for model, values in sorted(model_values.items())
        },
        "model_calls_per_turn": {
            "mean": round(sum(model_call_counts) / len(model_call_counts), 3)
            if model_call_counts
            else 0.0,
            "distribution": distribution(model_call_counts),
        },
        "retries_per_turn": {
            "mean": round(sum(retry_counts) / len(retry_counts), 3)
            if retry_counts
            else 0.0,
            "distribution": distribution(retry_counts),
        },
        "stage_durations": {
            stage: distribution(values) for stage, values in sorted(stage_values.items())
        },
        "failure_categories": dict(sorted(failure_categories.items())),
        "sse_disconnect_count": sse_disconnects,
        "sse_fallback_count": sse_fallbacks,
        "slowest_turns": slowest,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    latency = report.get("latency") or {}
    lines = [
        "# Turn Observability Report",
        "",
        f"- Turns: **{report.get('turn_count', 0)}**",
        f"- Success rate: **{float(report.get('success_rate', 0)) * 100:.1f}%**",
        f"- Failure rate: **{float(report.get('failure_rate', 0)) * 100:.1f}%**",
        f"- Timeout rate: **{float(report.get('timeout_rate', 0)) * 100:.1f}%**",
        (
            "- Latency: "
            f"p50 **{latency.get('p50_ms', 0):.1f} ms**, "
            f"p90 **{latency.get('p90_ms', 0):.1f} ms**, "
            f"p95 **{latency.get('p95_ms', 0):.1f} ms**, "
            f"p99 **{latency.get('p99_ms', 0):.1f} ms**"
        ),
        "",
        "## By Execution Mode",
        "",
        "| Mode | Turns | Success | p50 ms | p90 ms | p95 ms | p99 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, values in (report.get("by_mode") or {}).items():
        lines.append(
            f"| {mode} | {values.get('turn_count', 0)} | "
            f"{float(values.get('success_rate', 0)) * 100:.1f}% | "
            f"{values.get('p50_ms', 0):.1f} | {values.get('p90_ms', 0):.1f} | "
            f"{values.get('p95_ms', 0):.1f} | {values.get('p99_ms', 0):.1f} |"
        )
    lines.extend(
        [
            "",
            "## Stage Durations",
            "",
            "| Stage | Count | p50 ms | p90 ms | p95 ms | p99 ms |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for stage, values in (report.get("stage_durations") or {}).items():
        lines.append(
            f"| {stage} | {values.get('count', 0)} | "
            f"{values.get('p50_ms', 0):.1f} | {values.get('p90_ms', 0):.1f} | "
            f"{values.get('p95_ms', 0):.1f} | {values.get('p99_ms', 0):.1f} |"
        )
    malformed = int((report.get("input") or {}).get("malformed", 0))
    lines.extend(["", f"Malformed input lines skipped: **{malformed}**", ""])
    return "\n".join(lines)


__all__ = [
    "aggregate_traces",
    "distribution",
    "load_traces",
    "percentile",
    "render_markdown",
]
