#!/usr/bin/env python3
"""Golden resident-facing output checks for structured 311 answers."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for path in (str(ROOT), str(PORCH)):
    if path not in sys.path:
        sys.path.insert(0, path)

from answering_kernel import EvidenceBundle, EvidenceStatus, PlannerSource, StrictToolPlan  # noqa: E402
from civic_311 import CivicGeography, CivicPeriod, IssueFamily, OutputName, ToolName, deterministic_answer  # noqa: E402


FORBIDDEN = ("service_requests_311", "get_311_", "sql", "rag", "chroma", "qdrant", "call 911")


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def plan(intent: str, outputs: tuple[str, ...], tools: tuple[str, ...]) -> StrictToolPlan:
    period = CivicPeriod("LAST_MONTH", "last month (June 2026)", date(2026, 6, 1), date(2026, 7, 1))
    return StrictToolPlan(
        domain="311", intent=intent, issue_family=IssueFamily.ROAD_SURFACE_POTHOLE.value,
        geography=CivicGeography().to_dict(), period=period.to_dict(), outputs=outputs, tools=tools,
        needs_clarification=False, clarification_message=None, confidence=0.99,
        planner_source=PlannerSource.RULE,
    )


def bundle(*, facts: dict, status: EvidenceStatus = EvidenceStatus.PARTIALLY_COVERED) -> EvidenceBundle:
    return EvidenceBundle(
        question_scope={"domain": "311"}, issue_family=IssueFamily.ROAD_SURFACE_POTHOLE.value,
        geography=CivicGeography().to_dict(), requested_period={"label": "last month (June 2026)"},
        executed_period={"kind": "LAST_MONTH", "label": "the covered portion of last month (June 2026)", "start_date": "2026-06-01", "end_date_exclusive": "2026-06-28"},
        freshness={"data_through_date": "2026-06-27"}, coverage={"status": "PARTIAL"},
        facts=facts, tool_results={}, limitations=[
            "The local data only runs through June 27, 2026, so it covers only part of last month (June 2026)."
        ],
        source_records=[], evidence_status=status,
    )


def assert_resident_safe(answer: str) -> None:
    lowered = answer.lower()
    for word in FORBIDDEN:
        check(word not in lowered, f"internal or emergency wording leaked: {word}")
    check("Boston 311 service-request data" in answer, "humanized official source missing")


def test_recent_reports() -> None:
    evidence = bundle(facts={
        ToolName.ISSUE_COUNT.value: {"count": 23},
        ToolName.STATUS_BREAKDOWN.value: {"open_count": 7, "closed_count": 16},
        ToolName.TOP_STREETS.value: {"streets": [{"street": "Blue Hill Ave", "count": 4}, {"street": "Dorchester Ave", "count": 3}]},
    })
    answer, _ = deterministic_answer(evidence, plan(
        "recent_reports",
        (OutputName.ISSUE_COUNT.value, OutputName.STATUS_BREAKDOWN.value, OutputName.TOP_STREETS.value),
        (ToolName.ISSUE_COUNT.value, ToolName.STATUS_BREAKDOWN.value, ToolName.TOP_STREETS.value),
    ))
    assert_resident_safe(answer)
    check("23 pothole-related 311 service requests" in answer, "count answer missing")
    check("covers only part of last month" in answer and "June 27, 2026" in answer, "partial coverage missing")
    check("not necessarily distinct verified physical conditions" in answer, "service-request caveat missing")
    check("Blue Hill" not in answer and "Dorchester Ave" not in answer, "recent answer must not volunteer streets")


def test_comparison() -> None:
    evidence = bundle(facts={ToolName.PERIOD_COMPARISON.value: {
        "current_count": 23, "previous_count": 17, "difference": 6, "percentage_change": 35.3,
    }}, status=EvidenceStatus.STALE_BUT_USABLE)
    answer, _ = deterministic_answer(evidence, plan(
        "period_comparison", (OutputName.PERIOD_COMPARISON.value,), (ToolName.PERIOD_COMPARISON.value,),
    ))
    assert_resident_safe(answer)
    check("increased from 17 to 23" in answer, "comparison values missing")
    check("increase of 6 requests, or 35.3%" in answer, "comparison magnitude missing")
    check("getting worse" not in answer.lower(), "comparison must not overstate current conditions")


def test_category_overview() -> None:
    evidence = bundle(facts={ToolName.CATEGORY_DISTRIBUTION.value: {"categories": [
        {"label": "pothole", "count": 24},
        {"label": "streetlight", "count": 10},
    ]}})
    answer, _ = deterministic_answer(evidence, plan(
        "category_overview", (OutputName.CATEGORY_DISTRIBUTION.value,), (ToolName.CATEGORY_DISTRIBUTION.value,),
    ))
    assert_resident_safe(answer)
    check("pothole (24); streetlight (10)" in answer, "category listing missing")


def test_status_followup_does_not_invent_zero_count() -> None:
    evidence = bundle(facts={ToolName.STATUS_BREAKDOWN.value: {"open_count": 4, "closed_count": 9}}, status=EvidenceStatus.COMPLETE)
    answer, _ = deterministic_answer(evidence, plan(
        "status_followup", (OutputName.STATUS_BREAKDOWN.value,), (ToolName.STATUS_BREAKDOWN.value,),
    ))
    assert_resident_safe(answer)
    check("4 requests are recorded as open or in progress, and 9 are recorded as closed" in answer, "status values missing")
    check("I found 0" not in answer, "status-only answer must not invent a zero count")


def test_status_followup_handles_missing_status_values() -> None:
    evidence = bundle(facts={ToolName.STATUS_BREAKDOWN.value: {"open_count": None}})
    answer, _ = deterministic_answer(evidence, plan(
        "status_followup", (OutputName.STATUS_BREAKDOWN.value,), (ToolName.STATUS_BREAKDOWN.value,),
    ))
    assert_resident_safe(answer)
    check("status breakdown is incomplete" in answer.lower(), "missing status values need an explicit limitation")
    check("0 open" not in answer.lower(), "missing status values must not become zero")


def test_status_followup_uses_singular_grammar() -> None:
    evidence = bundle(facts={ToolName.STATUS_BREAKDOWN.value: {"open_count": 1, "closed_count": 4}}, status=EvidenceStatus.COMPLETE)
    answer, _ = deterministic_answer(evidence, plan(
        "status_followup", (OutputName.STATUS_BREAKDOWN.value,), (ToolName.STATUS_BREAKDOWN.value,),
    ))
    assert_resident_safe(answer)
    check("1 request is recorded as open or in progress, and 4 are recorded as closed" in answer, "singular grammar")
    check("1 open or in-progress requests" not in answer, "malformed singular grammar leaked")


def main() -> None:
    tests = [
        test_recent_reports,
        test_comparison,
        test_category_overview,
        test_status_followup_does_not_invent_zero_count,
        test_status_followup_handles_missing_status_values,
        test_status_followup_uses_singular_grammar,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} hybrid 311 golden tests passed")


if __name__ == "__main__":
    main()
