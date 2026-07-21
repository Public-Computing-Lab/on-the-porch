#!/usr/bin/env python3
"""Contract and compiler tests for the three semantic planner actions."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
sys.path[:0] = [str(PORCH), str(ROOT)]

from model_led_orchestrator import OrchestratorContractError, normalize_resident_input  # noqa: E402
from orchestrator_evaluation import _expected_plan, load_evaluation_dataset  # noqa: E402
from semantic_plan_compiler import (  # noqa: E402
    DerivedStateEffect,
    GuardedDisposition,
    compile_semantic_plan,
    guard_semantic_plan,
)
from semantic_planner import (  # noqa: E402
    PlannerAction,
    SemanticPlan,
    SemanticSafetyError,
    build_semantic_planner_prompt,
    planner_function_declarations,
    split_semantic_planner_prompt,
)

DATA = ROOT / "evaluation" / "model_led_orchestrator"
CONTEXT = {
    "default_geography": {
        "scope": "NEIGHBORHOOD", "location_id": "dorchester", "name": "Dorchester",
    },
    "active_topic": None,
    "active_entity": None,
    "time_scope": None,
    "previous_result_summary": None,
}


def gather_payload(**changes) -> dict:
    task = {
        "domain": "SERVICE_REQUESTS",
        "operation": "COUNT",
        "subject": None,
        "geography": {"mode": "DEFAULT", "value": None},
        "time": {"kind": "PREVIOUS_CALENDAR_MONTH", "value": None},
        "source_scope": "OFFICIAL",
    }
    task.update(changes.pop("task", {}))
    value = {"action": "gather_evidence", "arguments": {"tasks": [task]}}
    value.update(changes)
    return value


def parse(value: dict, *, context=None, tokens=()) -> SemanticPlan:
    return SemanticPlan.from_action(
        value["action"],
        value["arguments"],
        trusted_context=context or CONTEXT,
        available_trusted_reference_tokens=tokens,
    )


def assert_raises(exc_type, function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}")


def test_three_actions_and_generic_canonicalization() -> None:
    gather = parse(gather_payload(action="GATHER-EVIDENCE"))
    assert gather.action is PlannerAction.GATHER_EVIDENCE
    direct = parse({
        "action": "respond directly",
        "arguments": {"response_kind": "acknowledgement", "response_text": "Thanks."},
    })
    assert direct.action is PlannerAction.RESPOND_DIRECTLY
    clarify = parse({
        "action": "ask_clarification",
        "arguments": {"question": "Which area?", "missing_information": "geography"},
    })
    assert clarify.action is PlannerAction.ASK_CLARIFICATION
    assert_raises(
        OrchestratorContractError,
        parse,
        {"action": "gather_evidence", "arguments": {"tasks": [], "state": "replace"}},
    )


def test_direct_response_fact_and_trusted_summary_safety() -> None:
    unsafe_direct = parse({
        "action": "respond_directly",
        "arguments": {
            "response_kind": "SOCIAL",
            "response_text": "Dorchester had 42 requests last month.",
        },
    })
    guarded = guard_semantic_plan(unsafe_direct, trusted_context=CONTEXT)
    assert guarded.disposition is GuardedDisposition.INVALID_UNSAFE
    assert not guarded.tool_requests
    assert guarded.resident_response != unsafe_direct.response_text
    context = copy.deepcopy(CONTEXT)
    context["previous_result_summary"] = {
        "token": "summary:311:42", "text": "The validated result found 42 requests in Dorchester.",
    }
    plan = parse({
        "action": "respond_directly",
        "arguments": {
            "response_kind": "REPHRASE_TRUSTED_SUMMARY",
            "response_text": "There were 42 requests in Dorchester.",
            "trusted_summary_token": "summary:311:42",
        },
    }, context=context, tokens=("summary:311:42",))
    assert_raises(
        SemanticSafetyError,
        parse,
        gather_payload(task={"subject": "Use aggregate_311 directly"}),
    )
    assert plan.trusted_summary_token == "summary:311:42"
    unsupported_rephrase = parse({
        "action": "respond_directly",
        "arguments": {
            "response_kind": "REPHRASE_TRUSTED_SUMMARY",
            "response_text": "There were 99 requests in Cambridge.",
            "trusted_summary_token": "summary:311:42",
        },
    }, context=context, tokens=("summary:311:42",))
    guarded_rephrase = guard_semantic_plan(
        unsupported_rephrase,
        trusted_context=context,
        trusted_reference_tokens=("summary:311:42",),
    )
    assert guarded_rephrase.disposition is GuardedDisposition.INVALID_UNSAFE
    assert not guarded_rephrase.tool_requests


def test_compiler_derives_tools_status_and_state() -> None:
    result = compile_semantic_plan(parse(gather_payload()), trusted_context=CONTEXT)
    assert [item.tool_id.value for item in result.tool_requests] == [
        "aggregate_311", "get_source_status",
    ]
    assert result.derived_state_effect is DerivedStateEffect.REPLACE_TOPIC

    same_topic = copy.deepcopy(CONTEXT)
    same_topic["active_topic"] = "BOSTON_311"
    result = compile_semantic_plan(parse(gather_payload()), trusted_context=same_topic)
    assert result.derived_state_effect is DerivedStateEffect.REFINE

    events = parse(gather_payload(task={
        "domain": "EVENTS",
        "operation": "CHECK_EXISTENCE",
        "time": {"kind": "CURRENT_WEEK", "value": None},
    }))
    result = compile_semantic_plan(events, trusted_context=CONTEXT)
    assert [item.tool_id.value for item in result.tool_requests] == [
        "query_events", "get_source_status",
    ]

    status = parse(gather_payload(task={
        "operation": "STATUS",
        "geography": {"mode": "DEFAULT", "value": None},
        "time": {"kind": "CURRENT_WEEK", "value": None},
    }))
    guarded_status = guard_semantic_plan(status, trusted_context=CONTEXT)
    assert guarded_status.disposition is GuardedDisposition.EXECUTABLE
    assert [item.tool_id.value for item in guarded_status.tool_requests] == ["get_source_status"]


def test_compiler_is_subject_paraphrase_independent_for_tool_selection() -> None:
    one = parse(gather_payload(task={"subject": "potholes"}))
    two = parse(gather_payload(task={"subject": "damaged road surfaces"}))
    left = [item.tool_id.value for item in compile_semantic_plan(one, trusted_context=CONTEXT).tool_requests]
    right = [item.tool_id.value for item in compile_semantic_plan(two, trusted_context=CONTEXT).tool_requests]
    assert left == right == ["aggregate_311", "get_source_status"]


def test_time_geography_and_selected_event_are_deterministic() -> None:
    missing = parse(gather_payload(task={"geography": {"mode": "MISSING", "value": None}}))
    result = compile_semantic_plan(missing, trusted_context=CONTEXT)
    assert not result.tool_requests and "location" in (result.clarification_reason or "").lower()

    explicit = parse(gather_payload(task={
        "domain": "LOCATION",
        "operation": "FIND",
        "geography": {"mode": "EXPLICIT", "value": "Codman Square"},
        "time": {"kind": "NONE", "value": None},
        "source_scope": "ANY",
    }))
    result = compile_semantic_plan(explicit, trusted_context=CONTEXT)
    assert result.tool_requests[0].tool_id.value == "resolve_location"

    selected_context = copy.deepcopy(CONTEXT)
    selected_context["active_entity"] = {"token": "event:435", "title": "Parade"}
    event = parse(gather_payload(task={
        "domain": "EVENTS",
        "operation": "FIND",
        "subject": "parade",
        "geography": {"mode": "NONE", "value": None},
        "time": {"kind": "NONE", "value": None},
    }))
    result = compile_semantic_plan(
        event, trusted_context=selected_context, trusted_reference_tokens=("event:435",),
    )
    assert result.tool_requests[0].arguments["entity_token"] == "event:435"

    variant = parse(gather_payload(task={
        "domain": "DOCUMENTS",
        "operation": "EXPLAIN",
        "subject": "registration requirements",
        "geography": {"mode": "DEFAULT", "value": None},
        "time": {"kind": "TRUSTED_REFERENCE", "value": "period:event:435"},
        "source_scope": "ANY",
    }), tokens=("event:435", "period:event:435"))
    guarded = guard_semantic_plan(
        variant,
        trusted_context=selected_context,
        trusted_reference_tokens=("event:435", "period:event:435"),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    assert guarded.tool_requests[0].tool_id.value == "query_events"
    assert guarded.tool_requests[0].arguments["entity_token"] == "event:435"


def test_all_expected_labels_compile_without_execution() -> None:
    for filename, sealed in (
        ("contract_smoke.json", False),
        ("development.json", False),
        ("sealed_holdout_role_separated_v2.json", True),
    ):
        _, _, cases = load_evaluation_dataset(DATA / filename, allow_sealed_holdout=sealed)
        seen = set()
        for case in cases:
            if case.family_id in seen:
                continue
            seen.add(case.family_id)
            plan = _expected_plan(case.expected, case.trusted_context, case.trusted_reference_tokens)
            result = compile_semantic_plan(
                plan,
                trusted_context=case.trusted_context,
                trusted_reference_tokens=case.trusted_reference_tokens,
            )
            if plan.action is PlannerAction.GATHER_EVIDENCE:
                assert result.tool_requests, (filename, case.family_id, result.clarification_reason)


def test_prompt_modes_size_privacy_and_function_catalog() -> None:
    straight = build_semantic_planner_prompt(
        normalize_resident_input("What's happening next week?"),
        trusted_context=CONTEXT,
        output_mode="plain",
    )
    curly = build_semantic_planner_prompt(
        normalize_resident_input("What’s happening next week?"),
        trusted_context=CONTEXT,
        output_mode="plain",
    )
    function = build_semantic_planner_prompt(
        normalize_resident_input("What's happening next week?"),
        trusted_context=CONTEXT,
        output_mode="function",
    )
    assert straight == curly
    system_instruction, resident_content = split_semantic_planner_prompt(function)
    assert system_instruction and "TRUSTED_STATE=" not in system_instruction
    assert "RESIDENT_MESSAGE=" not in system_instruction
    structured_content = json.loads(resident_content)
    assert structured_content["resident_message"] == "What's happening next week?"
    assert structured_content["trusted_state"]["default_geography"]["name"] == "Dorchester"
    assert structured_content["trusted_tokens"] == []
    assert split_semantic_planner_prompt("health probe") == (None, "health probe")
    assert len(straight.encode("utf-8")) < 5_500
    assert len(function.encode("utf-8")) < 4_500
    for text in (straight, function):
        assert "aggregate_311" not in text and "tool_id" not in text and "state_intent" not in text
    declarations = planner_function_declarations()
    assert [item["name"] for item in declarations] == [
        "respond_directly", "ask_clarification", "gather_evidence",
    ]


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"semantic action/compiler tests passed: {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
