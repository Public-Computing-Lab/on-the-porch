"""Bounded, rule-only detection of clear active emergencies.

The detector consumes only the current validated resident message. It does not
accept history, client metadata, model output, or arbitrary extracted values.
Matching is concept- and grammar-oriented (actor + harm + immediacy), not a
catalog of one phrase per test case.
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
    ACTIVE_BREAK_IN = "active_break_in"
    BUILDING_FIRE = "building_fire"
    VEHICLE_FIRE = "vehicle_fire"
    SMOKE_IN_BUILDING = "smoke_in_building"
    UNRESPONSIVE_PERSON = "unresponsive_person"
    BREATHING_EMERGENCY = "breathing_emergency"
    SUSPECTED_HEART_ATTACK = "suspected_heart_attack"
    UNRESPONSIVE_OVERDOSE = "unresponsive_overdose"
    ACTIVE_OVERDOSE = "active_overdose"
    ACTIVE_SEIZURE = "active_seizure"
    CHILD_IN_DANGER = "child_in_danger"
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

# Shared concept fragments (compiled once).
_PERSON = (
    r"(?:someone|somebody|a person|a man|a woman|a stranger|they|he|she|"
    r"my (?:kid|child|baby|partner|neighbor)|an intruder)"
)
_PERSON_BE = (
    rf"(?:{_PERSON}(?:'s| is| are|'re)?|i(?:'m| am)|we(?:'re| are))"
)
_IMMEDIATE = (
    r"(?:right now|now|at this moment|this (?:second|instant)|as we speak|"
    r"currently|just (?:now|happened)|happening)"
)
_WEAPON = r"(?:gun|firearm|weapon|knife|pistol|rifle)"
_STRUCTURE = r"(?:house|home|apartment|building|unit|condo|flat|room)"
_VEHICLE = r"(?:car|vehicle|truck|van|suv)"
_CHILD = r"(?:(?:my |our |the )?(?:kid|child|baby|toddler|infant)|a child|a baby)"
_NONRESPONSIVE = (
    r"(?:unconscious|unresponsive|passed out|not responding|won't (?:wake|respond)|"
    r"will not (?:wake|respond)|can't wake|cannot wake|not waking)"
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


_VULNERABLE = (
    r"(?:(?:my |our |the )?(?:kid|child|baby|toddler|infant|elder|elderly|"
    r"grandma|grandfather|grandpa|grandmother|disabled (?:person|neighbor))|"
    r"a child|a baby|an elderly (?:person|neighbor))"
)

# Reported / non-current framing that must not trigger the fixed emergency reply.
_NON_CURRENT_CONTEXT = _patterns(
    r"^(?:someone (?:told|said|asked)|they (?:said|told)|my (?:friend|neighbor) "
    r"(?:said|told|asked)|i (?:heard|read|saw) (?:that|about)|"
    r"according to|in the news|on the news|the article|the story|"
    r"a (?:news )?report|hypothetical(?:ly)?|in (?:a |this )?movie|"
    r"in (?:a |this )?book|in (?:a |this )?novel|for (?:a )?story|"
    r"role[- ]?play|what if|suppose|imagine)\b",
    r"\b(?:last (?:year|month|week|night)|yesterday|in \d{4}|years ago|"
    r"when i was|back in|used to|historically|previously)\b.*"
    r"\b(?:fire|shooting|gunshots|broke in|intruder|overdose|heart attack)\b",
    r'^[\'"].*[\'"]$',
    r'\bsaid[,:]?\s+[\'"].*(?:help|911|gun|fire|break(?:ing)? in)',
    r"\b(?:quoting|quote|as (?:a )?quote)\b",
)


def _is_non_current_emergency_language(normalized: str) -> bool:
    """True for quoted, historical, fictional, or second-hand emergency wording."""
    return any(pattern.search(normalized) for pattern in _NON_CURRENT_CONTEXT)


_RULES: Tuple[
    Tuple[EmergencyCategory, EmergencyReasonCode, Tuple[Pattern[str], ...]],
    ...,
] = (
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ONGOING_SHOOTING,
        _patterns(
            rf"\bthere(?:'s| is) (?:a )?shooting "
            rf"(?:happening|going on|outside|nearby)"
            rf"(?:.*\b(?:{_IMMEDIATE})\b)?",
            rf"\b{_PERSON_BE} shooting\b.*\b(?:outside|nearby|{_IMMEDIATE})\b",
            rf"\bactive shooter\b",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.HEARD_GUNSHOTS_NOW,
        _patterns(
            rf"\b(?:i|we) (?:can )?(?:hear|am hearing|'m hearing|just heard) "
            rf"(?:gunshots|shots (?:being )?fired)"
            rf"(?:.*\b(?:{_IMMEDIATE})\b)?",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ARMED_THREAT,
        _patterns(
            rf"\b{_PERSON_BE} "
            rf"(?:has|have|holding|pointing|pointed) (?:a )?{_WEAPON}\b.*"
            rf"\b(?:threatening|pointing (?:it )?at|trying to "
            rf"(?:shoot|stab|hurt|kill)|at (?:me|us|them))\b",
            rf"\bthere(?:'s| is) someone "
            rf"(?:outside |nearby )?(?:with|holding|carrying) "
            rf"(?:a )?{_WEAPON}\b",
            rf"\b(?:{_PERSON}) (?:pulled|drew) (?:a |their )?{_WEAPON}\b",
            rf"\bat gunpoint\b",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ARMED_BREAK_IN,
        _patterns(
            rf"\b{_PERSON_BE} "
            rf"(?:trying to break|breaking) in\b.*"
            rf"\b(?:has|with|holding|carrying) (?:a )?{_WEAPON}\b",
            rf"\b{_PERSON}.*\b(?:{_WEAPON})\b.*"
            rf"\b(?:trying to break|breaking) in\b",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ACTIVE_BREAK_IN,
        _patterns(
            rf"\b(?:{_PERSON_BE}|an intruder) "
            rf"(?:trying to break|breaking) in\b",
            rf"\b(?:someone|somebody|an intruder) (?:is |just )?"
            rf"(?:broke|broken) into (?:my |our )?{_STRUCTURE}\b",
            rf"\bthere(?:'s| is) (?:an )?intruder (?:in|inside) "
            rf"(?:my |our )?{_STRUCTURE}\b",
            rf"\b(?:i|we) (?:just )?(?:caught|saw) (?:someone|somebody) "
            rf"(?:breaking|broke) in\b",
            rf"\b(?:active |current )?break[- ]?in (?:in|at|inside) "
            rf"(?:my |our )?{_STRUCTURE}\b",
            rf"\bsomeone (?:just )?broke into (?:my |our )home\b",
            rf"\bi (?:think )?there(?:'s| is) (?:a |an )?(?:burglar|intruder) "
            rf"(?:in|inside) (?:my |our )?{_STRUCTURE} "
            rf"(?:right now|now)\b",
        ),
    ),
    (
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ACTIVE_ATTACK,
        _patterns(
            rf"\b{_PERSON_BE} "
            rf"(?:attacking|stabbing|beating|assaulting|hurting) "
            rf"(?:someone|a person|people|me|him|her|them|{_CHILD})\b",
            rf"\b(?:i(?:'m| am)|we are) being "
            rf"(?:attacked|assaulted|beaten|robbed)\b",
        ),
    ),
    (
        EmergencyCategory.OTHER_IMMEDIATE_DANGER,
        EmergencyReasonCode.CHILD_IN_DANGER,
        _patterns(
            rf"\b{_CHILD}\b.*\b(?:in (?:immediate )?danger|being "
            rf"(?:hurt|attacked|abducted|taken)|won't wake|"
            rf"not breathing|unconscious)\b",
            rf"\b(?:someone|somebody) (?:is )?(?:hurting|attacking|abducting|"
            rf"kidnapping) (?:my |our |the )?{_CHILD}\b",
            rf"\b{_VULNERABLE}\b.*\b(?:in (?:immediate )?danger|being "
            rf"(?:hurt|attacked|hurt right now))\b",
            rf"\b(?:someone|somebody) (?:is )?(?:hurting|attacking) "
            rf"(?:my |our |the )?{_VULNERABLE}\b",
            rf"\b(?:my |our )?{_VULNERABLE} (?:is|are) "
            rf"(?:in immediate danger|being hurt)\b",
        ),
    ),
    (
        EmergencyCategory.FIRE,
        EmergencyReasonCode.BUILDING_FIRE,
        _patterns(
            rf"\b(?:my|our|the) {_STRUCTURE} (?:is|'s) on fire\b",
            rf"\bthere(?:'s| is) (?:a )?fire (?:in|inside|at) "
            rf"(?:my |our |the )?{_STRUCTURE}"
            rf"(?:.*\b(?:{_IMMEDIATE})\b)?",
            rf"\b(?:kitchen|bedroom|basement|hallway) (?:is|'s) on fire\b",
        ),
    ),
    (
        EmergencyCategory.FIRE,
        EmergencyReasonCode.VEHICLE_FIRE,
        _patterns(
            rf"\b(?:my|our|the) {_VEHICLE} (?:is|'s) on fire\b",
            rf"\bthere(?:'s| is) (?:a )?fire (?:in|inside|under) "
            rf"(?:my |our |the )?{_VEHICLE}\b",
        ),
    ),
    (
        EmergencyCategory.FIRE,
        EmergencyReasonCode.SMOKE_IN_BUILDING,
        _patterns(
            rf"\b(?:my|our|the) {_STRUCTURE} "
            rf"(?:is )?(?:filling|filled) with smoke\b",
            rf"\bthere(?:'s| is) smoke "
            rf"(?:filling|in) (?:my |our |the )?{_STRUCTURE}\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.UNRESPONSIVE_OVERDOSE,
        _patterns(
            rf"\b{_PERSON_BE} (?:has )?(?:overdosed|od(?:'d|ed)?) "
            rf"and (?:is |are )?{_NONRESPONSIVE}\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.ACTIVE_OVERDOSE,
        _patterns(
            rf"\b(?:{_PERSON_BE}|i(?:'m| am)?) "
            rf"(?:overdosing|overdosed|od(?:'d|ed)?)\b",
            rf"\b(?:i think )?{_PERSON_BE} overdosing\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.ACTIVE_SEIZURE,
        _patterns(
            rf"\b{_PERSON_BE} having "
            rf"(?:a )?seizure(?: and (?:will not|won't|is not|isn't) "
            rf"(?:wake up|respond))?\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.UNRESPONSIVE_PERSON,
        _patterns(
            rf"\b{_PERSON_BE} {_NONRESPONSIVE}\b",
            rf"\b(?:i|we) (?:can't|cannot) wake "
            rf"(?:him|her|them|someone|{_CHILD})\b",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.BREATHING_EMERGENCY,
        _patterns(
            # End-of-clause / present-severity cues avoid educational,
            # historical, exertion, and figurative follow-ons.
            r"\bi (?:can't|cannot) breathe(?:[.!?]|$)",
            rf"\bi(?:'m| am) unable to breathe(?:[.!?]|$|"
            rf".*\b(?:{_IMMEDIATE})\b)",
            rf"\bi(?:'m| am) having (?:severe )?"
            rf"(?:trouble|difficulty) breathing"
            rf".*\b(?:{_IMMEDIATE})\b",
            rf"\b{_PERSON_BE} "
            rf"(?:has |have )?(?:just )?stopped breathing\b",
            rf"\b{_PERSON}.*"
            r"\b(?:can't|cannot) breathe(?:[.!?]|$)",
            r"\b(?:my|their) throat is closing and "
            r"(?:i|they|he|she) (?:can't|cannot) breathe(?:[.!?]|$)",
        ),
    ),
    (
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.SUSPECTED_HEART_ATTACK,
        _patterns(
            r"\b(?:i think )?i(?:'m| am) (?:having|might be having) "
            r"(?:a )?heart attack\b",
            rf"\b{_PERSON_BE} (?:having|might be having) "
            r"(?:a )?heart attack\b",
        ),
    ),
    (
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_SELF_HARM,
        _patterns(
            rf"\b{_PERSON_BE} (?:says? )?(?:they |he |she )?"
            rf"going to (?:kill|hurt) "
            rf"(?:themselves|himself|herself).*\b(?:{_IMMEDIATE})\b",
            r"\bi(?:'m| am) (?:about to|going to) (?:hurt|kill) myself\b",
        ),
    ),
    (
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_HARM_TO_OTHERS,
        _patterns(
            rf"\b{_PERSON_BE} "
            rf"threatening to (?:kill|shoot|stab|hurt) "
            rf"(?:another person|someone|people|me|him|her|them|{_CHILD}).*"
            rf"\b(?:{_IMMEDIATE})\b",
        ),
    ),
    (
        EmergencyCategory.OTHER_IMMEDIATE_DANGER,
        EmergencyReasonCode.EXPLICIT_IMMEDIATE_DANGER,
        _patterns(
            rf"\b(?:i am|i'm|we are|someone is) in immediate danger "
            rf"(?:{_IMMEDIATE})\b",
            r"\b(?:i am|i'm|we are) in immediate danger\b",
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
    if _is_non_current_emergency_language(normalized):
        return _inactive(EmergencyReasonCode.NO_MATCH)
    for category, reason, patterns in _RULES:
        if any(pattern.search(normalized) for pattern in patterns):
            return _active(category, reason)
    return _inactive(EmergencyReasonCode.NO_MATCH)
