"""Resident-facing labels and safe degraded-response wording."""

from __future__ import annotations

import re
from typing import Optional


_EVENT_DETAIL_INTENTS = frozenset({"event_by_name"})
_INTERNAL_TERM_PATTERNS = (
    ("event_by_name", re.compile(r"\bevent_by_name\b", re.IGNORECASE)),
    ("prepared_sql", re.compile(r"\bprepared_sql\b", re.IGNORECASE)),
    ("RAG", re.compile(r"\bRAG\b", re.IGNORECASE)),
    ("Chroma", re.compile(r"\bChroma\b", re.IGNORECASE)),
    ("Qdrant", re.compile(r"\bQdrant\b", re.IGNORECASE)),
    ("SQL execution", re.compile(r"\bSQL\s+execution\b", re.IGNORECASE)),
    ("retrieval failure", re.compile(r"\bretrieval\s+failure\b", re.IGNORECASE)),
    ("tool call", re.compile(r"\btool\s+call\b", re.IGNORECASE)),
    (
        "exception name",
        re.compile(r"\b[A-Z][A-Za-z0-9_.]*(?:Error|Exception)\b"),
    ),
)


def resident_topic_for_intent(intent: Optional[str]) -> Optional[str]:
    """Return a display-safe topic for intents that need one."""
    if (intent or "").lower() in _EVENT_DETAIL_INTENTS:
        return "event details"
    return None


def execution_failure_message(
    *,
    path: str,
    intent: Optional[str] = None,
    timed_out: bool = False,
) -> str:
    """Return failure wording without exposing routes, providers, or intent IDs."""
    if (intent or "").lower() in _EVENT_DETAIL_INTENTS:
        return "I found the event listing, but I could not load additional details right now."
    if path == "rag":
        if timed_out:
            return "Community information was not available before this request timed out."
        return "Community information is temporarily unavailable."
    if path == "sql":
        if timed_out:
            return "The requested information was not available before this request timed out."
        return "The requested information is temporarily unavailable."
    return "Part of the requested information is temporarily unavailable."


def internal_terms(text: str) -> list[str]:
    """List internal labels present in a resident-facing response."""
    return [label for label, pattern in _INTERNAL_TERM_PATTERNS if pattern.search(text or "")]


def sanitize_degraded_answer(answer: str, *, fallback: str) -> str:
    """Replace only degraded answers that expose implementation details."""
    found = internal_terms(answer)
    if not found:
        return answer
    print(f"  Warning: sanitized internal terms from degraded answer: {', '.join(found)}")
    safe_fallback = (fallback or "").strip()
    if safe_fallback and not internal_terms(safe_fallback):
        return safe_fallback
    return "Some requested information is temporarily unavailable."
