"""Resident-facing labels and safe degraded-response wording."""

from __future__ import annotations

import re
from typing import Iterable, Optional


_EVENT_DETAIL_INTENTS = frozenset({"event_by_name"})
_RESIDENT_CATEGORIES = frozenset({
    "city-service information",
    "community information",
    "event details",
    "events",
    "general information",
    "local news",
    "neighborhood information",
    "public-safety information",
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


def resident_category(
    topic: Optional[str] = None,
    *,
    intent: Optional[str] = None,
) -> str:
    """Map untrusted planner labels to a fixed resident-facing vocabulary."""
    topic_key = re.sub(r"[\s-]+", " ", str(topic or "").strip().lower())
    if topic_key in _RESIDENT_CATEGORIES:
        return topic_key

    control_key = " ".join(
        value for value in (topic_key, str(intent or "").strip().lower()) if value
    )
    if "event_by_name" in control_key or "event detail" in control_key:
        return "event details"
    if "event" in control_key:
        return "events"
    if any(token in control_key for token in ("311", "city service", "pothole")):
        return "city-service information"
    if any(token in control_key for token in ("crime", "safety", "shooting", "homicide")):
        return "public-safety information"
    if any(token in control_key for token in ("community", "opinion", "transcript")):
        return "community information"
    if any(token in control_key for token in ("news", "rss", "article")):
        return "local news"
    if any(token in control_key for token in ("neighborhood", "location", "area")):
        return "neighborhood information"
    return "general information"


def resident_topic_for_intent(intent: Optional[str]) -> Optional[str]:
    """Map internal intent identifiers to a small resident-facing vocabulary."""
    if not (intent or "").strip():
        return None
    return resident_category(intent=intent)


def execution_failure_message(
    *,
    path: str,
    intent: Optional[str] = None,
    topic: Optional[str] = None,
    timed_out: bool = False,
) -> str:
    """Return failure wording without exposing routes, providers, or intent IDs."""
    if (intent or "").lower() in _EVENT_DETAIL_INTENTS:
        return "I found the event listing, but I could not load additional details right now."
    safe_topic = (
        resident_category(topic, intent=intent)
        if topic
        else resident_topic_for_intent(intent)
    )
    if safe_topic is None:
        safe_topic = "community information" if path == "rag" else "general information"
    label = safe_topic[0].upper() + safe_topic[1:]
    verb = "were" if safe_topic in ("event details", "events") else "was"
    present_verb = "are" if safe_topic in ("event details", "events") else "is"
    if timed_out:
        return f"{label} {verb} not available before this request timed out."
    return f"{label} {present_verb} temporarily unavailable."


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
