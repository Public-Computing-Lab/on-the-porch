"""Deterministic, bounded conversation-state transition planning.

This module accepts canonical server-owned values only. Relationship and dimension
classifiers remain shadow producers; only rule-resolved signals may authorize durable
changes. The transition is applied later by the lock-first persistence transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from conversation_dimensions import (
    CanonicalFilters,
    CanonicalTopic,
    ConversationDimensions,
    CostFilter,
    DecisionSource as DimensionDecisionSource,
    ModalityFilter,
    ReasonCode as DimensionReasonCode,
    RegistrationFilter,
    TimeOfDayFilter,
)
from conversation_relationship import (
    ConversationRelationship,
    DecisionSource as RelationshipDecisionSource,
    ReasonCode as RelationshipReasonCode,
    RelationshipDecision,
)
from conversation_state import ActiveEntity, ConversationState, ProposedTransition


NEW_YORK = ZoneInfo("America/New_York")
MAX_DIAGNOSTIC_BYTES = 2048


class TransitionAction(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    UPDATE = "UPDATE"
    CLEAR = "CLEAR"
    REPLACE = "REPLACE"


class CacheAction(str, Enum):
    PRESERVE = "PRESERVE"
    INVALIDATE = "INVALIDATE"


class CompletionStatus(str, Enum):
    ACCEPTABLE = "ACCEPTABLE"
    PARTIAL = "PARTIAL"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    CONFLICTED = "CONFLICTED"


class StateField(str, Enum):
    ACTIVE_TOPIC = "active_topic"
    ACTIVE_ENTITY = "active_entity"
    LOCATION = "location"
    TIME_RANGE = "time_range"
    FILTERS = "filters"
    PREVIOUS_ROUTE = "previous_route"


class TransitionReason(str, Enum):
    SMALL_TALK_PRESERVED = "small_talk_preserved"
    EMERGENCY_CONTEXT_PRESERVED = "emergency_context_preserved"
    EQUIVALENT_CONTEXT = "equivalent_normalized_context"
    EXPLICIT_TOPIC = "explicit_topic"
    EXPLICIT_LOCATION = "explicit_location"
    EXPLICIT_TIME = "explicit_time"
    EXPLICIT_FILTERS = "explicit_filters"
    AUTHORITATIVE_ENTITY = "authoritative_entity"
    AUTHORITATIVE_ENTITY_REPLACED = "authoritative_entity_replaced"
    STALE_ENTITY_CLEARED = "stale_entity_cleared"
    ACTIVE_ENTITY_FOLLOW_UP = "active_entity_follow_up"
    LOCATION_REFERENCE_RETAINED = "location_reference_retained"
    TIME_REFERENCE_RETAINED = "time_reference_retained"
    MISSING_REFERENCE = "missing_reference"
    RESULT_REFERENCE_NOT_PERSISTED = "result_reference_not_persisted"
    NEW_TOPIC_REPLACED = "new_topic_replaced"
    REFINEMENT_APPLIED = "refinement_applied"
    CORRECTION_APPLIED = "correction_applied"
    UNRESOLVED_CORRECTION = "unresolved_correction"
    UNTRUSTED_CLASSIFIER = "untrusted_classifier"
    AMBIGUOUS_DIMENSION = "ambiguous_dimension"
    UNKNOWN_TOPIC_IGNORED = "unknown_topic_ignored"
    UNSUPPORTED_FILTER_IGNORED = "unsupported_filter_ignored"
    COMPLETION_NOT_ACCEPTABLE = "completion_not_acceptable"
    ROUTE_INCOMPATIBLE = "route_incompatible"
    REFERENCE_NOT_APPLIED_TO_ROUTE = "reference_not_applied_to_route"
    FACTUAL_DIMENSION_CHANGED = "factual_dimension_changed"
    ROUTE_FAMILY_CHANGED = "route_family_changed"


_FIELD_ORDER = tuple(StateField)
_EXPLICIT_TOPIC_REASONS = frozenset(
    {
        DimensionReasonCode.EXPLICIT_EVENTS_TOPIC,
        DimensionReasonCode.EXPLICIT_CITY_SERVICES_TOPIC,
        DimensionReasonCode.EXPLICIT_CRIME_FACTS_TOPIC,
        DimensionReasonCode.EXPLICIT_PUBLIC_SAFETY_TOPIC,
        DimensionReasonCode.EXPLICIT_COMMUNITY_OPINION_TOPIC,
        DimensionReasonCode.EXPLICIT_MEETINGS_TOPIC,
        DimensionReasonCode.EXPLICIT_LOCAL_NEWS_TOPIC,
        DimensionReasonCode.EXPLICIT_NEIGHBORHOOD_INFORMATION_TOPIC,
    }
)
_EXPLICIT_LOCATION_REASONS = frozenset(
    {
        DimensionReasonCode.EXPLICIT_DORCHESTER_LOCATION,
        DimensionReasonCode.EXPLICIT_CODMAN_SQUARE_LOCATION,
        DimensionReasonCode.EXPLICIT_FIELDS_CORNER_LOCATION,
    }
)
_INVALID_LOCATION_REASONS = frozenset(
    {DimensionReasonCode.AMBIGUOUS_LOCATION, DimensionReasonCode.UNKNOWN_LOCATION}
)
_EXPLICIT_TIME_REASONS = frozenset(
    {
        DimensionReasonCode.EXPLICIT_RIGHT_NOW,
        DimensionReasonCode.EXPLICIT_TODAY,
        DimensionReasonCode.EXPLICIT_TOMORROW,
        DimensionReasonCode.EXPLICIT_THIS_WEEK,
        DimensionReasonCode.EXPLICIT_LAST_WEEK,
        DimensionReasonCode.EXPLICIT_THIS_WEEKEND,
        DimensionReasonCode.EXPLICIT_NEXT_WEEKEND,
        DimensionReasonCode.EXPLICIT_THIS_MONTH,
        DimensionReasonCode.EXPLICIT_LAST_MONTH,
        DimensionReasonCode.EXPLICIT_ROLLING_7_DAYS,
        DimensionReasonCode.EXPLICIT_ROLLING_30_DAYS,
        DimensionReasonCode.EXPLICIT_DATE,
        DimensionReasonCode.EXPLICIT_RANGE,
    }
)
_INVALID_TIME_REASONS = frozenset({DimensionReasonCode.AMBIGUOUS_EXPLICIT_DATE})
_EXPLICIT_FILTER_REASONS = frozenset(
    {
        DimensionReasonCode.EXPLICIT_FREE_FILTER,
        DimensionReasonCode.EXPLICIT_PAID_FILTER,
        DimensionReasonCode.EXPLICIT_UNKNOWN_COST_FILTER,
        DimensionReasonCode.EXPLICIT_MORNING_FILTER,
        DimensionReasonCode.EXPLICIT_AFTERNOON_FILTER,
        DimensionReasonCode.EXPLICIT_EVENING_FILTER,
        DimensionReasonCode.EXPLICIT_VIRTUAL_FILTER,
        DimensionReasonCode.EXPLICIT_IN_PERSON_FILTER,
        DimensionReasonCode.EXPLICIT_HYBRID_FILTER,
        DimensionReasonCode.EXPLICIT_REGISTRATION_REQUIRED_FILTER,
        DimensionReasonCode.EXPLICIT_REGISTRATION_NOT_REQUIRED_FILTER,
        DimensionReasonCode.EXPLICIT_REGISTRATION_UNKNOWN_FILTER,
    }
)
_TOPIC_BY_ROUTER_VALUE = {
    "events": CanonicalTopic.EVENTS,
    "311": CanonicalTopic.CITY_SERVICES,
    "crime": CanonicalTopic.CRIME_FACTS,
    "safety": CanonicalTopic.PUBLIC_SAFETY,
    "opinion": CanonicalTopic.COMMUNITY_OPINION,
    "community_note": CanonicalTopic.COMMUNITY_OPINION,
    "transcript": CanonicalTopic.MEETINGS,
    "news": CanonicalTopic.LOCAL_NEWS,
    "policy": CanonicalTopic.NEIGHBORHOOD_INFORMATION,
}
_ROUTE_COMPATIBILITY = {
    CanonicalTopic.EVENTS: frozenset({"sql", "rag", "hybrid", "structured_events"}),
    CanonicalTopic.CITY_SERVICES: frozenset({"sql", "rag", "hybrid"}),
    CanonicalTopic.CRIME_FACTS: frozenset({"sql", "hybrid"}),
    CanonicalTopic.PUBLIC_SAFETY: frozenset({"hybrid"}),
    CanonicalTopic.COMMUNITY_OPINION: frozenset({"rag", "hybrid"}),
    CanonicalTopic.MEETINGS: frozenset({"rag", "hybrid"}),
    CanonicalTopic.LOCAL_NEWS: frozenset({"rag", "hybrid"}),
    CanonicalTopic.NEIGHBORHOOD_INFORMATION: frozenset({"rag", "hybrid"}),
    CanonicalTopic.GENERAL: frozenset({"chitchat", "advisory"}),
}


@dataclass(frozen=True)
class ConversationTransition:
    action: TransitionAction
    relationship: ConversationRelationship
    retained: Tuple[StateField, ...]
    replaced: Tuple[StateField, ...]
    cleared: Tuple[StateField, ...]
    next_state: ConversationState
    cache_action: CacheAction
    reason_codes: Tuple[TransitionReason, ...]
    commit_allowed: bool
    requires_clarification: bool
    completion_status: CompletionStatus
    requested_dimensions: Tuple[StateField, ...] = ()
    applied_dimensions: Tuple[StateField, ...] = ()
    unapplied_dimensions: Tuple[StateField, ...] = ()
    retrieval_matched: Optional[bool] = None

    def __post_init__(self) -> None:
        if len(self.reason_codes) == 0 or len(self.reason_codes) > 12:
            raise ValueError("transition reason_codes must be bounded")
        if any(len(values) > len(StateField) for values in (self.retained, self.replaced, self.cleared)):
            raise ValueError("transition fields must be bounded")
        if set(self.retained) & (set(self.replaced) | set(self.cleared)):
            raise ValueError("transition fields cannot overlap")
        if set(self.replaced) & set(self.cleared):
            raise ValueError("transition fields cannot overlap")
        for values in (
            self.requested_dimensions,
            self.applied_dimensions,
            self.unapplied_dimensions,
        ):
            if len(values) > len(StateField) or len(set(values)) != len(values):
                raise ValueError("transition diagnostic dimensions must be bounded")

    @property
    def state_changed(self) -> bool:
        return self.action is not TransitionAction.NO_CHANGE

    @property
    def will_commit(self) -> bool:
        return self.commit_allowed and (
            self.state_changed or self.cache_action is CacheAction.INVALIDATE
        )

    def state_proposal(self, current: ConversationState) -> ProposedTransition:
        if not self.commit_allowed or not self.state_changed:
            return ProposedTransition.preserve(current)
        return ProposedTransition.replace_state(current, self.next_state)

    def to_diagnostic_dict(self, *, committed: Optional[bool] = None) -> Dict[str, Any]:
        value = {
            "action": self.action.value,
            "relationship": self.relationship.value,
            "cache_action": self.cache_action.value,
            "retained": [item.value for item in self.retained],
            "replaced": [item.value for item in self.replaced],
            "cleared": [item.value for item in self.cleared],
            "reason_codes": [item.value for item in self.reason_codes],
            "commit_allowed": self.commit_allowed,
            "committed": self.will_commit if committed is None else bool(committed),
            "requires_clarification": self.requires_clarification,
            "completion_status": self.completion_status.value,
            "requested_dimensions": [item.value for item in self.requested_dimensions],
            "applied_dimensions": [item.value for item in self.applied_dimensions],
            "unapplied_dimensions": [item.value for item in self.unapplied_dimensions],
            "retrieval_matched": self.retrieval_matched,
        }
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_DIAGNOSTIC_BYTES:
            raise ValueError("transition diagnostics exceed the size limit")
        return value


def with_transition_diagnostics(
    transition: ConversationTransition,
    *,
    requested_dimensions: Tuple[StateField, ...] = (),
    applied_dimensions: Tuple[StateField, ...] = (),
    unapplied_dimensions: Tuple[StateField, ...] = (),
    retrieval_matched: Optional[bool] = None,
) -> ConversationTransition:
    """Attach bounded execution diagnostics without changing transition behavior."""
    return replace(
        transition,
        requested_dimensions=tuple(dict.fromkeys(requested_dimensions)),
        applied_dimensions=tuple(dict.fromkeys(applied_dimensions)),
        unapplied_dimensions=tuple(dict.fromkeys(unapplied_dimensions)),
        retrieval_matched=retrieval_matched,
    )


def route_family(route: Optional[str]) -> Optional[str]:
    value = str(route or "").strip().lower()
    if value in {"sql", "structured", "event_detail", "structured_events"}:
        return "structured"
    if value in {"rag", "document"}:
        return "document"
    if value == "hybrid":
        return "hybrid"
    if value in {"chitchat", "general"}:
        return "general"
    if value == "advisory":
        return "advisory"
    if value == "history":
        return "history"
    if value == "semantic_cache":
        return "cache"
    return value or None


def semantic_signature(state: ConversationState, *, route: Optional[str] = None) -> str:
    """Serialize bounded semantic context for equality only; it is not persisted."""
    value = {
        "topic": state.active_topic.value if state.active_topic else None,
        "entity": state.active_entity.source_ref if state.active_entity else None,
        "location": state.location.id if state.location else None,
        "time": (
            [state.time_range.start_date, state.time_range.end_date_exclusive]
            if state.time_range
            else None
        ),
        "filters": {
            key: value
            for key, value in state.filters.to_dict().items()
            if value != "ANY"
        },
        "route_family": route_family(route if route is not None else state.previous_route),
    }
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def classify_completion_status(
    agent_result: Any,
    *,
    partial: bool = False,
    conflicted: bool = False,
) -> CompletionStatus:
    if conflicted:
        return CompletionStatus.CONFLICTED
    if partial:
        return CompletionStatus.PARTIAL
    if not isinstance(agent_result, Mapping):
        return CompletionStatus.FAILED
    answer = agent_result.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return CompletionStatus.FAILED
    if agent_result.get("mode") == "budget_timeout" or agent_result.get("execution_degraded") is True:
        return CompletionStatus.DEGRADED
    result = agent_result.get("result")
    if result is None:
        return CompletionStatus.ACCEPTABLE
    if not isinstance(result, Mapping):
        return CompletionStatus.FAILED
    if result.get("error") or result.get("timed_out") is True:
        return CompletionStatus.DEGRADED
    parts = result.get("parts")
    if isinstance(parts, list) and any(
        isinstance(part, Mapping) and (part.get("error") or part.get("timed_out") is True)
        for part in parts
    ):
        return CompletionStatus.DEGRADED
    for path in ("sql", "rag"):
        part = result.get(path)
        if isinstance(part, Mapping) and (part.get("error") or part.get("timed_out") is True):
            return CompletionStatus.DEGRADED
    return CompletionStatus.ACCEPTABLE


def _empty_filters() -> CanonicalFilters:
    return CanonicalFilters()


def _has_filters(filters: CanonicalFilters) -> bool:
    return filters != _empty_filters()


def _merge_explicit_filters(
    current: CanonicalFilters,
    incoming: CanonicalFilters,
) -> CanonicalFilters:
    return CanonicalFilters(
        cost=incoming.cost if incoming.cost is not CostFilter.ANY else current.cost,
        time_of_day=(
            incoming.time_of_day
            if incoming.time_of_day is not TimeOfDayFilter.ANY
            else current.time_of_day
        ),
        modality=(
            incoming.modality
            if incoming.modality is not ModalityFilter.ANY
            else current.modality
        ),
        registration=(
            incoming.registration
            if incoming.registration is not RegistrationFilter.ANY
            else current.registration
        ),
    )


def _trusted_relationship(decision: Optional[RelationshipDecision]) -> bool:
    return bool(decision and decision.decision_source is RelationshipDecisionSource.RULE)


def _trusted_dimensions(dimensions: Optional[ConversationDimensions]) -> bool:
    return bool(dimensions and dimensions.decision_source is DimensionDecisionSource.RULE)


def _explicit_topic(
    dimensions: Optional[ConversationDimensions],
    relationship: ConversationRelationship,
    detected_topic: Optional[str],
) -> Optional[CanonicalTopic]:
    if _trusted_dimensions(dimensions):
        reasons = set(dimensions.reason_codes)
        if dimensions.topic is not CanonicalTopic.UNKNOWN and reasons & _EXPLICIT_TOPIC_REASONS:
            return dimensions.topic
        if (
            dimensions.topic is not CanonicalTopic.UNKNOWN
            and DimensionReasonCode.TRUSTED_TOPIC_CONTEXT in reasons
            and relationship in {
                ConversationRelationship.FOLLOW_UP,
                ConversationRelationship.REFINEMENT,
                ConversationRelationship.CORRECTION,
            }
        ):
            return dimensions.topic
    return _TOPIC_BY_ROUTER_VALUE.get(str(detected_topic or "").strip().lower())


def _is_time_fresh(time_range: Any, today: date) -> bool:
    if time_range is None or not time_range.end_date_exclusive:
        return False
    try:
        return date.fromisoformat(time_range.end_date_exclusive) > today
    except ValueError:
        return False


def _route_is_compatible(topic: Optional[CanonicalTopic], completed_route: str) -> bool:
    if topic is None:
        return True
    return str(completed_route or "").strip().lower() in _ROUTE_COMPATIBILITY.get(topic, frozenset())


def _field_value(state: ConversationState, field: StateField) -> Any:
    return {
        StateField.ACTIVE_TOPIC: state.active_topic,
        StateField.ACTIVE_ENTITY: state.active_entity,
        StateField.LOCATION: state.location,
        StateField.TIME_RANGE: state.time_range,
        StateField.FILTERS: state.filters,
        StateField.PREVIOUS_ROUTE: route_family(state.previous_route),
    }[field]


def _is_empty_field(field: StateField, value: Any) -> bool:
    return value is None or (field is StateField.FILTERS and value == _empty_filters())


def _describe_fields(
    current: ConversationState,
    next_state: ConversationState,
) -> Tuple[Tuple[StateField, ...], Tuple[StateField, ...], Tuple[StateField, ...]]:
    retained = []
    replaced = []
    cleared = []
    for field in _FIELD_ORDER:
        before = _field_value(current, field)
        after = _field_value(next_state, field)
        if before == after:
            if not _is_empty_field(field, after):
                retained.append(field)
        elif _is_empty_field(field, after):
            cleared.append(field)
        else:
            replaced.append(field)
    return tuple(retained), tuple(replaced), tuple(cleared)


def _no_change(
    current: ConversationState,
    relationship: ConversationRelationship,
    completion: CompletionStatus,
    reason: TransitionReason,
    *,
    clarification: bool = False,
    commit_allowed: bool = True,
) -> ConversationTransition:
    retained, _, _ = _describe_fields(current, current)
    return ConversationTransition(
        action=TransitionAction.NO_CHANGE,
        relationship=relationship,
        retained=retained,
        replaced=(),
        cleared=(),
        next_state=current,
        cache_action=CacheAction.PRESERVE,
        reason_codes=(reason,),
        commit_allowed=commit_allowed,
        requires_clarification=clarification,
        completion_status=completion,
    )


def plan_conversation_transition(
    *,
    current: ConversationState,
    relationship: Optional[RelationshipDecision],
    dimensions: Optional[ConversationDimensions],
    authoritative_entity: Optional[ActiveEntity],
    detected_topic: Optional[str],
    completed_route: str,
    completion_status: CompletionStatus,
    stale_active_entity: bool = False,
    reference_context_applied: bool = True,
    now: Optional[datetime] = None,
) -> ConversationTransition:
    """Plan one transition without using message prose, answer text, or client state."""
    if not isinstance(current, ConversationState):
        raise TypeError("current state must be canonical")
    if authoritative_entity is not None and not isinstance(authoritative_entity, ActiveEntity):
        raise TypeError("authoritative_entity must be server-resolved")

    relation = (
        relationship.relationship
        if relationship is not None
        else ConversationRelationship.STANDALONE
    )
    if completion_status is not CompletionStatus.ACCEPTABLE:
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.COMPLETION_NOT_ACCEPTABLE,
            commit_allowed=False,
        )
    if relation is ConversationRelationship.SMALL_TALK:
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.SMALL_TALK_PRESERVED,
        )
    if not _trusted_relationship(relationship) and authoritative_entity is None:
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.UNTRUSTED_CLASSIFIER,
            commit_allowed=False,
        )

    if relationship and relationship.requires_clarification:
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.MISSING_REFERENCE,
            clarification=True,
            commit_allowed=False,
        )
    dim_reasons = set(dimensions.reason_codes) if _trusted_dimensions(dimensions) else set()
    if dim_reasons & (_INVALID_LOCATION_REASONS | _INVALID_TIME_REASONS):
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.AMBIGUOUS_DIMENSION,
            clarification=True,
            commit_allowed=False,
        )

    if stale_active_entity:
        next_state = ConversationState(
            revision=current.revision,
            active_topic=None,
            active_entity=None,
            location=current.location,
            time_range=current.time_range,
            filters=_empty_filters(),
            previous_route=completed_route,
            last_substantive_message_id=current.last_substantive_message_id,
        )
        retained, replaced, cleared = _describe_fields(current, next_state)
        return ConversationTransition(
            action=TransitionAction.CLEAR,
            relationship=relation,
            retained=retained,
            replaced=replaced,
            cleared=cleared,
            next_state=next_state,
            cache_action=CacheAction.INVALIDATE,
            reason_codes=(TransitionReason.STALE_ENTITY_CLEARED,),
            commit_allowed=True,
            requires_clarification=False,
            completion_status=completion_status,
        )

    if (
        relationship
        and relationship.reason_code is RelationshipReasonCode.ACTIVE_EVENT_PROPERTY_QUESTION
        and current.active_entity is not None
        and (
            authoritative_entity is None
            or authoritative_entity == current.active_entity
        )
    ):
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.ACTIVE_ENTITY_FOLLOW_UP,
        )

    trusted_dimensions = dimensions if _trusted_dimensions(dimensions) else None
    explicit_topic = _explicit_topic(trusted_dimensions, relation, detected_topic)
    explicit_location = (
        trusted_dimensions.location
        if trusted_dimensions and dim_reasons & _EXPLICIT_LOCATION_REASONS
        else None
    )
    explicit_time = (
        trusted_dimensions.time_range
        if trusted_dimensions and dim_reasons & _EXPLICIT_TIME_REASONS
        else None
    )
    explicit_filters = bool(trusted_dimensions and dim_reasons & _EXPLICIT_FILTER_REASONS)
    refs = trusted_dimensions.references if trusted_dimensions else None
    local_today = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK).date()
    reasons = []
    requires_clarification = False

    if (
        relation in {ConversationRelationship.FOLLOW_UP, ConversationRelationship.REFINEMENT}
        and current.active_topic is None
        and explicit_topic is None
        and authoritative_entity is None
    ):
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.MISSING_REFERENCE,
            clarification=True,
            commit_allowed=False,
        )

    if (
        refs is not None
        and (refs.location_reference is not None or refs.time_reference is not None)
        and not reference_context_applied
    ):
        return _no_change(
            current,
            relation,
            CompletionStatus.DEGRADED,
            TransitionReason.REFERENCE_NOT_APPLIED_TO_ROUTE,
            commit_allowed=False,
        )

    if relation in {ConversationRelationship.STANDALONE, ConversationRelationship.NEW_TOPIC}:
        topic = CanonicalTopic.EVENTS if authoritative_entity else explicit_topic
        if topic is None or topic is CanonicalTopic.UNKNOWN:
            return _no_change(
                current,
                relation,
                completion_status,
                TransitionReason.UNKNOWN_TOPIC_IGNORED,
                commit_allowed=False,
            )
        location = explicit_location
        time_range = explicit_time
        if refs is not None:
            if refs.location_reference is not None and explicit_location is None:
                if current.location is None:
                    requires_clarification = True
                else:
                    location = current.location
                    reasons.append(TransitionReason.LOCATION_REFERENCE_RETAINED)
            if refs.time_reference is not None and explicit_time is None:
                if _is_time_fresh(current.time_range, local_today):
                    time_range = current.time_range
                    reasons.append(TransitionReason.TIME_REFERENCE_RETAINED)
                else:
                    requires_clarification = True
        filters = (
            trusted_dimensions.filters
            if topic is CanonicalTopic.EVENTS and explicit_filters and trusted_dimensions
            else _empty_filters()
        )
        entity = authoritative_entity
        reasons.append(
            TransitionReason.NEW_TOPIC_REPLACED
            if relation is ConversationRelationship.NEW_TOPIC
            else TransitionReason.EXPLICIT_TOPIC
        )
    else:
        topic = current.active_topic or explicit_topic
        location = current.location
        time_range = current.time_range
        filters = current.filters
        entity = current.active_entity

        if explicit_topic is not None and explicit_topic != topic:
            topic = explicit_topic
            entity = None
            filters = _empty_filters()
            reasons.append(TransitionReason.EXPLICIT_TOPIC)
        if explicit_location is not None:
            location = explicit_location
            reasons.append(TransitionReason.EXPLICIT_LOCATION)
        elif refs and refs.location_reference is not None:
            if current.location is None:
                requires_clarification = True
            else:
                location = current.location
                reasons.append(TransitionReason.LOCATION_REFERENCE_RETAINED)
        if explicit_time is not None:
            time_range = explicit_time
            reasons.append(TransitionReason.EXPLICIT_TIME)
        elif refs and refs.time_reference is not None:
            if _is_time_fresh(current.time_range, local_today):
                time_range = current.time_range
                reasons.append(TransitionReason.TIME_REFERENCE_RETAINED)
            else:
                requires_clarification = True
        if explicit_filters and trusted_dimensions:
            if topic is CanonicalTopic.EVENTS:
                filters = _merge_explicit_filters(filters, trusted_dimensions.filters)
                reasons.append(TransitionReason.EXPLICIT_FILTERS)
            else:
                reasons.append(TransitionReason.UNSUPPORTED_FILTER_IGNORED)
        if relation is ConversationRelationship.REFINEMENT:
            reasons.append(TransitionReason.REFINEMENT_APPLIED)
        elif relation is ConversationRelationship.CORRECTION:
            reasons.append(TransitionReason.CORRECTION_APPLIED)

    if refs and refs.result_reference is not None:
        reasons.append(TransitionReason.RESULT_REFERENCE_NOT_PERSISTED)
        if refs.requires_prior_result:
            requires_clarification = requires_clarification or trusted_dimensions.requires_clarification

    if authoritative_entity is not None:
        replacing = current.active_entity not in (None, authoritative_entity)
        topic = CanonicalTopic.EVENTS
        entity = authoritative_entity
        reasons.append(
            TransitionReason.AUTHORITATIVE_ENTITY_REPLACED
            if replacing
            else TransitionReason.AUTHORITATIVE_ENTITY
        )
    elif relation is ConversationRelationship.CORRECTION:
        has_explicit_correction = any(
            (
                explicit_topic is not None and explicit_topic != current.active_topic,
                explicit_location,
                explicit_time,
                explicit_filters,
            )
        )
        if not has_explicit_correction:
            return _no_change(
                current,
                relation,
                completion_status,
                TransitionReason.UNRESOLVED_CORRECTION,
                clarification=True,
                commit_allowed=False,
            )

    if requires_clarification:
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.MISSING_REFERENCE,
            clarification=True,
            commit_allowed=False,
        )

    if topic is not CanonicalTopic.EVENTS:
        filters = _empty_filters()
    factual_changed = any(
        (
            topic != current.active_topic,
            entity != current.active_entity,
            location != current.location,
            time_range != current.time_range,
            filters != current.filters,
        )
    )
    if factual_changed and entity is not None and (
        location != current.location or time_range != current.time_range or filters != current.filters
    ):
        entity = authoritative_entity

    next_state = ConversationState(
        revision=current.revision,
        active_topic=topic,
        active_entity=entity,
        location=location,
        time_range=time_range,
        filters=filters,
        previous_route=completed_route,
        last_substantive_message_id=current.last_substantive_message_id,
    )
    same_context = semantic_signature(current) == semantic_signature(next_state)
    if same_context:
        return _no_change(
            current,
            relation,
            completion_status,
            TransitionReason.EQUIVALENT_CONTEXT,
        )
    if not _route_is_compatible(topic, completed_route):
        return _no_change(
            current,
            relation,
            CompletionStatus.DEGRADED,
            TransitionReason.ROUTE_INCOMPATIBLE,
            commit_allowed=False,
        )

    retained, replaced, cleared = _describe_fields(current, next_state)
    semantic_fields_changed = any(
        field is not StateField.PREVIOUS_ROUTE for field in (*replaced, *cleared)
    )
    if semantic_fields_changed:
        reasons.append(TransitionReason.FACTUAL_DIMENSION_CHANGED)
    if route_family(current.previous_route) != route_family(completed_route):
        reasons.append(TransitionReason.ROUTE_FAMILY_CHANGED)
    reasons = list(dict.fromkeys(reasons)) or [TransitionReason.FACTUAL_DIMENSION_CHANGED]
    action = TransitionAction.UPDATE
    if relation in {ConversationRelationship.CORRECTION, ConversationRelationship.NEW_TOPIC}:
        action = TransitionAction.REPLACE
    if all(
        _is_empty_field(field, _field_value(next_state, field))
        for field in (
            StateField.ACTIVE_TOPIC,
            StateField.ACTIVE_ENTITY,
            StateField.LOCATION,
            StateField.TIME_RANGE,
            StateField.FILTERS,
        )
    ):
        action = TransitionAction.CLEAR
    return ConversationTransition(
        action=action,
        relationship=relation,
        retained=retained,
        replaced=replaced,
        cleared=cleared,
        next_state=next_state,
        cache_action=CacheAction.INVALIDATE,
        reason_codes=tuple(reasons[:12]),
        commit_allowed=True,
        requires_clarification=False,
        completion_status=completion_status,
    )
