#!/usr/bin/env python3
"""Focused tests for natural-language Dorchester event discovery."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
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
    TimeLabelCode,
    extract_conversation_dimensions,
    resolve_canonical_time_expression,
)
from conversation_relationship import (  # noqa: E402
    ConversationRelationship,
    DecisionSource,
    ReasonCode,
    RelationshipDecision,
    classify_with_rules,
    RelationshipContext,
)
from conversation_state import ConversationState  # noqa: E402
from event_query_context import (  # noqa: E402
    EventPlanStatus,
    EventRelevanceKind,
    build_event_cache,
    classify_event_relevance,
    compose_event_answer,
    discover_events,
    event_cache_matches,
    is_direct_event_list_request,
    plan_event_query,
    retrieve_events,
    retrieve_sidebar_events,
)
from event_relevance import EventLocationStatus  # noqa: E402


NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=NY)


def _relationship(kind: ConversationRelationship) -> RelationshipDecision:
    reason = {
        ConversationRelationship.STANDALONE: ReasonCode.SELF_CONTAINED_CIVIC_QUESTION,
        ConversationRelationship.FOLLOW_UP: ReasonCode.REFERENTIAL_FOLLOW_UP,
        ConversationRelationship.REFINEMENT: ReasonCode.TIME_REFINEMENT,
        ConversationRelationship.NEW_TOPIC: ReasonCode.CIVIC_TOPIC_SWITCH,
    }.get(kind, ReasonCode.AMBIGUOUS_CONTEXT)
    return RelationshipDecision(kind, 0.99, reason, (), (), False, DecisionSource.RULE)


def _dimensions(message: str, state: Optional[ConversationState] = None):
    state = state or ConversationState()
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


def _plan(
    message: str,
    state: Optional[ConversationState] = None,
    relation: ConversationRelationship = ConversationRelationship.STANDALONE,
):
    state = state or ConversationState()
    return plan_event_query(
        relationship=_relationship(relation),
        dimensions=_dimensions(message, state),
        state=state,
        event_list_requested=True,
        now=NOW,
        message=message,
    )


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        self.params = ()
        self.filtered: List[Dict[str, Any]] = rows

    def execute(self, sql: str, params: tuple) -> None:
        self.params = params
        end_exclusive, start_date, _limit = params
        self.filtered = [
            row
            for row in self.rows
            if str(row.get("start_date")) < str(end_exclusive)
            and str(row.get("end_date") or row.get("start_date")) >= str(start_date)
        ]

    def fetchall(self) -> List[Dict[str, Any]]:
        return self.filtered

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.cursor_value = _Cursor(rows)

    def cursor(self, *, dictionary: bool = False) -> _Cursor:
        assert dictionary
        return self.cursor_value


class _SidebarCursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        self.params = ()

    def execute(self, sql: str, params: tuple) -> None:
        self.params = params
        start_date, end_exclusive, _limit = params
        self.filtered = [
            row
            for row in self.rows
            if str(row.get("start_date")) >= str(start_date)
            and str(row.get("start_date")) < str(end_exclusive)
        ]

    def fetchall(self) -> List[Dict[str, Any]]:
        return self.filtered

    def close(self) -> None:
        pass


class _SidebarConnection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.cursor_value = _SidebarCursor(rows)

    def cursor(self, *, dictionary: bool = False) -> _SidebarCursor:
        assert dictionary
        return self.cursor_value


def _row(
    event_id: int,
    name: str,
    location: str,
    *,
    start_date: str,
    end_date: Optional[str] = None,
    source: str = "codman",
    start_time: str = "10:00:00",
    end_time: str = "11:00:00",
) -> Dict[str, Any]:
    return {
        "id": event_id,
        "event_name": name,
        "event_date": start_date,
        "start_date": start_date,
        "end_date": end_date or start_date,
        "start_time": start_time,
        "end_time": end_time,
        "raw_text": f"{name}\nLocation: {location}\nURL: https://example.org/{event_id}",
        "source_pdf": source,
        "category": "Other",
    }


SCREENSHOT_ROWS = [
    _row(1, "Summer Wellness Virtual Therapy Group – Randolph", "", start_date="2026-07-20"),
    _row(2, "Virtual Baby Cafe", "Virtual", start_date="2026-07-22"),
    _row(
        3,
        "Recharge! Middle School Therapy Group – Randolph",
        "Codman Square Health Center at Randolph High School, 42 Highland Ave., Randolph",
        start_date="2026-07-23",
    ),
    _row(
        4,
        "Outside Range Dorchester Event",
        "Second Church of Dorchester, 44 Moultrie St., Dorchester",
        start_date="2026-07-28",
        source="REP-25_26web.pdf",
    ),
    _row(
        5,
        "Expired July 16 Meeting",
        "The Great Hall at Codman Square Health Center, 6 Norfolk St., Dorchester, MA",
        start_date="2026-07-16",
    ),
]


def test_natural_next_week_phrases() -> None:
    phrases = [
        "What events are coming up next week?",
        "What's going on next week?",
        "Anything happening next week?",
        "What is on next week?",
        "Events next wk?",
        "This coming week.",
    ]
    for message in phrases:
        dims = _dimensions(message)
        assert dims.time_range is not None, message
        assert dims.time_range.label_code is TimeLabelCode.NEXT_CALENDAR_WEEK, message
        assert (dims.time_range.start_date, dims.time_range.end_date_exclusive) == (
            "2026-07-20",
            "2026-07-27",
        ), message
        if "this coming week" in message.lower() and "event" not in message.lower():
            # Phrase-only refinement; plan requires event topic or prior state.
            continue
        if message.lower().startswith("this coming"):
            continue
        plan = _plan(message)
        assert plan is not None and plan.status is EventPlanStatus.READY, message
        assert (plan.start_date, plan.end_date_exclusive) == ("2026-07-20", "2026-07-27"), message
        assert plan.location.id == "dorchester", message


def test_rolling_and_calendar_distinctions() -> None:
    assert _dimensions("The week after next.").time_range.label_code is TimeLabelCode.WEEK_AFTER_NEXT
    assert (_dimensions("The week after next.").time_range.start_date,
            _dimensions("The week after next.").time_range.end_date_exclusive) == (
        "2026-07-27", "2026-08-03",
    )
    over = _dimensions("Over the next week.")
    assert over.time_range.label_code is TimeLabelCode.ROLLING_DAYS
    assert (over.time_range.start_date, over.time_range.end_date_exclusive) == (
        "2026-07-17", "2026-07-24",
    )
    seven = _dimensions("Next seven days.")
    assert (seven.time_range.start_date, seven.time_range.end_date_exclusive) == (
        "2026-07-17", "2026-07-24",
    )
    fourteen = _plan("What events are happening over the next 14 days?")
    assert (fourteen.start_date, fourteen.end_date_exclusive) == ("2026-07-17", "2026-07-31")
    later = _dimensions("Later this week.")
    assert later.time_range.label_code is TimeLabelCode.LATER_THIS_WEEK
    assert (later.time_range.start_date, later.time_range.end_date_exclusive) == (
        "2026-07-17", "2026-07-20",
    )
    earlier = _dimensions("Earlier this week.")
    assert earlier.time_range.label_code is TimeLabelCode.EARLIER_THIS_WEEK
    assert (earlier.time_range.start_date, earlier.time_range.end_date_exclusive) == (
        "2026-07-13", "2026-07-18",
    )


def test_upcoming_default_and_boundaries() -> None:
    upcoming = _plan("Upcoming events.")
    assert upcoming.start_date == "2026-07-17"
    assert upcoming.end_date_exclusive == "2026-07-31"
    tomorrow = _plan("What events are happening tomorrow?")
    assert (tomorrow.start_date, tomorrow.end_date_exclusive) == ("2026-07-18", "2026-07-19")
    explicit = _plan("What events are happening July 20?")
    assert (explicit.start_date, explicit.end_date_exclusive) == ("2026-07-20", "2026-07-21")
    explicit_range = _plan("What events are happening July 20 to July 22?")
    assert (explicit_range.start_date, explicit_range.end_date_exclusive) == (
        "2026-07-20", "2026-07-23",
    )
    # Month boundary from end of July
    end_july = datetime(2026, 7, 31, 12, 0, tzinfo=NY)
    dims = extract_conversation_dimensions(
        "What events next month?",
        context=DimensionContext(),
        now=end_july,
    )
    assert dims.time_range.label_code is TimeLabelCode.NEXT_MONTH
    assert dims.time_range.start_date == "2026-08-01"
    # Year boundary
    nye = datetime(2026, 12, 31, 12, 0, tzinfo=NY)
    dims = extract_conversation_dimensions(
        "What events next month?",
        context=DimensionContext(),
        now=nye,
    )
    assert dims.time_range.start_date == "2027-01-01"
    # DST spring-forward week in US (2026-03-08)
    dst = datetime(2026, 3, 6, 12, 0, tzinfo=NY)
    dims = extract_conversation_dimensions(
        "What events next week?",
        context=DimensionContext(),
        now=dst,
    )
    assert dims.time_range.start_date == "2026-03-09"
    assert dims.time_range.end_date_exclusive == "2026-03-16"


def test_geography_defaults_and_narrowing() -> None:
    generic = _plan("What events are coming up next week?")
    assert generic.location.id == "dorchester"
    assert generic.cache_signature()["subarea_id"] is None
    assert generic.cache_signature()["location_id"] == "dorchester"

    codman = _plan("What events are happening in Codman Square next week?")
    assert codman.location.id == "codman_square"
    assert codman.cache_signature()["subarea_id"] == "codman_square"

    fields = _plan("What events are happening in Fields Corner next week?")
    assert fields.location.id == "fields_corner"

    state = ConversationState(
        active_topic=CanonicalTopic.EVENTS,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
        time_range=_dimensions("What events next week?").time_range,
    )
    cleared = _plan(
        "Actually, all of Dorchester.",
        state,
        ConversationRelationship.REFINEMENT,
    )
    assert cleared.location.id == "dorchester"
    assert (cleared.start_date, cleared.end_date_exclusive) == ("2026-07-20", "2026-07-27")


def test_relevance_classification() -> None:
    baby = classify_event_relevance(SCREENSHOT_ROWS[1])
    assert baby.kind is EventRelevanceKind.DORCHESTER_SERVING_VIRTUAL
    assert baby.is_virtual
    assert baby.display_location == "Virtual"

    randolph_physical = classify_event_relevance(SCREENSHOT_ROWS[2])
    assert randolph_physical.kind is EventRelevanceKind.OUTSIDE_DORCHESTER_PHYSICAL
    assert randolph_physical.location_status is EventLocationStatus.OUTSIDE

    empty_virtualish = classify_event_relevance(SCREENSHOT_ROWS[0])
    assert empty_virtualish.kind in {
        EventRelevanceKind.UNKNOWN_LOCATION,
        EventRelevanceKind.UNKNOWN_RELEVANCE,
    }

    organizer_only = _row(
        99,
        "Codman Organizer Event",
        "Florian Hall, 55 Hallet St, Boston",
        start_date="2026-07-22",
    )
    assert classify_event_relevance(organizer_only).kind is EventRelevanceKind.UNKNOWN_RELEVANCE

    title_url = _row(
        98,
        "Dorchester Festival https://dorchester.example",
        "Some Hall, Quincy, MA",
        start_date="2026-07-22",
    )
    assert classify_event_relevance(title_url).kind is EventRelevanceKind.OUTSIDE_DORCHESTER_PHYSICAL

    unvetted_virtual = _row(
        97,
        "Random Online Meetup",
        "Virtual",
        start_date="2026-07-22",
        source="other",
    )
    assert classify_event_relevance(unvetted_virtual).kind in {
        EventRelevanceKind.UNKNOWN_RELEVANCE,
        EventRelevanceKind.OUTSIDE_DORCHESTER_VIRTUAL,
    }

    title_substring = _row(
        96,
        "Virtual Baby Cafe Special Session",
        "Virtual",
        start_date="2026-07-22",
    )
    assert classify_event_relevance(title_substring).kind is EventRelevanceKind.UNKNOWN_RELEVANCE

    source_substring = _row(
        95,
        "Virtual Baby Cafe",
        "Virtual",
        start_date="2026-07-22",
        source="codman-newsletter",
    )
    assert classify_event_relevance(source_substring).kind is EventRelevanceKind.UNKNOWN_RELEVANCE


def test_screenshot_next_week_answer() -> None:
    plan = _plan("What events are coming up next week?")
    rows, candidates = retrieve_events(_Connection(SCREENSHOT_ROWS), plan, now=NOW)
    ids = [row["id"] for row in rows]
    assert 2 in ids
    assert 1 not in ids
    assert 3 not in ids
    assert 4 not in ids
    assert 5 not in ids
    answer = compose_event_answer(plan, rows, candidate_count=candidates)
    assert "Monday, July 20 through Sunday, July 26" in answer
    assert "Virtual Baby Cafe" in answer
    assert "Virtual" in answer
    assert "couldn't find any events" not in answer.lower()
    assert "Randolph" not in answer or "Virtual Baby" in answer

    discovery = discover_events(_Connection(SCREENSHOT_ROWS), plan, now=NOW)
    payload = discovery.to_dict()
    assert payload["interpreted_scope"] == {
        "time_kind": "NEXT_CALENDAR_WEEK",
        "semantic_start_date": "2026-07-20",
        "semantic_end_date_exclusive": "2026-07-27",
        "effective_start_date": "2026-07-20",
        "effective_end_date_exclusive": "2026-07-27",
        "geography": "DORCHESTER",
        "subarea": None,
        "modality": None,
    }
    assert [row["id"] for row in payload["events"]] == [2]
    assert payload["coverage"]["candidate_count"] == 3
    assert payload["cache_signature"] == plan.cache_signature()


def test_codman_and_virtual_refinement() -> None:
    plan = _plan("What events are coming up in Codman Square next week?")
    rows, _ = retrieve_events(_Connection(SCREENSHOT_ROWS), plan, now=NOW)
    assert [row["id"] for row in rows] == [2]
    answer = compose_event_answer(plan, rows)
    assert "Codman Square" in answer
    assert "Virtual" in answer
    assert "Randolph High" not in answer

    state = ConversationState(
        active_topic=CanonicalTopic.EVENTS,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
        time_range=_dimensions("What events next week?").time_range,
    )
    virtual = _plan(
        "What about virtual events?",
        state,
        ConversationRelationship.REFINEMENT,
    )
    assert virtual.modality == "virtual"
    assert (virtual.start_date, virtual.end_date_exclusive) == ("2026-07-20", "2026-07-27")
    assert virtual.location.id == "codman_square"

    fourteen = _plan(
        "What about the next 14 days?",
        state,
        ConversationRelationship.REFINEMENT,
    )
    assert (fourteen.start_date, fourteen.end_date_exclusive) == ("2026-07-17", "2026-07-31")
    assert fourteen.location.id == "codman_square"


def test_conversation_context_dialogues() -> None:
    # A: retain Codman, replace time
    state = ConversationState(
        active_topic=CanonicalTopic.EVENTS,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
        time_range=_dimensions("Events in Codman Square this week.").time_range,
    )
    refined = _plan("What about next week?", state, ConversationRelationship.REFINEMENT)
    assert refined.location.id == "codman_square"
    assert (refined.start_date, refined.end_date_exclusive) == ("2026-07-20", "2026-07-27")

    # B: safety then standalone events → all Dorchester
    safety = ConversationState(
        active_topic=CanonicalTopic.PUBLIC_SAFETY,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
    )
    standalone = _plan(
        "What events are coming up next week?",
        safety,
        ConversationRelationship.STANDALONE,
    )
    assert standalone.location.id == "dorchester"

    # C: trusted there
    trusted = _plan(
        "Are there events there next week?",
        safety,
        ConversationRelationship.NEW_TOPIC,
    )
    assert trusted.location.id == "codman_square"

    # D: missing there
    missing = _plan(
        "Are there events there next week?",
        ConversationState(),
        ConversationRelationship.NEW_TOPIC,
    )
    assert missing.status is EventPlanStatus.CLARIFICATION
    assert "Which area" in compose_event_answer(missing, [])


def test_upcoming_vs_historical() -> None:
    this_week = _plan("What events are happening this week?")
    assert (this_week.start_date, this_week.end_date_exclusive) == ("2026-07-17", "2026-07-20")
    assert this_week.semantic_start_date == "2026-07-13"
    assert this_week.upcoming_intent is True
    rows, _ = retrieve_events(_Connection(SCREENSHOT_ROWS), this_week, now=NOW)
    assert all(row["id"] != 5 for row in rows)

    historical = _plan("What events happened on July 16?")
    assert (historical.start_date, historical.end_date_exclusive) == ("2026-07-16", "2026-07-17")
    assert historical.upcoming_intent is False
    hist_rows, _ = retrieve_events(_Connection(SCREENSHOT_ROWS), historical, now=NOW)
    assert 5 in [row["id"] for row in hist_rows]

    ended_earlier = _row(
        50,
        "Morning Meetup",
        "Second Church of Dorchester, 44 Moultrie St., Dorchester",
        start_date="2026-07-17",
        start_time="08:00:00",
        end_time="09:00:00",
        source="REP-25_26web.pdf",
    )
    later_today = _row(
        51,
        "Evening Meetup",
        "Second Church of Dorchester, 44 Moultrie St., Dorchester",
        start_date="2026-07-17",
        start_time="18:00:00",
        end_time="19:00:00",
        source="REP-25_26web.pdf",
    )
    today_plan = _plan("What events are happening today?")
    today_rows, _ = retrieve_events(
        _Connection([ended_earlier, later_today]), today_plan, now=NOW
    )
    assert [row["id"] for row in today_rows] == [51]


def test_cache_signatures_isolated() -> None:
    next_week = _plan("What events are coming up next week?")
    fourteen = _plan("What events are happening over the next 14 days?")
    codman = _plan("What events are happening in Codman Square next week?")
    assert next_week.cache_signature() != fourteen.cache_signature()
    assert next_week.cache_signature() != codman.cache_signature()
    assert next_week.cache_signature() == {
        "topic": "EVENTS",
        "start_date": "2026-07-20",
        "end_date_exclusive": "2026-07-27",
        "location_id": "dorchester",
        "subarea_id": None,
        "modality": None,
        "filters": {},
    }
    cache = build_event_cache(
        base_cache={},
        plan=next_week,
        rows=[SCREENSHOT_ROWS[1]],
        answer="ok",
        sources=[],
        now=NOW,
    )
    assert event_cache_matches(cache, next_week, now=NOW)
    assert not event_cache_matches(cache, fourteen, now=NOW)
    assert not event_cache_matches(cache, codman, now=NOW)


def test_sidebar_does_not_affect_chat_plan() -> None:
    # Sidebar days_ahead is a UI-only control; chat planning ignores it.
    plan = _plan("What events are coming up next week?")
    assert (plan.start_date, plan.end_date_exclusive) == ("2026-07-20", "2026-07-27")
    assert plan.cache_signature()["start_date"] != "2026-07-17"

    sidebar = retrieve_sidebar_events(
        _SidebarConnection(SCREENSHOT_ROWS),
        source_table="weekly_events",
        limit=10,
        days_ahead=14,
        now=NOW,
    )
    assert [row["id"] for row in sidebar] == [2, 4]
    assert sidebar[0]["location_label"] == "Virtual"
    assert sidebar[0]["modality"] == "virtual"


def test_no_llm_path_and_overmatch_guard() -> None:
    plan = _plan("What events are coming up next week?")
    assert plan is not None
    assert is_direct_event_list_request("What's going on next week?")
    # Unrelated text with substring "week" should not invent next-week ranges.
    dims = _dimensions("I worked all week on my garden fence.")
    assert dims.topic is not CanonicalTopic.EVENTS or dims.time_range is None or (
        dims.time_range.label_code is TimeLabelCode.THIS_CALENDAR_WEEK
        and dims.topic is not CanonicalTopic.EVENTS
    )

    previous = os.environ.get("OTP_FREEZE_NOW")
    os.environ["OTP_FREEZE_NOW"] = "2026-07-17T12:00:00-04:00"
    try:
        from query_plan import days_ahead_from_question

        assert days_ahead_from_question("What events are next week?") == 10
        resolved, _, _ = resolve_canonical_time_expression(
            "What events are next week?",
            now=NOW,
        )
        assert resolved is not None
        assert (resolved.start_date, resolved.end_date_exclusive) == (
            "2026-07-20",
            "2026-07-27",
        )
    finally:
        if previous is None:
            os.environ.pop("OTP_FREEZE_NOW", None)
        else:
            os.environ["OTP_FREEZE_NOW"] = previous


def test_relationship_all_dorchester_and_virtual() -> None:
    decision = classify_with_rules(
        RelationshipContext(
            current_message="Actually, all of Dorchester.",
            previous_turns=(),
            resolved_entity=None,
            current_topic="EVENTS",
            previous_topic="EVENTS",
            previous_route=None,
            previous_result_exists=True,
        )
    )
    assert decision.relationship is ConversationRelationship.REFINEMENT

    virtual = classify_with_rules(
        RelationshipContext(
            current_message="What about virtual events?",
            previous_turns=(),
            resolved_entity=None,
            current_topic="EVENTS",
            previous_topic="EVENTS",
            previous_route=None,
            previous_result_exists=True,
        )
    )
    assert virtual.relationship is ConversationRelationship.REFINEMENT


def main() -> int:
    cases = [
        test_natural_next_week_phrases,
        test_rolling_and_calendar_distinctions,
        test_upcoming_default_and_boundaries,
        test_geography_defaults_and_narrowing,
        test_relevance_classification,
        test_screenshot_next_week_answer,
        test_codman_and_virtual_refinement,
        test_conversation_context_dialogues,
        test_upcoming_vs_historical,
        test_cache_signatures_isolated,
        test_sidebar_does_not_affect_chat_plan,
        test_no_llm_path_and_overmatch_guard,
        test_relationship_all_dorchester_and_virtual,
    ]
    failed = 0
    for case in cases:
        try:
            case()
            print(f"PASS  {case.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {case.__name__}: {exc}")
    print(f"\nSummary: {len(cases) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
