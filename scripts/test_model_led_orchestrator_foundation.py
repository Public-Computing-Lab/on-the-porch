#!/usr/bin/env python3
"""Focused contract tests for the Phase 1 model-led foundation."""

from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
sys.path.insert(0, str(PORCH))

from answering_kernel import EvidenceBundle, EvidenceStatus, GlobalDeadline  # noqa: E402
from civic_evidence import (  # noqa: E402
    CivicEvidenceBundle,
    CivicEvidenceAtom,
    CivicEvidenceError,
    CivicSourceRecord,
    CivicSourceType,
    CivicToolResult,
    ToolResultStatus,
    from_legacy_311_bundle,
)
from civic_tool_catalog import (  # noqa: E402
    CivicDomain,
    RegisteredToolRequest,
    SemanticTimeScope,
    ToolContractError,
)
from model_call_ledger import (  # noqa: E402
    ModelCallLedger,
    ModelCallLedgerError,
    ModelCallRole,
    completed_replay_ledger,
)
from model_led_orchestrator import (  # noqa: E402
    OrchestratorContractError,
    OrchestratorDecision,
    build_orchestrator_prompt,
    normalize_resident_input,
)


def base_decision() -> dict:
    return {
        "schema_version": 1,
        "strategy": "DIRECT_RESPONSE",
        "relationship": "SMALL_TALK",
        "user_goal": "Respond politely to a greeting.",
        "domains": [],
        "trusted_reference_tokens": [],
        "geography_reference": None,
        "semantic_time_scope": None,
        "tool_requests": [],
        "answer_requirements": {
            "format": "DIRECT", "detail": "BRIEF", "source_types": [],
            "include_freshness": False, "clarification_fields": [],
        },
        "state_proposal": {
            "active_topic": None, "active_entity_token": None, "location_id": None,
            "time_scope": None, "filters": {}, "clear": [],
        },
        "direct_response_basis": "CONVERSATION_ONLY",
        "direct_response": "Hello! What would you like to know about Dorchester?",
        "clarification_question": None,
        "confidence": 0.98,
    }


def tool_decision() -> dict:
    value = base_decision()
    value.update({
        "strategy": "EXECUTE_TOOLS",
        "relationship": "STANDALONE",
        "user_goal": "Count all Dorchester 311 requests in the previous month.",
        "domains": ["BOSTON_311"],
        "geography_reference": {
            "scope": "NEIGHBORHOOD", "location_id": "dorchester",
            "location_text": None, "trusted_reference_token": None,
        },
        "semantic_time_scope": {"period": "PREVIOUS_CALENDAR_MONTH"},
        "tool_requests": [{
            "request_id": "request_1",
            "tool_id": "aggregate_311",
            "arguments": {
                "operation": "COUNT", "issue_scope": "ALL",
                "location_id": "dorchester",
                "time_scope": {"period": "PREVIOUS_CALENDAR_MONTH"},
            },
        }],
        "answer_requirements": {
            "format": "DIRECT", "detail": "STANDARD",
            "source_types": ["OFFICIAL_OPERATIONAL_DATA"],
            "include_freshness": True, "clarification_fields": [],
        },
        "state_proposal": {
            "active_topic": "BOSTON_311", "active_entity_token": None,
            "location_id": "dorchester",
            "time_scope": {"period": "PREVIOUS_CALENDAR_MONTH"},
            "filters": {}, "clear": [],
        },
        "direct_response_basis": "NONE",
        "direct_response": None,
        "confidence": 0.96,
    })
    return value


def assert_raises(exc_type, function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}")


def test_schema_and_cross_fields() -> None:
    direct = OrchestratorDecision.from_mapping(base_decision())
    assert direct.strategy.value == "DIRECT_RESPONSE"
    execute = OrchestratorDecision.from_mapping(tool_decision())
    assert execute.tool_requests[0].tool_id.value == "aggregate_311"

    bad = base_decision()
    bad["tool_requests"] = tool_decision()["tool_requests"]
    assert_raises(OrchestratorContractError, OrchestratorDecision.from_mapping, bad)

    clarify = base_decision()
    clarify.update({
        "strategy": "ASK_CLARIFICATION",
        "relationship": "STANDALONE",
        "domains": ["LOCATION"],
        "answer_requirements": {
            "format": "DIRECT", "detail": "BRIEF", "source_types": [],
            "include_freshness": False, "clarification_fields": ["location"],
        },
        "direct_response_basis": "NONE",
        "direct_response": None,
        "clarification_question": "Which Dorchester location do you mean?",
    })
    parsed = OrchestratorDecision.from_mapping(clarify)
    assert parsed.clarification_question

    bad = tool_decision()
    bad["direct_response"] = "There were 42 requests."
    assert_raises(OrchestratorContractError, OrchestratorDecision.from_mapping, bad)


def test_tool_security_and_compatibility() -> None:
    request = tool_decision()["tool_requests"][0]
    parsed = RegisteredToolRequest.from_mapping(
        request, selected_domains=[CivicDomain.BOSTON_311],
    )
    assert parsed.tool_id.value == "aggregate_311"
    assert_raises(
        ToolContractError,
        RegisteredToolRequest.from_mapping,
        request,
        selected_domains=[CivicDomain.EVENTS],
    )
    unknown = copy.deepcopy(request)
    unknown["tool_id"] = "answer_recent_potholes"
    assert_raises(
        ToolContractError,
        RegisteredToolRequest.from_mapping,
        unknown,
        selected_domains=[CivicDomain.BOSTON_311],
    )
    bad_event_detail = {
        "request_id": "event_1", "tool_id": "query_events",
        "arguments": {
            "operation": "DETAILS", "entity_token": "event:435", "limit": 2,
        },
    }
    assert_raises(
        ToolContractError,
        RegisteredToolRequest.from_mapping,
        bad_event_detail,
        selected_domains=[CivicDomain.EVENTS],
        trusted_reference_tokens=["event:435"],
    )
    sql = {
        "request_id": "docs_1", "tool_id": "search_documents",
        "arguments": {
            "operation": "SEARCH", "query": "SELECT secret FROM users",
            "corpora": ["NEWS"],
        },
    }
    assert_raises(
        ToolContractError,
        RegisteredToolRequest.from_mapping,
        sql,
        selected_domains=[CivicDomain.NEWS],
    )
    mismatched_corpus = copy.deepcopy(sql)
    mismatched_corpus["arguments"]["query"] = "Recent planning changes"
    assert_raises(
        ToolContractError,
        RegisteredToolRequest.from_mapping,
        mismatched_corpus,
        selected_domains=[CivicDomain.POLICY],
    )
    source_status = {
        "request_id": "status_1", "tool_id": "get_source_status",
        "arguments": {"operation": "STATUS", "source_ids": ["boston_311", "events"]},
    }
    assert_raises(
        ToolContractError,
        RegisteredToolRequest.from_mapping,
        source_status,
        selected_domains=[CivicDomain.BOSTON_311],
    )
    missing_scope = copy.deepcopy(request)
    missing_scope["arguments"]["issue_scope"] = None
    assert_raises(
        ToolContractError,
        RegisteredToolRequest.from_mapping,
        missing_scope,
        selected_domains=[CivicDomain.BOSTON_311],
    )
    url = copy.deepcopy(sql)
    url["arguments"]["query"] = "Read https://example.test/private"
    assert_raises(
        ToolContractError,
        RegisteredToolRequest.from_mapping,
        url,
        selected_domains=[CivicDomain.NEWS],
    )


def test_bounds_references_and_direct_facts() -> None:
    too_many = tool_decision()
    too_many["tool_requests"] = [
        {**copy.deepcopy(too_many["tool_requests"][0]), "request_id": f"req_{index}"}
        for index in range(5)
    ]
    assert_raises(OrchestratorContractError, OrchestratorDecision.from_mapping, too_many)

    untrusted = tool_decision()
    untrusted["trusted_reference_tokens"] = ["event:secret"]
    assert_raises(OrchestratorContractError, OrchestratorDecision.from_mapping, untrusted)

    factual = base_decision()
    factual["direct_response"] = "There were 42 requests at 3:00 PM."
    assert_raises(OrchestratorContractError, OrchestratorDecision.from_mapping, factual)

    civic_domain = base_decision()
    civic_domain["domains"] = ["EVENTS"]
    civic_domain["direct_response"] = "The parade is today."
    assert_raises(OrchestratorContractError, OrchestratorDecision.from_mapping, civic_domain)


def test_semantic_time_scope() -> None:
    parsed = SemanticTimeScope.from_mapping({"period": "LAST_N_DAYS", "n_days": 30})
    assert parsed.n_days == 30
    assert_raises(
        ToolContractError,
        SemanticTimeScope.from_mapping,
        {"period": "LAST_N_DAYS", "n_days": 0},
    )
    assert_raises(
        ToolContractError,
        SemanticTimeScope.from_mapping,
        {"period": "CURRENT_WEEK", "start_date": "2026-07-01"},
    )
    retained = SemanticTimeScope.from_mapping(
        {"period": "RETAIN_TRUSTED_PERIOD", "trusted_reference_token": "period:1"},
        trusted_reference_tokens=["period:1"],
    )
    assert retained.trusted_reference_token == "period:1"


def test_two_call_ledger() -> None:
    ledger = ModelCallLedger(GlobalDeadline.now())
    first = ledger.reserve(ModelCallRole.ORCHESTRATOR)
    assert first.ordinal == 1
    assert_raises(ModelCallLedgerError, ledger.reserve, ModelCallRole.ORCHESTRATOR)
    second = ledger.reserve(ModelCallRole.SYNTHESIS)
    assert second.ordinal == 2 and ledger.total_calls == 2
    assert_raises(ModelCallLedgerError, ledger.reserve, ModelCallRole.SYNTHESIS)

    failed = ModelCallLedger(GlobalDeadline.now())
    failed.reserve(ModelCallRole.ORCHESTRATOR)
    # A provider exception does not release the consumed reservation.
    assert_raises(ModelCallLedgerError, failed.reserve, ModelCallRole.ORCHESTRATOR)

    replay = completed_replay_ledger()
    assert replay.total_calls == 0
    assert_raises(ModelCallLedgerError, replay.reserve, ModelCallRole.ORCHESTRATOR)

    expired = ModelCallLedger(GlobalDeadline.from_started_at(time.perf_counter() - 20.0))
    assert_raises(ModelCallLedgerError, expired.reserve, ModelCallRole.ORCHESTRATOR)


def test_normalization_equivalence() -> None:
    variants = [
        "What's happening - next week?",
        "What\u2019s happening \u2014 next week?",
        "What\u2018s\u00a0 happening\u00a0\u00a0- next week?",
        "  What's   happening - next week?  ",
    ]
    normalized = [normalize_resident_input(item) for item in variants]
    assert len({item.comparison_text for item in normalized}) == 1
    assert normalized[1].original_text == variants[1]

    quote_variants = ['Tell me about "the parade"', "Tell me about \u201cthe parade\u201d"]
    assert len({normalize_resident_input(item).comparison_text for item in quote_variants}) == 1


def test_legacy_evidence_adapter() -> None:
    legacy = EvidenceBundle(
        question_scope={"intent": "count"}, issue_family="potholes",
        geography={"location_id": "dorchester"},
        requested_period={"kind": "LAST_N_DAYS"},
        executed_period={"kind": "LAST_N_DAYS"},
        freshness={"data_through_date": "2026-07-17"}, coverage={"status": "complete"},
        facts={"count": 7},
        source_records=[{
            "source_id": "boston_311", "source": "Boston 311 Service Requests",
            "authority": "City of Boston", "data_through_date": "2026-07-17",
            "citation_reference": "source:boston_311",
        }],
        evidence_status=EvidenceStatus.COMPLETE,
    )
    adapted = from_legacy_311_bundle(legacy)
    bundle = CivicEvidenceBundle((adapted,))
    payload = bundle.bounded_for_model()
    assert payload["tool_results"][0]["typed_facts"]["count"] == 7
    assert payload["tool_results"][0]["status"] == "COMPLETE"

    assert_raises(
        CivicEvidenceError,
        CivicToolResult,
        request_id="request_2",
        tool_id="query_events",
        status=ToolResultStatus.PARTIAL,
        requested_scope={},
        executed_scope={},
        partial_failures=("mysql_stack_trace",),
    )

    source = CivicSourceRecord(
        internal_source_id="events", resident_source_name="Boston events",
        source_type=CivicSourceType.OFFICIAL_OPERATIONAL_DATA,
        authority="City of Boston", publication_or_data_date="2026-07-18",
        geography=None, approved_citation_reference="source:events",
    )
    mismatched_atom = CivicEvidenceAtom(
        evidence_id="event.fact_1", claim_type="event_title", value="Community event",
        source_id="events", citation_reference="source:unapproved",
    )
    assert_raises(
        CivicEvidenceError,
        CivicToolResult,
        request_id="request_3",
        tool_id="query_events",
        status=ToolResultStatus.COMPLETE,
        requested_scope={},
        executed_scope={},
        evidence_atoms=(mismatched_atom,),
        source_records=(source,),
    )
    assert_raises(
        CivicEvidenceError,
        CivicToolResult,
        request_id="request_4",
        tool_id="unknown_tool",
        status=ToolResultStatus.ABSENT,
        requested_scope={},
        executed_scope={},
    )


def test_trusted_context_rejects_raw_history() -> None:
    normalized = normalize_resident_input("What is happening this week?")
    safe = {
        "default_geography": None, "active_topic": None, "active_entity": None,
        "time_scope": None, "previous_result_summary": None,
    }
    assert "Resident message" in build_orchestrator_prompt(normalized, trusted_context=safe)
    unsafe = {**safe, "history": [{"role": "assistant", "text": "unbounded"}]}
    assert_raises(
        OrchestratorContractError,
        build_orchestrator_prompt,
        normalized,
        trusted_context=unsafe,
    )


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"model-led orchestrator foundation tests passed: {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
