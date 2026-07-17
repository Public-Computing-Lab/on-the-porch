#!/usr/bin/env python3
"""Focused regression tests for trusted state-aware event-list retrieval."""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OTP = ROOT / "on_the_porch"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OTP))

from conversation_dimensions import (  # noqa: E402
    CanonicalLocation,
    CanonicalTopic,
    DimensionContext,
    GeographyKind,
    extract_conversation_dimensions,
)
from conversation_relationship import (  # noqa: E402
    ConversationRelationship,
    DecisionSource,
    ReasonCode,
    RelationshipDecision,
)
from conversation_state import ConversationState  # noqa: E402
from conversation_transitions import (  # noqa: E402
    CompletionStatus,
    plan_conversation_transition,
)
from event_query_context import (  # noqa: E402
    EventLocationStatus,
    EventPlanStatus,
    build_event_cache,
    classify_event_location,
    compose_event_answer,
    event_cache_matches,
    is_direct_event_list_request,
    plan_event_query,
    retrieve_events,
)


NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=NY)


def _relationship(kind: ConversationRelationship) -> RelationshipDecision:
    reason = {
        ConversationRelationship.STANDALONE: ReasonCode.SELF_CONTAINED_CIVIC_QUESTION,
        ConversationRelationship.FOLLOW_UP: ReasonCode.REFERENTIAL_FOLLOW_UP,
        ConversationRelationship.REFINEMENT: ReasonCode.TIME_REFINEMENT,
        ConversationRelationship.NEW_TOPIC: ReasonCode.CIVIC_TOPIC_SWITCH,
    }.get(kind, ReasonCode.AMBIGUOUS_CONTEXT)
    return RelationshipDecision(kind, 0.99, reason, (), (), False, DecisionSource.RULE)


def _dimensions(message: str, state: ConversationState):
    return extract_conversation_dimensions(
        message,
        context=DimensionContext(
            active_topic=state.active_topic,
            location=state.location,
            time_range=state.time_range,
            authoritative_active_event=state.active_entity is not None,
        ),
        now=NOW,
        resolve_yearless_named_dates=True,
    )


def _plan(message: str, state: ConversationState, relation: ConversationRelationship):
    return plan_event_query(
        relationship=_relationship(relation),
        dimensions=_dimensions(message, state),
        state=state,
        event_list_requested=True,
        now=NOW,
    )


def _events_state(*, location: str | None = None) -> ConversationState:
    canonical = None
    if location:
        canonical = CanonicalLocation(
            location,
            GeographyKind.NEIGHBORHOOD
            if location == "dorchester"
            else GeographyKind.SUB_NEIGHBORHOOD,
        )
    return ConversationState(active_topic=CanonicalTopic.EVENTS, location=canonical)


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        self.sql = ""
        self.params = ()

    def execute(self, sql: str, params: tuple) -> None:
        self.sql = sql
        self.params = params

    def fetchall(self) -> List[Dict[str, Any]]:
        return self.rows

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.cursor_value = _Cursor(rows)

    def cursor(self, *, dictionary: bool = False) -> _Cursor:
        assert dictionary
        return self.cursor_value


def _row(
    event_id: int,
    name: str,
    location: str,
    *,
    source: str = "codman",
) -> Dict[str, Any]:
    return {
        "id": event_id,
        "event_name": name,
        "event_date": "Saturday, July 18",
        "start_date": "2026-07-18",
        "end_date": "2026-07-18",
        "start_time": "10:00:00",
        "end_time": "11:00:00",
        "raw_text": f"{name}\nLocation: {location}\nURL: https://example.org/{event_id}",
        "source_pdf": source,
        "category": "Other",
    }


def test_time_and_precedence() -> None:
    assert is_direct_event_list_request("Events in Fields Corner")
    assert not is_direct_event_list_request("Tell me about the July 13 event")
    assert not is_direct_event_list_request('Tell me about "Crew & Cadet Street Parade"')
    old = _events_state(location="fields_corner")
    standalone = _plan(
        "What events are happening this weekend?",
        old,
        ConversationRelationship.STANDALONE,
    )
    assert standalone.status is EventPlanStatus.READY
    assert standalone.start_date == "2026-07-18"
    assert standalone.end_date_exclusive == "2026-07-20"
    assert standalone.location.id == "dorchester", "standalone must not inherit Fields Corner"

    weekend_state = ConversationState(
        active_topic=CanonicalTopic.EVENTS,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
        time_range=_dimensions(
            "What events are happening this weekend?", ConversationState()
        ).time_range,
    )
    refined = _plan(
        "What about next weekend?",
        weekend_state,
        ConversationRelationship.REFINEMENT,
    )
    assert refined.start_date == "2026-07-25"
    assert refined.end_date_exclusive == "2026-07-27"
    assert refined.location.id == "codman_square"

    tomorrow = _plan(
        "What events are happening tomorrow?",
        ConversationState(),
        ConversationRelationship.STANDALONE,
    )
    assert (tomorrow.start_date, tomorrow.end_date_exclusive) == (
        "2026-07-16", "2026-07-17",
    )

    explicit = _plan(
        "What events are happening July 20?",
        ConversationState(),
        ConversationRelationship.STANDALONE,
    )
    assert (explicit.start_date, explicit.end_date_exclusive) == (
        "2026-07-20", "2026-07-21",
    )


def test_references_and_unsupported_filters() -> None:
    missing = _plan(
        "Are there events there this weekend?",
        ConversationState(),
        ConversationRelationship.NEW_TOPIC,
    )
    assert missing.status is EventPlanStatus.CLARIFICATION
    assert "Which area" in compose_event_answer(missing, [])

    prior = ConversationState(
        active_topic=CanonicalTopic.PUBLIC_SAFETY,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
    )
    resolved = _plan(
        "Are there events there this weekend?",
        prior,
        ConversationRelationship.NEW_TOPIC,
    )
    assert resolved.status is EventPlanStatus.READY
    assert resolved.location.id == "codman_square"

    ambiguous_location = _plan(
        "What events are between Codman Square and Fields Corner this weekend?",
        ConversationState(),
        ConversationRelationship.STANDALONE,
    )
    assert ambiguous_location.status is EventPlanStatus.CLARIFICATION
    assert ambiguous_location.location is None

    ambiguous_date_dimensions = extract_conversation_dimensions(
        "What events are happening 7/20?",
        context=DimensionContext(),
        now=NOW,
    )
    ambiguous_date = plan_event_query(
        relationship=_relationship(ConversationRelationship.STANDALONE),
        dimensions=ambiguous_date_dimensions,
        state=ConversationState(),
        event_list_requested=True,
        now=NOW,
    )
    assert ambiguous_date.status is EventPlanStatus.CLARIFICATION
    assert "Which date" in compose_event_answer(ambiguous_date, [])

    event_state = _events_state(location="codman_square")
    for message in (
        "Only show free ones.",
        "Only show morning events.",
        "Only show events where registration is required.",
    ):
        unsupported = _plan(message, event_state, ConversationRelationship.REFINEMENT)
        assert unsupported.status is EventPlanStatus.UNSUPPORTED, message
        assert unsupported.cache_signature() is None
        assert "can't reliably filter" in compose_event_answer(unsupported, [])

    virtual = _plan(
        "Only show virtual events.",
        event_state,
        ConversationRelationship.REFINEMENT,
    )
    assert virtual.status is EventPlanStatus.READY
    assert virtual.modality == "virtual"
    assert virtual.location.id == "codman_square"


def test_authoritative_location_filtering() -> None:
    codman = _row(
        436,
        "Codman Event",
        "The Great Hall at Codman Square Health Center, 6 Norfolk St., Dorchester, MA",
    )
    dorchester = _row(
        430,
        "Dorchester Event",
        "Second Church of Dorchester, 44 Moultrie St., Dorchester",
        source="REP-25_26web.pdf",
    )
    unknown = _row(900, "Codman Organizer Event", "Florian Hall, 55 Hallet St, Boston")
    virtual = _row(901, "Codman Online Event", "Virtual")
    baby = _row(902, "Virtual Baby Cafe", "Virtual")
    assert classify_event_location(codman) is EventLocationStatus.CODMAN_SQUARE
    assert classify_event_location(dorchester) is EventLocationStatus.DORCHESTER
    assert classify_event_location(unknown) is EventLocationStatus.UNKNOWN
    assert classify_event_location(virtual) is EventLocationStatus.VIRTUAL
    duplicate = dict(codman)
    duplicate["raw_text"] = (
        "Location: Unvalidated venue in Randolph\n"
        "Location: The Great Hall at Codman Square Health Center, "
        "6 Norfolk St., Dorchester, MA"
    )
    assert classify_event_location(duplicate) is EventLocationStatus.CODMAN_SQUARE

    plan = _plan(
        "What events are happening in Codman Square this weekend?",
        ConversationState(),
        ConversationRelationship.STANDALONE,
    )
    conn = _Connection([codman, dorchester, unknown, virtual, baby])
    rows, candidates = retrieve_events(conn, plan)
    assert [row["id"] for row in rows] == [436, 902]
    assert candidates == 5
    assert conn.cursor_value.params == ("2026-07-20", "2026-07-18", 100)
    assert "%s" in conn.cursor_value.sql and "CURDATE" not in conn.cursor_value.sql

    answer = compose_event_answer(plan, rows, candidate_count=candidates)
    assert "Codman Square" in answer
    assert "Virtual Baby Cafe" in answer
    assert "Codman Organizer Event" not in answer
    assert "Codman Online Event" not in answer

    empty_answer = compose_event_answer(plan, [], candidate_count=2)
    assert "Codman Square" in empty_answer
    assert "no events exist" not in empty_answer.lower()
    assert "nothing is happening" not in empty_answer.lower()


def test_cache_signature_and_bounded_plan() -> None:
    plan = _plan(
        "What events are happening in Codman Square this weekend?",
        ConversationState(),
        ConversationRelationship.STANDALONE,
    )
    row = _row(
        436,
        "Codman Event",
        "The Great Hall at Codman Square Health Center, 6 Norfolk St., Dorchester, MA",
    )
    answer = compose_event_answer(plan, [row], candidate_count=1)
    cache = build_event_cache(
        base_cache={},
        plan=plan,
        rows=[row],
        answer=answer,
        sources=[],
        now=NOW,
    )
    assert cache["event_query_signature"] == {
        "topic": "EVENTS",
        "start_date": "2026-07-18",
        "end_date_exclusive": "2026-07-20",
        "location_id": "dorchester",
        "subarea_id": "codman_square",
        "modality": None,
        "filters": {},
    }
    assert "What events" not in json.dumps(cache["event_query_signature"])
    assert event_cache_matches(cache, plan, now=NOW + timedelta(seconds=299))
    assert not event_cache_matches(cache, plan, now=NOW + timedelta(seconds=301))

    changed = _plan(
        "What events are happening in Fields Corner this weekend?",
        ConversationState(),
        ConversationRelationship.STANDALONE,
    )
    assert not event_cache_matches(cache, changed, now=NOW)
    assert len(json.dumps(plan.to_dict()).encode("utf-8")) <= 2048


def test_transition_matches_executed_event_context() -> None:
    old = _events_state(location="fields_corner")
    dimensions = _dimensions("What events are happening this weekend?", old)
    transition = plan_conversation_transition(
        current=old,
        relationship=_relationship(ConversationRelationship.STANDALONE),
        dimensions=dimensions,
        authoritative_entity=None,
        detected_topic="events",
        completed_route="structured_events",
        completion_status=CompletionStatus.ACCEPTABLE,
    )
    assert transition.commit_allowed
    assert transition.next_state.active_topic is CanonicalTopic.EVENTS
    assert transition.next_state.time_range == dimensions.time_range
    assert transition.next_state.location is None
    assert transition.next_state.filters == old.filters

    degraded = plan_conversation_transition(
        current=old,
        relationship=_relationship(ConversationRelationship.REFINEMENT),
        dimensions=_dimensions("Only show free ones.", old),
        authoritative_entity=None,
        detected_topic="events",
        completed_route="structured_events",
        completion_status=CompletionStatus.DEGRADED,
    )
    assert not degraded.commit_allowed
    assert degraded.next_state == old


def test_plan_construction_latency() -> None:
    state = _events_state(location="codman_square")
    dimensions = _dimensions("What about next weekend?", state)
    relationship = _relationship(ConversationRelationship.REFINEMENT)
    samples = []
    for _ in range(1000):
        started = time.perf_counter()
        plan = plan_event_query(
            relationship=relationship,
            dimensions=dimensions,
            state=state,
            event_list_requested=True,
            now=NOW,
        )
        samples.append((time.perf_counter() - started) * 1000)
        assert plan.status is EventPlanStatus.READY
    ordered = sorted(samples)
    p99 = ordered[int(len(ordered) * 0.99) - 1]
    print(f"INFO event plan latency p50={statistics.median(samples):.4f}ms p99={p99:.4f}ms")
    assert p99 < 1.0


def main() -> None:
    tests = (
        test_time_and_precedence,
        test_references_and_unsupported_filters,
        test_authoritative_location_filtering,
        test_cache_signature_and_bounded_plan,
        test_transition_matches_executed_event_context,
        test_plan_construction_latency,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\nState-aware event retrieval tests: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
