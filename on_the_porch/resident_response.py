"""Resident-facing labels and safe degraded-response wording."""

from __future__ import annotations

import re
from typing import Iterable, Optional


_EVENT_DETAIL_INTENTS = frozenset({"event_by_name"})
_EVENT_INTENTS = frozenset({"events_by_source", "events_list", "events_weekend"})
_COMMUNITY_INTENTS = frozenset({"community", "opinion", "transcript"})
_PUBLIC_SAFETY_INTENTS = frozenset({
    "crime_general",
    "crime_hybrid",
    "crime_opinion",
    "crime_safety_context",
    "opinion_safety",
    "safety_overview",
})
_INTERNAL_IDENTIFIER_PATTERN = re.compile(
    r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b",
)
_INTERNAL_TERM_PATTERNS = (
    ("technical acronym", re.compile(r"\b(?:RAG|SQL)\b")),
    ("Chroma", re.compile(r"\bChroma\b", re.IGNORECASE)),
    ("Qdrant", re.compile(r"\bQdrant\b", re.IGNORECASE)),
    (
        "internal route",
        re.compile(r"\b(?:hybrid|rag|sql)\s+(?:mode|path|pipeline|route)\b", re.IGNORECASE),
    ),
    ("retrieval failure", re.compile(r"\bretrieval\s+failure\b", re.IGNORECASE)),
    ("tool call", re.compile(r"\btool\s+call\b", re.IGNORECASE)),
    ("Gemini", re.compile(r"\bGemini\b", re.IGNORECASE)),
    (
        "exception name",
        re.compile(r"\b[A-Z][A-Za-z0-9_.]*(?:Error|Exception)\b"),
    ),
)


def resident_topic_for_intent(intent: Optional[str]) -> Optional[str]:
    """Map internal intent identifiers to a small resident-facing vocabulary."""
    key = (intent or "").strip().lower()
    if not key:
        return None
    if key in _EVENT_DETAIL_INTENTS:
        return "event details"
    if key in _EVENT_INTENTS or key.startswith("event"):
        return "event information"
    if key.startswith("311_"):
        return "city-service information"
    if key.startswith("crime_") or key in _PUBLIC_SAFETY_INTENTS or "safety" in key:
        return "public-safety information"
    if key in _COMMUNITY_INTENTS or any(
        token in key for token in ("community", "opinion", "transcript")
    ):
        return "community information"
    return "information"


def execution_failure_message(
    *,
    path: str,
    intent: Optional[str] = None,
    timed_out: bool = False,
) -> str:
    """Return failure wording without exposing routes, providers, or intent IDs."""
    if (intent or "").lower() in _EVENT_DETAIL_INTENTS:
        return "I found the event listing, but I could not load additional details right now."
    topic = resident_topic_for_intent(intent)
    if topic is None:
        topic = "community information" if path == "rag" else "information"
    label = topic[0].upper() + topic[1:]
    if timed_out:
        return f"{label} was not available before this request timed out."
    return f"{label} is temporarily unavailable."


def internal_terms(text: str) -> list[str]:
    """List internal labels present in a resident-facing response."""
    content = text or ""
    found = list(dict.fromkeys(_INTERNAL_IDENTIFIER_PATTERN.findall(content)))
    found.extend(
        label
        for label, pattern in _INTERNAL_TERM_PATTERNS
        if pattern.search(content) and label not in found
    )
    return found


def sanitize_degraded_answer(
    answer: str,
    *,
    fallback: str,
    internal_identifiers: Iterable[str] = (),
) -> str:
    """Replace only degraded answers that expose implementation details."""
    found = internal_terms(answer)
    for identifier in internal_identifiers:
        value = (identifier or "").strip()
        if "_" not in value:
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", answer or "", re.I):
            found.append(value)
    found = list(dict.fromkeys(found))
    if not found:
        return answer
    print(f"  Warning: sanitized internal terms from degraded answer: {', '.join(found)}")
    safe_fallback = (fallback or "").strip()
    if safe_fallback and not internal_terms(safe_fallback):
        return safe_fallback
    return "Some requested information is temporarily unavailable."
