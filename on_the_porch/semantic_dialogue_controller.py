"""Bounded rule-first controller for deciding how a resident turn should run.

The controller is deliberately transport and data-source agnostic. Domain modules
provide reviewed hints; this module validates one decision before any capability
or legacy retrieval route is selected. It never returns SQL, URLs, tool names, or
resident-facing civic facts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from answering_kernel import GlobalDeadline


MAX_MESSAGE_CHARS = 1200
MAX_DIRECT_RESPONSE_CHARS = 500
MAX_CONTROLLER_OUTPUT_BYTES = 2048
MAX_CONTROLLER_PROMPT_BYTES = 6000
MAX_DIALOGUE_CONTEXT_BYTES = 4096
MIN_LLM_CONFIDENCE = 0.75


class TurnKind(str, Enum):
    FACTUAL_TOOL_QUERY = "FACTUAL_TOOL_QUERY"
    CONTEXTUAL_TOOL_FOLLOWUP = "CONTEXTUAL_TOOL_FOLLOWUP"
    CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
    RESPONSE_REPHRASE = "RESPONSE_REPHRASE"
    CORRECTION = "CORRECTION"
    DIRECT_CONVERSATIONAL = "DIRECT_CONVERSATIONAL"
    NEW_TOPIC = "NEW_TOPIC"
    SMALL_TALK = "SMALL_TALK"
    AMBIGUOUS = "AMBIGUOUS"
    CONTRADICTORY = "CONTRADICTORY"
    UNSATISFIABLE = "UNSATISFIABLE"


class DialogueDomain(str, Enum):
    NONE = "NONE"
    CIVIC_311 = "311"
    EVENTS = "EVENTS"
    CRIME_AND_SAFETY = "CRIME_AND_SAFETY"
    ZONING_AND_DEVELOPMENT = "ZONING_AND_DEVELOPMENT"
    GENERAL = "GENERAL"


class DialogueRelationship(str, Enum):
    STANDALONE = "STANDALONE"
    FOLLOW_UP = "FOLLOW_UP"
    REFINEMENT = "REFINEMENT"
    CORRECTION = "CORRECTION"
    NEW_TOPIC = "NEW_TOPIC"
    SMALL_TALK = "SMALL_TALK"


class ResponseStrategy(str, Enum):
    EXECUTE_CAPABILITY = "EXECUTE_CAPABILITY"
    DIRECT_RESPONSE = "DIRECT_RESPONSE"
    REPHRASE_PREVIOUS_RESULT = "REPHRASE_PREVIOUS_RESULT"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    SAFE_UNSUPPORTED_RESPONSE = "SAFE_UNSUPPORTED_RESPONSE"


class CapabilityIntent(str, Enum):
    NONE = "NONE"
    COUNT_311 = "COUNT_311"
    DISTRIBUTE_311_CATEGORIES = "DISTRIBUTE_311_CATEGORIES"
    QUERY_311 = "QUERY_311"
    LIST_EVENTS = "LIST_EVENTS"
    EVENT_DETAILS = "EVENT_DETAILS"
    LEGACY_DOMAIN_ROUTE = "LEGACY_DOMAIN_ROUTE"


class ClarificationReason(str, Enum):
    NONE = "NONE"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    UNBOUNDED_NEGATION = "UNBOUNDED_NEGATION"
    REQUIRED_LOCATION = "REQUIRED_LOCATION"
    INVALID_CONTROLLER_OUTPUT = "INVALID_CONTROLLER_OUTPUT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    CONTROLLER_TIMEOUT = "CONTROLLER_TIMEOUT"
    CONTROLLER_FAILURE = "CONTROLLER_FAILURE"


class TrustedReferenceTarget(str, Enum):
    NONE = "NONE"
    PENDING_311_ISSUE_SCOPE = "PENDING_311_ISSUE_SCOPE"
    ACTIVE_311_CONTEXT = "ACTIVE_311_CONTEXT"
    PREVIOUS_EVENT_RESULT = "PREVIOUS_EVENT_RESULT"
    ACTIVE_EVENT = "ACTIVE_EVENT"


class DecisionSource(str, Enum):
    RULE = "RULE"
    LLM = "LLM"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class DomainHint:
    turn_kind: TurnKind
    domain: DialogueDomain
    relationship: DialogueRelationship
    response_strategy: ResponseStrategy
    tool_required: bool
    capability_intent: CapabilityIntent
    clarification_reason: ClarificationReason = ClarificationReason.NONE
    trusted_reference_target: TrustedReferenceTarget = TrustedReferenceTarget.NONE
    confidence: float = 0.99
    direct_response: Optional[str] = None
    priority: int = 50

    def __post_init__(self) -> None:
        _validate_decision_fields(
            domain=self.domain,
            response_strategy=self.response_strategy,
            tool_required=self.tool_required,
            capability_intent=self.capability_intent,
            clarification_reason=self.clarification_reason,
            confidence=self.confidence,
            direct_response=self.direct_response,
        )


@dataclass(frozen=True)
class DialogueDecision:
    turn_kind: TurnKind
    domain: DialogueDomain
    relationship: DialogueRelationship
    response_strategy: ResponseStrategy
    tool_required: bool
    capability_intent: CapabilityIntent
    clarification_reason: ClarificationReason
    trusted_reference_target: TrustedReferenceTarget
    confidence: float
    decision_source: DecisionSource
    direct_response: Optional[str] = None
    controller_call_count: int = 0
    fallback_reason: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_decision_fields(
            domain=self.domain,
            response_strategy=self.response_strategy,
            tool_required=self.tool_required,
            capability_intent=self.capability_intent,
            clarification_reason=self.clarification_reason,
            confidence=self.confidence,
            direct_response=self.direct_response,
        )
        if self.controller_call_count not in {0, 1}:
            raise ValueError("controller call count must be zero or one")
        if self.decision_source is DecisionSource.RULE and self.controller_call_count:
            raise ValueError("rule decisions cannot record a controller model call")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_kind": self.turn_kind.value,
            "domain": self.domain.value,
            "relationship": self.relationship.value,
            "response_strategy": self.response_strategy.value,
            "tool_required": self.tool_required,
            "capability_intent": self.capability_intent.value,
            "clarification_reason": self.clarification_reason.value,
            "trusted_reference_target": self.trusted_reference_target.value,
            "confidence": round(float(self.confidence), 4),
            "decision_source": self.decision_source.value,
            "controller_call_count": self.controller_call_count,
            "fallback_reason": self.fallback_reason,
        }


def _validate_decision_fields(
    *,
    domain: DialogueDomain,
    response_strategy: ResponseStrategy,
    tool_required: bool,
    capability_intent: CapabilityIntent,
    clarification_reason: ClarificationReason,
    confidence: float,
    direct_response: Optional[str],
) -> None:
    if isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("controller confidence must be between zero and one")
    if tool_required != (response_strategy is ResponseStrategy.EXECUTE_CAPABILITY):
        raise ValueError("tool_required must match the execution strategy")
    if tool_required and capability_intent is CapabilityIntent.NONE:
        raise ValueError("capability execution requires a registered intent")
    if not tool_required and capability_intent is not CapabilityIntent.NONE:
        raise ValueError("no-tool decisions cannot carry a capability intent")
    capability_domains = {
        CapabilityIntent.COUNT_311: {DialogueDomain.CIVIC_311},
        CapabilityIntent.DISTRIBUTE_311_CATEGORIES: {DialogueDomain.CIVIC_311},
        CapabilityIntent.QUERY_311: {DialogueDomain.CIVIC_311},
        CapabilityIntent.LIST_EVENTS: {DialogueDomain.EVENTS},
        CapabilityIntent.EVENT_DETAILS: {DialogueDomain.EVENTS},
        CapabilityIntent.LEGACY_DOMAIN_ROUTE: {
            DialogueDomain.GENERAL,
            DialogueDomain.CRIME_AND_SAFETY,
            DialogueDomain.ZONING_AND_DEVELOPMENT,
        },
    }
    allowed_domains = capability_domains.get(capability_intent)
    if tool_required and (allowed_domains is None or domain not in allowed_domains):
        raise ValueError("capability intent is incompatible with the selected domain")
    if response_strategy is ResponseStrategy.ASK_CLARIFICATION and clarification_reason is ClarificationReason.NONE:
        raise ValueError("clarification decisions require a reason")
    if direct_response is not None:
        normalized = re.sub(r"\s+", " ", str(direct_response)).strip()
        if not normalized or len(normalized) > MAX_DIRECT_RESPONSE_CHARS:
            raise ValueError("direct response is invalid")
        if response_strategy not in {
            ResponseStrategy.DIRECT_RESPONSE,
            ResponseStrategy.REPHRASE_PREVIOUS_RESULT,
            ResponseStrategy.ASK_CLARIFICATION,
            ResponseStrategy.SAFE_UNSUPPORTED_RESPONSE,
        }:
            raise ValueError("capability decisions cannot contain direct responses")


def _small_talk_hint(message: str) -> Optional[DomainHint]:
    text = re.sub(r"\s+", " ", str(message or "")).strip().lower().strip(" .,!?")
    if re.fullmatch(r"(?:hi|hello|hey|good morning|good afternoon|good evening)(?:,? what can you help me with)?", text):
        return DomainHint(
            turn_kind=TurnKind.SMALL_TALK,
            domain=DialogueDomain.GENERAL,
            relationship=DialogueRelationship.SMALL_TALK,
            response_strategy=ResponseStrategy.DIRECT_RESPONSE,
            tool_required=False,
            capability_intent=CapabilityIntent.NONE,
            direct_response=(
                "I can help with Dorchester events, 311 service requests, safety data, "
                "and neighborhood news. What would you like to know?"
            ),
            priority=100,
        )
    if re.fullmatch(r"(?:thanks|thank you|thanks so much|thank you so much|okay|ok|got it)", text):
        return DomainHint(
            turn_kind=TurnKind.SMALL_TALK,
            domain=DialogueDomain.GENERAL,
            relationship=DialogueRelationship.SMALL_TALK,
            response_strategy=ResponseStrategy.DIRECT_RESPONSE,
            tool_required=False,
            capability_intent=CapabilityIntent.NONE,
            direct_response="You're welcome. What else would you like to know about Dorchester?",
            priority=100,
        )
    return None


def _bare_ambiguous_hint(message: str) -> Optional[DomainHint]:
    text = re.sub(r"\s+", " ", str(message or "")).strip().lower().strip(" .,!?")
    if text in {"overall", "all", "all of them", "total", "everything"}:
        return DomainHint(
            turn_kind=TurnKind.AMBIGUOUS,
            domain=DialogueDomain.GENERAL,
            relationship=DialogueRelationship.STANDALONE,
            response_strategy=ResponseStrategy.ASK_CLARIFICATION,
            tool_required=False,
            capability_intent=CapabilityIntent.NONE,
            clarification_reason=ClarificationReason.MISSING_CONTEXT,
            direct_response="What would you like an overall total or summary of?",
            priority=10,
        )
    return None


def _needs_semantic_call(message: str, trusted_context: Mapping[str, Any]) -> bool:
    text = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    has_context = bool(trusted_context.get("previous_domain") or trusted_context.get("previous_result_summary"))
    return bool(
        re.search(r"\b(?:not|don't|do not|instead|actually|i meant|say that|rephrase)\b", text)
        or (has_context and len(text.split()) <= 6 and re.search(r"\b(?:it|that|those|them|there)\b", text))
    )


_LLM_KEYS = frozenset(
    {
        "turn_kind",
        "domain",
        "relationship",
        "response_strategy",
        "tool_required",
        "capability_intent",
        "clarification_reason",
        "trusted_reference_target",
        "confidence",
    }
)


def _parse_llm_decision(raw: Any) -> Optional[DialogueDecision]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CONTROLLER_OUTPUT_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != _LLM_KEYS:
        return None
    if not isinstance(value.get("tool_required"), bool):
        return None
    try:
        decision = DialogueDecision(
            turn_kind=TurnKind(value["turn_kind"]),
            domain=DialogueDomain(value["domain"]),
            relationship=DialogueRelationship(value["relationship"]),
            response_strategy=ResponseStrategy(value["response_strategy"]),
            tool_required=value["tool_required"],
            capability_intent=CapabilityIntent(value["capability_intent"]),
            clarification_reason=ClarificationReason(value["clarification_reason"]),
            trusted_reference_target=TrustedReferenceTarget(value["trusted_reference_target"]),
            confidence=float(value["confidence"]),
            decision_source=DecisionSource.LLM,
            controller_call_count=1,
        )
    except (TypeError, ValueError):
        return None
    if decision.confidence < MIN_LLM_CONFIDENCE:
        return None
    return decision


def _controller_prompt(message: str, trusted_context: Mapping[str, Any]) -> str:
    safe_context = {
        key: trusted_context.get(key)
        for key in (
            "previous_turn_kind",
            "previous_domain",
            "previous_capability_intent",
            "pending_clarification_type",
            "pending_clarification_domain",
            "canonical_parameters",
            "previous_result_summary",
        )
    }
    prompt = (
        "Classify one resident turn. Return one JSON object with exactly these keys: "
        + ", ".join(sorted(_LLM_KEYS))
        + ". Use only these enums. turn_kind="
        + ",".join(item.value for item in TurnKind)
        + "; domain=" + ",".join(item.value for item in DialogueDomain)
        + "; relationship=" + ",".join(item.value for item in DialogueRelationship)
        + "; response_strategy=" + ",".join(item.value for item in ResponseStrategy)
        + "; capability_intent=" + ",".join(item.value for item in CapabilityIntent)
        + "; clarification_reason=" + ",".join(item.value for item in ClarificationReason)
        + "; trusted_reference_target=" + ",".join(item.value for item in TrustedReferenceTarget)
        + ". Do not return SQL, URLs, function names, tool arguments, prose, or facts. "
        + "Trusted state: " + json.dumps(safe_context, ensure_ascii=True, separators=(",", ":"))
        + ". Resident message: " + str(message or "")[:MAX_MESSAGE_CHARS]
    )
    if len(prompt.encode("utf-8")) > MAX_CONTROLLER_PROMPT_BYTES:
        raise ValueError("controller prompt exceeds the size limit")
    return prompt


ControllerLLM = Callable[[str, float], str]


def _fallback_decision(reason: ClarificationReason, *, call_count: int) -> DialogueDecision:
    wording = {
        ClarificationReason.CONTROLLER_TIMEOUT: "Could you say a little more about what you want me to check?",
        ClarificationReason.INVALID_CONTROLLER_OUTPUT: "Could you clarify what information you want?",
        ClarificationReason.LOW_CONFIDENCE: "Could you clarify whether you want new information or a change to the previous answer?",
    }.get(reason, "Could you clarify what you would like me to do?")
    return DialogueDecision(
        turn_kind=TurnKind.AMBIGUOUS,
        domain=DialogueDomain.GENERAL,
        relationship=DialogueRelationship.STANDALONE,
        response_strategy=ResponseStrategy.ASK_CLARIFICATION,
        tool_required=False,
        capability_intent=CapabilityIntent.NONE,
        clarification_reason=reason,
        trusted_reference_target=TrustedReferenceTarget.NONE,
        confidence=0.0,
        decision_source=DecisionSource.FALLBACK,
        direct_response=wording,
        controller_call_count=call_count,
        fallback_reason=reason.value,
    )


def decide_dialogue_turn(
    message: str,
    *,
    trusted_context: Optional[Mapping[str, Any]] = None,
    rule_hints: Sequence[DomainHint] = (),
    llm_classifier: Optional[ControllerLLM] = None,
    deadline: Optional[GlobalDeadline] = None,
) -> DialogueDecision:
    """Return exactly one validated decision, using rules before one optional LLM."""
    context = validate_dialogue_context(trusted_context)
    hints = [hint for hint in (list(rule_hints) + [_small_talk_hint(message), _bare_ambiguous_hint(message)]) if hint]
    if hints:
        hint = sorted(enumerate(hints), key=lambda item: (-item[1].priority, item[0]))[0][1]
        return DialogueDecision(
            turn_kind=hint.turn_kind,
            domain=hint.domain,
            relationship=hint.relationship,
            response_strategy=hint.response_strategy,
            tool_required=hint.tool_required,
            capability_intent=hint.capability_intent,
            clarification_reason=hint.clarification_reason,
            trusted_reference_target=hint.trusted_reference_target,
            confidence=hint.confidence,
            decision_source=DecisionSource.RULE,
            direct_response=hint.direct_response,
        )
    if llm_classifier is not None and deadline is not None and _needs_semantic_call(message, context):
        remaining = min(2.5, deadline.remaining(reserve_for_persistence=True))
        if remaining >= 0.25:
            try:
                parsed = _parse_llm_decision(llm_classifier(_controller_prompt(message, context), remaining))
                if parsed is not None:
                    return parsed
                return _fallback_decision(ClarificationReason.INVALID_CONTROLLER_OUTPUT, call_count=1)
            except TimeoutError:
                return _fallback_decision(ClarificationReason.CONTROLLER_TIMEOUT, call_count=1)
            except Exception:
                return _fallback_decision(ClarificationReason.CONTROLLER_FAILURE, call_count=1)
    return DialogueDecision(
        turn_kind=TurnKind.NEW_TOPIC,
        domain=DialogueDomain.GENERAL,
        relationship=DialogueRelationship.NEW_TOPIC,
        response_strategy=ResponseStrategy.EXECUTE_CAPABILITY,
        tool_required=True,
        capability_intent=CapabilityIntent.LEGACY_DOMAIN_ROUTE,
        clarification_reason=ClarificationReason.NONE,
        trusted_reference_target=TrustedReferenceTarget.NONE,
        confidence=0.8,
        decision_source=DecisionSource.RULE,
    )


_DIALOGUE_CONTEXT_KEYS = frozenset(
    {
        "semantic_revision",
        "previous_turn_kind",
        "previous_domain",
        "previous_capability_intent",
        "pending_clarification_type",
        "pending_clarification_domain",
        "canonical_parameters",
        "previous_result_summary",
    }
)


def validate_dialogue_context(value: Any) -> Dict[str, Any]:
    if value is None:
        return {
            "semantic_revision": 1,
            "previous_turn_kind": None,
            "previous_domain": None,
            "previous_capability_intent": None,
            "pending_clarification_type": None,
            "pending_clarification_domain": None,
            "canonical_parameters": {},
            "previous_result_summary": {},
        }
    if not isinstance(value, Mapping) or set(value) != _DIALOGUE_CONTEXT_KEYS:
        raise ValueError("dialogue context has an invalid schema")
    copied = json.loads(json.dumps(dict(value), ensure_ascii=True))
    if copied.get("semantic_revision") != 1:
        raise ValueError("dialogue context has an invalid revision")
    for key, enum_type in (
        ("previous_turn_kind", TurnKind),
        ("previous_domain", DialogueDomain),
        ("previous_capability_intent", CapabilityIntent),
    ):
        item = copied.get(key)
        if item is not None:
            enum_type(item)
    pending_type = copied.get("pending_clarification_type")
    pending_domain = copied.get("pending_clarification_domain")
    if pending_type is not None and pending_type not in {"311_ISSUE_SCOPE"}:
        raise ValueError("dialogue context has an invalid pending clarification")
    if pending_domain is not None:
        DialogueDomain(pending_domain)
    if bool(pending_type) != bool(pending_domain):
        raise ValueError("pending clarification fields must be paired")
    for key in ("canonical_parameters", "previous_result_summary"):
        if not isinstance(copied.get(key), dict):
            raise ValueError("dialogue context mappings are invalid")
    encoded = json.dumps(copied, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_DIALOGUE_CONTEXT_BYTES:
        raise ValueError("dialogue context exceeds the size limit")
    return copied


def next_dialogue_context(
    previous: Optional[Mapping[str, Any]],
    *,
    decision: DialogueDecision,
    canonical_parameters: Optional[Mapping[str, Any]] = None,
    previous_result_summary: Optional[Mapping[str, Any]] = None,
    pending_clarification_type: Optional[str] = None,
    pending_clarification_domain: Optional[DialogueDomain] = None,
) -> Dict[str, Any]:
    current = validate_dialogue_context(previous)
    preserve_summary = decision.turn_kind in {TurnKind.SMALL_TALK, TurnKind.RESPONSE_REPHRASE}
    value = {
        "semantic_revision": 1,
        "previous_turn_kind": decision.turn_kind.value,
        "previous_domain": decision.domain.value,
        "previous_capability_intent": decision.capability_intent.value,
        "pending_clarification_type": pending_clarification_type,
        "pending_clarification_domain": (
            pending_clarification_domain.value if pending_clarification_domain else None
        ),
        "canonical_parameters": (
            dict(current.get("canonical_parameters") or {})
            if preserve_summary and canonical_parameters is None
            else dict(canonical_parameters or {})
        ),
        "previous_result_summary": (
            dict(current.get("previous_result_summary") or {})
            if preserve_summary and previous_result_summary is None
            else dict(previous_result_summary or {})
        ),
    }
    return validate_dialogue_context(value)
