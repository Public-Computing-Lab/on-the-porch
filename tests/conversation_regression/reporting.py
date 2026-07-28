"""JSON and Markdown reports for conversation regression evaluations."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


def build_report(
    scenario_reports: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    baseline: Mapping[str, Any],
    skipped: Iterable[Mapping[str, str]] = (),
) -> Dict[str, Any]:
    skipped_items = list(skipped)
    domain_totals: Dict[str, Counter[str]] = defaultdict(Counter)
    category_totals: Dict[str, Counter[str]] = defaultdict(Counter)
    dimension_values: Dict[str, list[int]] = defaultdict(list)
    dimension_names: set[str] = set()
    critical_failures: list[Dict[str, str]] = []

    for scenario in scenario_reports:
        outcome = "passed" if scenario.get("passed") else "failed"
        domain_totals[str(scenario.get("domain"))][outcome] += 1
        category_totals[str(scenario.get("conversation_category"))][outcome] += 1
        for failure in scenario.get("critical_failures", []):
            critical_failures.append(
                {
                    "scenario_id": str(scenario.get("scenario_id")),
                    "failure": str(failure),
                }
            )
        for turn in scenario.get("turns", []):
            for dimension, score in turn.get("dimension_scores", {}).items():
                dimension_names.add(dimension)
                if isinstance(score, int):
                    dimension_values[dimension].append(score)

    passed = sum(1 for item in scenario_reports if item.get("passed"))
    failed = len(scenario_reports) - passed
    return {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "baseline": dict(baseline),
        "totals": {
            "evaluated": len(scenario_reports),
            "passed": passed,
            "failed": failed,
            "skipped": len(skipped_items),
            "critical_failures": len(critical_failures),
        },
        "by_domain": {
            key: dict(value) for key, value in sorted(domain_totals.items())
        },
        "by_conversation_category": {
            key: dict(value) for key, value in sorted(category_totals.items())
        },
        "by_dimension": {
            dimension: {
                "evaluated_turns": len(scores),
                "average": round(sum(scores) / len(scores), 2) if scores else None,
                "minimum": min(scores) if scores else None,
            }
            for dimension in sorted(dimension_names)
            for scores in [dimension_values[dimension]]
        },
        "critical_failures": critical_failures,
        "scenarios": list(scenario_reports),
        "skipped": skipped_items,
    }


def report_to_markdown(report: Mapping[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Conversation Regression Report",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Generated: `{report['generated_at']}`",
        f"- Baseline: `{report['baseline'].get('commit')}`",
        f"- Evaluated: **{totals['evaluated']}**",
        f"- Passed: **{totals['passed']}**",
        f"- Failed: **{totals['failed']}**",
        f"- Skipped: **{totals['skipped']}**",
        f"- Critical failures: **{totals['critical_failures']}**",
        "",
        "## Results by domain",
        "",
        "| Domain | Passed | Failed |",
        "|---|---:|---:|",
    ]
    for domain, values in report["by_domain"].items():
        lines.append(
            f"| {domain} | {values.get('passed', 0)} | {values.get('failed', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Evaluation dimensions",
            "",
            "| Dimension | Evaluated turns | Average | Minimum |",
            "|---|---:|---:|---:|",
        ]
    )
    for dimension, values in report["by_dimension"].items():
        average = values["average"] if values["average"] is not None else "n/a"
        minimum = values["minimum"] if values["minimum"] is not None else "n/a"
        lines.append(
            f"| {dimension} | {values['evaluated_turns']} | {average} | {minimum} |"
        )
    lines.extend(["", "## Critical failures", ""])
    if report["critical_failures"]:
        for failure in report["critical_failures"]:
            lines.append(f"- `{failure['scenario_id']}`: {failure['failure']}")
    else:
        lines.append("None.")
    lines.extend(["", "## Scenario details", ""])
    for scenario in report["scenarios"]:
        status = "PASS" if scenario["passed"] else "FAIL"
        lines.append(f"### {status}: {scenario['scenario_id']} - {scenario['title']}")
        for turn in scenario["turns"]:
            turn_status = "pass" if turn["passed"] else "fail"
            lines.append(
                f"- Turn {turn['turn_index'] + 1} ({turn_status}): "
                f"`{turn['user']}`"
            )
            for check in turn["checks"]:
                if not check["passed"]:
                    lines.append(f"  - {check['message']}")
        lines.append("")
    if report["skipped"]:
        lines.extend(["## Skipped", ""])
        for item in report["skipped"]:
            lines.append(f"- `{item['scenario_id']}`: {item['reason']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(
    report: Mapping[str, Any],
    output_dir: Path,
    stem: str,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(report_to_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
