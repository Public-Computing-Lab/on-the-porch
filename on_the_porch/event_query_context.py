"""Trusted, deterministic planning and retrieval for event-list questions only."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from conversation_dimensions import (
    CanonicalLocation,
    CanonicalTimeRange,
    CanonicalTopic,
    ConversationDimensions,
    DecisionSource as DimensionDecisionSource,
    GeographyKind,
    ModalityFilter,
    ReasonCode as DimensionReasonCode,
    TimeLabelCode,
    TimeGranularity,
    resolve_app_now,
)
from conversation_relationship import (
    ConversationRelationship,
    DecisionSource as RelationshipDecisionSource,
    RelationshipDecision,
)
from conversation_state import ConversationState
from event_relevance import (
    EventLocationStatus,
    EventRelevanceKind,
    classify_event_location,
    classify_event_relevance,
    extract_labeled_event_location,
    is_default_sidebar_event,
    matches_requested_scope,
)
from sql_chat import prepared_sql


NEW_YORK = ZoneInfo("America/New_York")
MAX_PLAN_BYTES = 2048
MAX_REASON_CODES = 12
MAX_EVENT_ROWS = 100
MAX_RESULT_ROWS = 25
EVENT_CACHE_TTL_SECONDS = 300
DEFAULT_EVENT_WINDOW_DAYS = 14


class EventPlanStatus(str, Enum):
    READY = "READY"
    CLARIFICATION = "CLARIFICATION"
    UNSUPPORTED = "UNSUPPORTED"


class EventQueryDimension(str, Enum):
    TOPIC = "topic"
    TIME_RANGE = "time_range"
    LOCATION = "location"
    FILTERS = "filters"


class EventPlanReason(str, Enum):
    EXPLICIT_EVENTS_TOPIC = "explicit_events_topic"
    TRUSTED_EVENTS_STATE = "trusted_events_state"
    EXPLICIT_TIME = "explicit_time"
    TRUSTED_TIME_REFERENCE = "trusted_time_reference"
    RETAINED_TIME = "retained_time"
    DEFAULT_UPCOMING_WINDOW = "default_upcoming_14_days"
    EXPLICIT_LOCATION = "explicit_location"
    TRUSTED_LOCATION_REFERENCE = "trusted_location_reference"
    RETAINED_LOCATION = "retained_location"
    DEFAULT_DORCHESTER_SCOPE = "default_dorchester_scope"
    MISSING_LOCATION_REFERENCE = "missing_location_reference"
    MISSING_TIME_REFERENCE = "missing_time_reference"
    AMBIGUOUS_LOCATION = "ambiguous_location"
    AMBIGUOUS_TIME = "ambiguous_time"
    UNSUPPORTED_FILTER = "unsupported_filter"
    UNSUPPORTED_TIME = "unsupported_time"
    UPCOMING_FLOOR_APPLIED = "upcoming_floor_applied"
    HISTORICAL_QUERY = "historical_query"
    VIRTUAL_MODALITY = "virtual_modality"


_SUPPORTED_TIME_LABELS = frozenset(
    {
        TimeLabelCode.TODAY,
        TimeLabelCode.TOMORROW,
        TimeLabelCode.THIS_WEEK,
        TimeLabelCode.THIS_CALENDAR_WEEK,
        TimeLabelCode.NEXT_CALENDAR_WEEK,
        TimeLabelCode.WEEK_AFTER_NEXT,
        TimeLabelCode.LATER_THIS_WEEK,
        TimeLabelCode.EARLIER_THIS_WEEK,
        TimeLabelCode.THIS_WEEKEND,
        TimeLabelCode.NEXT_WEEKEND,
        TimeLabelCode.THIS_MONTH,
        TimeLabelCode.NEXT_MONTH,
        TimeLabelCode.ROLLING_DAYS,
        TimeLabelCode.UPCOMING_DEFAULT,
        TimeLabelCode.EXPLICIT_DATE,
        TimeLabelCode.EXPLICIT_RANGE,
        TimeLabelCode.LAST_WEEK,
        TimeLabelCode.ROLLING_7_DAYS,
        TimeLabelCode.ROLLING_30_DAYS,
        TimeLabelCode.LAST_MONTH,
    }
)
_EXPLICIT_TIME_REASONS = frozenset(
    {
        DimensionReasonCode.EXPLICIT_TODAY,
        DimensionReasonCode.EXPLICIT_TOMORROW,
        DimensionReasonCode.EXPLICIT_THIS_WEEK,
        DimensionReasonCode.EXPLICIT_NEXT_CALENDAR_WEEK,
        DimensionReasonCode.EXPLICIT_WEEK_AFTER_NEXT,
        DimensionReasonCode.EXPLICIT_LATER_THIS_WEEK,
        DimensionReasonCode.EXPLICIT_EARLIER_THIS_WEEK,
        DimensionReasonCode.EXPLICIT_THIS_WEEKEND,
        DimensionReasonCode.EXPLICIT_NEXT_WEEKEND,
        DimensionReasonCode.EXPLICIT_THIS_MONTH,
        DimensionReasonCode.EXPLICIT_NEXT_MONTH,
        DimensionReasonCode.EXPLICIT_LAST_MONTH,
        DimensionReasonCode.EXPLICIT_LAST_WEEK,
        DimensionReasonCode.EXPLICIT_ROLLING_DAYS,
        DimensionReasonCode.EXPLICIT_ROLLING_7_DAYS,
        DimensionReasonCode.EXPLICIT_ROLLING_30_DAYS,
        DimensionReasonCode.EXPLICIT_UPCOMING_DEFAULT,
        DimensionReasonCode.EXPLICIT_DATE,
        DimensionReasonCode.EXPLICIT_RANGE,
        DimensionReasonCode.EXPLICIT_RIGHT_NOW,
    }
)
_EXPLICIT_LOCATION_REASONS = frozenset(
    {
        DimensionReasonCode.EXPLICIT_DORCHESTER_LOCATION,
        DimensionReasonCode.EXPLICIT_CODMAN_SQUARE_LOCATION,
        DimensionReasonCode.EXPLICIT_FIELDS_CORNER_LOCATION,
    }
)
_UNSUPPORTED_FILTER_REASONS = frozenset(
    {
        DimensionReasonCode.EXPLICIT_FREE_FILTER,
        DimensionReasonCode.EXPLICIT_PAID_FILTER,
        DimensionReasonCode.EXPLICIT_UNKNOWN_COST_FILTER,
        DimensionReasonCode.EXPLICIT_MORNING_FILTER,
        DimensionReasonCode.EXPLICIT_AFTERNOON_FILTER,
        DimensionReasonCode.EXPLICIT_EVENING_FILTER,
        DimensionReasonCode.EXPLICIT_HYBRID_FILTER,
        DimensionReasonCode.EXPLICIT_REGISTRATION_REQUIRED_FILTER,
        DimensionReasonCode.EXPLICIT_REGISTRATION_NOT_REQUIRED_FILTER,
        DimensionReasonCode.EXPLICIT_REGISTRATION_UNKNOWN_FILTER,
        DimensionReasonCode.UNSUPPORTED_FILTER,
    }
)
_HISTORICAL_TIME_LABELS = frozenset(
    {
        TimeLabelCode.EARLIER_THIS_WEEK,
        TimeLabelCode.LAST_WEEK,
        TimeLabelCode.LAST_MONTH,
        TimeLabelCode.ROLLING_7_DAYS,
        TimeLabelCode.ROLLING_30_DAYS,
    }
)


def _dedupe(values: Iterable[Enum]) -> Tuple[Any, ...]:
    return tuple(dict.fromkeys(values))


def is_direct_event_list_request(message: str) -> bool:
    """Recognize explicit plural list shapes without matching one-event details."""
    value = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    if not value or re.search(r"['\"][^'\"]{4,}['\"]", value):
        return False
    if any(
        phrase in value
        for phrase in (
            "tell me about",
            "more about",
            "details about",
            "when is the",
            "where is the",
        )
    ):
        return False
    return bool(
        re.match(r"^(?:events\b|what events\b|which events\b|are there (?:any )?events\b)", value)
        or re.match(r"^(?:show|list|find) (?:me )?(?:any )?events\b", value)
        or re.match(
            r"^(?:what(?:'s|s| is)? going on|what(?:'s|s| is)? happening|"
            r"anything happening|what(?:'s|s| is)? on)\b",
            value,
        )
    )


def is_historical_event_intent(message: str, time_range: Optional[CanonicalTimeRange]) -> bool:
    text = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    if time_range is not None and time_range.label_code in _HISTORICAL_TIME_LABELS:
        return True
    return bool(
        re.search(
            r"\b(?:happened|were there|was there|held|took place|earlier this week|"
            r"last week|yesterday|what events happened|what happened)\b",
            text,
        )
    )


@dataclass(frozen=True)
class EventQueryPlan:
    status: EventPlanStatus
    topic: CanonicalTopic
    start_date: Optional[str]
    end_date_exclusive: Optional[str]
    location: Optional[CanonicalLocation]
    applied_dimensions: Tuple[EventQueryDimension, ...]
    unapplied_dimensions: Tuple[EventQueryDimension, ...]
    requires_clarification: bool
    reason_codes: Tuple[EventPlanReason, ...]
    semantic_start_date: Optional[str] = None
    semantic_end_date_exclusive: Optional[str] = None
    semantic_label_code: Optional[str] = None
    modality: Optional[str] = None
    upcoming_intent: bool = True

    def __post_init__(self) -> None:
        if self.topic is not CanonicalTopic.EVENTS:
            raise ValueError("event plans must use the EVENTS topic")
        if not self.reason_codes or len(self.reason_codes) > MAX_REASON_CODES:
            raise ValueError("event plan reasons must be bounded")
        if self.status is EventPlanStatus.READY:
            if not self.start_date or not self.end_date_exclusive:
                raise ValueError("ready event plans require a date range")
            if date.fromisoformat(self.end_date_exclusive) <= date.fromisoformat(self.start_date):
                raise ValueError("event plan date range must be positive")
        elif self.start_date is not None or self.end_date_exclusive is not None:
            raise ValueError("unexecuted event plans cannot carry dates")
        if self.status is EventPlanStatus.CLARIFICATION and not self.requires_clarification:
            raise ValueError("clarification plans must require clarification")
        self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        value = {
            "status": self.status.value,
            "topic": self.topic.value,
            "start_date": self.start_date,
            "end_date_exclusive": self.end_date_exclusive,
            "location": self.location.to_dict() if self.location else None,
            "applied_dimensions": [item.value for item in self.applied_dimensions],
            "unapplied_dimensions": [item.value for item in self.unapplied_dimensions],
            "requires_clarification": self.requires_clarification,
            "reason_codes": [item.value for item in self.reason_codes],
            "semantic_start_date": self.semantic_start_date,
            "semantic_end_date_exclusive": self.semantic_end_date_exclusive,
            "semantic_label_code": self.semantic_label_code,
            "modality": self.modality,
            "upcoming_intent": self.upcoming_intent,
        }
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PLAN_BYTES:
            raise ValueError("event query plan exceeds the size limit")
        return value

    def cache_signature(self) -> Optional[Dict[str, Any]]:
        if self.status is not EventPlanStatus.READY:
            return None
        location_id = "dorchester"
        subarea_id = None
        if self.location is not None:
            if self.location.id == "dorchester":
                location_id = "dorchester"
                subarea_id = None
            else:
                location_id = "dorchester"
                subarea_id = self.location.id
        return {
            "topic": CanonicalTopic.EVENTS.value,
            "start_date": self.start_date,
            "end_date_exclusive": self.end_date_exclusive,
            "location_id": location_id,
            "subarea_id": subarea_id,
            "modality": self.modality,
            "filters": {},
        }


def _trusted_inputs(
    relationship: Optional[RelationshipDecision],
    dimensions: Any,
) -> bool:
    return bool(
        relationship
        and relationship.decision_source is RelationshipDecisionSource.RULE
        and dimensions
        and dimensions.decision_source is DimensionDecisionSource.RULE
    )


def _default_time(now: datetime) -> CanonicalTimeRange:
    today = now.astimezone(NEW_YORK).date()
    return CanonicalTimeRange(
        label_code=TimeLabelCode.UPCOMING_DEFAULT,
        start_date=today.isoformat(),
        end_date_exclusive=(today + timedelta(days=DEFAULT_EVENT_WINDOW_DAYS)).isoformat(),
        timezone="America/New_York",
        granularity=TimeGranularity.RANGE,
    )


def _default_location() -> CanonicalLocation:
    return CanonicalLocation("dorchester", GeographyKind.NEIGHBORHOOD)


def _effective_range(
    time_range: CanonicalTimeRange,
    *,
    today: date,
    historical: bool,
) -> Tuple[str, str, bool]:
    semantic_start = date.fromisoformat(str(time_range.start_date))
    semantic_end = date.fromisoformat(str(time_range.end_date_exclusive))
    if historical:
        return semantic_start.isoformat(), semantic_end.isoformat(), False
    effective_start = max(semantic_start, today)
    if effective_start >= semantic_end:
        # Empty upcoming window — keep a zero-width-safe single-day empty range.
        return effective_start.isoformat(), (effective_start + timedelta(days=1)).isoformat(), True
    floored = effective_start > semantic_start
    return effective_start.isoformat(), semantic_end.isoformat(), floored


def plan_event_query(
    *,
    relationship: Optional[RelationshipDecision],
    dimensions: ConversationDimensions,
    state: ConversationState,
    event_list_requested: bool,
    now: Optional[datetime] = None,
    message: str = "",
) -> Optional[EventQueryPlan]:
    """Build a plan only for a trusted event-list request."""
    if not event_list_requested:
        return None
    if not _trusted_inputs(relationship, dimensions):
        return None

    relation = relationship.relationship
    reasons = set(dimensions.reason_codes)
    explicit_topic = (
        dimensions.topic is CanonicalTopic.EVENTS
        and DimensionReasonCode.EXPLICIT_EVENTS_TOPIC in reasons
    )
    explicit_time = dimensions.time_range if reasons & _EXPLICIT_TIME_REASONS else None
    explicit_location = dimensions.location if reasons & _EXPLICIT_LOCATION_REASONS else None
    unsupported_filters = bool(reasons & _UNSUPPORTED_FILTER_REASONS)
    virtual_modality = (
        dimensions.filters.modality is ModalityFilter.VIRTUAL
        and DimensionReasonCode.EXPLICIT_VIRTUAL_FILTER in reasons
    )
    in_person_modality = (
        dimensions.filters.modality is ModalityFilter.IN_PERSON
        and DimensionReasonCode.EXPLICIT_IN_PERSON_FILTER in reasons
    )
    refs = dimensions.references

    plan_reasons: List[EventPlanReason] = []
    time_range: Optional[CanonicalTimeRange] = None
    location: Optional[CanonicalLocation] = None
    modality: Optional[str] = None

    if relation is ConversationRelationship.STANDALONE:
        if not explicit_topic:
            return None
        plan_reasons.append(EventPlanReason.EXPLICIT_EVENTS_TOPIC)
    elif relation is ConversationRelationship.NEW_TOPIC:
        if not explicit_topic:
            return None
        plan_reasons.append(EventPlanReason.EXPLICIT_EVENTS_TOPIC)
    elif relation in {ConversationRelationship.REFINEMENT, ConversationRelationship.FOLLOW_UP}:
        if state.active_topic is not CanonicalTopic.EVENTS and not explicit_topic:
            return None
        plan_reasons.append(
            EventPlanReason.EXPLICIT_EVENTS_TOPIC
            if explicit_topic
            else EventPlanReason.TRUSTED_EVENTS_STATE
        )
    else:
        return None

    if DimensionReasonCode.AMBIGUOUS_LOCATION in reasons or DimensionReasonCode.UNKNOWN_LOCATION in reasons:
        return EventQueryPlan(
            EventPlanStatus.CLARIFICATION,
            CanonicalTopic.EVENTS,
            None,
            None,
            None,
            (EventQueryDimension.TOPIC,),
            (EventQueryDimension.LOCATION,),
            True,
            tuple(_dedupe((*plan_reasons, EventPlanReason.AMBIGUOUS_LOCATION))),
        )
    if DimensionReasonCode.AMBIGUOUS_EXPLICIT_DATE in reasons:
        return EventQueryPlan(
            EventPlanStatus.CLARIFICATION,
            CanonicalTopic.EVENTS,
            None,
            None,
            None,
            (EventQueryDimension.TOPIC,),
            (EventQueryDimension.TIME_RANGE,),
            True,
            tuple(_dedupe((*plan_reasons, EventPlanReason.AMBIGUOUS_TIME))),
        )

    if unsupported_filters and not (virtual_modality or in_person_modality):
        return EventQueryPlan(
            EventPlanStatus.UNSUPPORTED,
            CanonicalTopic.EVENTS,
            None,
            None,
            None,
            (EventQueryDimension.TOPIC,),
            (EventQueryDimension.FILTERS,),
            False,
            (
                EventPlanReason.EXPLICIT_EVENTS_TOPIC
                if explicit_topic
                else EventPlanReason.TRUSTED_EVENTS_STATE,
                EventPlanReason.UNSUPPORTED_FILTER,
            ),
        )
    if virtual_modality:
        modality = "virtual"
        plan_reasons.append(EventPlanReason.VIRTUAL_MODALITY)
    elif in_person_modality:
        modality = "in_person"
        plan_reasons.append(EventPlanReason.VIRTUAL_MODALITY)

    if refs.location_reference is not None and explicit_location is None:
        if state.location is None:
            return EventQueryPlan(
                EventPlanStatus.CLARIFICATION,
                CanonicalTopic.EVENTS,
                None,
                None,
                None,
                (EventQueryDimension.TOPIC,),
                (EventQueryDimension.LOCATION,),
                True,
                tuple(_dedupe((*plan_reasons, EventPlanReason.MISSING_LOCATION_REFERENCE))),
            )
        location = state.location
        plan_reasons.append(EventPlanReason.TRUSTED_LOCATION_REFERENCE)
    elif explicit_location is not None:
        location = explicit_location
        plan_reasons.append(EventPlanReason.EXPLICIT_LOCATION)
    elif relation in {ConversationRelationship.REFINEMENT, ConversationRelationship.FOLLOW_UP}:
        if state.location is not None:
            location = state.location
            plan_reasons.append(EventPlanReason.RETAINED_LOCATION)

    if refs.time_reference is not None and explicit_time is None:
        if state.time_range is None:
            return EventQueryPlan(
                EventPlanStatus.CLARIFICATION,
                CanonicalTopic.EVENTS,
                None,
                None,
                None,
                (EventQueryDimension.TOPIC,),
                (EventQueryDimension.TIME_RANGE,),
                True,
                tuple(_dedupe((*plan_reasons, EventPlanReason.MISSING_TIME_REFERENCE))),
            )
        time_range = state.time_range
        plan_reasons.append(EventPlanReason.TRUSTED_TIME_REFERENCE)
    elif explicit_time is not None:
        time_range = explicit_time
        plan_reasons.append(EventPlanReason.EXPLICIT_TIME)
    elif relation in {ConversationRelationship.REFINEMENT, ConversationRelationship.FOLLOW_UP}:
        if state.time_range is not None:
            time_range = state.time_range
            plan_reasons.append(EventPlanReason.RETAINED_TIME)

    local_now = resolve_app_now(now)
    if time_range is None:
        time_range = _default_time(local_now)
        plan_reasons.append(EventPlanReason.DEFAULT_UPCOMING_WINDOW)
    if time_range.label_code not in _SUPPORTED_TIME_LABELS:
        return EventQueryPlan(
            EventPlanStatus.UNSUPPORTED,
            CanonicalTopic.EVENTS,
            None,
            None,
            None,
            (EventQueryDimension.TOPIC,),
            (EventQueryDimension.TIME_RANGE,),
            False,
            tuple(_dedupe((*plan_reasons, EventPlanReason.UNSUPPORTED_TIME))),
        )
    if location is None:
        location = _default_location()
        plan_reasons.append(EventPlanReason.DEFAULT_DORCHESTER_SCOPE)

    # Retain modality from prior event state when refining time/location only.
    if (
        modality is None
        and relation in {ConversationRelationship.REFINEMENT, ConversationRelationship.FOLLOW_UP}
        and state.filters is not None
        and state.filters.modality is ModalityFilter.VIRTUAL
    ):
        modality = "virtual"

    historical = is_historical_event_intent(message or "", time_range)
    if historical:
        plan_reasons.append(EventPlanReason.HISTORICAL_QUERY)
    effective_start, effective_end, floored = _effective_range(
        time_range,
        today=local_now.date(),
        historical=historical,
    )
    if floored and not historical:
        plan_reasons.append(EventPlanReason.UPCOMING_FLOOR_APPLIED)

    applied = [EventQueryDimension.TOPIC, EventQueryDimension.TIME_RANGE]
    if location is not None:
        applied.append(EventQueryDimension.LOCATION)
    if modality is not None:
        applied.append(EventQueryDimension.FILTERS)
    return EventQueryPlan(
        EventPlanStatus.READY,
        CanonicalTopic.EVENTS,
        effective_start,
        effective_end,
        location,
        tuple(applied),
        (),
        False,
        tuple(_dedupe(plan_reasons)),
        semantic_start_date=time_range.start_date,
        semantic_end_date_exclusive=time_range.end_date_exclusive,
        semantic_label_code=time_range.label_code.value,
        modality=modality,
        upcoming_intent=not historical,
    )


def _parse_clock(value: Any) -> Optional[time]:
    if value is None:
        return None
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return time(hour=hours % 24, minute=minutes, second=seconds)
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _event_still_active(row: Mapping[str, Any], *, now: datetime, upcoming: bool) -> bool:
    if not upcoming:
        return True
    start_raw = row.get("start_date")
    end_raw = row.get("end_date") or start_raw
    try:
        start_day = date.fromisoformat(str(start_raw))
        end_day = date.fromisoformat(str(end_raw))
    except (TypeError, ValueError):
        return False
    today = now.date()
    if end_day < today:
        return False
    if end_day > today:
        return True
    # Ends today — honor end_time when present.
    end_clock = _parse_clock(row.get("end_time"))
    if end_clock is None:
        return True
    return datetime.combine(end_day, end_clock, tzinfo=NEW_YORK) > now


def retrieve_events(
    conn: Any,
    plan: EventQueryPlan,
    *,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    if plan.status is not EventPlanStatus.READY:
        return [], 0
    local_now = resolve_app_now(now)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, event_name, event_date, start_date, end_date,
                   start_time, end_time, raw_text, source_pdf, category
            FROM weekly_events
            WHERE start_date < %s
              AND COALESCE(end_date, start_date) >= %s
            ORDER BY start_date ASC, start_time ASC, event_name ASC, id ASC
            LIMIT %s
            """,
            (plan.end_date_exclusive, plan.start_date, MAX_EVENT_ROWS),
        )
        candidates = [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()

    active = [
        row
        for row in candidates
        if _event_still_active(row, now=local_now, upcoming=plan.upcoming_intent)
    ]
    if plan.location is None:
        return active[:MAX_RESULT_ROWS], len(candidates)
    matches = []
    for row in active:
        relevance = classify_event_relevance(row)
        if matches_requested_scope(
            requested_location_id=plan.location.id,
            relevance=relevance,
            modality=plan.modality,
        ):
            enriched = dict(row)
            enriched["_relevance_kind"] = relevance.kind.value
            enriched["_display_location"] = (
                "Virtual" if relevance.is_virtual else relevance.display_location
            )
            enriched["_is_virtual"] = relevance.is_virtual
            matches.append(enriched)
    return matches[:MAX_RESULT_ROWS], len(candidates)


def _human_date_range(plan: EventQueryPlan) -> str:
    start = date.fromisoformat(str(plan.start_date))
    end_inclusive = date.fromisoformat(str(plan.end_date_exclusive)) - timedelta(days=1)
    if start == end_inclusive:
        return f"{start.strftime('%A')}, {start.strftime('%B')} {start.day}"
    return (
        f"{start.strftime('%A')}, {start.strftime('%B')} {start.day} through "
        f"{end_inclusive.strftime('%A')}, {end_inclusive.strftime('%B')} {end_inclusive.day}"
    )


def _location_name(location: Optional[CanonicalLocation]) -> str:
    return {
        "dorchester": "Dorchester",
        "codman_square": "Codman Square",
        "fields_corner": "Fields Corner",
    }.get(location.id if location else "", "")


def _event_line(row: Mapping[str, Any]) -> str:
    name = str(row.get("event_name") or "Untitled event").strip()
    event_date = prepared_sql._fmt_event_date(row.get("start_date") or row.get("event_date"))
    start_time = prepared_sql._fmt_clock(row.get("start_time"))
    end_time = prepared_sql._fmt_clock(row.get("end_time"))
    time_label = start_time
    if start_time and end_time and end_time != start_time:
        time_label = f"{start_time}–{end_time}"
    location = row.get("_display_location") or extract_labeled_event_location(row.get("raw_text"))
    if row.get("_is_virtual"):
        location = "Virtual"
    details = [item for item in (event_date, time_label, location) if item]
    suffix = f" — {'; '.join(details)}" if details else ""
    url = prepared_sql._extract_event_url(str(row.get("raw_text") or ""))
    title = f"[{name}]({url})" if url else name
    source = str(row.get("source_pdf") or "").strip()
    source_bit = f" ({source})" if source and source.lower() not in {"codman", "csndc"} else ""
    if source.lower() == "codman":
        source_bit = " (Codman Square Health Center)"
    elif source.lower() == "csndc":
        source_bit = " (CSNDC)"
    return f"- **{title}**{suffix}{source_bit}"


def build_event_sources(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    sources: List[Dict[str, str]] = []
    seen = set()
    for row in rows[:MAX_RESULT_ROWS]:
        entries = prepared_sql.build_event_sources([dict(row)], include_entity_ref=True)
        for entry in entries:
            key = (entry.get("source"), entry.get("path"), entry.get("entity_ref"))
            if key not in seen:
                seen.add(key)
                sources.append(entry)
    return sources[:MAX_RESULT_ROWS]


def compose_event_answer(
    plan: EventQueryPlan,
    rows: Sequence[Mapping[str, Any]],
    *,
    candidate_count: int = 0,
) -> str:
    if plan.status is EventPlanStatus.CLARIFICATION:
        if EventPlanReason.MISSING_TIME_REFERENCE in plan.reason_codes or EventPlanReason.AMBIGUOUS_TIME in plan.reason_codes:
            return "Which date or date range should I use for the event search?"
        return (
            "Which area are you asking about? You can name Dorchester, Codman Square, "
            "or Fields Corner."
        )
    if plan.status is EventPlanStatus.UNSUPPORTED:
        if EventQueryDimension.FILTERS in plan.unapplied_dimensions:
            return (
                "I can narrow events by date and confirmed location right now, but I "
                "can't reliably filter by cost, time of day, format, or registration yet "
                "because many listings do not provide that information in structured fields."
            )
        return "I can search event listings by a specific date or date range."

    date_label = _human_date_range(plan)
    location_name = _location_name(plan.location)
    remaining_prefix = ""
    if (
        plan.upcoming_intent
        and plan.semantic_label_code
        in {TimeLabelCode.THIS_CALENDAR_WEEK.value, TimeLabelCode.THIS_WEEK.value}
        and EventPlanReason.UPCOMING_FLOOR_APPLIED in plan.reason_codes
    ):
        remaining_prefix = "remaining "

    if not rows:
        if location_name and location_name != "Dorchester":
            return (
                f"I couldn't find any events with a location confirmed in {location_name} "
                f"for {date_label}. I can also show events across the rest of Dorchester."
            )
        if location_name == "Dorchester":
            return (
                f"I couldn't find any events in the current Dorchester listings for "
                f"{date_label}."
            )
        return f"I couldn't find any events in the current listings for {date_label}."

    virtual_rows = [row for row in rows if row.get("_is_virtual")]
    physical_rows = [row for row in rows if not row.get("_is_virtual")]

    if location_name == "Dorchester" and virtual_rows and not physical_rows:
        opening = (
            f"I found these Dorchester-serving virtual events for {date_label}. "
            "I did not find a physical Dorchester location confirmed for another event "
            "in that period."
        )
    elif location_name == "Dorchester":
        opening = (
            f"Here are the {remaining_prefix}events in the current Dorchester listings "
            f"for {date_label}."
        )
    elif location_name:
        opening = (
            f"Here are the {remaining_prefix}events in the current listings for "
            f"{location_name} for {date_label}."
        )
    else:
        opening = f"Here are the {remaining_prefix}events in the current listings for {date_label}."

    if plan.modality == "virtual":
        opening = (
            f"Here are the {remaining_prefix}virtual events in the current "
            f"{location_name or 'Dorchester'} listings for {date_label}."
        )

    lines = [opening, "", *(_event_line(row) for row in rows)]
    return "\n".join(lines)


def build_event_cache(
    *,
    base_cache: Mapping[str, Any],
    plan: EventQueryPlan,
    rows: Sequence[Mapping[str, Any]],
    answer: str,
    sources: Sequence[Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    cache = copy.deepcopy(dict(base_cache))
    timestamp = resolve_app_now(now).isoformat()
    cache.update(
        {
            "mode": "structured_events",
            "timestamp": timestamp,
            "question": None,
            "sql_result": {"rows": [dict(row) for row in rows[:MAX_RESULT_ROWS]]},
            "sql_query": None,
            "rag_chunks": None,
            "rag_metadata": None,
            "answer": answer,
            "event_query_signature": plan.cache_signature(),
            "event_sources": [dict(source) for source in sources[:MAX_RESULT_ROWS]],
        }
    )
    return cache


def event_cache_matches(
    cache: Mapping[str, Any],
    plan: EventQueryPlan,
    *,
    now: Optional[datetime] = None,
) -> bool:
    if cache.get("mode") != "structured_events":
        return False
    if cache.get("event_query_signature") != plan.cache_signature():
        return False
    timestamp = cache.get("timestamp")
    try:
        created = datetime.fromisoformat(str(timestamp))
        if created.tzinfo is None:
            created = created.replace(tzinfo=NEW_YORK)
    except (TypeError, ValueError):
        return False
    current = resolve_app_now(now)
    age = (current - created.astimezone(NEW_YORK)).total_seconds()
    return 0 <= age <= EVENT_CACHE_TTL_SECONDS


def cached_event_result(cache: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], str, List[Dict[str, str]]]:
    sql_result = cache.get("sql_result")
    rows = sql_result.get("rows") if isinstance(sql_result, Mapping) else []
    sources = cache.get("event_sources")
    return (
        [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else [],
        str(cache.get("answer") or ""),
        [dict(item) for item in sources if isinstance(item, Mapping)]
        if isinstance(sources, list)
        else [],
    )


__all__ = [
    "EventLocationStatus",
    "EventPlanReason",
    "EventPlanStatus",
    "EventQueryDimension",
    "EventQueryPlan",
    "EventRelevanceKind",
    "build_event_cache",
    "build_event_sources",
    "cached_event_result",
    "classify_event_location",
    "classify_event_relevance",
    "compose_event_answer",
    "event_cache_matches",
    "extract_labeled_event_location",
    "is_default_sidebar_event",
    "is_direct_event_list_request",
    "is_historical_event_intent",
    "plan_event_query",
    "retrieve_events",
]
