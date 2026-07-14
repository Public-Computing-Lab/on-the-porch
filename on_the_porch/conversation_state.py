"""Backward-compatible, bounded conversation state stored with retrieval cache data."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, Mapping, Optional


ENVELOPE_VERSION = 1
MAX_THREAD_STATE_BYTES = 1_048_576
MAX_MESSAGE_ID_CHARS = 128
_ENVELOPE_KEYS = frozenset({"version", "retrieval_cache", "conversation_state"})
_CONVERSATION_STATE_KEYS = frozenset(
    {
        "state_version",
        "active_topic",
        "active_entity",
        "previous_route",
        "last_substantive_message_id",
    }
)
_ACTIVE_ENTITY_KEYS = frozenset({"type", "id", "source_ref"})
_EVENT_ID_PATTERN = re.compile(r"[1-9][0-9]{0,9}")
_MESSAGE_ID_PATTERN = re.compile(r"[A-Za-z0-9-]{1,128}")
_ROUTE_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class ConversationStateError(ValueError):
    """Raised when persisted state violates the bounded contract."""


class TransitionAction(str, Enum):
    PRESERVE = "preserve"
    ACTIVATE_EVENT = "activate_event"
    CLEAR_EVENT = "clear_event"


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


@dataclass(frozen=True)
class ConversationState:
    state_version: int = 0
    active_topic: Optional[str] = None
    active_entity: Optional[ActiveEntity] = None
    previous_route: Optional[str] = None
    last_substantive_message_id: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Any) -> "ConversationState":
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping) or set(raw) != _CONVERSATION_STATE_KEYS:
            raise ConversationStateError("conversation_state has an invalid schema")
        version = raw.get("state_version")
        if isinstance(version, bool) or not isinstance(version, int) or not 0 <= version <= 2_147_483_647:
            raise ConversationStateError("invalid conversation state version")
        active_topic = raw.get("active_topic")
        if active_topic not in (None, "events"):
            raise ConversationStateError("unsupported active topic")
        active_entity = ActiveEntity.from_mapping(raw.get("active_entity"))
        if (active_topic == "events") != (active_entity is not None):
            raise ConversationStateError("active topic and entity must be set together")
        previous_route = _bounded_optional(raw.get("previous_route"), _ROUTE_PATTERN)
        message_id = _bounded_optional(
            raw.get("last_substantive_message_id"),
            _MESSAGE_ID_PATTERN,
        )
        return cls(
            state_version=version,
            active_topic=active_topic,
            active_entity=active_entity,
            previous_route=previous_route,
            last_substantive_message_id=message_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_version": self.state_version,
            "active_topic": self.active_topic,
            "active_entity": self.active_entity.to_dict() if self.active_entity else None,
            "previous_route": self.previous_route,
            "last_substantive_message_id": self.last_substantive_message_id,
        }


@dataclass(frozen=True)
class ThreadStateEnvelope:
    retrieval_cache: Dict[str, Any]
    conversation_state: ConversationState = ConversationState()
    version: int = ENVELOPE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "retrieval_cache": copy.deepcopy(self.retrieval_cache),
            "conversation_state": self.conversation_state.to_dict(),
        }


@dataclass(frozen=True)
class ProposedTransition:
    action: TransitionAction
    expected_state_version: int
    event_id: Optional[str] = None
    previous_route: Optional[str] = None

    @classmethod
    def preserve(cls, state: ConversationState) -> "ProposedTransition":
        return cls(TransitionAction.PRESERVE, state.state_version)

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
            state.state_version,
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
            state.state_version,
            previous_route=_bounded_optional(previous_route, _ROUTE_PATTERN),
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


def load_thread_state(
    raw: Any,
    *,
    empty_retrieval_cache: Optional[Dict[str, Any]] = None,
) -> ThreadStateEnvelope:
    """Load a versioned envelope or wrap the legacy top-level retrieval cache."""
    parsed = _decode(raw)
    if not parsed:
        return ThreadStateEnvelope(copy.deepcopy(empty_retrieval_cache or {}))

    is_envelope = any(key in parsed for key in _ENVELOPE_KEYS)
    if not is_envelope:
        return ThreadStateEnvelope(copy.deepcopy(parsed))
    if set(parsed) != _ENVELOPE_KEYS or parsed.get("version") != ENVELOPE_VERSION:
        raise ConversationStateError("thread state envelope has an invalid schema")
    cache = parsed.get("retrieval_cache")
    if not isinstance(cache, dict):
        raise ConversationStateError("retrieval_cache must be an object")
    state = ConversationState.from_mapping(parsed.get("conversation_state"))
    return ThreadStateEnvelope(copy.deepcopy(cache), state)


def with_retrieval_cache(
    envelope: ThreadStateEnvelope,
    retrieval_cache: Mapping[str, Any],
) -> ThreadStateEnvelope:
    if not isinstance(retrieval_cache, Mapping):
        raise ConversationStateError("retrieval_cache must be an object")
    return replace(envelope, retrieval_cache=copy.deepcopy(dict(retrieval_cache)))


def apply_transition(
    state: ConversationState,
    proposal: ProposedTransition,
    *,
    last_substantive_message_id: Optional[str],
) -> ConversationState:
    if state.state_version != proposal.expected_state_version:
        raise ConversationStateError("conversation state version conflict")
    if proposal.action is TransitionAction.PRESERVE:
        return state
    message_id = _bounded_optional(last_substantive_message_id, _MESSAGE_ID_PATTERN)
    if proposal.action is TransitionAction.ACTIVATE_EVENT:
        entity = ActiveEntity.event(proposal.event_id)
        return ConversationState(
            state_version=state.state_version + 1,
            active_topic="events",
            active_entity=entity,
            previous_route=proposal.previous_route or "sql",
            last_substantive_message_id=message_id,
        )
    if proposal.action is TransitionAction.CLEAR_EVENT:
        if state.active_entity is None:
            return state
        return ConversationState(
            state_version=state.state_version + 1,
            active_topic=None,
            active_entity=None,
            previous_route=proposal.previous_route,
            last_substantive_message_id=message_id,
        )
    raise ConversationStateError("unsupported conversation state transition")


def with_conversation_state(
    envelope: ThreadStateEnvelope,
    state: ConversationState,
) -> ThreadStateEnvelope:
    return replace(envelope, conversation_state=state)


def serialize_thread_state(envelope: ThreadStateEnvelope) -> str:
    encoded = json.dumps(
        envelope.to_dict(),
        ensure_ascii=True,
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
) -> CommitMergeResult:
    """Merge one request result without overwriting newer state or cache data."""
    current_state = current.conversation_state
    if current_state.state_version != transition.expected_state_version:
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
    if cache_matches:
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
        cache_update_applied=cache_matches,
        version_conflict=False,
    )
