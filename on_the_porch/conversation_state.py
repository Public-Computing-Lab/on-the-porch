"""Versioned, bounded conversation state stored beside retrieval cache data.

Three version concepts are intentionally separate:

* ``ThreadStateEnvelope.version`` is the outer storage-envelope format.
* ``ConversationState.schema_version`` is the semantic-state schema format.
* ``ConversationState.revision`` is the optimistic concurrency revision.

Persisted forms migrate in memory as follows. Loading never writes storage:

* legacy top-level retrieval cache -> envelope v1 + empty state v2;
* envelope v1 without conversation state -> empty state v2;
* envelope v1 + legacy ``state_version`` state -> state v2, preserving the
  ``state_version`` value as ``revision`` and preserving any active event;
* envelope v1 + state v2 -> validated state v2.

Malformed optional v2 dimensions are dropped rather than trusted during persisted
state migration. Structural violations, unknown keys, malformed JSON, and invalid
active entities still fail closed. Strict callers can use
``validate_conversation_state_v2`` to reject any invalid optional dimension.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, Mapping, Optional

from conversation_dimensions import (
    CanonicalFilters,
    CanonicalLocation,
    CanonicalTimeRange,
    CanonicalTopic,
    CostFilter,
    ModalityFilter,
    RegistrationFilter,
    TimeGranularity,
    TimeLabelCode,
    TimeOfDayFilter,
)


ENVELOPE_VERSION = 1
CONVERSATION_STATE_SCHEMA_VERSION = 2
MAX_THREAD_STATE_BYTES = 1_048_576
MAX_CONVERSATION_STATE_BYTES = 8_192
MAX_TIME_RANGE_DAYS = 366
MAX_MESSAGE_ID_CHARS = 128

_ENVELOPE_KEYS = frozenset({"version", "retrieval_cache", "conversation_state"})
_ENVELOPE_REQUIRED_KEYS = frozenset({"version", "retrieval_cache"})
_V1_CONVERSATION_STATE_KEYS = frozenset(
    {
        "state_version",
        "active_topic",
        "active_entity",
        "previous_route",
        "last_substantive_message_id",
    }
)
_V2_CONVERSATION_STATE_KEYS = frozenset(
    {
        "schema_version",
        "revision",
        "active_topic",
        "active_entity",
        "location",
        "time_range",
        "filters",
        "previous_route",
        "last_substantive_message_id",
    }
)
_ACTIVE_ENTITY_KEYS = frozenset({"type", "id", "source_ref"})
_FILTER_KEYS = frozenset({"cost", "time_of_day", "modality", "registration"})
_PERSISTED_TOPICS = frozenset(topic for topic in CanonicalTopic if topic is not CanonicalTopic.UNKNOWN)
_EVENT_ID_PATTERN = re.compile(r"[1-9][0-9]{0,9}")
_MESSAGE_ID_PATTERN = re.compile(r"[A-Za-z0-9-]{1,128}")
_ROUTE_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_TIME_GRANULARITY_BY_LABEL = {
    TimeLabelCode.RIGHT_NOW: TimeGranularity.INSTANT,
    TimeLabelCode.TODAY: TimeGranularity.DAY,
    TimeLabelCode.TOMORROW: TimeGranularity.DAY,
    TimeLabelCode.THIS_WEEK: TimeGranularity.WEEK,
    TimeLabelCode.LAST_WEEK: TimeGranularity.WEEK,
    TimeLabelCode.THIS_WEEKEND: TimeGranularity.RANGE,
    TimeLabelCode.NEXT_WEEKEND: TimeGranularity.RANGE,
    TimeLabelCode.THIS_MONTH: TimeGranularity.MONTH,
    TimeLabelCode.LAST_MONTH: TimeGranularity.MONTH,
    TimeLabelCode.ROLLING_7_DAYS: TimeGranularity.RANGE,
    TimeLabelCode.ROLLING_30_DAYS: TimeGranularity.RANGE,
    TimeLabelCode.EXPLICIT_DATE: TimeGranularity.DAY,
    TimeLabelCode.EXPLICIT_RANGE: TimeGranularity.RANGE,
}


class ConversationStateError(ValueError):
    """Raised when persisted state violates the bounded contract."""


class ThreadStateForm(str, Enum):
    EMPTY = "empty"
    LEGACY_RETRIEVAL_CACHE = "legacy_retrieval_cache"
    ENVELOPE_V1_RETRIEVAL_ONLY = "envelope_v1_retrieval_only"
    ENVELOPE_V1_STATE_V1 = "envelope_v1_state_v1"
    ENVELOPE_V1_STATE_V2 = "envelope_v1_state_v2"


class TransitionAction(str, Enum):
    PRESERVE = "preserve"
    ACTIVATE_EVENT = "activate_event"
    CLEAR_EVENT = "clear_event"
    REPLACE_STATE = "replace_state"


@dataclass(frozen=True)
class ActiveEntity:
    entity_type: str
    entity_id: str
    source_ref: str

    @classmethod
    def event(cls, event_id: Any) -> "ActiveEntity":
        canonical_id = _canonical_event_id(event_id)
        return cls("event", canonical_id, f"event:{canonical_id}")

    @classmethod
    def from_mapping(cls, raw: Any) -> Optional["ActiveEntity"]:
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or set(raw) != _ACTIVE_ENTITY_KEYS:
            raise ConversationStateError("active_entity has an invalid schema")
        if raw.get("type") != "event":
            raise ConversationStateError("unsupported active entity type")
        entity = cls.event(raw.get("id"))
        if raw.get("source_ref") != entity.source_ref:
            raise ConversationStateError("active_entity source_ref does not match its ID")
        return entity

    def to_dict(self) -> Dict[str, str]:
        return {
            "type": self.entity_type,
            "id": self.entity_id,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True, init=False)
class ConversationState:
    schema_version: int
    revision: int
    active_topic: Optional[CanonicalTopic]
    active_entity: Optional[ActiveEntity]
    location: Optional[CanonicalLocation]
    time_range: Optional[CanonicalTimeRange]
    filters: CanonicalFilters
    previous_route: Optional[str]
    last_substantive_message_id: Optional[str]

    def __init__(
        self,
        *,
        schema_version: int = CONVERSATION_STATE_SCHEMA_VERSION,
        revision: Optional[int] = None,
        state_version: Optional[int] = None,
        active_topic: Optional[Any] = None,
        active_entity: Optional[ActiveEntity] = None,
        location: Optional[Any] = None,
        time_range: Optional[Any] = None,
        filters: Optional[Any] = None,
        previous_route: Optional[str] = None,
        last_substantive_message_id: Optional[str] = None,
    ) -> None:
        if revision is not None and state_version is not None and revision != state_version:
            raise ConversationStateError("revision and legacy state_version disagree")
        resolved_revision = 0 if revision is None and state_version is None else (
            revision if revision is not None else state_version
        )
        resolved_topic = _normalize_constructor_topic(active_topic)
        resolved_location = _coerce_location(location)
        resolved_time = _coerce_time_range(time_range)
        resolved_filters = _coerce_filters(filters)

        _validate_schema_version(schema_version)
        _validate_revision(resolved_revision)
        if active_entity is not None and not isinstance(active_entity, ActiveEntity):
            raise ConversationStateError("active_entity must be authoritative")
        if active_entity is not None and resolved_topic not in (None, CanonicalTopic.EVENTS):
            raise ConversationStateError("an active event requires the events topic")
        if active_entity is not None and resolved_topic is None:
            resolved_topic = CanonicalTopic.EVENTS
        _validate_topic_filters(resolved_topic, resolved_filters)

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "revision", int(resolved_revision))
        object.__setattr__(self, "active_topic", resolved_topic)
        object.__setattr__(self, "active_entity", active_entity)
        object.__setattr__(self, "location", resolved_location)
        object.__setattr__(self, "time_range", resolved_time)
        object.__setattr__(self, "filters", resolved_filters)
        object.__setattr__(
            self,
            "previous_route",
            _bounded_optional(previous_route, _ROUTE_PATTERN),
        )
        object.__setattr__(
            self,
            "last_substantive_message_id",
            _bounded_optional(last_substantive_message_id, _MESSAGE_ID_PATTERN),
        )
        _validate_conversation_state_size(self)

    @property
    def state_version(self) -> int:
        """Compatibility reader for callers that used the old revision name."""
        return self.revision

    @classmethod
    def from_mapping(cls, raw: Any) -> "ConversationState":
        """Strictly parse either the legacy v1 schema or canonical v2 schema."""
        if raw is None:
            return safe_empty_state()
        if not isinstance(raw, Mapping):
            raise ConversationStateError("conversation_state must be an object")
        if set(raw) == _V1_CONVERSATION_STATE_KEYS:
            return normalize_version_1_state(raw)
        return validate_conversation_state_v2(raw)

    def to_dict(self) -> Dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "active_topic": self.active_topic.value if self.active_topic else None,
            "active_entity": self.active_entity.to_dict() if self.active_entity else None,
            "location": self.location.to_dict() if self.location else None,
            "time_range": self.time_range.to_dict() if self.time_range else None,
            "filters": _serialize_filters(self.filters),
            "previous_route": self.previous_route,
            "last_substantive_message_id": self.last_substantive_message_id,
        }
        _validate_state_mapping_size(value)
        return value


@dataclass(frozen=True)
class ThreadStateEnvelope:
    retrieval_cache: Dict[str, Any]
    conversation_state: ConversationState = field(default_factory=ConversationState)
    version: int = ENVELOPE_VERSION
    source_form: ThreadStateForm = field(
        default=ThreadStateForm.ENVELOPE_V1_STATE_V2,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.version != ENVELOPE_VERSION:
            raise ConversationStateError("unsupported thread state envelope version")
        if not isinstance(self.retrieval_cache, Mapping):
            raise ConversationStateError("retrieval_cache must be an object")
        if not isinstance(self.conversation_state, ConversationState):
            raise ConversationStateError("conversation_state must be canonical")
        object.__setattr__(self, "retrieval_cache", copy.deepcopy(dict(self.retrieval_cache)))

    @property
    def is_legacy(self) -> bool:
        return self.source_form is not ThreadStateForm.ENVELOPE_V1_STATE_V2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "retrieval_cache": copy.deepcopy(self.retrieval_cache),
            "conversation_state": self.conversation_state.to_dict(),
        }


@dataclass(frozen=True)
class ProposedTransition:
    action: TransitionAction
    expected_revision: int
    event_id: Optional[str] = None
    previous_route: Optional[str] = None
    next_state: Optional[ConversationState] = None

    @property
    def expected_state_version(self) -> int:
        """Compatibility reader for the former optimistic-revision field name."""
        return self.expected_revision

    @classmethod
    def preserve(cls, state: ConversationState) -> "ProposedTransition":
        return cls(TransitionAction.PRESERVE, state.revision)

    @classmethod
    def activate_event(
        cls,
        state: ConversationState,
        event_id: Any,
        *,
        previous_route: str = "sql",
    ) -> "ProposedTransition":
        return cls(
            TransitionAction.ACTIVATE_EVENT,
            state.revision,
            event_id=_canonical_event_id(event_id),
            previous_route=_bounded_optional(previous_route, _ROUTE_PATTERN),
        )

    @classmethod
    def clear_event(
        cls,
        state: ConversationState,
        *,
        previous_route: Optional[str],
    ) -> "ProposedTransition":
        return cls(
            TransitionAction.CLEAR_EVENT,
            state.revision,
            previous_route=_bounded_optional(previous_route, _ROUTE_PATTERN),
        )

    @classmethod
    def replace_state(
        cls,
        state: ConversationState,
        next_state: ConversationState,
    ) -> "ProposedTransition":
        """Replace canonical semantic fields after the final transaction rechecks revision."""
        if not isinstance(next_state, ConversationState):
            raise ConversationStateError("replacement state must be canonical")
        if next_state.revision != state.revision:
            raise ConversationStateError("replacement state must keep the expected revision")
        return cls(
            TransitionAction.REPLACE_STATE,
            state.revision,
            next_state=next_state,
        )


@dataclass(frozen=True)
class CommitMergeResult:
    envelope: ThreadStateEnvelope
    transition_applied: bool
    cache_update_applied: bool
    version_conflict: bool


def _canonical_event_id(value: Any) -> str:
    if isinstance(value, bool):
        raise ConversationStateError("invalid event ID")
    text = str(value or "")
    if not _EVENT_ID_PATTERN.fullmatch(text):
        raise ConversationStateError("invalid event ID")
    parsed = int(text)
    if parsed > 2_147_483_647:
        raise ConversationStateError("invalid event ID")
    return str(parsed)


def _bounded_optional(value: Any, pattern: re.Pattern[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ConversationStateError("invalid bounded state value")
    return value


def _validate_schema_version(value: Any) -> None:
    if value != CONVERSATION_STATE_SCHEMA_VERSION:
        raise ConversationStateError("unsupported conversation state schema version")


def _validate_revision(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
        raise ConversationStateError("invalid conversation state revision")


def _normalize_constructor_topic(value: Any) -> Optional[CanonicalTopic]:
    if value is None:
        return None
    if value == "events":
        return CanonicalTopic.EVENTS
    try:
        topic = value if isinstance(value, CanonicalTopic) else CanonicalTopic(value)
    except (TypeError, ValueError) as exc:
        raise ConversationStateError("unsupported active topic") from exc
    if topic not in _PERSISTED_TOPICS:
        raise ConversationStateError("UNKNOWN cannot be persisted as an active topic")
    return topic


def _coerce_location(value: Any) -> Optional[CanonicalLocation]:
    if value is None or isinstance(value, CanonicalLocation):
        return value
    if not isinstance(value, Mapping):
        raise ConversationStateError("location must be a canonical object or null")
    try:
        return CanonicalLocation.from_mapping(value)
    except (TypeError, ValueError) as exc:
        raise ConversationStateError("invalid canonical location") from exc


def _validate_time_range(value: CanonicalTimeRange) -> CanonicalTimeRange:
    if value.label_code is TimeLabelCode.UNKNOWN or value.granularity is TimeGranularity.UNKNOWN:
        raise ConversationStateError("unknown time ranges cannot be persisted")
    expected_granularity = _TIME_GRANULARITY_BY_LABEL.get(value.label_code)
    if expected_granularity is None or value.granularity is not expected_granularity:
        raise ConversationStateError("time label and granularity are inconsistent")
    try:
        start = date.fromisoformat(str(value.start_date))
        end = date.fromisoformat(str(value.end_date_exclusive))
    except ValueError as exc:
        raise ConversationStateError("invalid canonical time range") from exc
    if (end - start).days > MAX_TIME_RANGE_DAYS:
        raise ConversationStateError("canonical time range is too large")
    return value


def _coerce_time_range(value: Any) -> Optional[CanonicalTimeRange]:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, CanonicalTimeRange) else CanonicalTimeRange.from_mapping(value)
        return _validate_time_range(parsed)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ConversationStateError):
            raise
        raise ConversationStateError("invalid canonical time range") from exc


def _coerce_filters(value: Any) -> CanonicalFilters:
    if value is None:
        return CanonicalFilters()
    if isinstance(value, CanonicalFilters):
        return value
    if not isinstance(value, Mapping) or not set(value).issubset(_FILTER_KEYS):
        raise ConversationStateError("filters have an invalid schema")
    merged = {
        "cost": CostFilter.ANY.value,
        "time_of_day": TimeOfDayFilter.ANY.value,
        "modality": ModalityFilter.ANY.value,
        "registration": RegistrationFilter.ANY.value,
        **dict(value),
    }
    try:
        return CanonicalFilters.from_mapping(merged)
    except (TypeError, ValueError) as exc:
        raise ConversationStateError("invalid canonical filters") from exc


def _serialize_filters(filters: CanonicalFilters) -> Dict[str, str]:
    values = filters.to_dict()
    return {key: value for key, value in values.items() if value != "ANY"}


def _filters_are_empty(filters: CanonicalFilters) -> bool:
    return not _serialize_filters(filters)


def _validate_topic_filters(
    topic: Optional[CanonicalTopic],
    filters: CanonicalFilters,
) -> None:
    if topic is not CanonicalTopic.EVENTS and not _filters_are_empty(filters):
        raise ConversationStateError("event filters require the events topic")


def _validate_state_mapping_size(value: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_CONVERSATION_STATE_BYTES:
        raise ConversationStateError("conversation state exceeds the size limit")


def _validate_conversation_state_size(state: ConversationState) -> None:
    value = {
        "schema_version": state.schema_version,
        "revision": state.revision,
        "active_topic": state.active_topic.value if state.active_topic else None,
        "active_entity": state.active_entity.to_dict() if state.active_entity else None,
        "location": state.location.to_dict() if state.location else None,
        "time_range": state.time_range.to_dict() if state.time_range else None,
        "filters": _serialize_filters(state.filters),
        "previous_route": state.previous_route,
        "last_substantive_message_id": state.last_substantive_message_id,
    }
    _validate_state_mapping_size(value)


def safe_empty_state(*, revision: int = 0) -> ConversationState:
    """Return a new canonical empty state."""
    return ConversationState(revision=revision)


def normalize_version_1_state(raw: Mapping[str, Any]) -> ConversationState:
    """Migrate the exact legacy state schema into canonical v2 in memory."""
    if set(raw) != _V1_CONVERSATION_STATE_KEYS:
        raise ConversationStateError("version-1 conversation_state has an invalid schema")
    revision = raw.get("state_version")
    _validate_revision(revision)
    active_topic = raw.get("active_topic")
    if active_topic not in (None, "events"):
        active_topic = None
    active_entity = ActiveEntity.from_mapping(raw.get("active_entity"))
    if active_entity is not None:
        active_topic = CanonicalTopic.EVENTS
    elif active_topic == "events":
        active_topic = None
    return ConversationState(
        revision=revision,
        active_topic=active_topic,
        active_entity=active_entity,
        previous_route=_bounded_optional(raw.get("previous_route"), _ROUTE_PATTERN),
        last_substantive_message_id=_bounded_optional(
            raw.get("last_substantive_message_id"),
            _MESSAGE_ID_PATTERN,
        ),
    )


def validate_conversation_state_v2(raw: Any) -> ConversationState:
    """Strictly validate an externally supplied version-2 state mapping."""
    if not isinstance(raw, Mapping) or set(raw) != _V2_CONVERSATION_STATE_KEYS:
        raise ConversationStateError("version-2 conversation_state has an invalid schema")
    _validate_schema_version(raw.get("schema_version"))
    _validate_revision(raw.get("revision"))
    topic = _normalize_constructor_topic(raw.get("active_topic"))
    entity = ActiveEntity.from_mapping(raw.get("active_entity"))
    if entity is not None and topic is not CanonicalTopic.EVENTS:
        raise ConversationStateError("an active event requires the events topic")
    state = ConversationState(
        schema_version=raw["schema_version"],
        revision=raw["revision"],
        active_topic=topic,
        active_entity=entity,
        location=_coerce_location(raw.get("location")),
        time_range=_coerce_time_range(raw.get("time_range")),
        filters=_coerce_filters(raw.get("filters")),
        previous_route=_bounded_optional(raw.get("previous_route"), _ROUTE_PATTERN),
        last_substantive_message_id=_bounded_optional(
            raw.get("last_substantive_message_id"),
            _MESSAGE_ID_PATTERN,
        ),
    )
    _validate_state_mapping_size(raw)
    return state


def migrate_conversation_state(raw: Any) -> ConversationState:
    """Load legacy/current state while dropping malformed optional v2 dimensions."""
    if raw is None:
        return safe_empty_state()
    if not isinstance(raw, Mapping):
        raise ConversationStateError("conversation_state must be an object")
    if set(raw) == _V1_CONVERSATION_STATE_KEYS:
        return normalize_version_1_state(raw)
    if set(raw) != _V2_CONVERSATION_STATE_KEYS:
        raise ConversationStateError("conversation_state has an invalid schema")

    _validate_schema_version(raw.get("schema_version"))
    _validate_revision(raw.get("revision"))
    entity = ActiveEntity.from_mapping(raw.get("active_entity"))
    try:
        topic = _normalize_constructor_topic(raw.get("active_topic"))
    except ConversationStateError:
        topic = None
    if entity is not None:
        topic = CanonicalTopic.EVENTS

    try:
        location = _coerce_location(raw.get("location"))
    except ConversationStateError:
        location = None
    try:
        time_range = _coerce_time_range(raw.get("time_range"))
    except ConversationStateError:
        time_range = None
    try:
        filters = _coerce_filters(raw.get("filters"))
        _validate_topic_filters(topic, filters)
    except ConversationStateError:
        filters = CanonicalFilters()

    return ConversationState(
        schema_version=raw["schema_version"],
        revision=raw["revision"],
        active_topic=topic,
        active_entity=entity,
        location=location,
        time_range=time_range,
        filters=filters,
        previous_route=_bounded_optional(raw.get("previous_route"), _ROUTE_PATTERN),
        last_substantive_message_id=_bounded_optional(
            raw.get("last_substantive_message_id"),
            _MESSAGE_ID_PATTERN,
        ),
    )


def _decode(raw: Any) -> Dict[str, Any]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, Mapping):
        parsed = dict(raw)
    elif isinstance(raw, (str, bytes, bytearray)):
        encoded = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        if len(encoded) > MAX_THREAD_STATE_BYTES:
            raise ConversationStateError("thread state exceeds the size limit")
        try:
            value = json.loads(encoded.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ConversationStateError("thread state is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ConversationStateError("thread state must be an object")
        parsed = value
    else:
        raise ConversationStateError("thread state has an invalid type")
    if len(json.dumps(parsed, ensure_ascii=True, default=str).encode("utf-8")) > MAX_THREAD_STATE_BYTES:
        raise ConversationStateError("thread state exceeds the size limit")
    return parsed


def detect_thread_state_form(raw: Any) -> ThreadStateForm:
    parsed = _decode(raw)
    if not parsed:
        return ThreadStateForm.EMPTY
    if "retrieval_cache" not in parsed and "conversation_state" not in parsed:
        return ThreadStateForm.LEGACY_RETRIEVAL_CACHE
    if not _ENVELOPE_REQUIRED_KEYS.issubset(parsed) or not set(parsed).issubset(_ENVELOPE_KEYS):
        raise ConversationStateError("thread state envelope has an invalid schema")
    if parsed.get("version") != ENVELOPE_VERSION:
        raise ConversationStateError("thread state envelope has an invalid version")
    if "conversation_state" not in parsed or parsed.get("conversation_state") is None:
        return ThreadStateForm.ENVELOPE_V1_RETRIEVAL_ONLY
    state = parsed.get("conversation_state")
    if not isinstance(state, Mapping):
        raise ConversationStateError("conversation_state must be an object")
    if set(state) == _V1_CONVERSATION_STATE_KEYS:
        return ThreadStateForm.ENVELOPE_V1_STATE_V1
    if set(state) == _V2_CONVERSATION_STATE_KEYS:
        return ThreadStateForm.ENVELOPE_V1_STATE_V2
    raise ConversationStateError("conversation_state has an invalid schema")


def load_thread_state(
    raw: Any,
    *,
    empty_retrieval_cache: Optional[Dict[str, Any]] = None,
) -> ThreadStateEnvelope:
    """Read any supported persisted form without writing or sharing mutable data."""
    parsed = _decode(raw)
    form = detect_thread_state_form(parsed)
    if form is ThreadStateForm.EMPTY:
        return ThreadStateEnvelope(
            copy.deepcopy(empty_retrieval_cache or {}),
            source_form=form,
        )
    if form is ThreadStateForm.LEGACY_RETRIEVAL_CACHE:
        return ThreadStateEnvelope(copy.deepcopy(parsed), source_form=form)

    cache = parsed.get("retrieval_cache")
    if not isinstance(cache, dict):
        raise ConversationStateError("retrieval_cache must be an object")
    state = migrate_conversation_state(parsed.get("conversation_state"))
    return ThreadStateEnvelope(copy.deepcopy(cache), state, source_form=form)


def extract_retrieval_cache(envelope: ThreadStateEnvelope) -> Dict[str, Any]:
    return copy.deepcopy(envelope.retrieval_cache)


def extract_conversation_state(envelope: ThreadStateEnvelope) -> ConversationState:
    return envelope.conversation_state


def with_retrieval_cache(
    envelope: ThreadStateEnvelope,
    retrieval_cache: Mapping[str, Any],
) -> ThreadStateEnvelope:
    if not isinstance(retrieval_cache, Mapping):
        raise ConversationStateError("retrieval_cache must be an object")
    return ThreadStateEnvelope(
        copy.deepcopy(dict(retrieval_cache)),
        envelope.conversation_state,
        envelope.version,
        envelope.source_form,
    )


def with_conversation_state(
    envelope: ThreadStateEnvelope,
    state: ConversationState,
) -> ThreadStateEnvelope:
    if not isinstance(state, ConversationState):
        raise ConversationStateError("conversation_state must be canonical")
    return ThreadStateEnvelope(
        envelope.retrieval_cache,
        state,
        envelope.version,
        ThreadStateForm.ENVELOPE_V1_STATE_V2,
    )


def increment_revision(state: ConversationState) -> int:
    if state.revision >= 2_147_483_647:
        raise ConversationStateError("conversation state revision is exhausted")
    return state.revision + 1


def apply_transition(
    state: ConversationState,
    proposal: ProposedTransition,
    *,
    last_substantive_message_id: Optional[str],
) -> ConversationState:
    if state.revision != proposal.expected_revision:
        raise ConversationStateError("conversation state revision conflict")
    if proposal.action is TransitionAction.PRESERVE:
        return state
    message_id = _bounded_optional(last_substantive_message_id, _MESSAGE_ID_PATTERN)
    if proposal.action is TransitionAction.ACTIVATE_EVENT:
        entity = ActiveEntity.event(proposal.event_id)
        return ConversationState(
            revision=increment_revision(state),
            active_topic=CanonicalTopic.EVENTS,
            active_entity=entity,
            location=state.location,
            time_range=state.time_range,
            filters=state.filters,
            previous_route=proposal.previous_route or "sql",
            last_substantive_message_id=message_id,
        )
    if proposal.action is TransitionAction.CLEAR_EVENT:
        if state.active_entity is None:
            return state
        return ConversationState(
            revision=increment_revision(state),
            active_topic=None,
            active_entity=None,
            location=state.location,
            time_range=state.time_range,
            filters=CanonicalFilters(),
            previous_route=proposal.previous_route,
            last_substantive_message_id=message_id,
        )
    if proposal.action is TransitionAction.REPLACE_STATE:
        replacement = proposal.next_state
        if replacement is None or replacement.revision != state.revision:
            raise ConversationStateError("invalid canonical replacement state")
        if replacement == state:
            return state
        return ConversationState(
            revision=increment_revision(state),
            active_topic=replacement.active_topic,
            active_entity=replacement.active_entity,
            location=replacement.location,
            time_range=replacement.time_range,
            filters=replacement.filters,
            previous_route=replacement.previous_route,
            last_substantive_message_id=message_id,
        )
    raise ConversationStateError("unsupported conversation state transition")


def serialize_thread_state(envelope: ThreadStateEnvelope) -> str:
    """Serialize deterministically with independent semantic/envelope size limits."""
    state = envelope.conversation_state.to_dict()
    _validate_state_mapping_size(state)
    encoded = json.dumps(
        {
            "version": envelope.version,
            "retrieval_cache": copy.deepcopy(envelope.retrieval_cache),
            "conversation_state": state,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if len(encoded.encode("utf-8")) > MAX_THREAD_STATE_BYTES:
        raise ConversationStateError("thread state exceeds the size limit")
    return encoded


def retrieval_cache_hash(retrieval_cache: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(retrieval_cache),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def response_allows_state_transition(agent_result: Mapping[str, Any]) -> bool:
    """Reject explicit timeout/dependency failures before durable state changes."""
    if not isinstance(agent_result, Mapping):
        return False
    if (
        agent_result.get("mode") == "budget_timeout"
        or agent_result.get("execution_degraded") is True
    ):
        return False
    result = agent_result.get("result")
    if not isinstance(result, Mapping):
        return True
    if result.get("error") or result.get("timed_out") is True:
        return False
    parts = result.get("parts")
    if isinstance(parts, list) and any(
        isinstance(part, Mapping) and (part.get("error") or part.get("timed_out") is True)
        for part in parts
    ):
        return False
    for path in ("sql", "rag"):
        part = result.get(path)
        if isinstance(part, Mapping) and (part.get("error") or part.get("timed_out") is True):
            return False
    return True


def merge_for_commit(
    current: ThreadStateEnvelope,
    expected: ThreadStateEnvelope,
    *,
    next_retrieval_cache: Mapping[str, Any],
    transition: ProposedTransition,
    last_substantive_message_id: str,
    force_cache_update: bool = False,
) -> CommitMergeResult:
    """Merge one request result without overwriting newer state or cache data."""
    current_state = current.conversation_state
    if current_state.revision != transition.expected_revision:
        return CommitMergeResult(
            envelope=current,
            transition_applied=False,
            cache_update_applied=False,
            version_conflict=True,
        )
    cache_matches = (
        retrieval_cache_hash(current.retrieval_cache)
        == retrieval_cache_hash(expected.retrieval_cache)
    )
    envelope = current
    if cache_matches or force_cache_update:
        envelope = with_retrieval_cache(envelope, next_retrieval_cache)
    next_state = apply_transition(
        current_state,
        transition,
        last_substantive_message_id=last_substantive_message_id,
    )
    envelope = with_conversation_state(envelope, next_state)
    return CommitMergeResult(
        envelope=envelope,
        transition_applied=transition.action is not TransitionAction.PRESERVE,
        cache_update_applied=cache_matches or force_cache_update,
        version_conflict=False,
    )
