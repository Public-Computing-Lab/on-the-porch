"""Strict UI-selected event validation and authoritative MySQL resolution."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Literal, Optional, Tuple

from data_ingestion.event_utils import normalize_title
from sql_chat import prepared_sql


MAX_UI_CONTEXT_BYTES = 1024
MAX_EVENT_ID = 2_147_483_647
MAX_EVENT_TITLE_LENGTH = 255
MISSING_EVENT_REFERENCE_MESSAGE = (
    "Which event are you asking about? You can select an event from the Upcoming "
    "Events list or tell me its name."
)
STALE_ACTIVE_EVENT_MESSAGE = (
    "That event listing is no longer available. Please select it again from the "
    "Upcoming Events list."
)
ALLOWED_UI_CONTEXT_KEYS = frozenset({
    "entity_type",
    "entity_id",
    "entity_title",
    "entity_date",
    "source",
})

ResolutionStatus = Literal[
    "exact_id",
    "fallback_title_date",
    "ambiguous",
    "stale",
    "absent",
]

_EVENT_COLUMNS = (
    "`id`, `event_name`, `event_date`, `start_date`, `end_date`, "
    "`start_time`, `end_time`, `raw_text`, `source_pdf`"
)


class EventContextValidationError(ValueError):
    """Raised when a supplied ui_context violates the public contract."""


@dataclass(frozen=True)
class ValidatedEventContext:
    entity_id: str
    entity_title: Optional[str]
    entity_date: Optional[str]
    source: str = "events_sidebar"
    entity_type: str = "event"

    def user_message_metadata(self) -> Dict[str, Any]:
        return {
            "ui_context": {
                "entity_type": self.entity_type,
                "submitted_entity_id": self.entity_id,
                "source": self.source,
                "validation_status": "valid",
            }
        }


@dataclass(frozen=True)
class EventResolution:
    status: ResolutionStatus
    context: Optional[ValidatedEventContext]
    event: Optional[Dict[str, Any]] = None
    diagnostic_mismatches: Tuple[str, ...] = ()

    def resolved_entity(self) -> Dict[str, str]:
        if not self.event:
            return {"type": "event", "resolution": self.status}
        event_id = str(int(self.event["id"]))
        out = {
            "type": "event",
            "id": event_id,
            "title": str(self.event.get("event_name") or "Event"),
            "resolution": self.status,
            "source_ref": f"event:{event_id}",
        }
        event_date = _iso_date(self.event.get("start_date"))
        if event_date:
            out["date"] = event_date
        return out


@dataclass(frozen=True)
class SelectedEventResponse:
    answer: str
    sources: list[Dict[str, str]]
    resolved_entity: Dict[str, str]
    result: Dict[str, Any]
    mode: str = "sql"


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _canonical_event_id(value: Any) -> str:
    if isinstance(value, bool):
        raise EventContextValidationError("boolean event ID")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise EventContextValidationError("invalid event ID type")
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise EventContextValidationError("invalid event ID")
    parsed = int(text)
    if parsed > MAX_EVENT_ID:
        raise EventContextValidationError("event ID out of range")
    return str(parsed)


def _canonical_optional_title(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EventContextValidationError("invalid event title type")
    title = value.strip()
    if not title or len(title) > MAX_EVENT_TITLE_LENGTH or _has_control_characters(title):
        raise EventContextValidationError("invalid event title")
    return title


def _canonical_optional_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise EventContextValidationError("invalid event date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise EventContextValidationError("invalid event date") from exc
    if parsed.isoformat() != value:
        raise EventContextValidationError("non-canonical event date")
    return value


def validate_event_ui_context(raw: Any) -> ValidatedEventContext:
    if not isinstance(raw, dict):
        raise EventContextValidationError("ui_context must be an object")
    try:
        encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EventContextValidationError("ui_context is not JSON serializable") from exc
    if len(encoded) > MAX_UI_CONTEXT_BYTES:
        raise EventContextValidationError("ui_context is too large")
    if set(raw) - ALLOWED_UI_CONTEXT_KEYS:
        raise EventContextValidationError("ui_context contains unsupported fields")
    if raw.get("entity_type") != "event":
        raise EventContextValidationError("unsupported entity type")
    if raw.get("source") != "events_sidebar":
        raise EventContextValidationError("unsupported UI source")
    return ValidatedEventContext(
        entity_id=_canonical_event_id(raw.get("entity_id")),
        entity_title=_canonical_optional_title(raw.get("entity_title")),
        entity_date=_canonical_optional_date(raw.get("entity_date")),
    )


def _iso_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = str(value)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _diagnostic_mismatches(
    context: ValidatedEventContext,
    event: Dict[str, Any],
) -> Tuple[str, ...]:
    mismatches = []
    if context.entity_title and normalize_title(context.entity_title) != normalize_title(
        str(event.get("event_name") or "")
    ):
        mismatches.append("title")
    authoritative_date = _iso_date(event.get("start_date"))
    if context.entity_date and context.entity_date != authoritative_date:
        mismatches.append("date")
    return tuple(mismatches)


def resolve_event_context(conn, context: ValidatedEventContext) -> EventResolution:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"SELECT {_EVENT_COLUMNS} FROM `weekly_events` WHERE `id` = %s LIMIT 1",
            (int(context.entity_id),),
        )
        event = cursor.fetchone()
        if event:
            return EventResolution(
                status="exact_id",
                context=context,
                event=dict(event),
                diagnostic_mismatches=_diagnostic_mismatches(context, event),
            )

        if not context.entity_title or not context.entity_date:
            return EventResolution(status="stale", context=context)

        cursor.execute(
            f"SELECT {_EVENT_COLUMNS} FROM `weekly_events` "
            "WHERE `normalized_name` = %s AND `start_date` = %s "
            "ORDER BY `id` ASC LIMIT 2",
            (normalize_title(context.entity_title), context.entity_date),
        )
        matches = [dict(row) for row in cursor.fetchall()]
        if len(matches) == 1:
            return EventResolution(
                status="fallback_title_date",
                context=context,
                event=matches[0],
            )
        if len(matches) > 1:
            return EventResolution(status="ambiguous", context=context)
        return EventResolution(status="stale", context=context)
    finally:
        cursor.close()


def resolve_event_by_id(conn, event_id: Any) -> EventResolution:
    """Re-fetch one authoritative event using a fixed, bound primary-key lookup."""
    canonical_id = _canonical_event_id(event_id)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"SELECT {_EVENT_COLUMNS} FROM `weekly_events` WHERE `id` = %s LIMIT 1",
            (int(canonical_id),),
        )
        event = cursor.fetchone()
        if not event:
            return EventResolution(status="stale", context=None)
        return EventResolution(status="exact_id", context=None, event=dict(event))
    finally:
        cursor.close()


def _event_property_answer(event: Dict[str, Any], property_name: str) -> str:
    name = prepared_sql._normalize_event_content(  # noqa: SLF001
        event.get("event_name") or "The event",
        preserve_newlines=False,
    )
    raw_text = prepared_sql._normalize_event_content(event.get("raw_text") or "")  # noqa: SLF001
    _description, fields = prepared_sql._parse_labeled_event_fields(raw_text)  # noqa: SLF001
    location = prepared_sql._normalize_event_content(  # noqa: SLF001
        event.get("location")
        or event.get("event_location")
        or fields.get("location", ""),
        preserve_newlines=False,
    )
    organizer = prepared_sql._reliable_event_organizer(  # noqa: SLF001
        str(event.get("source_pdf") or "")
    )
    is_virtual = prepared_sql._is_explicitly_virtual(raw_text, location, name)  # noqa: SLF001
    phones = prepared_sql._event_phones(  # noqa: SLF001
        " ".join((raw_text, fields.get("contact", ""), fields.get("registration", "")))
    )
    emails = prepared_sql._event_emails(  # noqa: SLF001
        " ".join((fields.get("contact", ""), fields.get("registration", "")))
    )
    contact = prepared_sql._contact_sentence(  # noqa: SLF001
        raw_text=raw_text,
        fields=fields,
        phones=phones,
        emails=emails,
        organizer=organizer,
    )

    if property_name == "location":
        if is_virtual:
            answer = f"{name} is a virtual event, so there is no physical location listed."
            return f"{answer} {contact}".strip() if contact else answer
        if location:
            return f"{name} takes place at {location.rstrip('.')}."
        return "The event listing does not provide a separate location."

    if property_name == "date_time":
        event_date = prepared_sql._fmt_event_date(  # noqa: SLF001
            event.get("start_date") or event.get("event_date") or fields.get("date") or ""
        )
        start_time = prepared_sql._fmt_clock(event.get("start_time"))  # noqa: SLF001
        end_time = prepared_sql._fmt_clock(event.get("end_time"))  # noqa: SLF001
        if event_date and start_time and end_time:
            return f"{name} is scheduled for {event_date}, from {start_time} to {end_time}."
        if event_date and start_time:
            return f"{name} is scheduled for {event_date} at {start_time}."
        if event_date:
            return f"{name} is scheduled for {event_date}; the listing does not provide a time."
        if start_time and end_time:
            return f"{name} runs from {start_time} to {end_time}; the listing does not provide a date."
        if start_time:
            return f"{name} starts at {start_time}; the listing does not provide a date."
        return "The event listing does not provide a date or time."

    if property_name == "cost":
        cost = prepared_sql._normalize_event_content(  # noqa: SLF001
            fields.get("cost", ""),
            preserve_newlines=False,
        )
        if not cost:
            return "The event listing does not specify a cost."
        if cost.lower() == "free":
            return f"The listing says {name} is free."
        return f"The listed cost for {name} is {cost.rstrip('.')}."

    if property_name == "registration_contact":
        if contact:
            return contact
        return "The event listing does not provide registration or contact information."

    if property_name == "organizer":
        if organizer:
            return f"{name} is organized by {organizer}."
        return "The event listing does not identify a separate organizer."

    return prepared_sql.format_event_detail_answer(
        "Tell me more about this event",
        {"rows": [event]},
    )


def build_active_event_response(
    question: str,
    resolution: EventResolution,
    *,
    property_name: str,
) -> SelectedEventResponse:
    """Build a zero-LLM event-property answer from one re-fetched event row."""
    if not resolution.event:
        return SelectedEventResponse(
            answer=STALE_ACTIVE_EVENT_MESSAGE,
            sources=[],
            resolved_entity={"type": "event", "resolution": "stale"},
            result={"answer": "", "result": {"columns": [], "rows": []}},
        )
    event = resolution.event
    result = {"columns": list(event.keys()), "rows": [event]}
    sources = prepared_sql.build_event_sources(
        result["rows"],
        question,
        include_entity_ref=True,
    )
    answer = _event_property_answer(event, property_name)
    return SelectedEventResponse(
        answer=answer,
        sources=sources,
        resolved_entity=resolution.resolved_entity(),
        result={
            "answer": answer,
            "result": result,
            "prepared_intent": "event_by_name",
            "db_backed": True,
            "event_sources": sources,
        },
    )


def build_selected_event_response(
    question: str,
    resolution: EventResolution,
) -> SelectedEventResponse:
    resolved_entity = resolution.resolved_entity()
    if resolution.event:
        result = {
            "columns": list(resolution.event.keys()),
            "rows": [resolution.event],
        }
        answer = prepared_sql.format_event_detail_answer(question, result)
        sources = prepared_sql.build_event_sources(
            result["rows"],
            question,
            include_entity_ref=True,
        )
        payload = {
            "answer": answer,
            "result": result,
            "prepared_intent": "event_by_name",
            "db_backed": True,
            "event_sources": sources,
        }
    elif resolution.status == "ambiguous":
        answer = (
            "I found more than one current event matching that selection. "
            "Please refresh the event list and choose the event again."
        )
        sources = []
        payload = {"answer": answer, "result": {"columns": [], "rows": []}}
    else:
        answer = (
            "The selected event listing is no longer available. "
            "Please refresh the event list and select it again."
        )
        sources = []
        payload = {"answer": answer, "result": {"columns": [], "rows": []}}
    return SelectedEventResponse(
        answer=answer,
        sources=sources,
        resolved_entity=resolved_entity,
        result=payload,
    )
