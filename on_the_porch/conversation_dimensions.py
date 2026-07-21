"""Bounded, shadow-only extraction of canonical conversation dimensions.

The result produced here is diagnostic metadata. It is deliberately isolated from
routing, retrieval, answer generation, and durable conversation state.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


NEW_YORK_TIMEZONE = "America/New_York"
NEW_YORK = ZoneInfo(NEW_YORK_TIMEZONE)


def resolve_app_now(now: Optional[datetime] = None) -> datetime:
    """America/New_York clock; OTP_FREEZE_NOW enables deterministic local tests."""
    if now is not None:
        value = now if now.tzinfo is not None else now.replace(tzinfo=NEW_YORK)
        return value.astimezone(NEW_YORK)
    raw = str(os.environ.get("OTP_FREEZE_NOW") or "").strip()
    if raw:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=NEW_YORK)
        return parsed.astimezone(NEW_YORK)
    return datetime.now(NEW_YORK)
MAX_MESSAGE_CHARS = 1200
MAX_REASON_CODES = 16
MAX_SERIALIZED_BYTES = 4096
MAX_LLM_OUTPUT_BYTES = 4096
MAX_LLM_PROMPT_BYTES = 4096
LLM_FALLBACK_TIMEOUT_SECONDS = 2.0


class CanonicalTopic(str, Enum):
    EVENTS = "EVENTS"
    CITY_SERVICES = "CITY_SERVICES"
    CRIME_FACTS = "CRIME_FACTS"
    PUBLIC_SAFETY = "PUBLIC_SAFETY"
    COMMUNITY_OPINION = "COMMUNITY_OPINION"
    MEETINGS = "MEETINGS"
    LOCAL_NEWS = "LOCAL_NEWS"
    NEIGHBORHOOD_INFORMATION = "NEIGHBORHOOD_INFORMATION"
    GENERAL = "GENERAL"
    UNKNOWN = "UNKNOWN"


class GeographyKind(str, Enum):
    NEIGHBORHOOD = "neighborhood"
    SUB_NEIGHBORHOOD = "sub_neighborhood"


class TimeLabelCode(str, Enum):
    RIGHT_NOW = "RIGHT_NOW"
    TODAY = "TODAY"
    TOMORROW = "TOMORROW"
    THIS_WEEK = "THIS_WEEK"
    THIS_CALENDAR_WEEK = "THIS_CALENDAR_WEEK"
    NEXT_CALENDAR_WEEK = "NEXT_CALENDAR_WEEK"
    WEEK_AFTER_NEXT = "WEEK_AFTER_NEXT"
    LAST_WEEK = "LAST_WEEK"
    THIS_WEEKEND = "THIS_WEEKEND"
    NEXT_WEEKEND = "NEXT_WEEKEND"
    LATER_THIS_WEEK = "LATER_THIS_WEEK"
    EARLIER_THIS_WEEK = "EARLIER_THIS_WEEK"
    THIS_MONTH = "THIS_MONTH"
    NEXT_MONTH = "NEXT_MONTH"
    LAST_MONTH = "LAST_MONTH"
    ROLLING_DAYS = "ROLLING_DAYS"
    ROLLING_7_DAYS = "ROLLING_7_DAYS"
    ROLLING_30_DAYS = "ROLLING_30_DAYS"
    UPCOMING_DEFAULT = "UPCOMING_DEFAULT"
    EXPLICIT_DATE = "EXPLICIT_DATE"
    EXPLICIT_RANGE = "EXPLICIT_RANGE"
    UNKNOWN = "UNKNOWN"


class TimeGranularity(str, Enum):
    INSTANT = "INSTANT"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class CostFilter(str, Enum):
    ANY = "ANY"
    FREE_ONLY = "FREE_ONLY"
    PAID_ONLY = "PAID_ONLY"
    UNKNOWN_ONLY = "UNKNOWN_ONLY"


class TimeOfDayFilter(str, Enum):
    ANY = "ANY"
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    EVENING = "EVENING"


class ModalityFilter(str, Enum):
    ANY = "ANY"
    IN_PERSON = "IN_PERSON"
    VIRTUAL = "VIRTUAL"
    HYBRID = "HYBRID"


class RegistrationFilter(str, Enum):
    ANY = "ANY"
    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNKNOWN = "UNKNOWN"


class LocationReference(str, Enum):
    THERE = "there"
    THAT_AREA = "that_area"
    NEARBY = "nearby"


class TimeReference(str, Enum):
    THEN = "then"
    THAT_DAY = "that_day"
    DURING_THAT_PERIOD = "during_that_period"


class ResultReference(str, Enum):
    THOSE_EVENTS = "those_events"
    THOSE_COMPLAINTS = "those_complaints"
    THAT_TREND = "that_trend"


class TrustedResultKind(str, Enum):
    EVENTS = "events"
    COMPLAINTS = "complaints"
    TREND = "trend"


class DecisionSource(str, Enum):
    RULE = "rule"
    LLM = "llm"
    FALLBACK = "fallback"


class ReasonCode(str, Enum):
    EXPLICIT_EVENTS_TOPIC = "explicit_events_topic"
    EXPLICIT_CITY_SERVICES_TOPIC = "explicit_city_services_topic"
    EXPLICIT_CRIME_FACTS_TOPIC = "explicit_crime_facts_topic"
    EXPLICIT_PUBLIC_SAFETY_TOPIC = "explicit_public_safety_topic"
    EXPLICIT_COMMUNITY_OPINION_TOPIC = "explicit_community_opinion_topic"
    EXPLICIT_MEETINGS_TOPIC = "explicit_meetings_topic"
    EXPLICIT_LOCAL_NEWS_TOPIC = "explicit_local_news_topic"
    EXPLICIT_NEIGHBORHOOD_INFORMATION_TOPIC = "explicit_neighborhood_information_topic"
    TRUSTED_TOPIC_CONTEXT = "trusted_topic_context"
    EXPLICIT_DORCHESTER_LOCATION = "explicit_dorchester_location"
    EXPLICIT_CODMAN_SQUARE_LOCATION = "explicit_codman_square_location"
    EXPLICIT_FIELDS_CORNER_LOCATION = "explicit_fields_corner_location"
    AMBIGUOUS_LOCATION = "ambiguous_location"
    UNKNOWN_LOCATION = "unknown_location"
    EXPLICIT_RIGHT_NOW = "explicit_right_now"
    EXPLICIT_TODAY = "explicit_today"
    EXPLICIT_TOMORROW = "explicit_tomorrow"
    EXPLICIT_THIS_WEEK = "explicit_this_week"
    EXPLICIT_NEXT_CALENDAR_WEEK = "explicit_next_calendar_week"
    EXPLICIT_WEEK_AFTER_NEXT = "explicit_week_after_next"
    EXPLICIT_LATER_THIS_WEEK = "explicit_later_this_week"
    EXPLICIT_EARLIER_THIS_WEEK = "explicit_earlier_this_week"
    EXPLICIT_LAST_WEEK = "explicit_last_week"
    EXPLICIT_THIS_WEEKEND = "explicit_this_weekend"
    EXPLICIT_NEXT_WEEKEND = "explicit_next_weekend"
    EXPLICIT_THIS_MONTH = "explicit_this_month"
    EXPLICIT_NEXT_MONTH = "explicit_next_month"
    EXPLICIT_LAST_MONTH = "explicit_last_month"
    EXPLICIT_ROLLING_DAYS = "explicit_rolling_days"
    EXPLICIT_ROLLING_7_DAYS = "explicit_rolling_7_days"
    EXPLICIT_ROLLING_30_DAYS = "explicit_rolling_30_days"
    EXPLICIT_UPCOMING_DEFAULT = "explicit_upcoming_default"
    EXPLICIT_DATE = "explicit_date"
    EXPLICIT_RANGE = "explicit_range"
    AMBIGUOUS_EXPLICIT_DATE = "ambiguous_explicit_date"
    EXPLICIT_FREE_FILTER = "explicit_free_filter"
    EXPLICIT_PAID_FILTER = "explicit_paid_filter"
    EXPLICIT_UNKNOWN_COST_FILTER = "explicit_unknown_cost_filter"
    EXPLICIT_MORNING_FILTER = "explicit_morning_filter"
    EXPLICIT_AFTERNOON_FILTER = "explicit_afternoon_filter"
    EXPLICIT_EVENING_FILTER = "explicit_evening_filter"
    EXPLICIT_VIRTUAL_FILTER = "explicit_virtual_filter"
    EXPLICIT_IN_PERSON_FILTER = "explicit_in_person_filter"
    EXPLICIT_HYBRID_FILTER = "explicit_hybrid_filter"
    EXPLICIT_REGISTRATION_REQUIRED_FILTER = "explicit_registration_required_filter"
    EXPLICIT_REGISTRATION_NOT_REQUIRED_FILTER = "explicit_registration_not_required_filter"
    EXPLICIT_REGISTRATION_UNKNOWN_FILTER = "explicit_registration_unknown_filter"
    UNSUPPORTED_FILTER = "unsupported_filter"
    LOCATION_REFERENCE = "location_reference"
    TIME_REFERENCE = "time_reference"
    RESULT_REFERENCE = "result_reference"
    MISSING_TREND_REFERENCE = "missing_trend_reference"
    SMALL_TALK = "small_talk"
    RULE_NO_MATCH = "rule_no_match"
    CLASSIFIER_OUTPUT_INVALID = "classifier_output_invalid"
    CLASSIFIER_TIMEOUT = "classifier_timeout"
    CLASSIFIER_FAILURE = "classifier_failure"


_RESULT_KEYS = frozenset(
    {
        "topic",
        "location",
        "time_range",
        "filters",
        "references",
        "confidence",
        "reason_codes",
        "decision_source",
        "requires_clarification",
        "shadow",
    }
)
_LOCATION_KEYS = frozenset({"id", "kind"})
_TIME_KEYS = frozenset(
    {"label_code", "start_date", "end_date_exclusive", "timezone", "granularity"}
)
_FILTER_KEYS = frozenset({"cost", "time_of_day", "modality", "registration"})
_REFERENCE_KEYS = frozenset(
    {
        "location_reference",
        "time_reference",
        "result_reference",
        "requires_prior_location",
        "requires_prior_time",
        "requires_prior_result",
    }
)


@dataclass(frozen=True)
class GeographyEntry:
    id: str
    name: str
    kind: GeographyKind
    parent_id: Optional[str]
    aliases: Tuple[str, ...]


GEOGRAPHY_REGISTRY: Tuple[GeographyEntry, ...] = (
    GeographyEntry(
        id="dorchester",
        name="Dorchester",
        kind=GeographyKind.NEIGHBORHOOD,
        parent_id=None,
        aliases=("dorchester", "all of dorchester", "rest of dorchester", "across dorchester"),
    ),
    GeographyEntry(
        id="codman_square",
        name="Codman Square",
        kind=GeographyKind.SUB_NEIGHBORHOOD,
        parent_id="dorchester",
        aliases=("codman square", "codman sq"),
    ),
    GeographyEntry(
        id="fields_corner",
        name="Fields Corner",
        kind=GeographyKind.SUB_NEIGHBORHOOD,
        parent_id="dorchester",
        aliases=("fields corner", "fields corners"),
    ),
)


_GEOGRAPHY_BY_ID = {entry.id: entry for entry in GEOGRAPHY_REGISTRY}


def _normalize_text(value: Any, maximum: int = MAX_MESSAGE_CHARS) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()[:maximum]


def _contains_phrase(normalized: str, phrase: str) -> bool:
    return f" {_normalize_text(normalized)} ".find(f" {_normalize_text(phrase)} ") >= 0


def geography_name(location_id: str) -> Optional[str]:
    entry = _GEOGRAPHY_BY_ID.get(str(location_id or ""))
    return entry.name if entry else None


def resolve_registered_geography(value: Any) -> Optional["CanonicalLocation"]:
    """Resolve one semantic place value against the reviewed geography registry.

    This accepts a compiler-produced place concept, not a resident turn, and
    therefore does not perform topic or relationship interpretation.
    """
    normalized = _normalize_text(value, maximum=160)
    if not normalized:
        return None
    exact_matches = []
    phrase_matches = []
    for entry in GEOGRAPHY_REGISTRY:
        names = {_normalize_text(entry.id), _normalize_text(entry.name)}
        names.update(_normalize_text(alias) for alias in entry.aliases)
        if normalized in names:
            exact_matches.append(entry)
        elif any(_contains_phrase(normalized, name) for name in names if name):
            phrase_matches.append(entry)
    if len(exact_matches) == 1:
        entry = exact_matches[0]
        return CanonicalLocation(entry.id, entry.kind)
    if exact_matches:
        return None
    if len(phrase_matches) == 1:
        entry = phrase_matches[0]
        return CanonicalLocation(entry.id, entry.kind)
    # A sub-neighborhood may be qualified by its parent, as in
    # "Fields Corner, Dorchester". Two distinct local areas stay ambiguous.
    children = [entry for entry in phrase_matches if entry.parent_id]
    if len(children) != 1:
        return None
    entry = children[0]
    if any(
        candidate.id not in {entry.id, entry.parent_id}
        for candidate in phrase_matches
    ):
        return None
    return CanonicalLocation(entry.id, entry.kind)


@dataclass(frozen=True)
class CanonicalLocation:
    id: str
    kind: GeographyKind

    def __post_init__(self) -> None:
        entry = _GEOGRAPHY_BY_ID.get(self.id)
        if entry is None or entry.kind is not self.kind:
            raise ValueError("location must come from the server-owned registry")

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "kind": self.kind.value}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanonicalLocation":
        if set(value.keys()) != _LOCATION_KEYS:
            raise ValueError("location has an invalid schema")
        return cls(id=str(value.get("id") or ""), kind=GeographyKind(value.get("kind")))


@dataclass(frozen=True)
class CanonicalTimeRange:
    label_code: TimeLabelCode
    start_date: Optional[str]
    end_date_exclusive: Optional[str]
    timezone: str
    granularity: TimeGranularity

    def __post_init__(self) -> None:
        if self.timezone != NEW_YORK_TIMEZONE:
            raise ValueError("time range must use America/New_York")
        if self.label_code is TimeLabelCode.UNKNOWN:
            if self.start_date is not None or self.end_date_exclusive is not None:
                raise ValueError("unknown time ranges cannot carry dates")
            return
        if not self.start_date or not self.end_date_exclusive:
            raise ValueError("resolved time ranges require dates")
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date_exclusive)
        if end <= start:
            raise ValueError("time range must be a positive half-open interval")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label_code": self.label_code.value,
            "start_date": self.start_date,
            "end_date_exclusive": self.end_date_exclusive,
            "timezone": self.timezone,
            "granularity": self.granularity.value,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanonicalTimeRange":
        if set(value.keys()) != _TIME_KEYS:
            raise ValueError("time range has an invalid schema")
        start = value.get("start_date")
        end = value.get("end_date_exclusive")
        if start is not None and not isinstance(start, str):
            raise ValueError("start_date must be a string or null")
        if end is not None and not isinstance(end, str):
            raise ValueError("end_date_exclusive must be a string or null")
        return cls(
            label_code=TimeLabelCode(value.get("label_code")),
            start_date=start,
            end_date_exclusive=end,
            timezone=str(value.get("timezone") or ""),
            granularity=TimeGranularity(value.get("granularity")),
        )


@dataclass(frozen=True)
class CanonicalFilters:
    cost: CostFilter = CostFilter.ANY
    time_of_day: TimeOfDayFilter = TimeOfDayFilter.ANY
    modality: ModalityFilter = ModalityFilter.ANY
    registration: RegistrationFilter = RegistrationFilter.ANY

    def to_dict(self) -> Dict[str, str]:
        return {
            "cost": self.cost.value,
            "time_of_day": self.time_of_day.value,
            "modality": self.modality.value,
            "registration": self.registration.value,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanonicalFilters":
        if set(value.keys()) != _FILTER_KEYS:
            raise ValueError("filters have an invalid schema")
        return cls(
            cost=CostFilter(value.get("cost")),
            time_of_day=TimeOfDayFilter(value.get("time_of_day")),
            modality=ModalityFilter(value.get("modality")),
            registration=RegistrationFilter(value.get("registration")),
        )


@dataclass(frozen=True)
class ReferenceDiagnostics:
    location_reference: Optional[LocationReference] = None
    time_reference: Optional[TimeReference] = None
    result_reference: Optional[ResultReference] = None
    requires_prior_location: bool = False
    requires_prior_time: bool = False
    requires_prior_result: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "location_reference": (
                self.location_reference.value if self.location_reference else None
            ),
            "time_reference": self.time_reference.value if self.time_reference else None,
            "result_reference": self.result_reference.value if self.result_reference else None,
            "requires_prior_location": self.requires_prior_location,
            "requires_prior_time": self.requires_prior_time,
            "requires_prior_result": self.requires_prior_result,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReferenceDiagnostics":
        if set(value.keys()) != _REFERENCE_KEYS:
            raise ValueError("references have an invalid schema")
        for key in (
            "requires_prior_location",
            "requires_prior_time",
            "requires_prior_result",
        ):
            if not isinstance(value.get(key), bool):
                raise ValueError("reference requirement flags must be boolean")
        location = value.get("location_reference")
        time_value = value.get("time_reference")
        result = value.get("result_reference")
        return cls(
            location_reference=LocationReference(location) if location is not None else None,
            time_reference=TimeReference(time_value) if time_value is not None else None,
            result_reference=ResultReference(result) if result is not None else None,
            requires_prior_location=value["requires_prior_location"],
            requires_prior_time=value["requires_prior_time"],
            requires_prior_result=value["requires_prior_result"],
        )


@dataclass(frozen=True)
class DimensionContext:
    """Only server-trusted state may be supplied to the extractor."""

    active_topic: Optional[CanonicalTopic] = None
    location: Optional[CanonicalLocation] = None
    time_range: Optional[CanonicalTimeRange] = None
    result_kind: Optional[TrustedResultKind] = None
    authoritative_active_event: bool = False

    def __post_init__(self) -> None:
        if self.active_topic is CanonicalTopic.UNKNOWN:
            raise ValueError("UNKNOWN is not a trusted active topic")
        if self.authoritative_active_event and self.active_topic not in (
            None,
            CanonicalTopic.EVENTS,
        ):
            raise ValueError("an active event requires the events topic")

    def to_llm_dict(self) -> Dict[str, Any]:
        return {
            "active_topic": self.active_topic.value if self.active_topic else None,
            "location": self.location.to_dict() if self.location else None,
            "time_range": self.time_range.to_dict() if self.time_range else None,
            "result_kind": self.result_kind.value if self.result_kind else None,
            "authoritative_active_event": self.authoritative_active_event,
        }


@dataclass(frozen=True)
class ConversationDimensions:
    topic: CanonicalTopic
    location: Optional[CanonicalLocation]
    time_range: Optional[CanonicalTimeRange]
    filters: CanonicalFilters
    references: ReferenceDiagnostics
    confidence: float
    reason_codes: Tuple[ReasonCode, ...]
    decision_source: DecisionSource
    requires_clarification: bool
    shadow: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.confidence, bool) or not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not self.reason_codes or len(self.reason_codes) > MAX_REASON_CODES:
            raise ValueError("reason_codes must be a non-empty bounded list")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must be unique")
        if self.shadow is not True:
            raise ValueError("conversation dimensions must remain shadow-only")

    def to_dict(self) -> Dict[str, Any]:
        value = {
            "topic": self.topic.value,
            "location": self.location.to_dict() if self.location else None,
            "time_range": self.time_range.to_dict() if self.time_range else None,
            "filters": self.filters.to_dict(),
            "references": self.references.to_dict(),
            "confidence": round(float(self.confidence), 4),
            "reason_codes": [reason.value for reason in self.reason_codes],
            "decision_source": self.decision_source.value,
            "requires_clarification": self.requires_clarification,
            "shadow": True,
        }
        if len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) > MAX_SERIALIZED_BYTES:
            raise ValueError("conversation dimensions exceed the serialized size limit")
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ConversationDimensions":
        if set(value.keys()) != _RESULT_KEYS:
            raise ValueError("conversation dimensions have an invalid schema")
        confidence = value.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be numeric")
        if not isinstance(value.get("requires_clarification"), bool):
            raise ValueError("requires_clarification must be boolean")
        if value.get("shadow") is not True:
            raise ValueError("shadow must be true")
        location_raw = value.get("location")
        time_raw = value.get("time_range")
        filters_raw = value.get("filters")
        refs_raw = value.get("references")
        reasons_raw = value.get("reason_codes")
        if location_raw is not None and not isinstance(location_raw, Mapping):
            raise ValueError("location must be an object or null")
        if time_raw is not None and not isinstance(time_raw, Mapping):
            raise ValueError("time_range must be an object or null")
        if not isinstance(filters_raw, Mapping) or not isinstance(refs_raw, Mapping):
            raise ValueError("filters and references must be objects")
        if not isinstance(reasons_raw, list) or not reasons_raw:
            raise ValueError("reason_codes must be a non-empty list")
        return cls(
            topic=CanonicalTopic(value.get("topic")),
            location=(CanonicalLocation.from_mapping(location_raw) if location_raw else None),
            time_range=(CanonicalTimeRange.from_mapping(time_raw) if time_raw else None),
            filters=CanonicalFilters.from_mapping(filters_raw),
            references=ReferenceDiagnostics.from_mapping(refs_raw),
            confidence=float(confidence),
            reason_codes=tuple(ReasonCode(item) for item in reasons_raw),
            decision_source=DecisionSource(value.get("decision_source")),
            requires_clarification=value["requires_clarification"],
            shadow=True,
        )


def _dedupe_reasons(reasons: Iterable[ReasonCode]) -> Tuple[ReasonCode, ...]:
    output = []
    seen = set()
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        output.append(reason)
        if len(output) >= MAX_REASON_CODES:
            break
    return tuple(output)


def conservative_unknown(
    reason: ReasonCode = ReasonCode.CLASSIFIER_FAILURE,
) -> ConversationDimensions:
    return ConversationDimensions(
        topic=CanonicalTopic.UNKNOWN,
        location=None,
        time_range=None,
        filters=CanonicalFilters(),
        references=ReferenceDiagnostics(),
        confidence=0.0,
        reason_codes=(reason,),
        decision_source=DecisionSource.FALLBACK,
        requires_clarification=True,
    )


def _resolve_location(
    normalized: str,
    registry: Sequence[GeographyEntry] = GEOGRAPHY_REGISTRY,
) -> Tuple[Optional[CanonicalLocation], Optional[ReasonCode], bool]:
    matches = []
    for entry in registry:
        if any(_contains_phrase(normalized, alias) for alias in entry.aliases):
            matches.append(entry)
    unique = {entry.id: entry for entry in matches}
    if len(unique) > 1:
        return None, ReasonCode.AMBIGUOUS_LOCATION, True
    if unique:
        entry = next(iter(unique.values()))
        reason = {
            "dorchester": ReasonCode.EXPLICIT_DORCHESTER_LOCATION,
            "codman_square": ReasonCode.EXPLICIT_CODMAN_SQUARE_LOCATION,
            "fields_corner": ReasonCode.EXPLICIT_FIELDS_CORNER_LOCATION,
        }[entry.id]
        canonical_entry = _GEOGRAPHY_BY_ID.get(entry.id)
        if canonical_entry is None or canonical_entry.kind is not entry.kind:
            return None, ReasonCode.AMBIGUOUS_LOCATION, True
        return CanonicalLocation(entry.id, entry.kind), reason, False
    if _looks_like_unknown_location(normalized):
        return None, ReasonCode.UNKNOWN_LOCATION, True
    return None, None, False


_NON_LOCATION_CUE_PREFIXES = (
    "person",
    "the morning",
    "the afternoon",
    "the evening",
    "that",
    "this",
    "next",
    "last",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_NON_LOCATION_WHAT_ABOUT = frozenset(
    {
        "it",
        "that",
        "this",
        "crime",
        "safety",
        "events",
        "potholes",
        "today",
        "tomorrow",
        "this weekend",
        "next weekend",
        "this week",
        "next week",
        "later this week",
        "earlier this week",
        "the week after next",
        "week after next",
        "the next 14 days",
        "next 14 days",
        "the next seven days",
        "next seven days",
        "over the next week",
        "virtual",
        "virtual events",
        "online",
        "online events",
        "in person",
        "in person events",
    }
)


def _looks_like_unknown_location(normalized: str) -> bool:
    cue = re.search(r"\b(?:in|near|around)\s+([a-z][a-z ]{1,60})", normalized)
    if cue:
        suffix = cue.group(1).strip()
        if not suffix.startswith(_NON_LOCATION_CUE_PREFIXES):
            return True
    match = re.fullmatch(r"what about ([a-z0-9][a-z0-9 ]{1,50})", normalized)
    return bool(match and match.group(1).strip() not in _NON_LOCATION_WHAT_ABOUT)


def _day_range(label: TimeLabelCode, day: date, granularity: TimeGranularity) -> CanonicalTimeRange:
    return CanonicalTimeRange(
        label_code=label,
        start_date=day.isoformat(),
        end_date_exclusive=(day + timedelta(days=1)).isoformat(),
        timezone=NEW_YORK_TIMEZONE,
        granularity=granularity,
    )


def _range(
    label: TimeLabelCode,
    start: date,
    end_exclusive: date,
    granularity: TimeGranularity,
) -> CanonicalTimeRange:
    return CanonicalTimeRange(
        label_code=label,
        start_date=start.isoformat(),
        end_date_exclusive=end_exclusive.isoformat(),
        timezone=NEW_YORK_TIMEZONE,
        granularity=granularity,
    )


def _local_now(value: Optional[datetime]) -> datetime:
    return resolve_app_now(value)


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_PATTERN = "|".join(_MONTHS)


def _parse_month_date(month: str, day_value: str, year_value: str) -> date:
    return date(int(year_value), _MONTHS[month.lower()], int(day_value))


def _next_named_date(month: str, day_value: str, today: date) -> date:
    resolved = date(today.year, _MONTHS[month.lower()], int(day_value))
    if resolved < today:
        resolved = date(today.year + 1, resolved.month, resolved.day)
    return resolved


def _past_named_date(month: str, day_value: str, today: date) -> date:
    resolved = date(today.year, _MONTHS[month.lower()], int(day_value))
    if resolved > today:
        resolved = date(today.year - 1, resolved.month, resolved.day)
    return resolved


def _extract_explicit_time(
    original: str,
    today: date,
    *,
    resolve_yearless_named_dates: bool,
    prefer_past: bool = False,
) -> Tuple[Optional[CanonicalTimeRange], Optional[ReasonCode], bool]:
    iso_range = re.search(
        r"\b(\d{4}-\d{2}-\d{2})\s+(?:to|through)\s+(\d{4}-\d{2}-\d{2})\b",
        original,
        flags=re.IGNORECASE,
    )
    if iso_range:
        try:
            start = date.fromisoformat(iso_range.group(1))
            end = date.fromisoformat(iso_range.group(2))
            return _range(TimeLabelCode.EXPLICIT_RANGE, start, end + timedelta(days=1), TimeGranularity.RANGE), ReasonCode.EXPLICIT_RANGE, False
        except ValueError:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True

    month_range = re.search(
        rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+(\d{{4}})\s+(?:to|through)\s+"
        rf"({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+(\d{{4}})\b",
        original,
        flags=re.IGNORECASE,
    )
    if month_range:
        try:
            start = _parse_month_date(*month_range.group(1, 2, 3))
            end = _parse_month_date(*month_range.group(4, 5, 6))
            return _range(TimeLabelCode.EXPLICIT_RANGE, start, end + timedelta(days=1), TimeGranularity.RANGE), ReasonCode.EXPLICIT_RANGE, False
        except ValueError:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True

    iso_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", original)
    if iso_date:
        try:
            resolved = date.fromisoformat(iso_date.group(1))
            return _day_range(TimeLabelCode.EXPLICIT_DATE, resolved, TimeGranularity.DAY), ReasonCode.EXPLICIT_DATE, False
        except ValueError:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True

    month_date = re.search(
        rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+(\d{{4}})\b",
        original,
        flags=re.IGNORECASE,
    )
    if month_date:
        try:
            resolved = _parse_month_date(*month_date.group(1, 2, 3))
            return _day_range(TimeLabelCode.EXPLICIT_DATE, resolved, TimeGranularity.DAY), ReasonCode.EXPLICIT_DATE, False
        except ValueError:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True

    yearless_month_range = re.search(
        rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:to|through)\s+"
        rf"({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
        original,
        flags=re.IGNORECASE,
    )
    if yearless_month_range:
        if not resolve_yearless_named_dates:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True
        try:
            resolver = _past_named_date if prefer_past else _next_named_date
            start = resolver(*yearless_month_range.group(1, 2), today)
            end = date(start.year, _MONTHS[yearless_month_range.group(3).lower()], int(yearless_month_range.group(4)))
            if prefer_past and end > start:
                pass
            elif end < start:
                end = date(start.year + 1, end.month, end.day)
            return _range(TimeLabelCode.EXPLICIT_RANGE, start, end + timedelta(days=1), TimeGranularity.RANGE), ReasonCode.EXPLICIT_RANGE, False
        except ValueError:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True

    yearless_month_date = re.search(
        rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*,?\s*\d{{4}})",
        original,
        flags=re.IGNORECASE,
    )
    if yearless_month_date:
        if not resolve_yearless_named_dates:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True
        try:
            resolver = _past_named_date if prefer_past else _next_named_date
            resolved = resolver(*yearless_month_date.group(1, 2), today)
            return _day_range(TimeLabelCode.EXPLICIT_DATE, resolved, TimeGranularity.DAY), ReasonCode.EXPLICIT_DATE, False
        except ValueError:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True

    calendar_month = re.fullmatch(
        rf"\s*({_MONTH_PATTERN})(?:\s+(\d{{4}}))?\s*",
        original,
        flags=re.IGNORECASE,
    )
    if calendar_month:
        month = _MONTHS[calendar_month.group(1).lower()]
        year = int(calendar_month.group(2) or today.year)
        if calendar_month.group(2) is None and month > today.month:
            year -= 1
        start = date(year, month, 1)
        return (
            _range(
                TimeLabelCode.EXPLICIT_RANGE,
                start,
                _next_month_start(start),
                TimeGranularity.MONTH,
            ),
            ReasonCode.EXPLICIT_RANGE,
            False,
        )

    month_comparison = re.fullmatch(
        rf"\s*({_MONTH_PATTERN})(?:\s+(\d{{4}}))?\s+"
        rf"(?:and|vs\.?|versus|than|to)\s+"
        rf"({_MONTH_PATTERN})(?:\s+(\d{{4}}))?\s*",
        original,
        flags=re.IGNORECASE,
    )
    if month_comparison:
        starts = []
        for month_name_value, year_value in (
            month_comparison.group(1, 2),
            month_comparison.group(3, 4),
        ):
            month = _MONTHS[month_name_value.lower()]
            year = int(year_value or today.year)
            if year_value is None and month > today.month:
                year -= 1
            starts.append(date(year, month, 1))
        later, earlier = max(starts), min(starts)
        if _next_month_start(earlier) != later:
            return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True
        return (
            _range(
                TimeLabelCode.EXPLICIT_RANGE,
                later,
                _next_month_start(later),
                TimeGranularity.MONTH,
            ),
            ReasonCode.EXPLICIT_RANGE,
            False,
        )

    ambiguous = re.search(
        r"\b\d{1,2}/\d{1,2}\b(?!/\d{2,4})",
        original,
        flags=re.IGNORECASE,
    )
    if ambiguous:
        return None, ReasonCode.AMBIGUOUS_EXPLICIT_DATE, True
    return None, None, False


def resolve_explicit_date_expression(
    value: Any,
    *,
    now: Optional[datetime] = None,
) -> Optional[CanonicalTimeRange]:
    """Resolve a compiler-owned explicit date expression without topic parsing."""
    text = str(value or "").strip()
    if not text:
        return None
    resolved, _reason, ambiguous = _extract_explicit_time(
        text,
        resolve_app_now(now).date(),
        resolve_yearless_named_dates=True,
    )
    return None if ambiguous else resolved


def _week_start_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _next_month_start(day: date) -> date:
    return (_month_start(day).replace(day=28) + timedelta(days=4)).replace(day=1)


def _extract_time(
    original: str,
    normalized: str,
    now: datetime,
    *,
    resolve_yearless_named_dates: bool,
) -> Tuple[Optional[CanonicalTimeRange], Optional[ReasonCode], bool]:
    today = now.date()
    this_week = _week_start_monday(today)
    next_week = this_week + timedelta(days=7)
    week_after_next = next_week + timedelta(days=7)

    if _contains_phrase(normalized, "right now"):
        return _day_range(TimeLabelCode.RIGHT_NOW, today, TimeGranularity.INSTANT), ReasonCode.EXPLICIT_RIGHT_NOW, False
    if _contains_phrase(normalized, "next weekend"):
        current_start = _current_or_upcoming_weekend(today)
        start = current_start + timedelta(days=7)
        return _range(TimeLabelCode.NEXT_WEEKEND, start, start + timedelta(days=2), TimeGranularity.RANGE), ReasonCode.EXPLICIT_NEXT_WEEKEND, False
    if _contains_phrase(normalized, "this weekend"):
        start = _current_or_upcoming_weekend(today)
        return _range(TimeLabelCode.THIS_WEEKEND, start, start + timedelta(days=2), TimeGranularity.RANGE), ReasonCode.EXPLICIT_THIS_WEEKEND, False
    if (
        _contains_phrase(normalized, "the week after next")
        or _contains_phrase(normalized, "week after next")
    ):
        return (
            _range(
                TimeLabelCode.WEEK_AFTER_NEXT,
                week_after_next,
                week_after_next + timedelta(days=7),
                TimeGranularity.WEEK,
            ),
            ReasonCode.EXPLICIT_WEEK_AFTER_NEXT,
            False,
        )
    if (
        _contains_phrase(normalized, "over the next week")
        or _contains_phrase(normalized, "next seven days")
        or _contains_phrase(normalized, "next 7 days")
    ):
        return (
            _range(
                TimeLabelCode.ROLLING_DAYS,
                today,
                today + timedelta(days=7),
                TimeGranularity.RANGE,
            ),
            ReasonCode.EXPLICIT_ROLLING_DAYS,
            False,
        )
    if (
        _contains_phrase(normalized, "next 14 days")
        or _contains_phrase(normalized, "next fourteen days")
        or _contains_phrase(normalized, "the next 14 days")
        or _contains_phrase(normalized, "over the next 14 days")
    ):
        return (
            _range(
                TimeLabelCode.ROLLING_DAYS,
                today,
                today + timedelta(days=14),
                TimeGranularity.RANGE,
            ),
            ReasonCode.EXPLICIT_ROLLING_DAYS,
            False,
        )
    if _contains_phrase(normalized, "last 7 days") or _contains_phrase(normalized, "past 7 days"):
        return _range(TimeLabelCode.ROLLING_7_DAYS, today - timedelta(days=6), today + timedelta(days=1), TimeGranularity.RANGE), ReasonCode.EXPLICIT_ROLLING_7_DAYS, False
    if _contains_phrase(normalized, "last 30 days") or _contains_phrase(normalized, "past 30 days"):
        return _range(TimeLabelCode.ROLLING_30_DAYS, today - timedelta(days=29), today + timedelta(days=1), TimeGranularity.RANGE), ReasonCode.EXPLICIT_ROLLING_30_DAYS, False
    if (
        _contains_phrase(normalized, "coming up next week")
        or _contains_phrase(normalized, "this coming week")
        or _contains_phrase(normalized, "sometime next week")
        or _contains_phrase(normalized, "next week")
        or _contains_phrase(normalized, "next wk")
        or _contains_phrase(normalized, "whats on next week")
        or _contains_phrase(normalized, "what is on next week")
    ):
        return (
            _range(
                TimeLabelCode.NEXT_CALENDAR_WEEK,
                next_week,
                next_week + timedelta(days=7),
                TimeGranularity.WEEK,
            ),
            ReasonCode.EXPLICIT_NEXT_CALENDAR_WEEK,
            False,
        )
    if _contains_phrase(normalized, "later this week"):
        return (
            _range(
                TimeLabelCode.LATER_THIS_WEEK,
                today,
                this_week + timedelta(days=7),
                TimeGranularity.RANGE,
            ),
            ReasonCode.EXPLICIT_LATER_THIS_WEEK,
            False,
        )
    if _contains_phrase(normalized, "earlier this week"):
        return (
            _range(
                TimeLabelCode.EARLIER_THIS_WEEK,
                this_week,
                today + timedelta(days=1),
                TimeGranularity.RANGE,
            ),
            ReasonCode.EXPLICIT_EARLIER_THIS_WEEK,
            False,
        )
    if _contains_phrase(normalized, "last week"):
        return _range(TimeLabelCode.LAST_WEEK, this_week - timedelta(days=7), this_week, TimeGranularity.WEEK), ReasonCode.EXPLICIT_LAST_WEEK, False
    if _contains_phrase(normalized, "this week"):
        # Semantic calendar week Mon–Sun; upcoming floor is applied at retrieval.
        return (
            _range(
                TimeLabelCode.THIS_CALENDAR_WEEK,
                this_week,
                this_week + timedelta(days=7),
                TimeGranularity.WEEK,
            ),
            ReasonCode.EXPLICIT_THIS_WEEK,
            False,
        )
    if _contains_phrase(normalized, "next month"):
        start = _next_month_start(today)
        end = _next_month_start(start)
        return _range(TimeLabelCode.NEXT_MONTH, start, end, TimeGranularity.MONTH), ReasonCode.EXPLICIT_NEXT_MONTH, False
    if _contains_phrase(normalized, "last month"):
        this_month = _month_start(today)
        previous_month_end = this_month - timedelta(days=1)
        previous_month = _month_start(previous_month_end)
        return _range(TimeLabelCode.LAST_MONTH, previous_month, this_month, TimeGranularity.MONTH), ReasonCode.EXPLICIT_LAST_MONTH, False
    if _contains_phrase(normalized, "this month"):
        this_month = _month_start(today)
        return _range(TimeLabelCode.THIS_MONTH, this_month, _next_month_start(today), TimeGranularity.MONTH), ReasonCode.EXPLICIT_THIS_MONTH, False
    if _contains_phrase(normalized, "later today"):
        return _day_range(TimeLabelCode.TODAY, today, TimeGranularity.DAY), ReasonCode.EXPLICIT_TODAY, False
    if _contains_phrase(normalized, "tomorrow"):
        return _day_range(TimeLabelCode.TOMORROW, today + timedelta(days=1), TimeGranularity.DAY), ReasonCode.EXPLICIT_TOMORROW, False
    if _contains_phrase(normalized, "today") or _contains_phrase(normalized, "tonight"):
        return _day_range(TimeLabelCode.TODAY, today, TimeGranularity.DAY), ReasonCode.EXPLICIT_TODAY, False
    if (
        _contains_phrase(normalized, "upcoming")
        or _contains_phrase(normalized, "coming up")
        or re.search(r"\bsoon\b", normalized)
    ):
        return (
            _range(
                TimeLabelCode.UPCOMING_DEFAULT,
                today,
                today + timedelta(days=14),
                TimeGranularity.RANGE,
            ),
            ReasonCode.EXPLICIT_UPCOMING_DEFAULT,
            False,
        )
    prefer_past = bool(
        re.search(
            r"\b(?:happened|were there|was there|held|took place|yesterday)\b",
            normalized,
        )
    )
    return _extract_explicit_time(
        original,
        today,
        resolve_yearless_named_dates=resolve_yearless_named_dates,
        prefer_past=prefer_past,
    )


def resolve_canonical_time_expression(
    message: str,
    *,
    now: Optional[datetime] = None,
    resolve_yearless_named_dates: bool = False,
) -> Tuple[Optional[CanonicalTimeRange], Optional[ReasonCode], bool]:
    """Resolve one natural-language time expression through the canonical parser.

    Event discovery and compatibility callers use this entry point so phrase
    semantics cannot drift from conversation dimensions.
    """
    original = str(message or "")[:MAX_MESSAGE_CHARS]
    return _extract_time(
        original,
        _normalize_text(original),
        _local_now(now),
        resolve_yearless_named_dates=resolve_yearless_named_dates,
    )


def _current_or_upcoming_weekend(today: date) -> date:
    # Monday=0 ... Sunday=6. Saturday/Sunday remain in the current weekend.
    if today.weekday() == 5:
        return today
    if today.weekday() == 6:
        return today - timedelta(days=1)
    return today + timedelta(days=5 - today.weekday())


_SMALL_TALK = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|thanks so much|thank you so much|ok|okay|got it)$",
    flags=re.IGNORECASE,
)


def _extract_topic(
    normalized: str,
    *,
    location: Optional[CanonicalLocation],
    context: DimensionContext,
    has_filter_refinement: bool,
) -> Tuple[CanonicalTopic, ReasonCode]:
    if _SMALL_TALK.fullmatch(normalized):
        return CanonicalTopic.UNKNOWN, ReasonCode.SMALL_TALK
    opinion_language = bool(
        re.search(r"\b(?:residents?|community|people)\b", normalized)
        and re.search(r"\b(?:say|saying|concerned|concerns|feel|think|opinion)\b", normalized)
    )
    if opinion_language:
        return CanonicalTopic.COMMUNITY_OPINION, ReasonCode.EXPLICIT_COMMUNITY_OPINION_TOPIC
    if re.search(r"\b(?:safe|safety|unsafe|dangerous|danger)\b", normalized):
        return CanonicalTopic.PUBLIC_SAFETY, ReasonCode.EXPLICIT_PUBLIC_SAFETY_TOPIC
    if re.search(r"\b(?:meetings?|hearing|hearings)\b", normalized):
        return CanonicalTopic.MEETINGS, ReasonCode.EXPLICIT_MEETINGS_TOPIC
    if re.search(r"\b(?:events?|festival|calendar)\b", normalized):
        return CanonicalTopic.EVENTS, ReasonCode.EXPLICIT_EVENTS_TOPIC
    natural_event_discovery = bool(
        re.search(
            r"\b(?:what(?:\s+s|\s+is)?\s+going\s+on|whats\s+going\s+on|"
            r"what(?:\s+s|\s+is)?\s+happening|whats\s+happening|anything\s+happening|"
            r"what(?:\s+s|\s+is)?\s+on|whats\s+on|things\s+to\s+do)\b",
            normalized,
        )
        and re.search(
            r"\b(?:today|tomorrow|tonight|weekend|week|month|upcoming|coming\s+up|"
            r"soon|days|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            normalized,
        )
    )
    # Without an explicit "events" keyword, named-place "what's happening" stays
    # neighborhood information. Bare temporal discovery defaults to events.
    if natural_event_discovery and location is None:
        return CanonicalTopic.EVENTS, ReasonCode.EXPLICIT_EVENTS_TOPIC
    city_service = re.search(
        r"\b(?:311|potholes?|trash|garbage|streetlights?|missed pickup|city services?|"
        r"drains?|catchbasins?|flooding|standing water|road conditions?|roadway repair|"
        r"pavement|illegal dumping|dumping)\b",
        normalized,
    )
    if city_service:
        return CanonicalTopic.CITY_SERVICES, ReasonCode.EXPLICIT_CITY_SERVICES_TOPIC
    if re.search(r"\b(?:shootings?|homicides?|crime|criminal|crime incidents?)\b", normalized):
        return CanonicalTopic.CRIME_FACTS, ReasonCode.EXPLICIT_CRIME_FACTS_TOPIC
    if re.search(r"\b(?:local news|news|newspaper|articles?|journalism)\b", normalized):
        return CanonicalTopic.LOCAL_NEWS, ReasonCode.EXPLICIT_LOCAL_NEWS_TOPIC
    if location is not None and re.search(
        r"\b(?:what is happening|whats happening|tell me about|what is going on|whats going on)\b",
        normalized,
    ):
        return (
            CanonicalTopic.NEIGHBORHOOD_INFORMATION,
            ReasonCode.EXPLICIT_NEIGHBORHOOD_INFORMATION_TOPIC,
        )
    correction = bool(
        re.match(
            r"^(?:no i mean|i meant|not that one|actually i mean|actually all of|all of dorchester)\b",
            normalized,
        )
    )
    if context.active_topic is not None and (has_filter_refinement or correction):
        return context.active_topic, ReasonCode.TRUSTED_TOPIC_CONTEXT
    return CanonicalTopic.UNKNOWN, ReasonCode.RULE_NO_MATCH


def _extract_filters(normalized: str) -> Tuple[CanonicalFilters, Tuple[ReasonCode, ...]]:
    reasons = []
    event_filter_scope = bool(
        re.search(r"\b(?:events?|ones)\b", normalized)
        or re.match(r"^(?:only|show|list|find|just show|show only|what about)\b", normalized)
    )
    cost = CostFilter.ANY
    if event_filter_scope and re.search(r"\b(?:free|no cost|at no charge)\b", normalized):
        cost = CostFilter.FREE_ONLY
        reasons.append(ReasonCode.EXPLICIT_FREE_FILTER)
    elif event_filter_scope and re.search(r"\b(?:cost unknown|price unknown|unknown cost)\b", normalized):
        cost = CostFilter.UNKNOWN_ONLY
        reasons.append(ReasonCode.EXPLICIT_UNKNOWN_COST_FILTER)
    elif event_filter_scope and re.search(r"\b(?:paid|costs? \d+|ticket price|admission fee|fee of)\b", normalized):
        cost = CostFilter.PAID_ONLY
        reasons.append(ReasonCode.EXPLICIT_PAID_FILTER)

    time_of_day = TimeOfDayFilter.ANY
    if re.search(r"\b(?:tonight|evening|after work)\b", normalized):
        time_of_day = TimeOfDayFilter.EVENING
        reasons.append(ReasonCode.EXPLICIT_EVENING_FILTER)
    elif re.search(r"\bmorning\b", normalized):
        time_of_day = TimeOfDayFilter.MORNING
        reasons.append(ReasonCode.EXPLICIT_MORNING_FILTER)
    elif re.search(r"\bafternoon\b", normalized):
        time_of_day = TimeOfDayFilter.AFTERNOON
        reasons.append(ReasonCode.EXPLICIT_AFTERNOON_FILTER)

    virtual = bool(event_filter_scope and re.search(r"\b(?:virtual|online|remote)\b", normalized))
    in_person = bool(event_filter_scope and re.search(r"\bin person\b", normalized))
    modality = ModalityFilter.ANY
    if re.search(r"\bhybrid\b", normalized) or (virtual and in_person):
        modality = ModalityFilter.HYBRID
        reasons.append(ReasonCode.EXPLICIT_HYBRID_FILTER)
    elif virtual:
        modality = ModalityFilter.VIRTUAL
        reasons.append(ReasonCode.EXPLICIT_VIRTUAL_FILTER)
    elif in_person:
        modality = ModalityFilter.IN_PERSON
        reasons.append(ReasonCode.EXPLICIT_IN_PERSON_FILTER)

    registration = RegistrationFilter.ANY
    if event_filter_scope and re.search(r"\b(?:no registration|registration not required|drop in)\b", normalized):
        registration = RegistrationFilter.NOT_REQUIRED
        reasons.append(ReasonCode.EXPLICIT_REGISTRATION_NOT_REQUIRED_FILTER)
    elif event_filter_scope and re.search(r"\b(?:registration unknown|sign up unknown)\b", normalized):
        registration = RegistrationFilter.UNKNOWN
        reasons.append(ReasonCode.EXPLICIT_REGISTRATION_UNKNOWN_FILTER)
    elif event_filter_scope and re.search(r"\b(?:registration (?:is )?required|must register|need to register|sign up)\b", normalized):
        registration = RegistrationFilter.REQUIRED
        reasons.append(ReasonCode.EXPLICIT_REGISTRATION_REQUIRED_FILTER)

    if re.search(r"\b(?:family friendly|wheelchair accessible|kid friendly)\b", normalized):
        reasons.append(ReasonCode.UNSUPPORTED_FILTER)

    return CanonicalFilters(cost, time_of_day, modality, registration), tuple(reasons)


def _extract_references(
    normalized: str,
    context: DimensionContext,
) -> Tuple[ReferenceDiagnostics, Tuple[ReasonCode, ...], bool]:
    reasons = []
    clarification = False
    location_ref = None
    if _contains_phrase(normalized, "that area"):
        location_ref = LocationReference.THAT_AREA
    elif _contains_phrase(normalized, "nearby"):
        location_ref = LocationReference.NEARBY
    elif normalized == "there" or re.search(
        r"\b(?:events?|meetings?|happening|available|located)\s+there\b", normalized,
    ) or normalized.count("there") >= 2:
        location_ref = LocationReference.THERE
    if location_ref is not None:
        reasons.append(ReasonCode.LOCATION_REFERENCE)
        clarification = clarification or context.location is None

    time_ref = None
    if _contains_phrase(normalized, "that day"):
        time_ref = TimeReference.THAT_DAY
    elif _contains_phrase(normalized, "during that period"):
        time_ref = TimeReference.DURING_THAT_PERIOD
    elif normalized == "then" or re.search(r"\b(?:events?|happened|happening)\s+then\b", normalized):
        time_ref = TimeReference.THEN
    if time_ref is not None:
        reasons.append(ReasonCode.TIME_REFERENCE)
        clarification = clarification or context.time_range is None

    result_ref = None
    if _contains_phrase(normalized, "those events"):
        result_ref = ResultReference.THOSE_EVENTS
    elif _contains_phrase(normalized, "those complaints"):
        result_ref = ResultReference.THOSE_COMPLAINTS
    elif _contains_phrase(normalized, "that trend"):
        result_ref = ResultReference.THAT_TREND
    elif normalized in {"why", "why is that", "how come"}:
        if context.result_kind is TrustedResultKind.TREND:
            result_ref = ResultReference.THAT_TREND
        else:
            reasons.append(ReasonCode.MISSING_TREND_REFERENCE)
            clarification = True
    if result_ref is not None:
        reasons.append(ReasonCode.RESULT_REFERENCE)
        expected = {
            ResultReference.THOSE_EVENTS: TrustedResultKind.EVENTS,
            ResultReference.THOSE_COMPLAINTS: TrustedResultKind.COMPLAINTS,
            ResultReference.THAT_TREND: TrustedResultKind.TREND,
        }[result_ref]
        clarification = clarification or context.result_kind is not expected

    refs = ReferenceDiagnostics(
        location_reference=location_ref,
        time_reference=time_ref,
        result_reference=result_ref,
        requires_prior_location=location_ref is not None,
        requires_prior_time=time_ref is not None,
        requires_prior_result=result_ref is not None or ReasonCode.MISSING_TREND_REFERENCE in reasons,
    )
    return refs, tuple(reasons), clarification


def extract_with_rules(
    message: str,
    *,
    context: Optional[DimensionContext] = None,
    now: Optional[datetime] = None,
    resolve_yearless_named_dates: bool = False,
) -> ConversationDimensions:
    trusted = context or DimensionContext()
    original = str(message or "")[:MAX_MESSAGE_CHARS]
    normalized = _normalize_text(original)
    filters, filter_reasons = _extract_filters(normalized)
    references, reference_reasons, reference_clarification = _extract_references(
        normalized,
        trusted,
    )
    location, location_reason, location_clarification = _resolve_location(normalized)
    time_range, time_reason, time_clarification = resolve_canonical_time_expression(
        original,
        now=now,
        resolve_yearless_named_dates=resolve_yearless_named_dates,
    )
    topic, topic_reason = _extract_topic(
        normalized,
        location=location,
        context=trusted,
        has_filter_refinement=bool(filter_reasons),
    )
    reasons = [] if topic_reason is ReasonCode.RULE_NO_MATCH else [topic_reason]
    if location_reason:
        reasons.append(location_reason)
    if time_reason:
        reasons.append(time_reason)
    reasons.extend(filter_reasons)
    reasons.extend(reference_reasons)
    reasons = list(_dedupe_reasons(reasons))
    substantive_signal = any(
        (
            topic is not CanonicalTopic.UNKNOWN,
            location is not None,
            location_reason is not None,
            time_range is not None,
            time_reason is not None,
            bool(filter_reasons),
            bool(reference_reasons),
            topic_reason is ReasonCode.SMALL_TALK,
        )
    )
    if not substantive_signal:
        reasons = [ReasonCode.RULE_NO_MATCH]
    confidence = 0.0
    if topic_reason is ReasonCode.SMALL_TALK:
        confidence = 0.99
    elif substantive_signal:
        confidence = 0.98 if not (location_clarification or time_clarification or reference_clarification) else 0.75
    return ConversationDimensions(
        topic=topic,
        location=location,
        time_range=time_range,
        filters=filters,
        references=references,
        confidence=confidence,
        reason_codes=tuple(reasons),
        decision_source=DecisionSource.RULE,
        requires_clarification=(
            location_clarification or time_clarification or reference_clarification
        ),
    )


def _result_has_rule_signal(result: ConversationDimensions) -> bool:
    return result.reason_codes != (ReasonCode.RULE_NO_MATCH,)


def _remaining_timeout() -> float:
    try:
        from request_budget import remaining_seconds  # type: ignore

        remaining = remaining_seconds(floor=0.0)
    except Exception:
        remaining = LLM_FALLBACK_TIMEOUT_SECONDS
    return max(0.0, min(LLM_FALLBACK_TIMEOUT_SECONDS, remaining - 0.25))


def _llm_prompt(message: str, context: DimensionContext) -> str:
    payload = {
        "current_message": str(message or "")[:MAX_MESSAGE_CHARS],
        "trusted_context": context.to_llm_dict(),
        "allowed_topics": [item.value for item in CanonicalTopic],
        "allowed_location_ids": list(_GEOGRAPHY_BY_ID),
        "timezone": NEW_YORK_TIMEZONE,
        "required_schema": sorted(_RESULT_KEYS),
    }
    prompt = (
        "Return exactly one JSON object with the required schema. Do not answer the resident. "
        "Use only allowed enum values and server-owned location IDs. shadow must be true and "
        "decision_source must be llm. Input: "
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    )
    if len(prompt.encode("utf-8")) > MAX_LLM_PROMPT_BYTES:
        raise ValueError("dimension classifier prompt exceeds its size limit")
    return prompt


def _parse_llm_result(raw: Any) -> ConversationDimensions:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_LLM_OUTPUT_BYTES:
        raise ValueError("dimension classifier output exceeds its size limit")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("dimension classifier output must be an object")
    result = ConversationDimensions.from_mapping(parsed)
    if result.decision_source is not DecisionSource.LLM:
        raise ValueError("LLM dimension decision_source must be llm")
    result.to_dict()
    return result


def _run_llm_fallback(
    prompt: str,
    classifier: Callable[[str, float], str],
) -> ConversationDimensions:
    timeout = _remaining_timeout()
    if timeout <= 0.0:
        return conservative_unknown(ReasonCode.CLASSIFIER_TIMEOUT)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dimension-shadow")
    future = executor.submit(classifier, prompt, timeout)
    try:
        raw = future.result(timeout=timeout)
        return _parse_llm_result(raw)
    except (FutureTimeoutError, TimeoutError):
        future.cancel()
        return conservative_unknown(ReasonCode.CLASSIFIER_TIMEOUT)
    except Exception:
        future.cancel()
        return conservative_unknown(ReasonCode.CLASSIFIER_OUTPUT_INVALID)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def extract_conversation_dimensions(
    message: str,
    *,
    enabled: bool = True,
    context: Optional[DimensionContext] = None,
    now: Optional[datetime] = None,
    llm_fallback_enabled: bool = False,
    llm_classifier: Optional[Callable[[str, float], str]] = None,
    resolve_yearless_named_dates: bool = False,
) -> Optional[ConversationDimensions]:
    """Return diagnostic dimensions without mutating any behavioral input."""
    if not enabled:
        return None
    trusted = context or DimensionContext()
    try:
        rules = extract_with_rules(
            message,
            context=trusted,
            now=now,
            resolve_yearless_named_dates=resolve_yearless_named_dates,
        )
        if _result_has_rule_signal(rules):
            rules.to_dict()
            return rules
        if not llm_fallback_enabled or llm_classifier is None:
            return rules
        return _run_llm_fallback(_llm_prompt(message, trusted), llm_classifier)
    except Exception:
        return conservative_unknown(ReasonCode.CLASSIFIER_FAILURE)


def diagnostic_metrics(result: ConversationDimensions) -> Dict[str, Any]:
    """Return bounded developer metrics without resident text or source content."""
    return {
        "canonical_topic": result.topic.value,
        "canonical_location_id": result.location.id if result.location else None,
        "time_label_code": (
            result.time_range.label_code.value if result.time_range else TimeLabelCode.UNKNOWN.value
        ),
        "cost_filter": result.filters.cost.value,
        "modality_filter": result.filters.modality.value,
        "decision_source": result.decision_source.value,
        "requires_clarification": result.requires_clarification,
    }
