#!/usr/bin/env python3
"""Representative Phase 3 module and turn-service regression checks."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "on_the_porch"), str(ROOT / "api"), str(ROOT)]

from answering_kernel import GlobalDeadline  # noqa: E402
from civic_crime import CrimePlan, execute_crime_plan  # noqa: E402
from civic_documents import DocumentPlan, execute_document_plan  # noqa: E402
from civic_entities import resolve_entity  # noqa: E402
from civic_evidence import (  # noqa: E402
    CivicEvidenceAtom,
    CivicSourceRecord,
    CivicSourceType,
    CivicToolResult,
    ToolResultStatus,
)
from grounded_synthesis import validate_synthesis_against_evidence  # noqa: E402
from model_led_turn_service import (  # noqa: E402
    ModelLedTurnService,
    TurnRequest,
    TurnServiceStatus,
)
from conversation_state import ConversationState, ThreadStateEnvelope  # noqa: E402
from orchestrator_evaluation import ProviderResponse  # noqa: E402


class FakeProvider:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    def generate(self, prompt, *, timeout_seconds):
        self.calls += 1
        return ProviderResponse(self.payload, 10, 10, "fake", {"finish_reason": "STOP"})


def _envelope() -> ThreadStateEnvelope:
    return ThreadStateEnvelope({}, ConversationState(revision=1))


def test_entity_resolve_and_ambiguity() -> None:
    resolved = resolve_entity("What is Eversource?")
    assert resolved.status == "RESOLVED"
    assert resolved.profile is not None
    assert "utility" in resolved.answer.casefold()
    assert resolved.sources
    ambiguous = resolve_entity("boston")
    assert ambiguous.status in {"AMBIGUOUS", "NOT_FOUND", "RESOLVED"}


def test_documents_empty_limitation() -> None:
    plan = DocumentPlan(
        query="zzzz-nonexistent-civic-topic",
        corpora=("POLICY",),
        location_id="dorchester",
        time_scope=None,
        source_types=(),
        limit=4,
    )
    deadline = GlobalDeadline.from_started_at(time.perf_counter())
    result = execute_document_plan(
        "zzzz",
        plan=plan,
        deadline=deadline,
        retrieve=lambda *args, **kwargs: {"chunks": [], "metadata": []},
    )
    assert "didn't find" in result.answer.casefold() or "no matching" in result.answer.casefold()
    assert result.evidence.facts.get("passage_count") == 0


def test_crime_dependency_path() -> None:
    plan = CrimePlan(
        operation="COUNT",
        location_id="dorchester",
        period={
            "start_date": "2026-06-01",
            "end_date_exclusive": "2026-07-01",
            "label": "June 2026",
        },
    )
    deadline = GlobalDeadline.from_started_at(time.perf_counter())

    def boom():
        raise RuntimeError("db down")

    result = execute_crime_plan(
        "safety overview",
        plan=plan,
        connection_factory=boom,
        deadline=deadline,
    )
    assert "couldn't load" in result.answer.casefold()
    assert result.synthesis_call_count == 0


def test_synthesis_validator() -> None:
    source = CivicSourceRecord(
        internal_source_id="bpd_crime",
        resident_source_name="Boston Police Department incident reports",
        source_type=CivicSourceType.OFFICIAL_OPERATIONAL_DATA,
        authority="City of Boston",
        publication_or_data_date="2026-06-30",
        geography={"location_id": "dorchester"},
        approved_citation_reference="https://data.boston.gov/crime",
    )
    tool = CivicToolResult(
        request_id="crime_1",
        tool_id="aggregate_crime",
        status=ToolResultStatus.COMPLETE,
        requested_scope={"operation": "COUNT"},
        executed_scope={"operation": "COUNT"},
        typed_facts={"incident_report_count": 12},
        evidence_atoms=(
            CivicEvidenceAtom(
                evidence_id="crime.count",
                claim_type="incident_report_count",
                value=12,
                source_id="bpd_crime",
                citation_reference="https://data.boston.gov/crime",
            ),
        ),
        source_records=(source,),
    )
    ok, issues = validate_synthesis_against_evidence(
        "Dorchester had 12 BPD incident reports according to Boston Police Department incident reports.",
        (tool,),
    )
    assert ok, issues
    bad_ok, bad_issues = validate_synthesis_against_evidence(
        "Dorchester had 999999 invented crimes according to the Secret Bureau.",
        (tool,),
    )
    assert not bad_ok
    assert bad_issues
    envelope_ok, envelope_issues = validate_synthesis_against_evidence(
        '{"action":"respond_directly","arguments":{"response_kind":"SOCIAL","response_text":"Dorchester had 12 reports."}}',
        (tool,),
    )
    assert not envelope_ok
    assert "protocol_envelope" in envelope_issues


def test_entity_turn_call_counts() -> None:
    payload = json.dumps({
        "action": "gather_evidence",
        "arguments": {
            "tasks": [{
                "domain": "DOCUMENTS",
                "operation": "EXPLAIN",
                "subject": "Eversource",
                "geography": {"mode": "NONE", "value": None},
                "time": {"kind": "NONE", "value": None},
                "source_scope": "ANY",
                "goal_clarity": "CLEAR",
            }]
        },
    })
    provider = FakeProvider(payload)
    service = ModelLedTurnService(provider=provider, connection_factory=lambda: None)
    result = service.run(TurnRequest(message="What is Eversource?", state_envelope=_envelope()))
    assert result.status is TurnServiceStatus.COMPLETED
    assert result.planner_call_count == 1
    assert result.synthesis_call_count == 0
    assert result.repair_call_count == 0
    assert result.embedding_call_count == 0
    assert result.secondary_planner_call_count == 0
    assert "Eversource" in (result.answer or "")
    diagnostics = result.to_safe_dict()["diagnostics"]
    assert diagnostics["planner_call_count"] == 1
    assert diagnostics["secondary_planner_call_count"] == 0


if __name__ == "__main__":
    test_entity_resolve_and_ambiguity()
    test_documents_empty_limitation()
    test_crime_dependency_path()
    test_synthesis_validator()
    test_entity_turn_call_counts()
    print("phase3 civic module tests passed")
