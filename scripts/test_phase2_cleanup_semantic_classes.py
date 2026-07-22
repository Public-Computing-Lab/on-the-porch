#!/usr/bin/env python3
"""Focused semantic-class tests for Phase 2 cleanup compiler/planner rules.

These cases exercise general trusted-reference and clarity behavior. They do
not encode production phrase handlers.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from semantic_plan_compiler import (  # noqa: E402
    GuardedDisposition,
    guard_semantic_plan,
)
from semantic_planner import (  # noqa: E402
    GeographyMode,
    PlannerAction,
    SemanticDomain,
    SemanticGeography,
    SemanticGoalClarity,
    SemanticOperation,
    SemanticPlan,
    SemanticTask,
    SemanticTime,
    SemanticTimeKind,
    SourceScope,
)


def _task(
    domain: SemanticDomain,
    operation: SemanticOperation,
    *,
    subject: str | None = None,
    geography: SemanticGeography | None = None,
    time: SemanticTime | None = None,
    clarity: SemanticGoalClarity = SemanticGoalClarity.CLEAR,
) -> SemanticTask:
    return SemanticTask(
        domain,
        operation,
        subject,
        geography or SemanticGeography(GeographyMode.DEFAULT, None),
        time or SemanticTime(SemanticTimeKind.NONE, None),
        SourceScope.ANY,
        clarity,
    )


def _gather(*tasks: SemanticTask) -> SemanticPlan:
    return SemanticPlan(PlannerAction.GATHER_EVIDENCE, tasks=tasks)


def test_selected_event_location_domain_rewrites_to_events() -> None:
    token = "evt_selected_1"
    plan = _gather(
        _task(
            SemanticDomain.LOCATION,
            SemanticOperation.FIND,
            geography=SemanticGeography(GeographyMode.NONE, None),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "active_entity": {"token": token, "kind": "event", "id": "42"},
            "selected_entity_for_turn": token,
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(token,),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    assert guarded.tool_requests
    request = guarded.tool_requests[0]
    assert request.tool_id.value == "query_events"
    assert request.arguments.get("operation") == "DETAILS"
    assert request.arguments.get("detail_field") == "LOCATION"


def test_selected_event_datetime_aspect_stays_events() -> None:
    token = "evt_selected_2"
    plan = _gather(
        _task(
            SemanticDomain.EVENTS,
            SemanticOperation.FIND,
            subject="DATE_TIME",
            geography=SemanticGeography(GeographyMode.NONE, None),
            time=SemanticTime(SemanticTimeKind.NONE, None),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "active_entity": {"token": token, "kind": "event", "id": "43"},
            "selected_entity_for_turn": token,
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(token,),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    request = guarded.tool_requests[0]
    assert request.arguments.get("detail_field") == "DATE_TIME"


def test_location_domain_without_selected_event_is_not_event_detail() -> None:
    plan = _gather(
        _task(
            SemanticDomain.LOCATION,
            SemanticOperation.FIND,
            geography=SemanticGeography(GeographyMode.EXPLICIT, "Uphams Corner"),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    request = guarded.tool_requests[0]
    assert request.tool_id.value == "resolve_location"


def test_location_domain_ignored_when_entity_not_selected_for_turn() -> None:
    token = "evt_old"
    plan = _gather(
        _task(
            SemanticDomain.LOCATION,
            SemanticOperation.FIND,
            geography=SemanticGeography(GeographyMode.EXPLICIT, "Uphams Corner"),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "active_entity": {"token": token, "kind": "event", "id": "99"},
            # No selected_entity_for_turn: must not invent selection.
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(token,),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    request = guarded.tool_requests[0]
    assert request.tool_id.value == "resolve_location"


def test_domain_only_without_operation_clarifies() -> None:
    plan = _gather(
        _task(
            SemanticDomain.EVENTS,
            SemanticOperation.FIND,
            clarity=SemanticGoalClarity.DOMAIN_ONLY,
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(),
    )
    assert guarded.disposition is GuardedDisposition.NEEDS_CLARIFICATION
    assert guarded.reason_code == "MISSING_USER_MEANING"
    assert not guarded.tool_requests


def test_clear_event_listing_compiles_without_event_type() -> None:
    plan = _gather(
        _task(
            SemanticDomain.EVENTS,
            SemanticOperation.FIND,
            subject=None,
            time=SemanticTime(SemanticTimeKind.NONE, None),
            clarity=SemanticGoalClarity.CLEAR,
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    request = guarded.tool_requests[0]
    assert request.tool_id.value == "query_events"
    assert request.arguments.get("operation") == "LIST"


def test_service_request_none_time_defaults_previous_month() -> None:
    plan = _gather(
        _task(
            SemanticDomain.SERVICE_REQUESTS,
            SemanticOperation.COUNT,
            subject="potholes",
            time=SemanticTime(SemanticTimeKind.NONE, None),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    request = guarded.tool_requests[0]
    assert request.tool_id.value == "aggregate_311"
    assert request.arguments["time_scope"]["period"] == "PREVIOUS_CALENDAR_MONTH"


def test_null_subject_status_missing_time_is_source_freshness() -> None:
    """Source freshness/coverage must not demand a civic time period."""
    plan = _gather(
        _task(
            SemanticDomain.SERVICE_REQUESTS,
            SemanticOperation.STATUS,
            subject=None,
            geography=SemanticGeography(GeographyMode.DEFAULT, None),
            time=SemanticTime(SemanticTimeKind.MISSING, None),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "active_topic": "EVENTS",
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    assert any(req.tool_id.value == "get_source_status" for req in guarded.tool_requests)
    assert not any(req.tool_id.value == "aggregate_311" for req in guarded.tool_requests)


def test_null_subject_status_missing_geography_is_source_freshness() -> None:
    plan = _gather(
        _task(
            SemanticDomain.SERVICE_REQUESTS,
            SemanticOperation.STATUS,
            subject=None,
            geography=SemanticGeography(GeographyMode.MISSING, None),
            time=SemanticTime(SemanticTimeKind.NONE, None),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "active_topic": "EVENTS",
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    assert any(req.tool_id.value == "get_source_status" for req in guarded.tool_requests)


def test_reviewed_pavement_surface_aliases_compile() -> None:
    from civic_311 import canonical_issue_family_id

    assert canonical_issue_family_id("rough pavement") == "STREET_AND_PAVEMENT_CONDITION"
    assert canonical_issue_family_id("poor roadway surface") == "STREET_AND_PAVEMENT_CONDITION"
    plan = _gather(
        _task(
            SemanticDomain.SERVICE_REQUESTS,
            SemanticOperation.COUNT,
            subject="rough pavement",
            time=SemanticTime(SemanticTimeKind.PREVIOUS_CALENDAR_MONTH, None),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    assert guarded.tool_requests[0].tool_id.value == "aggregate_311"
    assert guarded.tool_requests[0].arguments["issue_family_id"] == "street_and_pavement_condition"


def test_event_list_followup_missing_geography_retains_default() -> None:
    """Prior EVENT_LIST follow-ups may omit geography when the listing is trusted."""
    plan = _gather(
        _task(
            SemanticDomain.EVENTS,
            SemanticOperation.CHECK_EXISTENCE,
            geography=SemanticGeography(GeographyMode.MISSING, None),
            time=SemanticTime(SemanticTimeKind.TRUSTED_REFERENCE, "time_tok"),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
            "previous_result_summary": {
                "kind": "EVENT_LIST",
                "status": "EVENTS_FOUND",
                "returned_count": 2,
            },
            "time_scope": {"token": "time_tok"},
        },
        trusted_reference_tokens=("time_tok",),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    assert guarded.tool_requests
    assert guarded.tool_requests[0].tool_id.value == "query_events"


def test_service_request_issue_find_compiles_to_count() -> None:
    """Reviewed issue-family FIND asks compile to aggregate COUNT, not record search."""
    plan = _gather(
        _task(
            SemanticDomain.SERVICE_REQUESTS,
            SemanticOperation.FIND,
            subject="missed trash pickup",
            time=SemanticTime(SemanticTimeKind.PREVIOUS_CALENDAR_MONTH, None),
        )
    )
    guarded = guard_semantic_plan(
        plan,
        trusted_context={
            "default_geography": {"name": "Dorchester", "location_id": "dorchester"},
        },
        trusted_reference_tokens=(),
    )
    assert guarded.disposition is GuardedDisposition.EXECUTABLE
    request = guarded.tool_requests[0]
    assert request.tool_id.value == "aggregate_311"
    assert request.arguments["operation"] == "COUNT"
    assert request.arguments["issue_family_id"] == "trash_and_illegal_dumping"


if __name__ == "__main__":
    tests = [
        test_selected_event_location_domain_rewrites_to_events,
        test_selected_event_datetime_aspect_stays_events,
        test_location_domain_without_selected_event_is_not_event_detail,
        test_location_domain_ignored_when_entity_not_selected_for_turn,
        test_domain_only_without_operation_clarifies,
        test_clear_event_listing_compiles_without_event_type,
        test_service_request_none_time_defaults_previous_month,
        test_null_subject_status_missing_time_is_source_freshness,
        test_null_subject_status_missing_geography_is_source_freshness,
        test_reviewed_pavement_surface_aliases_compile,
        test_event_list_followup_missing_geography_retains_default,
        test_service_request_issue_find_compiles_to_count,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(f"focused semantic tests passed: {len(tests)}")
