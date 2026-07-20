#!/usr/bin/env python3
"""Contract and deterministic compiler tests for semantic planning."""

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
from semantic_plan_compiler import compile_semantic_plan  # noqa: E402
from semantic_planner import SemanticPlan, build_semantic_planner_prompt, semantic_plan_response_schema  # noqa: E402

DATA = ROOT / "evaluation" / "model_led_orchestrator"
CONTEXT = {
    "default_geography": {"scope": "NEIGHBORHOOD", "location_id": "dorchester", "name": "Dorchester"},
    "active_topic": None, "active_entity": None, "time_scope": None,
    "previous_result_summary": None,
}


def plan_payload(**changes) -> dict:
    value = {
        "schema_version": "1",
        "strategy": "GATHER_EVIDENCE",
        "relationship": "STANDALONE",
        "user_goal": "Count service requests from the previous month.",
        "tasks": [{
            "domain": "SERVICE_REQUESTS", "goal": "COUNT",
            "subject": {"kind": "ALL", "text": None},
            "evidence_needs": ["COUNT"],
            "geography": {"mode": "DEFAULT", "value": None},
            "time": {"kind": "PREVIOUS_CALENDAR_MONTH", "value": None},
            "source_types": ["OFFICIAL_DATA"],
        }],
        "trusted_summary_reference": None,
        "state_intent": "REPLACE_TOPIC",
        "direct_response": None,
        "clarification_question": None,
        "confidence": 0.95,
    }
    value.update(changes)
    return value


def assert_raises(exc_type, function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}")


def test_strategy_cross_field_validation() -> None:
    gather = SemanticPlan.from_mapping(plan_payload())
    assert gather.strategy.value == "GATHER_EVIDENCE" and len(gather.tasks) == 1

    direct = plan_payload(
        strategy="DIRECT_RESPONSE", tasks=[], state_intent="PRESERVE",
        direct_response="Happy to help.",
    )
    SemanticPlan.from_mapping(direct)
    direct["tasks"] = copy.deepcopy(plan_payload()["tasks"])
    assert_raises(OrchestratorContractError, SemanticPlan.from_mapping, direct)

    clarify = plan_payload(
        strategy="ASK_CLARIFICATION", tasks=[], state_intent="PRESERVE",
        clarification_question="Which time periods should I compare?",
    )
    SemanticPlan.from_mapping(clarify)
    clarify["direct_response"] = "I found facts."
    assert_raises(OrchestratorContractError, SemanticPlan.from_mapping, clarify)


def test_compiler_domain_evidence_mappings() -> None:
    cases = [
        (plan_payload(), ["aggregate_311"]),
        (plan_payload(tasks=[{
            "domain": "SERVICE_REQUESTS", "goal": "GROUP",
            "subject": {"kind": "ALL", "text": None},
            "evidence_needs": ["CATEGORY_DISTRIBUTION"],
            "geography": {"mode": "DEFAULT", "value": None},
            "time": {"kind": "CURRENT_CALENDAR_MONTH", "value": None},
            "source_types": ["OFFICIAL_DATA"],
        }]), ["aggregate_311"]),
        (plan_payload(tasks=[{
            "domain": "EVENTS", "goal": "FIND",
            "subject": {"kind": "ALL", "text": None}, "evidence_needs": ["RECORDS"],
            "geography": {"mode": "DEFAULT", "value": None},
            "time": {"kind": "CURRENT_WEEK", "value": None},
            "source_types": ["OFFICIAL_DATA"],
        }]), ["query_events"]),
        (plan_payload(tasks=[{
            "domain": "CRIME", "goal": "COMPARE",
            "subject": {"kind": "ALL", "text": None},
            "evidence_needs": ["COUNT", "PERIOD_COMPARISON"],
            "geography": {"mode": "DEFAULT", "value": None},
            "time": {"kind": "CURRENT_WEEK", "value": None},
            "source_types": ["OFFICIAL_DATA"],
        }]), ["aggregate_crime"]),
        (plan_payload(tasks=[{
            "domain": "DOCUMENTS", "goal": "SUMMARIZE",
            "subject": {"kind": "POLICY", "text": None},
            "evidence_needs": ["DOCUMENT_PASSAGES"],
            "geography": {"mode": "DEFAULT", "value": None},
            "time": {"kind": "NONE", "value": None},
            "source_types": ["PLANNING_POLICY"],
        }]), ["search_documents"]),
    ]
    for raw, expected in cases:
        compiled = compile_semantic_plan(
            SemanticPlan.from_mapping(raw), trusted_context=CONTEXT,
        )
        assert [item.tool_id.value for item in compiled.tool_requests] == expected
        assert compiled.clarification_reason is None


def test_compiler_is_paraphrase_independent() -> None:
    raw = plan_payload()
    one = SemanticPlan.from_mapping({**raw, "user_goal": "Count all requests last month."})
    two = SemanticPlan.from_mapping({**raw, "user_goal": "How many cases came in during the prior month?"})
    left = compile_semantic_plan(one, trusted_context=CONTEXT).to_safe_dict()
    right = compile_semantic_plan(two, trusted_context=CONTEXT).to_safe_dict()
    assert left["tool_requests"] == right["tool_requests"]


def test_time_geography_and_clarification() -> None:
    retained = plan_payload(tasks=[{
        "domain": "SERVICE_REQUESTS", "goal": "FIND",
        "subject": {"kind": "ALL", "text": None}, "evidence_needs": ["RECORDS"],
        "geography": {"mode": "DEFAULT", "value": None},
        "time": {"kind": "RETAIN_TRUSTED_PERIOD", "value": "period:week"},
        "source_types": ["OFFICIAL_DATA"],
    }])
    plan = SemanticPlan.from_mapping(retained, available_trusted_reference_tokens=["period:week"])
    compiled = compile_semantic_plan(
        plan, trusted_context=CONTEXT, trusted_reference_tokens=["period:week"],
    )
    assert compiled.tool_requests[0].arguments["time_scope"]["trusted_reference_token"] == "period:week"

    missing = plan_payload(tasks=[{
        **copy.deepcopy(plan_payload()["tasks"][0]),
        "geography": {"mode": "MISSING", "value": None},
    }])
    result = compile_semantic_plan(SemanticPlan.from_mapping(missing), trusted_context=CONTEXT)
    assert not result.tool_requests and "location" in (result.clarification_reason or "").lower()

    explicit = plan_payload(tasks=[{
        "domain": "LOCATION", "goal": "FIND", "subject": {"kind": "ALL", "text": None},
        "evidence_needs": ["LOCATION_RESOLUTION"],
        "geography": {"mode": "EXPLICIT", "value": "Codman Square"},
        "time": {"kind": "NONE", "value": None}, "source_types": [],
    }])
    result = compile_semantic_plan(SemanticPlan.from_mapping(explicit), trusted_context=CONTEXT)
    assert result.tool_requests[0].tool_id.value == "resolve_location"


def test_all_expected_labels_compile_without_execution() -> None:
    for filename, sealed in (
        ("contract_smoke.json", False),
        ("development.json", False),
        ("sealed_holdout.json", True),
    ):
        _, _, cases = load_evaluation_dataset(DATA / filename, allow_sealed_holdout=sealed)
        seen = set()
        for case in cases:
            if case.family_id in seen:
                continue
            seen.add(case.family_id)
            plan = _expected_plan(case.expected, case.trusted_reference_tokens)
            result = compile_semantic_plan(
                plan,
                trusted_context=case.trusted_context,
                trusted_reference_tokens=case.trusted_reference_tokens,
            )
            if plan.strategy.value == "GATHER_EVIDENCE":
                assert result.tool_requests, (filename, case.family_id, result.clarification_reason)


def test_prompt_size_normalization_and_native_schema() -> None:
    straight = build_semantic_planner_prompt(
        normalize_resident_input("What's happening next week?"), trusted_context=CONTEXT,
    )
    curly = build_semantic_planner_prompt(
        normalize_resident_input("What’s happening next week?"), trusted_context=CONTEXT,
    )
    assert straight == curly
    assert len(straight.encode("utf-8")) < 3_800
    assert "aggregate_311" not in straight and "tool_id" not in straight
    schema = semantic_plan_response_schema()
    assert schema["type"] == "object" and set(schema["required"]) == set(schema["properties"])


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"semantic planner/compiler tests passed: {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
