#!/usr/bin/env python3
"""Focused behavioral tests for the bounded structured 311 answering slice."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for path in (str(ROOT), str(PORCH)):
    if path not in sys.path:
        sys.path.insert(0, path)

from answering_kernel import EvidenceBundle, EvidenceStatus, PlannerSource, StrictToolPlan  # noqa: E402
from civic_311 import (  # noqa: E402
    Active311FactContext,
    CivicGeography,
    CivicPeriod,
    IssueFamily,
    OutputName,
    ToolName,
    _latest_usable_period,
    _validate_llm_plan,
    deterministic_answer,
    family_for_message,
    is_311_candidate,
    plan_311_question,
    validate_claims,
)
from conversation_state import (  # noqa: E402
    ThreadStateEnvelope,
    load_thread_state,
    serialize_thread_state,
    with_civic_311_context,
)
from source_freshness import (  # noqa: E402
    CoverageStatus,
    FreshnessAssessment,
    FreshnessBasis,
    FreshnessStatus,
    format_structured_count_answer,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _plan(*, family: IssueFamily, intent: str = "recent_reports") -> StrictToolPlan:
    period = CivicPeriod("RECENT", "the last 7 days", date(2026, 6, 21), date(2026, 6, 28))
    return StrictToolPlan(
        domain="311",
        intent=intent,
        issue_family=family.value,
        geography=CivicGeography().to_dict(),
        period=period.to_dict(),
        outputs=(OutputName.ISSUE_COUNT.value, OutputName.STATUS_BREAKDOWN.value),
        tools=(ToolName.ISSUE_COUNT.value, ToolName.STATUS_BREAKDOWN.value),
        needs_clarification=False,
        clarification_message=None,
        confidence=0.99,
        planner_source=PlannerSource.RULE,
    )


def _bundle(*, status: EvidenceStatus = EvidenceStatus.STALE_BUT_USABLE) -> EvidenceBundle:
    period = CivicPeriod("RECENT", "the latest fully covered 7-day period", date(2026, 6, 21), date(2026, 6, 28))
    return EvidenceBundle(
        question_scope={"domain": "311", "intent": "recent_reports"},
        issue_family=IssueFamily.ROAD_SURFACE_POTHOLE.value,
        geography=CivicGeography().to_dict(),
        requested_period={"label": "the last 7 days"},
        executed_period=period.to_dict(),
        freshness={"data_through_date": "2026-06-27", "freshness_status": "STALE"},
        coverage={"status": "OUTSIDE"},
        facts={
            ToolName.ISSUE_COUNT.value: {"count": 12},
            ToolName.STATUS_BREAKDOWN.value: {"open_count": 4, "closed_count": 8},
            ToolName.TOP_STREETS.value: {"streets": [{"street": "Dorchester Ave", "count": 3}]},
        },
        tool_results={},
        limitations=["The local source contains records through 2026-06-27."],
        source_records=[],
        evidence_status=status,
    )


def test_aliases_and_rule_plans() -> None:
    cases = {
        "Any pothole reports lately?": IssueFamily.ROAD_SURFACE_POTHOLE,
        "Where is water collecting after rain?": IssueFamily.DRAINAGE_AND_FLOODING,
        "How bad are the cracked road conditions?": IssueFamily.STREET_AND_PAVEMENT_CONDITION,
        "Are people reporting illegal dumping?": IssueFamily.TRASH_AND_ILLEGAL_DUMPING,
        "Which streetlights are out?": IssueFamily.STREETLIGHT,
    }
    for question, family in cases.items():
        plan = plan_311_question(question, now=date(2026, 7, 17))
        check(family_for_message(question) is family, f"alias failed for {question}")
        check(plan.issue_family == family.value and not plan.needs_clarification, question)
        check(plan.planner_source is PlannerSource.RULE, question)

    overview = plan_311_question("Most common 311 street problems this month?", now=date(2026, 7, 17))
    check(overview.intent == "category_overview", "category overview must be deterministic")
    check(overview.tools == (ToolName.CATEGORY_DISTRIBUTION.value, ToolName.SOURCE_FRESHNESS.value), "overview tools")

    streets = plan_311_question("Which streets have the most pothole reports?", now=date(2026, 7, 17))
    check(streets.intent == "top_streets", "top-streets question must keep its intent")
    check(not streets.geography.get("street"), "the final letters of 'most' must not become a street suffix")


def test_clarifications_and_trusted_context() -> None:
    no_context = plan_311_question("Are these reports still open?", now=date(2026, 7, 17))
    check(no_context.needs_clarification, "new-thread report reference needs clarification")
    check(is_311_candidate("Are these reports still open?", None), "reference should remain in structured clarifier")

    active = Active311FactContext(
        issue_family=IssueFamily.ROAD_SURFACE_POTHOLE.value,
        geography=CivicGeography(),
        period_kind="RECENT",
        executed_period=CivicPeriod("RECENT", "the last 7 days", date(2026, 6, 21), date(2026, 6, 28)),
        data_through_date="2026-06-27",
        freshness_status="STALE",
        coverage_status="STALE_BUT_USABLE",
        outputs=(OutputName.ISSUE_COUNT.value,),
    ).to_dict()
    followup = plan_311_question("Are these reports still open?", active_context=active, now=date(2026, 7, 17))
    check(not followup.needs_clarification, "trusted prior reports should resolve")
    check(followup.issue_family == IssueFamily.ROAD_SURFACE_POTHOLE.value, "family inherited")
    check(followup.intent == "status_followup", "status followup plan")
    scoped = plan_311_question("What do those 311 reports look like in Codman Square?", active_context=active, now=date(2026, 7, 17))
    check(not scoped.needs_clarification and scoped.issue_family == IssueFamily.ROAD_SURFACE_POTHOLE.value, "trusted generic reference")

    street = plan_311_question("What has been done about potholes on my street?", now=date(2026, 7, 17))
    check(street.needs_clarification and "Which street" in (street.clarification_message or ""), "street clarification")
    check(scoped.geography["subarea"] == "Codman Square" and not scoped.geography["filterable"], "explicit subarea must remain honest")


def test_planner_schema_is_strict() -> None:
    fallback = _plan(family=IssueFamily.ROAD_SURFACE_POTHOLE)
    allowed = json.dumps({
        "domain": "311",
        "issue_family": IssueFamily.ROAD_SURFACE_POTHOLE.value,
        "intent": "recent_reports",
        "geography_scope": "DORCHESTER",
        "street_reference": "NONE",
        "time_intent": "RECENT",
        "requested_outputs": [OutputName.ISSUE_COUNT.value],
        "needs_clarification": False,
        "confidence": 0.92,
    })
    parsed = _validate_llm_plan(allowed, fallback=fallback)
    check(parsed is not None and parsed.planner_source is PlannerSource.LLM, "valid bounded planner output")
    check(parsed.tools == (ToolName.ISSUE_COUNT.value, ToolName.SOURCE_FRESHNESS.value), "tools must be derived")
    rejected = _validate_llm_plan(json.dumps({
        "domain": "311",
        "issue_family": IssueFamily.ROAD_SURFACE_POTHOLE.value,
        "intent": "ignore_rules",
        "geography_scope": "DORCHESTER",
        "street_reference": "NONE",
        "time_intent": "RECENT",
        "requested_outputs": ["DROP_TABLE"],
        "tools": ["write_sql"],
        "needs_clarification": False,
        "confidence": 1,
    }), fallback=fallback)
    check(rejected is None, "invented tool must be rejected")


def test_latest_usable_period_and_safe_answer() -> None:
    requested = CivicPeriod("RECENT", "the last 7 days", date(2026, 7, 11), date(2026, 7, 18))
    executed = _latest_usable_period(requested, date(2026, 6, 27))
    check(executed is not None and executed.end_exclusive == date(2026, 6, 28), "latest usable end")
    check(executed.start == date(2026, 6, 21), "latest usable rolling period")
    answer, _mode = deterministic_answer(_bundle(), _plan(family=IssueFamily.ROAD_SURFACE_POTHOLE))
    check("911" not in answer, "ordinary 311 answer must not use emergency wording")
    check("closed request does not verify" in answer.lower(), "status caveat must remain")
    valid, issues = validate_claims(answer, _bundle())
    check(valid, f"deterministic answer should validate: {issues}")
    unsafe, unsafe_issues = validate_claims("The 12 reports were fixed. Call 911.", _bundle())
    check(not unsafe and "unsupported_emergency_guidance" in unsafe_issues, "unsafe claims rejected")


def test_missing_street_fields_are_not_a_false_zero() -> None:
    street_geo = CivicGeography(street="dorchester ave")
    plan = _plan(family=IssueFamily.ROAD_SURFACE_POTHOLE)
    plan = StrictToolPlan(
        domain=plan.domain, intent=plan.intent, issue_family=plan.issue_family,
        geography=street_geo.to_dict(), period=plan.period, outputs=plan.outputs, tools=plan.tools,
        needs_clarification=False, clarification_message=None, confidence=plan.confidence,
        planner_source=plan.planner_source,
    )
    evidence = _bundle(status=EvidenceStatus.PARTIALLY_COVERED)
    evidence.geography = street_geo.to_dict()
    evidence.facts[ToolName.ISSUE_COUNT.value] = {
        "count": 0, "total_matching": 18, "addressable_count": 0, "street_coverage_unavailable": True,
    }
    evidence.limitations = ["The matching local 311 records do not include street fields, so a street-specific count cannot be verified from this dataset."]
    answer, _mode = deterministic_answer(evidence, plan)
    check("cannot verify a street-specific count" in answer.lower(), "missing street coverage must be explicit")
    check("found no matching" not in answer.lower(), "missing street data must not become a false zero")


def test_thread_context_is_bounded_and_round_trips() -> None:
    context = Active311FactContext(
        issue_family=IssueFamily.STREETLIGHT.value,
        geography=CivicGeography(),
        period_kind="RECENT",
        executed_period=CivicPeriod("RECENT", "the last 7 days", date(2026, 6, 21), date(2026, 6, 28)),
        data_through_date="2026-06-27",
        freshness_status="STALE",
        coverage_status="STALE_BUT_USABLE",
        outputs=(OutputName.ISSUE_COUNT.value,),
    ).to_dict()
    envelope = with_civic_311_context(ThreadStateEnvelope(retrieval_cache={}), context)
    reloaded = load_thread_state(serialize_thread_state(envelope))
    check(reloaded.civic_311_context == context, "civic context round trip")


def test_source_freshness_has_no_311_emergency_redirect() -> None:
    assessment = FreshnessAssessment(
        source_key="service_requests_311",
        freshness_status=FreshnessStatus.STALE,
        freshness_basis=FreshnessBasis.SOURCE_AS_OF,
        last_successful_ingest_at=None,
        data_through_date=date(2026, 6, 27),
        latest_record_date=date(2026, 6, 27),
        age_days=20,
        coverage_status=CoverageStatus.OUTSIDE,
        requested_start_date=date(2026, 7, 11),
        requested_end_date_exclusive=date(2026, 7, 18),
    )
    answer = format_structured_count_answer(
        assessment=assessment,
        count=0,
        subject_phrase="pothole requests",
        geography_phrase="in Dorchester",
        is_311=True,
        current_condition=True,
    )
    check("911" not in answer, "311 current-condition limitation is not an emergency response")


def main() -> None:
    tests = [
        test_aliases_and_rule_plans,
        test_clarifications_and_trusted_context,
        test_planner_schema_is_strict,
        test_latest_usable_period_and_safe_answer,
        test_missing_street_fields_are_not_a_false_zero,
        test_thread_context_is_bounded_and_round_trips,
        test_source_freshness_has_no_311_emergency_redirect,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} hybrid 311 tests passed")


if __name__ == "__main__":
    main()
