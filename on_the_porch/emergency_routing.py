"""Bounded, rule-only detection of clear active emergencies.

The detector consumes only the current validated resident message. It does not
accept history, client metadata, model output, or arbitrary extracted values.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Pattern, Tuple


MAX_MESSAGE_CHARS = 8_000
MAX_SERIALIZED_BYTES = 512
EMERGENCY_RESPONSE = (
    "Call 911 now. If you can do so safely, move away from the immediate "
    "danger and do not confront anyone. Give the dispatcher your exact "
    "location and follow their instructions."
)


class EmergencyCategory(str, Enum):
    VIOLENT_THREAT = "VIOLENT_THREAT"
    FIRE = "FIRE"
    MEDICAL_EMERGENCY = "MEDICAL_EMERGENCY"
    IMMINENT_SELF_HARM_OR_HARM = "IMMINENT_SELF_HARM_OR_HARM"
    OTHER_IMMEDIATE_DANGER = "OTHER_IMMEDIATE_DANGER"
    NONE = "NONE"


class EmergencyReasonCode(str, Enum):
    ONGOING_SHOOTING = "ongoing_shooting"
    HEARD_GUNSHOTS_NOW = "heard_gunshots_now"
    ARMED_THREAT = "armed_threat"
    ACTIVE_ATTACK = "active_attack"
    ARMED_BREAK_IN = "armed_break_in"
    BUILDING_FIRE = "building_fire"
    SMOKE_IN_BUILDING = "smoke_in_building"
    UNRESPONSIVE_PERSON = "unresponsive_person"
    BREATHING_EMERGENCY = "breathing_emergency"
    SUSPECTED_HEART_ATTACK = "suspected_heart_attack"
    UNRESPONSIVE_OVERDOSE = "unresponsive_overdose"
    ACTIVE_SEIZURE = "active_seizure"
    IMMINENT_SELF_HARM = "imminent_self_harm"
    IMMINENT_HARM_TO_OTHERS = "imminent_harm_to_others"
    EXPLICIT_IMMEDIATE_DANGER = "explicit_immediate_danger"
    NO_MATCH = "no_match"
    INVALID_INPUT = "invalid_input"


class EmergencyDecisionSource(str, Enum):
    RULE = "rule"


_DECISION_KEYS = frozenset(
    {
        "is_active_emergency",
        "category",
        "reason_code",
        "decision_source",
        "confidence",
    }
)
_INACTIVE_REASONS = frozenset(
    {EmergencyReasonCode.NO_MATCH, EmergencyReasonCode.INVALID_INPUT}
)


@dataclass(frozen=True)
class EmergencyDecision:
    is_active_emergency: bool
    category: EmergencyCategory
    reason_code: EmergencyReasonCode
    decision_source: EmergencyDecisionSource
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.is_active_emergency, bool):
            raise ValueError("is_active_emergency must be boolean")
        if not isinstance(self.category, EmergencyCategory):
            raise ValueError("category must be a bounded emergency category")
        if not isinstance(self.reason_code, EmergencyReasonCode):
            raise ValueError("reason_code must be a bounded emergency reason")
        if self.decision_source is not EmergencyDecisionSource.RULE:
            raise ValueError("emergency decisions must be rule-based")
        expected_confidence = 1.0 if self.is_active_emergency else 0.0
        if (
            isinstance(self.confidence, bool)
            or float(self.confidence) != expected_confidence
        ):
            raise ValueError("emergency confidence must match the decision")
        if self.is_active_emergency:
            if self.category is EmergencyCategory.NONE:
                raise ValueError("active emergencies require a category")
            if self.reason_code in _INACTIVE_REASONS:
                raise ValueError("active emergencies require an active reason")
        elif (
            self.category is not EmergencyCategory.NONE
            or self.reason_code not in _INACTIVE_REASONS
        ):
            raise ValueError(
                "inactive decisions must use NONE and an inactive reason"
            )
        self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        value = {
            "is_active_emergency": self.is_active_emergency,
            "category": self.category.value,
            "reason_code": self.reason_code.value,
            "decision_source": self.decision_source.value,
            "confidence": self.confidence,
        }
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_SERIALIZED_BYTES:
            raise ValueError("emergency decision exceeds the size limit")
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EmergencyDecision":
        if not isinstance(value, Mapping) or set(value) != _DECISION_KEYS:
            raise ValueError("emergency decision has an invalid schema")
        active = value.get("is_active_emergency")
        confidence = value.get("confidence")
        if not isinstance(active, bool):
            raise ValueError("is_active_emergency must be boolean")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
        ):
            raise ValueError("confidence must be numeric")
        return cls(
            is_active_emergency=active,
            category=EmergencyCategory(value.get("category")),
            reason_code=EmergencyReasonCode(value.get("reason_code")),
            decision_source=EmergencyDecisionSource(
                value.get("decision_source")
            ),
            confidence=float(confidence),
        )


def _inactive(reason: EmergencyReasonCode) -> EmergencyDecision:
    return EmergencyDecision(
        is_active_emergency=False,
        category=EmergencyCategory.NONE,
        reason_code=reason,
        decision_source=EmergencyDecisionSource.RULE,
        confidence=0.0,
    )


def _active(
    category: EmergencyCategory,
    reason: EmergencyReasonCode,
) -> EmergencyDecision:
    return EmergencyDecision(
        is_active_emergency=True,
        category=category,
        reason_code=reason,
        decision_source=EmergencyDecisionSource.RULE,
        confidence=1.0,
    )


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).lower().replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _patterns(*values: str) -> Tuple[Pattern[str], ...]:
    return tuple(re.compile(value, flags=re.IGNORECASE) for value in values)


_RULES: Tuple[
    Tuple[EmergencyCategory, EmergencyReasonCode, Tuple[Pattern[str], ...]],
    ...,
] = (
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ONGOING_SHOOTING,
        _patterns(
            r"\bthere(?:'s| is) (?:a )?shooting "
            r"(?:happening|going on|outside|nearby).*\b(?:right now|now)\b",
            r"\b(?:someone(?:'s| is)|somebody (?:is|'s)) shooting\b"
            r".*\b(?:outside|nearby|right now|now)\b",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.HEARD_GUNSHOTS_NOW,
        _patterns(
            r"\b(?:i|we) (?:can )?(?:hear|am hearing|'m hearing) "
            r"(?:gunshots|shots (?:being )?fired).*\b(?:right now|now)\b",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ARMED_THREAT,
        _patterns(
            r"\b(?:someone|somebody|a person|they) "
            r"(?:has|have|is holding|are holding|is pointing|are pointing) "
            r"(?:a )?(?:gun|firearm|weapon|knife)\b.*"
            r"\b(?:threatening|pointing (?:it )?at|trying to "
            r"(?:shoot|stab|hurt|kill))\b",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ARMED_BREAK_IN,
        _patterns(
            r"\b(?:someone|somebody|a person) (?:is )?"
            r"(?:trying to break|breaking) in\b.*"
            r"\b(?:has|with|holding|carrying) (?:a )?(?:weapon|gun|knife)\b",
            r"\b(?:someone|somebody|a person).*\b(?:weapon|gun|knife)\b.*"
            r"\b(?:trying to break|breaking) in\b",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ACTIVE_ATTACK,
        _patterns(
            r"\b(?:someone|somebody|a person) (?:is )?"
            r"(?:attacking|stabbing|beating|assaulting) "
            r"(?:someone|a person|people|me|him|her|them)\b",
        ),
    ),
    (
        EmergencyCategory.FIRE,
        EmergencyReasonCode.BUILDING_FIRE,
        _patterns(
            r"\b(?:my|our|the) (?:house|home|apartment|building) "
            r"(?:is|'s) on fire\b",
            r"\bthere(?:'s| is) (?:a )?fire (?:in|inside|at) "
            r"(?:my |our |the )?(?:house|home|apartment|building).*"
            r"\b(?:right now|now)\b",
        ),
    ),
    (
        EmergencyCategory.FIRE,
        EmergencyReasonCode.SMOKE_IN_BUILDING,
        _patterns(
            r"\b(?:my|our|the) (?:apartment|house|home|building|room) "
            r"(?:is )?(?:filling|filled) with smoke\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.UNRESPONSIVE_OVERDOSE,
        _patterns(
            r"\b(?:someone|a person|he|she|they) (?:has )?overdosed "
            r"and (?:is |are )?not responding\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.ACTIVE_SEIZURE,
        _patterns(
            r"\b(?:someone|a person|he|she|they) (?:is |are )?having "
            r"(?:a )?seizure and (?:will not|won't|is not|isn't) "
            r"(?:wake up|respond)\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.UNRESPONSIVE_PERSON,
        _patterns(
            r"\b(?:someone|a person|he|she|they) (?:is |are )?"
            r"(?:unconscious|passed out) and (?:is |are )?not responding\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.BREATHING_EMERGENCY,
        _patterns(
            # Require the complaint to end the clause so educational or
            # figurative follow-ons ("when I laugh", "underwater") do not match.
            r"\bi (?:can't|cannot) breathe(?:[.!?]|$)",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.SUSPECTED_HEART_ATTACK,
        _patterns(
            r"\b(?:i think )?i(?:'m| am) (?:having|might be having) "
            r"(?:a )?heart attack\b",
        ),
    ),
    (
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_SELF_HARM,
        _patterns(
            r"\b(?:someone|he|she|they) (?:says? )?(?:they |he |she )?"
            r"(?:are|is) going to (?:kill|hurt) "
            r"(?:themselves|himself|herself).*\b(?:right now|now)\b",
            r"\bi(?:'m| am) (?:about to|going to) (?:hurt|kill) myself\b",
        ),
    ),
    (
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_HARM_TO_OTHERS,
        _patterns(
            r"\b(?:someone|a person|he|she|they) (?:is |are )?"
            r"threatening to (?:kill|shoot|stab|hurt) "
            r"(?:another person|someone|people|me|him|her|them).*"
            r"\b(?:right now|now)\b",
        ),
    ),
    (
        EmergencyCategory.OTHER_IMMEDIATE_DANGER,
        EmergencyReasonCode.EXPLICIT_IMMEDIATE_DANGER,
        _patterns(
            r"\b(?:i am|i'm|we are|someone is) in immediate danger "
            r"(?:right now|now)\b",
        ),
    ),
)


def detect_active_emergency(message: Any) -> EmergencyDecision:
    """Classify only explicit current/imminent danger; never call a model."""
    if not isinstance(message, str) or not message.strip():
        return _inactive(EmergencyReasonCode.INVALID_INPUT)
    if len(message) > MAX_MESSAGE_CHARS:
        return _inactive(EmergencyReasonCode.INVALID_INPUT)

    normalized = _normalize(message)
    for category, reason, patterns in _RULES:
        if any(pattern.search(normalized) for pattern in patterns):
            return _active(category, reason)
    return _inactive(EmergencyReasonCode.NO_MATCH)
