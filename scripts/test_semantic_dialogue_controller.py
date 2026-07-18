#!/usr/bin/env python3
"""Focused cross-domain tests for the shared semantic dialogue controller."""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for value in (str(ROOT), str(PORCH)):
    if value not in sys.path:
        sys.path.insert(0, value)

from answering_kernel import EvidenceBundle, EvidenceStatus, GlobalDeadline  # noqa: E402
from civic_311 import (  # noqa: E402
    Active311FactContext,
    CivicGeography,
    CivicPeriod,
    IssueFamily,
    IssueScope,
    OutputName,
    ToolName,
    _where_clause,
    build_311_dialogue_hint,
    deterministic_answer,
    plan_311_question,
)
from conversation_state import ThreadStateEnvelope, load_thread_state, serialize_thread_state  # noqa: E402
from event_query_context import build_event_dialogue_hint  # noqa: E402
from semantic_dialogue_controller import (  # noqa: E402
    CapabilityIntent,
    ClarificationReason,
    DialogueDomain,
    DecisionSource,
    ResponseStrategy,
    TurnKind,
    decide_dialogue_turn,
    next_dialogue_context,
)


TODAY = date(2026, 7, 18)


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _active_311(
    *,
    scope: IssueScope,
    family: IssueFamily | None,
    period: CivicPeriod | None = None,
    coverage: str = "COMPLETE",
) -> dict:
    return Active311FactContext(
        issue_family=family.value if family else None,
        issue_scope=scope.value,
        geography=CivicGeography(),
        period_kind=(period or CivicPeriod("LAST_MONTH", "last month (June 2026)", date(2026, 6, 1), date(2026, 7, 1))).kind,
        executed_period=period or CivicPeriod("LAST_MONTH", "last month (June 2026)", date(2026, 6, 1), date(2026, 7, 1)),
        data_through_date="2026-06-27",
        freshness_status="STALE",
        coverage_status=coverage,
        outputs=(OutputName.ISSUE_COUNT.value, OutputName.SOURCE_FRESHNESS.value),
    ).to_dict()


def _decision(message: str, *, civic: dict | None = None, dialogue: dict | None = None):
    hints = tuple(
        hint
        for hint in (
            build_311_dialogue_hint(message, civic),
            build_event_dialogue_hint(message, dialogue_context=dialogue),
        )
        if hint is not None
    )
    return decide_dialogue_turn(message, trusted_context=dialogue, rule_hints=hints)


def test_aggregate_311_rule_matrix() -> None:
    questions = (
        "How many 311 requests were filed last month?",
        "How many total 311 requests were there last month?",
        "What was the overall number of 311 requests?",
        "Count all 311 requests in Dorchester.",
        "How many requests were there regardless of issue?",
        "How many 311 cases were submitted in June?",
    )
    for question in questions:
        decision = _decision(question)
        check(decision.domain is DialogueDomain.CIVIC_311, question)
        check(decision.capability_intent is CapabilityIntent.COUNT_311, question)
        check(decision.controller_call_count == 0, question)
        plan = plan_311_question(question, now=TODAY)
        check(plan.issue_scope == IssueScope.ALL_311.value, question)
        check(plan.issue_family is None and not plan.needs_clarification, question)
    last_month = plan_311_question(questions[0], now=TODAY)
    check(last_month.period["start_date"] == "2026-06-01", "last month start")
    check(last_month.period["end_date_exclusive"] == "2026-07-01", "last month end")
    check(last_month.intent == "exact_count", "aggregate count intent")
    where, params = _where_clause(
        family=None,
        geography=CivicGeography(),
        period=CivicPeriod("LAST_MONTH", "last month", date(2026, 6, 1), date(2026, 7, 1)),
        all_311=True,
    )
    check("service_name" not in where and "case_topic" not in where, "aggregate SQL has no issue filter")
    check(params == ["Dorchester", "C11", "2026-06-01", "2026-07-01"], "aggregate SQL params")

    bundle = EvidenceBundle(
        question_scope={"domain": "311", "intent": "exact_count", "issue_scope": "ALL_311", "issue_label": "all 311 service requests"},
        issue_family=None,
        geography=CivicGeography().to_dict(),
        requested_period=last_month.period,
        executed_period=CivicPeriod(
            "LAST_MONTH", "the covered portion of last month (June 2026)", date(2026, 6, 1), date(2026, 6, 28),
        ).to_dict(),
        freshness={"data_through_date": "2026-06-27", "freshness_status": "STALE"},
        coverage={"status": "PARTIAL", "data_through_date": "2026-06-27"},
        facts={ToolName.ISSUE_COUNT.value: {"count": 42}},
        model_facts={"issue_count": {"count": 42}},
        tool_results={ToolName.ISSUE_COUNT.value: {"count": 42}},
        limitations=["The local data only runs through June 27, 2026, so it covers only part of last month."],
        source_records=[],
        evidence_status=EvidenceStatus.PARTIALLY_COVERED,
    )
    answer, _mode = deterministic_answer(bundle, last_month)
    check("42" in answer and "311 service request" in answer, "aggregate answer count")
    check("June 28 through June 30 are not covered" in answer, "partial June missing days")
    check("not treated as zero" in answer, "partial June zero caveat")

    outside_plan = plan_311_question(
        "How many 311 requests were filed last week?", now=TODAY,
    )
    outside = EvidenceBundle(
        question_scope={"domain": "311", "intent": "exact_count", "issue_scope": "ALL_311"},
        issue_family=None,
        geography=CivicGeography().to_dict(),
        requested_period=outside_plan.period,
        executed_period=outside_plan.period,
        freshness={"data_through_date": "2026-06-27", "freshness_status": "STALE"},
        coverage={"status": "OUTSIDE", "data_through_date": "2026-06-27"},
        facts={},
        model_facts={},
        tool_results={},
        limitations=["The local data only runs through June 27, 2026."],
        source_records=[],
        evidence_status=EvidenceStatus.OUTSIDE_COVERAGE,
    )
    outside_answer, _mode = deterministic_answer(outside, outside_plan)
    check("can't provide a reliable count" in outside_answer.lower(), "outside count must be unavailable")
    check("latest fully covered" not in outside_answer.lower(), "outside exact count must not shift periods")


def test_pending_overall_and_bare_overall() -> None:
    pending = _active_311(
        scope=IssueScope.UNSPECIFIED,
        family=None,
        coverage="PENDING_CLARIFICATION",
    )
    decision = _decision("overall", civic=pending)
    check(decision.turn_kind is TurnKind.CLARIFICATION_RESPONSE, "pending overall relationship")
    check(decision.capability_intent is CapabilityIntent.COUNT_311, "pending overall capability")
    plan = plan_311_question("overall", active_context=pending, now=TODAY)
    check(plan.issue_scope == IssueScope.ALL_311.value and plan.issue_family is None, "pending overall plan")
    check(plan.period == pending["executed_period"], "pending overall must retain period")
    bare = _decision("overall")
    check(bare.response_strategy is ResponseStrategy.ASK_CLARIFICATION, "bare overall clarification")
    check(bare.domain is DialogueDomain.GENERAL, "bare overall must not become 311")


def test_aggregate_followups_and_refinement() -> None:
    aggregate = _active_311(scope=IssueScope.ALL_311, family=None)
    before = _decision("What about the month before?", civic=aggregate)
    check(before.capability_intent is CapabilityIntent.COUNT_311, "month-before route")
    before_plan = plan_311_question("What about the month before?", active_context=aggregate, now=TODAY)
    check(before_plan.issue_scope == IssueScope.ALL_311.value, "month-before scope")
    check(before_plan.period["start_date"] == "2026-05-01", "month-before start")
    check(before_plan.period["end_date_exclusive"] == "2026-06-01", "month-before end")
    categories = _decision("What types were most common?", civic=aggregate)
    check(categories.capability_intent is CapabilityIntent.DISTRIBUTE_311_CATEGORIES, "category route")
    categories_plan = plan_311_question("What types were most common?", active_context=aggregate, now=TODAY)
    check(categories_plan.issue_scope == IssueScope.ALL_311.value, "category aggregate scope")
    check(categories_plan.intent == "category_overview", "category intent")

    pothole = _active_311(scope=IssueScope.SPECIFIC_FAMILY, family=IssueFamily.ROAD_SURFACE_POTHOLE)
    overall = plan_311_question("What about overall?", active_context=pothole, now=TODAY)
    check(overall.issue_scope == IssueScope.ALL_311.value, "specific-to-overall scope")
    check(overall.issue_family is None and overall.intent == "exact_count", "specific-to-overall intent")


def test_event_tool_rephrase_contradiction_and_correction() -> None:
    listing = _decision("What events are happening next week?")
    check(listing.capability_intent is CapabilityIntent.LIST_EVENTS, "event list capability")
    check(listing.response_strategy is ResponseStrategy.EXECUTE_CAPABILITY, "event tool strategy")

    context = next_dialogue_context(
        None,
        decision=listing,
        canonical_parameters={"semantic_time": "THIS_WEEK"},
        previous_result_summary={"kind": "EVENT_LIST", "status": "NO_EVENTS", "returned_count": 0},
    )
    rephrase = _decision("if this week events are done, just say that", dialogue=context)
    check(rephrase.turn_kind is TurnKind.RESPONSE_REPHRASE, "event rephrase kind")
    check(not rephrase.tool_required, "event rephrase must not execute")
    check(rephrase.direct_response == "The listed events for this week are over.", "event rephrase wording")

    contradictory = _decision("what events are not happening next week. dont say those which are")
    check(contradictory.turn_kind is TurnKind.UNSATISFIABLE, "contradictory event kind")
    check(contradictory.response_strategy is ResponseStrategy.ASK_CLARIFICATION, "contradictory event strategy")
    check(not contradictory.tool_required, "contradictory event must not execute")

    correction = _decision("No, I meant next calendar week.", dialogue=context)
    check(correction.turn_kind is TurnKind.CORRECTION, "event correction kind")
    check(correction.capability_intent is CapabilityIntent.LIST_EVENTS, "event correction route")


def test_small_talk_and_controller_fallbacks() -> None:
    hello = _decision("Hello, what can you help me with?")
    check(hello.turn_kind is TurnKind.SMALL_TALK and not hello.tool_required, "small talk direct")
    check("Dorchester events" in str(hello.direct_response), "small talk scope")

    calls = {"count": 0}

    def invalid(_prompt: str, _timeout: float) -> str:
        calls["count"] += 1
        return json.dumps({"turn_kind": "INVENTED"})

    context = next_dialogue_context(
        None,
        decision=_decision("What events are happening next week?"),
        previous_result_summary={"kind": "EVENT_LIST", "status": "EVENTS_FOUND"},
    )
    invalid_result = decide_dialogue_turn(
        "Actually, do that instead",
        trusted_context=context,
        llm_classifier=invalid,
        deadline=GlobalDeadline.from_started_at(time.perf_counter()),
    )
    check(calls["count"] == 1 and invalid_result.controller_call_count == 1, "one invalid call")
    check(invalid_result.decision_source is DecisionSource.FALLBACK, "invalid schema fallback")

    timeout_calls = {"count": 0}

    def timeout(_prompt: str, _timeout: float) -> str:
        timeout_calls["count"] += 1
        raise TimeoutError

    timeout_result = decide_dialogue_turn(
        "Actually, do that instead",
        trusted_context=context,
        llm_classifier=timeout,
        deadline=GlobalDeadline.from_started_at(time.perf_counter()),
    )
    check(timeout_calls["count"] == 1, "one timeout call")
    check(timeout_result.clarification_reason is ClarificationReason.CONTROLLER_TIMEOUT, "timeout fallback")

    incompatible = decide_dialogue_turn(
        "Could you check that?",
        trusted_context=context,
        llm_classifier=lambda _prompt, _timeout: json.dumps(
            {
                "turn_kind": "CONTEXTUAL_TOOL_FOLLOWUP",
                "domain": "EVENTS",
                "relationship": "FOLLOW_UP",
                "response_strategy": "EXECUTE_CAPABILITY",
                "tool_required": True,
                "capability_intent": "COUNT_311",
                "clarification_reason": "NONE",
                "trusted_reference_target": "PREVIOUS_EVENT_RESULT",
                "confidence": 0.99,
            }
        ),
        deadline=GlobalDeadline.from_started_at(time.perf_counter()),
    )
    check(
        incompatible.clarification_reason is ClarificationReason.INVALID_CONTROLLER_OUTPUT,
        "cross-domain capabilities must fail closed",
    )

    provider_disabled = decide_dialogue_turn(
        "Actually, do that instead",
        trusted_context=context,
        llm_classifier=None,
        deadline=GlobalDeadline.from_started_at(time.perf_counter()),
    )
    check(provider_disabled.controller_call_count == 0, "provider-disabled fallback must use no model")
    check(provider_disabled.capability_intent is CapabilityIntent.LEGACY_DOMAIN_ROUTE, "provider-disabled safe route")


def test_dialogue_state_round_trip() -> None:
    decision = _decision("What events are happening next week?")
    context = next_dialogue_context(
        None,
        decision=decision,
        canonical_parameters={"start_date": "2026-07-20", "end_date_exclusive": "2026-07-27"},
        previous_result_summary={"kind": "EVENT_LIST", "status": "NO_EVENTS", "returned_count": 0},
    )
    envelope = ThreadStateEnvelope(retrieval_cache={}, dialogue_context=context)
    loaded = load_thread_state(serialize_thread_state(envelope))
    check(loaded.dialogue_context == context, "dialogue context round trip")
    check("Resident question" not in json.dumps(context), "raw conversation must not be stored")


def main() -> None:
    tests = (
        test_aggregate_311_rule_matrix,
        test_pending_overall_and_bare_overall,
        test_aggregate_followups_and_refinement,
        test_event_tool_rephrase_contradiction_and_correction,
        test_small_talk_and_controller_fallbacks,
        test_dialogue_state_round_trip,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} semantic dialogue controller tests passed")


if __name__ == "__main__":
    main()
