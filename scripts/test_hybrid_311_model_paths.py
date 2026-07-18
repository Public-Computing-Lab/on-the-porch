#!/usr/bin/env python3
"""Focused planner, synthesis, repair, and deadline tests for hybrid 311."""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for path in (str(ROOT), str(PORCH)):
    if path not in sys.path:
        sys.path.insert(0, path)

from answering_kernel import AnswerMode, EvidenceBundle, EvidenceStatus, GlobalDeadline, PlannerSource, StrictToolPlan  # noqa: E402
from civic_311 import (  # noqa: E402
    CivicGeography,
    CivicPeriod,
    IssueFamily,
    OutputName,
    ToolName,
    compose_311_answer,
    is_311_candidate,
    plan_311_question,
    process_311_question,
    validate_claims,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def plan(intent: str = "recent_reports") -> StrictToolPlan:
    return StrictToolPlan(
        domain="311",
        intent=intent,
        issue_family=IssueFamily.ROAD_SURFACE_POTHOLE.value,
        geography=CivicGeography().to_dict(),
        period=CivicPeriod("RECENT", "the last 7 days", date(2026, 7, 11), date(2026, 7, 18)).to_dict(),
        outputs=(
            OutputName.ISSUE_COUNT.value,
            OutputName.STATUS_BREAKDOWN.value,
            OutputName.TOP_STREETS.value,
            OutputName.SOURCE_FRESHNESS.value,
        ),
        tools=(
            ToolName.ISSUE_COUNT.value,
            ToolName.STATUS_BREAKDOWN.value,
            ToolName.TOP_STREETS.value,
            ToolName.SOURCE_FRESHNESS.value,
        ),
        needs_clarification=False,
        clarification_message=None,
        confidence=0.99,
        planner_source=PlannerSource.RULE,
    )


def bundle() -> EvidenceBundle:
    return EvidenceBundle(
        question_scope={"domain": "311", "intent": "recent_reports"},
        issue_family=IssueFamily.ROAD_SURFACE_POTHOLE.value,
        geography=CivicGeography().to_dict(),
        requested_period={
            "kind": "RECENT",
            "label": "the last 7 days",
            "start_date": "2026-07-11",
            "end_date_exclusive": "2026-07-18",
        },
        executed_period={
            "kind": "RECENT",
            "label": "the latest fully covered 7-day period",
            "start_date": "2026-06-21",
            "end_date_exclusive": "2026-06-28",
        },
        freshness={"data_through_date": "2026-06-27", "freshness_status": "STALE"},
        coverage={"status": "OUTSIDE"},
        facts={
            ToolName.ISSUE_COUNT.value: {"count": 12},
            ToolName.STATUS_BREAKDOWN.value: {"open_count": 4, "closed_count": 8},
            ToolName.TOP_STREETS.value: {"streets": []},
        },
        tool_results={},
        limitations=["The local data only runs through June 27, 2026, so it does not describe current conditions."],
        source_records=[{
            "type": "official",
            "source": "Boston 311 service-request data",
            "authority": "Official City of Boston service-request data",
            "data_through_date": "2026-06-27",
            "source_key": "service_requests_311",
        }],
        evidence_status=EvidenceStatus.STALE_BUT_USABLE,
    )


def valid_synthesized_answer() -> str:
    return (
        "For the latest fully covered 7-day period, Dorchester had 12 pothole-related 311 service requests: "
        "4 requests are recorded as open or in progress, and 8 are recorded as closed. "
        "The local data only runs through June 27, 2026, so it does not describe current conditions. "
        "These service requests are not necessarily distinct verified physical conditions. "
        "A closed request does not verify that a physical problem was repaired, and an open request does not show active work. "
        "Source: Boston 311 service-request data."
    )


def semantic_plan_json() -> str:
    return json.dumps({
        "domain": "311",
        "intent": "street_status",
        "issue_family": "DRAINAGE_AND_FLOODING",
        "geography_scope": "STREET",
        "street_reference": "MISSING",
        "time_intent": "RECENT",
        "requested_outputs": [
            "MATCHING_REQUESTS",
            "STATUS_BREAKDOWN",
            "RECENT_EXAMPLES",
            "SOURCE_FRESHNESS",
        ],
        "needs_clarification": True,
        "confidence": 0.96,
    })


def test_exact_count_stays_rule_only() -> None:
    calls = []
    result = plan_311_question(
        "How many pothole reports were recorded in June?",
        now=date(2026, 7, 17),
        planner_llm=lambda *_args: calls.append(True) or "{}",
        deadline=GlobalDeadline.now(),
    )
    check(result.intent == "exact_count", "exact count intent")
    check(result.period["label"] == "June 2026", "named month period")
    check(result.tools == (ToolName.ISSUE_COUNT.value, ToolName.SOURCE_FRESHNESS.value), "exact tools")
    check(not calls, "exact question must not call planner")

    category_calls = []
    category = plan_311_question(
        "What street problems are people reporting around Dorchester?",
        now=date(2026, 7, 17),
        planner_llm=lambda *_args: category_calls.append(True) or "{}",
        deadline=GlobalDeadline.now(),
    )
    check(category.intent == "category_overview", "category overview rule plan")
    check(not category_calls, "reviewed category overview must stay rule-first")


def test_semantic_planner_clarifies_and_persists_pending_scope() -> None:
    calls = []

    def planner(prompt: str, _timeout: float) -> str:
        calls.append(prompt)
        return semantic_plan_json()

    result = process_311_question(
        "Has anything been done about the water collecting on my street, and have other people complained?",
        connection_factory=lambda: (_ for _ in ()).throw(AssertionError("tools must not run")),
        now=datetime(2026, 7, 17, 12, 0),
        planner_llm=planner,
    )
    check(len(calls) == 1 and result.telemetry.planner_call_count == 1, "one planner call")
    check(result.plan.planner_source is PlannerSource.LLM, "semantic plan accepted")
    check(result.mode is AnswerMode.CLARIFICATION, "missing street clarifies")
    check(result.next_context is not None, "pending canonical scope persists")
    check(
        set(result.next_context["outputs"]) == {
            "MATCHING_REQUESTS",
            "STATUS_BREAKDOWN",
            "RECENT_EXAMPLES",
            "SOURCE_FRESHNESS",
        },
        "semantic planner must preserve compound information needs",
    )
    check(result.evidence.tool_results == {}, "clarification has no tools")
    check(is_311_candidate("Dorchester Ave", result.next_context), "bare street continues pending 311")
    continued = plan_311_question("Dorchester Ave", active_context=result.next_context, now=date(2026, 7, 17))
    check(continued.intent == "street_status", "street continues compound information need")
    check(continued.issue_family == IssueFamily.DRAINAGE_AND_FLOODING.value, "family is inherited")
    check(continued.geography["street"] == "dorchester ave", "street is canonical")

    narrowed = json.loads(semantic_plan_json())
    narrowed["intent"] = "recent_reports"
    narrowed["requested_outputs"] = ["ISSUE_COUNT", "RECENT_EXAMPLES"]
    narrowed_stats: dict = {}
    narrowed_result = plan_311_question(
        "Has anything been done about the water collecting on my street, and have other people complained?",
        now=date(2026, 7, 17),
        planner_llm=lambda *_args: json.dumps(narrowed),
        deadline=GlobalDeadline.now(),
        planner_stats=narrowed_stats,
    )
    check(narrowed_stats.get("failure") == "invalid_output", "narrowed compound plan must be rejected")
    check(narrowed_result.planner_source is PlannerSource.RULE, "narrowed plan must use rule fallback")
    check(
        set(narrowed_result.outputs) == {
            "MATCHING_REQUESTS",
            "STATUS_BREAKDOWN",
            "RECENT_EXAMPLES",
            "SOURCE_FRESHNESS",
        },
        "rule fallback must preserve the complete compound request",
    )


def test_planner_timeout_and_invalid_output_fall_back_once() -> None:
    timeout_calls = []

    def timeout_planner(_prompt: str, _timeout: float) -> str:
        timeout_calls.append(True)
        raise TimeoutError("slow")

    stats: dict = {}
    timed_out = plan_311_question(
        "Has anything been done about the water collecting on my street, and have other people complained?",
        now=date(2026, 7, 17),
        planner_llm=timeout_planner,
        deadline=GlobalDeadline.now(),
        planner_stats=stats,
    )
    check(len(timeout_calls) == 1 and stats == {"call_count": 1, "failure": "timeout"}, "planner timeout accounting")
    check(timed_out.needs_clarification and timed_out.planner_source is PlannerSource.RULE, "timeout fallback")

    invalid_stats: dict = {}
    invalid = plan_311_question(
        "Has anything been done about the water collecting on my street, and have other people complained?",
        now=date(2026, 7, 17),
        planner_llm=lambda *_args: json.dumps({"sql": "SELECT *"}),
        deadline=GlobalDeadline.now(),
        planner_stats=invalid_stats,
    )
    check(invalid.needs_clarification, "invalid output fallback")
    check(invalid_stats == {"call_count": 1, "failure": "invalid_output"}, "invalid output accounting")


def test_grounded_synthesis_uses_one_call_and_sanitized_evidence() -> None:
    prompts = []

    def synth(prompt: str, _timeout: float) -> str:
        prompts.append(prompt)
        check("source_key" not in prompt, "internal source key must not reach synthesis")
        check("get_311_" not in prompt, "internal tool key must not reach synthesis")
        check("ROAD_SURFACE_POTHOLE" not in prompt, "internal issue enum must not reach synthesis")
        check("pothole" in prompt.lower(), "resident-friendly issue label must reach synthesis")
        check("end_date_exclusive" not in prompt, "half-open storage date must not reach synthesis")
        check("end_date_inclusive" in prompt, "model evidence needs inclusive date semantics")
        return valid_synthesized_answer()

    outcome = compose_311_answer(
        "Have people complained about potholes recently?",
        bundle=bundle(),
        plan=plan(),
        deadline=GlobalDeadline.now(),
        synthesis_llm=synth,
        repair_llm=synth,
    )
    check(outcome.mode is AnswerMode.EVIDENCE_BOUNDED_SYNTHESIS, "grounded synthesis mode")
    check(outcome.synthesis_call_count == 1 and outcome.repair_call_count == 0, "one synthesis only")
    check("service_requests_311" not in prompts[0], "internal source key must not enter model evidence")


def test_synthesis_timeout_and_provider_unavailable_fall_back() -> None:
    calls = []

    def timeout(_prompt: str, _seconds: float) -> str:
        calls.append(True)
        raise TimeoutError("slow")

    timed_out = compose_311_answer(
        "Have people complained about potholes recently?",
        bundle=bundle(),
        plan=plan(),
        deadline=GlobalDeadline.now(),
        synthesis_llm=timeout,
    )
    check(len(calls) == 1 and timed_out.synthesis_call_count == 1, "one timed synthesis")
    check(timed_out.timeout_stage == "synthesis" and timed_out.fallback_used, "timeout fallback metadata")
    check(timed_out.mode is not AnswerMode.EVIDENCE_BOUNDED_SYNTHESIS, "timeout deterministic answer")

    unavailable = compose_311_answer(
        "Have people complained about potholes recently?",
        bundle=bundle(),
        plan=plan(),
        deadline=GlobalDeadline.now(),
    )
    check(unavailable.fallback_used and unavailable.synthesis_call_count == 0, "provider unavailable fallback")


def test_partial_tool_failure_never_becomes_a_false_zero() -> None:
    partial = bundle()
    partial.evidence_status = EvidenceStatus.PARTIAL
    partial.facts.pop(ToolName.ISSUE_COUNT.value)
    partial.model_facts = None
    partial.partial_failures = [ToolName.ISSUE_COUNT.value]
    outcome = compose_311_answer(
        "Have people complained about potholes recently?",
        bundle=partial,
        plan=plan(),
        deadline=GlobalDeadline.now(),
    )
    check(outcome.mode is AnswerMode.DETERMINISTIC_FALLBACK, "partial result must use fallback mode")
    check("temporarily unavailable" in outcome.answer, "partial result must disclose unavailable evidence")
    check("I found 0" not in outcome.answer, "missing count must not be presented as zero")


def test_validation_failure_repairs_once_then_stops() -> None:
    primary_calls = []
    repair_calls = []

    def bad(_prompt: str, _seconds: float) -> str:
        primary_calls.append(True)
        return valid_synthesized_answer().replace("12 pothole", "99 pothole")

    def repair(_prompt: str, _seconds: float) -> str:
        repair_calls.append(True)
        return valid_synthesized_answer()

    repaired = compose_311_answer(
        "Have people complained about potholes recently?",
        bundle=bundle(),
        plan=plan(),
        deadline=GlobalDeadline.now(),
        synthesis_llm=bad,
        repair_llm=repair,
    )
    check(len(primary_calls) == len(repair_calls) == 1, "one primary and one repair")
    check(repaired.repair_call_count == 1 and repaired.mode is AnswerMode.EVIDENCE_BOUNDED_SYNTHESIS, "repair accepted")
    check("99 pothole" not in repaired.answer, "unsupported number removed")

    failed = compose_311_answer(
        "Have people complained about potholes recently?",
        bundle=bundle(),
        plan=plan(),
        deadline=GlobalDeadline.now(),
        synthesis_llm=bad,
        repair_llm=bad,
    )
    check(failed.synthesis_call_count == 1 and failed.repair_call_count == 1, "repair cap")
    check(failed.mode is AnswerMode.DETERMINISTIC_FALLBACK, "invalid repair uses deterministic fallback")


def test_deadline_pressure_skips_synthesis() -> None:
    calls = []
    deadline = GlobalDeadline.from_started_at(time.perf_counter() - 14.0)
    outcome = compose_311_answer(
        "Have people complained about potholes recently?",
        bundle=bundle(),
        plan=plan(),
        deadline=deadline,
        synthesis_llm=lambda *_args: calls.append(True) or valid_synthesized_answer(),
    )
    check(not calls and outcome.synthesis_call_count == 0, "deadline must skip synthesis")
    check(outcome.timeout_stage == "synthesis_budget" and outcome.fallback_used, "deadline fallback metadata")


def test_zero_result_uses_cautious_deterministic_answer() -> None:
    evidence = bundle()
    evidence.question_scope = {"domain": "311", "intent": "street_status"}
    evidence.geography = CivicGeography(street="dorchester ave").to_dict()
    evidence.facts[ToolName.ISSUE_COUNT.value] = {"count": 0}
    evidence.facts[ToolName.STATUS_BREAKDOWN.value] = {"open_count": 0, "closed_count": 0}
    calls = []
    outcome = compose_311_answer(
        "What has been done about drainage on Dorchester Ave?",
        bundle=evidence,
        plan=plan("street_status"),
        deadline=GlobalDeadline.now(),
        synthesis_llm=lambda *_args: calls.append(True) or valid_synthesized_answer(),
    )
    check(not calls and outcome.synthesis_call_count == 0, "zero result must not be synthesized")
    check("I did not find matching" in outcome.answer, "zero result needs cautious wording")
    check("There were no" not in outcome.answer and "I found 0" not in outcome.answer, "zero overclaim")
    check("formatted differently" in outcome.answer, "street normalization limitation")


def test_validator_rejects_unsupported_claims() -> None:
    evidence = bundle()
    cases = {
        "There were 99 pothole requests for the latest fully covered 7-day period. Source: Boston 311 service-request data.": "unsupported_number",
        "Blue Hill Avenue had pothole requests for the latest fully covered 7-day period. Source: Boston 311 service-request data.": "unsupported_street",
        valid_synthesized_answer().replace(
            "only runs through June 27, 2026, so it does not describe current conditions",
            "is current",
        ): "stale_claimed_current",
        valid_synthesized_answer() + " It covered June 21 through June 28, 2026.": "unsupported_date_range",
        valid_synthesized_answer().replace("does not show active work", "shows active work"): "open_is_active_work",
        valid_synthesized_answer().replace("4 requests are recorded as open or in progress, and 8 are recorded as closed. ", ""): "missing_status_breakdown",
        valid_synthesized_answer() + " Call 911.": "unsupported_emergency_guidance",
        valid_synthesized_answer() + " That is a -58.3% decrease.": "signed_directional_percentage",
        valid_synthesized_answer() + " The RAG pipeline failed.": "internal_term",
        valid_synthesized_answer().replace("311 service requests", "311 complaints"): "complaint_terminology",
        valid_synthesized_answer().replace("4 requests are", "1 open or in-progress requests are"): "singular_request_grammar",
    }
    for answer, expected in cases.items():
        valid, issues = validate_claims(answer, evidence)
        check(not valid and expected in issues, f"validator missing {expected}: {issues}")

    street_evidence = bundle()
    street_evidence.question_scope = {"domain": "311", "intent": "street_status"}
    street_evidence.geography = {
        "label": "Dorchester",
        "kind": "DORCHESTER",
        "street": "dorchester ave",
        "subarea": None,
        "filterable": True,
    }
    street_without_completion_limit = (
        "For the latest fully covered 7-day period, Dorchester Ave had 12 pothole service requests: "
        "4 requests are recorded as open or in progress, and 8 are recorded as closed. "
        "The local data only runs through June 27, 2026, so it does not describe current conditions. "
        "Service requests are not necessarily distinct verified physical conditions. "
        "Source: Boston 311 service-request data."
    )
    valid, issues = validate_claims(street_without_completion_limit, street_evidence)
    check(
        not valid and "missing_physical_completion_caveat" in issues,
        "street answer needs physical-completion limit",
    )
    street_with_completion_limit = street_without_completion_limit.replace(
        "Source: Boston 311 service-request data.",
        "Request records or their absence do not determine whether the physical condition was addressed. "
        "Source: Boston 311 service-request data.",
    )
    valid, issues = validate_claims(street_with_completion_limit, street_evidence)
    check(valid, f"provider-style physical-completion limit should validate: {issues}")

    zero_street_evidence = bundle()
    zero_street_evidence.question_scope = {"domain": "311", "intent": "street_status"}
    zero_street_evidence.geography = dict(street_evidence.geography)
    zero_street_evidence.facts[ToolName.ISSUE_COUNT.value] = {"count": 0}
    zero_street_evidence.facts[ToolName.STATUS_BREAKDOWN.value] = {
        "open_count": 0,
        "closed_count": 0,
    }
    categorical_zero = (
        "There were 0 pothole-related 311 service requests on Dorchester Ave during the latest fully covered 7-day period. "
        "The local data only runs through June 27, 2026, so it does not describe current conditions. "
        "Request records or their absence do not determine whether the physical condition was addressed. "
        "Source: Boston 311 service-request data."
    )
    valid, issues = validate_claims(categorical_zero, zero_street_evidence)
    check(not valid and "categorical_zero_claim" in issues, "categorical zero should be rejected")
    cautious_zero = categorical_zero.replace(
        "There were 0 pothole-related 311 service requests",
        "I did not find matching pothole-related 311 service requests",
    )
    valid, issues = validate_claims(cautious_zero, zero_street_evidence)
    check(valid, f"cautious zero-result street answer should validate: {issues}")

    category_evidence = bundle()
    category_evidence.question_scope = {"domain": "311", "intent": "category_overview"}
    awkward_category = (
        "In Dorchester, the latest fully covered 7-day period, there were 50 street-related 311 service requests. "
        "The local data only runs through June 27, 2026, so it does not describe current conditions. "
        "Source: Boston 311 service-request data."
    )
    valid, issues = validate_claims(awkward_category, category_evidence)
    check(not valid and "awkward_category_sentence" in issues, "awkward category lead should be rejected")


def main() -> None:
    tests = [
        test_exact_count_stays_rule_only,
        test_semantic_planner_clarifies_and_persists_pending_scope,
        test_planner_timeout_and_invalid_output_fall_back_once,
        test_grounded_synthesis_uses_one_call_and_sanitized_evidence,
        test_synthesis_timeout_and_provider_unavailable_fall_back,
        test_partial_tool_failure_never_becomes_a_false_zero,
        test_validation_failure_repairs_once_then_stops,
        test_deadline_pressure_skips_synthesis,
        test_zero_result_uses_cautious_deterministic_answer,
        test_validator_rejects_unsupported_claims,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} hybrid 311 model-path tests passed")


if __name__ == "__main__":
    main()
