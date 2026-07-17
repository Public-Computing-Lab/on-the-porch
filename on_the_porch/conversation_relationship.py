"""Bounded, diagnostic-only conversation relationship classification."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple


MAX_CURRENT_MESSAGE_CHARS = 1200
MAX_TURN_TEXT_CHARS = 500
MAX_ENTITY_TITLE_CHARS = 200
MAX_LLM_OUTPUT_BYTES = 2048
MAX_LLM_PROMPT_BYTES = 6000
LLM_FALLBACK_TIMEOUT_SECONDS = 2.0


class ConversationRelationship(str, Enum):
    STANDALONE = "STANDALONE"
    FOLLOW_UP = "FOLLOW_UP"
    REFINEMENT = "REFINEMENT"
    CORRECTION = "CORRECTION"
    NEW_TOPIC = "NEW_TOPIC"
    SMALL_TALK = "SMALL_TALK"


class DecisionSource(str, Enum):
    RULE = "rule"
    LLM = "llm"
    FALLBACK = "fallback"


class ContextDimension(str, Enum):
    ACTIVE_TOPIC = "active_topic"
    ACTIVE_ENTITY = "active_entity"
    LOCATION = "location"
    TIME_RANGE = "time_range"
    FILTERS = "filters"
    PREVIOUS_ROUTE = "previous_route"
    PREVIOUS_SOURCES = "previous_sources"


class ReasonCode(str, Enum):
    SMALL_TALK_GREETING = "small_talk_greeting"
    SMALL_TALK_THANKS = "small_talk_thanks"
    SMALL_TALK_ACKNOWLEDGEMENT = "small_talk_acknowledgement"
    EXPLICIT_CORRECTION = "explicit_correction"
    CIVIC_TOPIC_SWITCH = "civic_topic_switch"
    ACTIVE_EVENT_PROPERTY_QUESTION = "active_event_property_question"
    FILTER_REFINEMENT = "filter_refinement"
    TIME_REFINEMENT = "time_refinement"
    LOCATION_REFINEMENT = "location_refinement"
    REFERENTIAL_FOLLOW_UP = "referential_follow_up"
    UNSUPPORTED_CAUSALITY_FOLLOW_UP = "unsupported_causality_follow_up"
    MISSING_REFERENCE = "missing_reference"
    SELF_CONTAINED_CIVIC_QUESTION = "self_contained_civic_question"
    NO_PRIOR_CONTEXT = "no_prior_context"
    AMBIGUOUS_CONTEXT = "ambiguous_context"
    CLASSIFIER_OUTPUT_INVALID = "classifier_output_invalid"
    CLASSIFIER_TIMEOUT = "classifier_timeout"
    CLASSIFIER_FAILURE = "classifier_failure"


class EventProperty(str, Enum):
    LOCATION = "location"
    DATE_TIME = "date_time"
    COST = "cost"
    REGISTRATION_CONTACT = "registration_contact"
    ORGANIZER = "organizer"
    DETAILS = "details"


_CIVIC_TOPICS = frozenset(
    {
        "311",
        "911",
        "community_note",
        "crime",
        "events",
        "news",
        "opinion",
        "policy",
        "safety",
        "transcript",
    }
)

_DECISION_KEYS = frozenset(
    {
        "relationship",
        "confidence",
        "reason_code",
        "retained",
        "cleared",
        "requires_clarification",
        "decision_source",
        "shadow",
    }
)


def _bounded_text(value: Any, maximum: int) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    return normalized[:maximum]


def _small_talk_reason(message: str) -> Optional[ReasonCode]:
    text = _bounded_text(message, 200).lower().strip(" .,!?")
    if re.fullmatch(r"(?:hi|hello|hey|good morning|good afternoon|good evening)", text):
        return ReasonCode.SMALL_TALK_GREETING
    if re.fullmatch(
        r"(?:thanks|thank you|thanks so much|thank you so much|much appreciated)", text,
    ):
        return ReasonCode.SMALL_TALK_THANKS
    if re.fullmatch(r"(?:ok|okay|got it|understood|sounds good|all right|alright)", text):
        return ReasonCode.SMALL_TALK_ACKNOWLEDGEMENT
    return None


def _parse_json_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _bounded_topic(value: Any) -> Optional[str]:
    topic = str(value or "").strip().lower()
    return topic if topic in _CIVIC_TOPICS else None


@dataclass(frozen=True)
class CompactTurn:
    user: str
    assistant: str
    route: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user": self.user,
            "assistant": self.assistant,
            "route": self.route,
        }


@dataclass(frozen=True)
class AuthoritativeEntity:
    entity_type: str
    entity_id: str
    title: str
    date: Optional[str]

    @classmethod
    def from_mapping(cls, value: Any) -> Optional["AuthoritativeEntity"]:
        if not isinstance(value, dict):
            return None
        entity_type = _bounded_text(value.get("type"), 32).lower()
        entity_id = _bounded_text(value.get("id"), 128)
        title = _bounded_text(value.get("title"), MAX_ENTITY_TITLE_CHARS)
        date = _bounded_text(value.get("date"), 32) or None
        if entity_type not in {"event", "location", "source", "topic"}:
            return None
        if not entity_id or not title:
            return None
        return cls(entity_type=entity_type, entity_id=entity_id, title=title, date=date)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.entity_type,
            "id": self.entity_id,
            "title": self.title,
            "date": self.date,
        }


@dataclass(frozen=True)
class RelationshipContext:
    current_message: str
    previous_turns: Tuple[CompactTurn, ...]
    resolved_entity: Optional[AuthoritativeEntity]
    current_topic: Optional[str]
    previous_topic: Optional[str]
    previous_route: Optional[str]
    previous_result_exists: bool
    authoritative_active_event: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_message": self.current_message,
            "previous_turns": [turn.to_dict() for turn in self.previous_turns],
            "resolved_entity": (
                self.resolved_entity.to_dict() if self.resolved_entity is not None else None
            ),
            "current_topic": self.current_topic,
            "previous_topic": self.previous_topic,
            "previous_route": self.previous_route,
            "previous_result_exists": self.previous_result_exists,
            "authoritative_active_event": self.authoritative_active_event,
        }

    def to_llm_dict(self) -> Dict[str, Any]:
        """Exclude assistant prose that may summarize private community source text."""
        return {
            "current_message": self.current_message,
            "previous_turns": [
                {
                    "user": turn.user,
                    "assistant_result_exists": bool(turn.assistant),
                    "route": turn.route,
                }
                for turn in self.previous_turns
            ],
            "resolved_entity": (
                self.resolved_entity.to_dict() if self.resolved_entity is not None else None
            ),
            "current_topic": self.current_topic,
            "previous_topic": self.previous_topic,
            "previous_route": self.previous_route,
            "previous_result_exists": self.previous_result_exists,
            "authoritative_active_event": self.authoritative_active_event,
        }


@dataclass(frozen=True)
class RelationshipDecision:
    relationship: ConversationRelationship
    confidence: float
    reason_code: ReasonCode
    retained: Tuple[ContextDimension, ...]
    cleared: Tuple[ContextDimension, ...]
    requires_clarification: bool
    decision_source: DecisionSource
    shadow: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.confidence, bool) or not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if len(self.retained) > len(ContextDimension) or len(self.cleared) > len(ContextDimension):
            raise ValueError("too many context dimensions")
        if len(set(self.retained)) != len(self.retained):
            raise ValueError("retained dimensions must be unique")
        if len(set(self.cleared)) != len(self.cleared):
            raise ValueError("cleared dimensions must be unique")
        if set(self.retained) & set(self.cleared):
            raise ValueError("a context dimension cannot be retained and cleared")
        if self.shadow is not True:
            raise ValueError("relationship decisions must remain shadow-only")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship": self.relationship.value,
            "confidence": round(float(self.confidence), 4),
            "reason_code": self.reason_code.value,
            "retained": [item.value for item in self.retained],
            "cleared": [item.value for item in self.cleared],
            "requires_clarification": self.requires_clarification,
            "decision_source": self.decision_source.value,
            "shadow": True,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RelationshipDecision":
        if set(value.keys()) != _DECISION_KEYS:
            raise ValueError("relationship decision has an invalid schema")
        confidence = value.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be numeric")
        if not isinstance(value.get("requires_clarification"), bool):
            raise ValueError("requires_clarification must be boolean")
        if value.get("shadow") is not True:
            raise ValueError("shadow must be true")
        retained = _parse_dimensions(value.get("retained"))
        cleared = _parse_dimensions(value.get("cleared"))
        return cls(
            relationship=ConversationRelationship(str(value.get("relationship"))),
            confidence=float(confidence),
            reason_code=ReasonCode(str(value.get("reason_code"))),
            retained=retained,
            cleared=cleared,
            requires_clarification=value["requires_clarification"],
            decision_source=DecisionSource(str(value.get("decision_source"))),
            shadow=True,
        )


def _parse_dimensions(value: Any) -> Tuple[ContextDimension, ...]:
    if not isinstance(value, list) or len(value) > len(ContextDimension):
        raise ValueError("context dimensions must be a bounded list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("context dimensions must be strings")
    return tuple(ContextDimension(item) for item in value)


def _topic_detector(message: str) -> Optional[str]:
    try:
        import intent_router  # type: ignore

        return _bounded_topic(intent_router.detect_civic_topic(message))
    except Exception:
        return None


def build_relationship_context(
    current_message: str,
    history_rows: Sequence[Mapping[str, Any]],
    *,
    topic_detector: Optional[Callable[[str], Optional[str]]] = None,
    authoritative_active_event: bool = False,
) -> RelationshipContext:
    """Build a compact context without cache rows, source text, or client metadata."""
    detect = topic_detector or _topic_detector
    paired_turns = []
    pending_user: Optional[Mapping[str, Any]] = None
    latest_entity: Optional[AuthoritativeEntity] = None

    for row in history_rows:
        role = str(row.get("role") or "").lower()
        if role == "assistant":
            meta = _parse_json_mapping(row.get("message_meta_json"))
            candidate = AuthoritativeEntity.from_mapping(meta.get("resolved_entity"))
            if candidate is not None:
                latest_entity = candidate
        if role == "user":
            pending_user = row
            continue
        if role != "assistant" or pending_user is None:
            continue
        user_text = _bounded_text(pending_user.get("content"), MAX_TURN_TEXT_CHARS)
        if user_text and _small_talk_reason(user_text) is None:
            paired_turns.append(
                CompactTurn(
                    user=user_text,
                    assistant=_bounded_text(row.get("content"), MAX_TURN_TEXT_CHARS),
                    route=_bounded_text(row.get("response_mode"), 32) or None,
                )
            )
        pending_user = None

    previous_turns = tuple(paired_turns[-2:])
    previous_topic = None
    for turn in reversed(previous_turns):
        previous_topic = _bounded_topic(detect(turn.user))
        if previous_topic:
            break
    previous_route = previous_turns[-1].route if previous_turns else None
    previous_result_exists = bool(previous_turns and previous_turns[-1].assistant)
    return RelationshipContext(
        current_message=_bounded_text(current_message, MAX_CURRENT_MESSAGE_CHARS),
        previous_turns=previous_turns,
        resolved_entity=latest_entity,
        current_topic=_bounded_topic(detect(current_message)),
        previous_topic=previous_topic,
        previous_route=previous_route,
        previous_result_exists=previous_result_exists,
        authoritative_active_event=bool(authoritative_active_event),
    )


def _decision(
    relationship: ConversationRelationship,
    confidence: float,
    reason: ReasonCode,
    *,
    retained: Iterable[ContextDimension] = (),
    cleared: Iterable[ContextDimension] = (),
    clarification: bool = False,
    source: DecisionSource = DecisionSource.RULE,
) -> RelationshipDecision:
    return RelationshipDecision(
        relationship=relationship,
        confidence=confidence,
        reason_code=reason,
        retained=tuple(retained),
        cleared=tuple(cleared),
        requires_clarification=clarification,
        decision_source=source,
    )


def conservative_fallback(reason: ReasonCode = ReasonCode.AMBIGUOUS_CONTEXT) -> RelationshipDecision:
    """Fail closed diagnostically; this decision never authorizes state reuse."""
    return _decision(
        ConversationRelationship.STANDALONE,
        0.0,
        reason,
        cleared=(
            ContextDimension.ACTIVE_TOPIC,
            ContextDimension.ACTIVE_ENTITY,
            ContextDimension.LOCATION,
            ContextDimension.TIME_RANGE,
            ContextDimension.FILTERS,
            ContextDimension.PREVIOUS_ROUTE,
            ContextDimension.PREVIOUS_SOURCES,
        ),
        clarification=True,
        source=DecisionSource.FALLBACK,
    )


_EVENT_PROPERTY_PATTERNS = (
    (EventProperty.LOCATION, re.compile(
        r"^(?:where is it|is it virtual|is it online|where does it take place)\??$",
        flags=re.IGNORECASE,
    )),
    (EventProperty.DATE_TIME, re.compile(
        r"^(?:when is it|when does it start|what time does it start|when does it end)\??$",
        flags=re.IGNORECASE,
    )),
    (EventProperty.COST, re.compile(
        r"^(?:is it free|how much does it cost)\??$",
        flags=re.IGNORECASE,
    )),
    (EventProperty.REGISTRATION_CONTACT, re.compile(
        r"^(?:how do i register|who do i contact|do i need to sign up)\??$",
        flags=re.IGNORECASE,
    )),
    (EventProperty.ORGANIZER, re.compile(
        r"^(?:who is organizing it|who organizes it|who is hosting it)\??$",
        flags=re.IGNORECASE,
    )),
    (EventProperty.DETAILS, re.compile(
        r"^(?:tell me more about it)\??$",
        flags=re.IGNORECASE,
    )),
)
_CORRECTION_PATTERN = re.compile(
    r"^(?:no[, ]+i mean|i meant|not that one|no[, ]+not|actually[, ]+i mean)\b",
    flags=re.IGNORECASE,
)
_TIME_REFINEMENT_PATTERN = re.compile(
    r"^(?:what about|how about|and what about)?\s*(?:this weekend|next weekend|"
    r"today|tomorrow|next week|this week|the week after next|week after next|"
    r"later this week|earlier this week|"
    r"(?:the )?next (?:7|seven|14|fourteen) days|over the next week|"
    r"over the next 14 days|in the evening|after work)\??$",
    flags=re.IGNORECASE,
)
_LOCATION_REFINEMENT_PATTERN = re.compile(
    r"^(?:what about|how about|near|around|in)\s+[A-Za-z0-9 .'-]{2,80}\??$",
    flags=re.IGNORECASE,
)
_ALL_DORCHESTER_PATTERN = re.compile(
    r"^(?:actually[, ]+)?(?:all of |across |the rest of )?dorchester\.?\??$",
    flags=re.IGNORECASE,
)
_FILTER_REFINEMENT_PATTERN = re.compile(
    r"^(?:only\b|just show\b|show only\b|free ones?\b|paid ones?\b|"
    r"what about (?:virtual|online|in[- ]person|hybrid)(?: events?)?\b|"
    r"family[- ]friendly\b|(?:morning|afternoon|evening|virtual|online|"
    r"in[- ]person|hybrid) (?:events?|ones?)\b|(?:registration|sign[- ]up) "
    r"(?:required|not required|unknown)\b)",
    flags=re.IGNORECASE,
)
_REFERENTIAL_PATTERN = re.compile(
    r"^(?:tell me more|more detail|go on|continue|can you explain(?: that)?|"
    r"could you explain(?: that)?|what about (?:that|it|them|this)|"
    r"how about (?:that|it|them|this))\??$",
    flags=re.IGNORECASE,
)
_WHY_PATTERN = re.compile(r"^(?:why|why is that|how come)\??$", flags=re.IGNORECASE)


def detect_event_property(message: str) -> Optional[EventProperty]:
    normalized = _bounded_text(message, 200).rstrip(" .!?")
    for property_name, pattern in _EVENT_PROPERTY_PATTERNS:
        if pattern.fullmatch(normalized):
            return property_name
    return None


def classify_with_rules(context: RelationshipContext) -> Optional[RelationshipDecision]:
    message = context.current_message.strip()
    has_prior = bool(context.previous_turns or context.previous_result_exists)
    small_talk = _small_talk_reason(message)
    if small_talk is not None:
        return _decision(
            ConversationRelationship.SMALL_TALK,
            0.99,
            small_talk,
            retained=(
                ContextDimension.ACTIVE_TOPIC,
                ContextDimension.ACTIVE_ENTITY,
                ContextDimension.LOCATION,
                ContextDimension.TIME_RANGE,
                ContextDimension.FILTERS,
            ),
        )

    if _CORRECTION_PATTERN.search(message):
        return _decision(
            ConversationRelationship.CORRECTION,
            0.98,
            ReasonCode.EXPLICIT_CORRECTION,
            retained=(ContextDimension.ACTIVE_TOPIC,),
            cleared=(
                ContextDimension.ACTIVE_ENTITY,
                ContextDimension.TIME_RANGE,
                ContextDimension.FILTERS,
            ),
        )

    if has_prior and _ALL_DORCHESTER_PATTERN.fullmatch(message.strip()):
        return _decision(
            ConversationRelationship.REFINEMENT,
            0.97,
            ReasonCode.LOCATION_REFINEMENT,
            retained=(
                ContextDimension.ACTIVE_TOPIC,
                ContextDimension.TIME_RANGE,
                ContextDimension.FILTERS,
            ),
        )

    active_event = (
        context.authoritative_active_event
        or (
            context.resolved_entity is not None
            and context.resolved_entity.entity_type == "event"
            and context.previous_topic in (None, "events")
        )
    )
    if detect_event_property(message) is not None:
        if active_event:
            return _decision(
                ConversationRelationship.FOLLOW_UP,
                0.99,
                ReasonCode.ACTIVE_EVENT_PROPERTY_QUESTION,
                retained=(ContextDimension.ACTIVE_ENTITY, ContextDimension.ACTIVE_TOPIC),
            )
        return _decision(
            ConversationRelationship.FOLLOW_UP,
            0.75,
            ReasonCode.MISSING_REFERENCE,
            clarification=True,
        )

    if has_prior and _FILTER_REFINEMENT_PATTERN.search(message):
        return _decision(
            ConversationRelationship.REFINEMENT,
            0.96,
            ReasonCode.FILTER_REFINEMENT,
            retained=(ContextDimension.ACTIVE_TOPIC, ContextDimension.ACTIVE_ENTITY),
        )
    if has_prior and _TIME_REFINEMENT_PATTERN.fullmatch(message):
        return _decision(
            ConversationRelationship.REFINEMENT,
            0.94,
            ReasonCode.TIME_REFINEMENT,
            retained=(ContextDimension.ACTIVE_TOPIC, ContextDimension.ACTIVE_ENTITY),
        )
    if has_prior and _LOCATION_REFINEMENT_PATTERN.fullmatch(message):
        return _decision(
            ConversationRelationship.REFINEMENT,
            0.94,
            ReasonCode.LOCATION_REFINEMENT,
            retained=(ContextDimension.ACTIVE_TOPIC, ContextDimension.ACTIVE_ENTITY),
        )
    if (
        context.current_topic
        and context.previous_topic
        and context.current_topic != context.previous_topic
    ):
        retained = (ContextDimension.LOCATION,) if re.search(r"\bthere\b", message, re.I) else ()
        return _decision(
            ConversationRelationship.NEW_TOPIC,
            0.97,
            ReasonCode.CIVIC_TOPIC_SWITCH,
            retained=retained,
            cleared=(
                ContextDimension.ACTIVE_TOPIC,
                ContextDimension.ACTIVE_ENTITY,
                ContextDimension.TIME_RANGE,
                ContextDimension.FILTERS,
                ContextDimension.PREVIOUS_ROUTE,
                ContextDimension.PREVIOUS_SOURCES,
            ),
        )
    if _WHY_PATTERN.fullmatch(message):
        return _decision(
            ConversationRelationship.FOLLOW_UP,
            0.86 if has_prior else 0.65,
            (
                ReasonCode.UNSUPPORTED_CAUSALITY_FOLLOW_UP
                if has_prior
                else ReasonCode.MISSING_REFERENCE
            ),
            retained=(ContextDimension.ACTIVE_TOPIC,) if has_prior else (),
            clarification=True,
        )
    if _REFERENTIAL_PATTERN.fullmatch(message):
        return _decision(
            ConversationRelationship.FOLLOW_UP,
            0.9 if has_prior else 0.65,
            ReasonCode.REFERENTIAL_FOLLOW_UP if has_prior else ReasonCode.MISSING_REFERENCE,
            retained=(ContextDimension.ACTIVE_TOPIC, ContextDimension.ACTIVE_ENTITY) if has_prior else (),
            clarification=not has_prior,
        )
    if context.current_topic:
        return _decision(
            ConversationRelationship.STANDALONE,
            0.95,
            ReasonCode.SELF_CONTAINED_CIVIC_QUESTION,
            cleared=(
                ContextDimension.ACTIVE_TOPIC,
                ContextDimension.ACTIVE_ENTITY,
                ContextDimension.LOCATION,
                ContextDimension.TIME_RANGE,
                ContextDimension.FILTERS,
            ),
        )
    if not has_prior:
        return _decision(
            ConversationRelationship.STANDALONE,
            0.9,
            ReasonCode.NO_PRIOR_CONTEXT,
            clarification=False,
        )
    return None


def _llm_prompt(context: RelationshipContext) -> str:
    payload = json.dumps(context.to_llm_dict(), ensure_ascii=True, separators=(",", ":"))
    relationships = ", ".join(item.value for item in ConversationRelationship)
    reasons = ", ".join(item.value for item in ReasonCode)
    dimensions = ", ".join(item.value for item in ContextDimension)
    prompt = (
        "Classify the relationship of the current message to the compact prior context. "
        "Return exactly one JSON object and no prose. Do not answer the resident. "
        f"Allowed relationship values: {relationships}. "
        f"Allowed reason_code values: {reasons}. "
        f"Allowed retained/cleared values: {dimensions}. "
        "decision_source must be llm and shadow must be true. Use this exact schema: "
        '{"relationship":"STANDALONE","confidence":0.5,'
        '"reason_code":"ambiguous_context","retained":[],"cleared":[],'
        '"requires_clarification":true,"decision_source":"llm","shadow":true}. '
        f"Compact context: {payload}"
    )
    encoded = prompt.encode("utf-8")
    if len(encoded) > MAX_LLM_PROMPT_BYTES:
        raise ValueError("classifier prompt exceeds the bounded size")
    return prompt


def _remaining_timeout() -> float:
    try:
        from request_budget import remaining_seconds  # type: ignore

        remaining = remaining_seconds(floor=0.0)
    except Exception:
        remaining = LLM_FALLBACK_TIMEOUT_SECONDS
    return max(0.0, min(LLM_FALLBACK_TIMEOUT_SECONDS, remaining - 0.25))


def _parse_llm_decision(raw: str) -> RelationshipDecision:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_LLM_OUTPUT_BYTES:
        raise ValueError("classifier output exceeds the bounded size")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("classifier output must be an object")
    decision = RelationshipDecision.from_mapping(parsed)
    if decision.decision_source is not DecisionSource.LLM:
        raise ValueError("LLM decision_source must be llm")
    return decision


def classify_relationship(
    context: RelationshipContext,
    *,
    llm_fallback_enabled: bool = False,
    llm_classifier: Optional[Callable[[str, float], str]] = None,
) -> RelationshipDecision:
    rule_decision = classify_with_rules(context)
    if rule_decision is not None:
        return rule_decision
    if not llm_fallback_enabled or llm_classifier is None:
        return conservative_fallback()
    timeout = _remaining_timeout()
    if timeout <= 0.0:
        return conservative_fallback(ReasonCode.CLASSIFIER_TIMEOUT)
    try:
        raw = llm_classifier(_llm_prompt(context), timeout)
        return _parse_llm_decision(raw)
    except TimeoutError:
        return conservative_fallback(ReasonCode.CLASSIFIER_TIMEOUT)
    except Exception:
        return conservative_fallback(ReasonCode.CLASSIFIER_OUTPUT_INVALID)


def classify_shadow_relationship(
    current_message: str,
    history_rows: Sequence[Mapping[str, Any]],
    *,
    enabled: bool,
    llm_fallback_enabled: bool = False,
    llm_classifier: Optional[Callable[[str, float], str]] = None,
    topic_detector: Optional[Callable[[str], Optional[str]]] = None,
    authoritative_active_event: bool = False,
) -> Optional[RelationshipDecision]:
    if not enabled:
        return None
    context = build_relationship_context(
        current_message,
        history_rows,
        topic_detector=topic_detector,
        authoritative_active_event=authoritative_active_event,
    )
    return classify_relationship(
        context,
        llm_fallback_enabled=llm_fallback_enabled,
        llm_classifier=llm_classifier,
    )
