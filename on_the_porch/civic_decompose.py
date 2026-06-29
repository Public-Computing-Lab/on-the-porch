"""Detect when a civic question asks about multiple topics and build focused sub-questions.

Used by prepared SQL to run one query per topic (e.g. 311 AND 911) and merge
answers conversationally. Rule-based only — no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from query_plan import resolve_period


@dataclass
class CivicPart:
    topic: str
    sub_question: str


def _q(question: str) -> str:
    return (question or "").lower().strip()


def _mentions_311(q: str) -> bool:
    if "311" in q:
        return True
    return any(
        h in q
        for h in (
            "service request", "service requests", "city complaint",
            "pothole", "potholes", "missed trash", "streetlight", "street light",
        )
    )


def _mentions_911_dispatch(q: str) -> bool:
    if "911" not in q:
        return False
    if any(h in q for h in ("police report", "crime report", "incident report", "bpd")):
        return False
    return any(h in q for h in ("call", "calls", "dispatch", "emergency", "how many", "count", "number of", "total"))


def _mentions_shootings(q: str) -> bool:
    return any(h in q for h in ("shooting", "shootings", "shots fired", "gun violence"))


def _mentions_homicide(q: str) -> bool:
    return any(h in q for h in ("homicide", "homicides", "murder", "murders"))


def _mentions_crime_general(q: str) -> bool:
    if _mentions_shootings(q) or _mentions_homicide(q):
        return False
    return any(
        h in q
        for h in (
            "crime", "crimes", "incident", "incidents", "assault", "robbery",
            "offense", "offenses", "police report",
        )
    )


def _mentions_events(q: str) -> bool:
    return any(h in q for h in ("event", "events", "calendar", "happening", "what's on", "whats on"))


def _period_suffix(question: str) -> str:
    ql = _q(question)
    if "last month" in ql:
        return "last month"
    if "this month" in ql:
        return "this month"
    if "last week" in ql:
        return "last week"
    if "this week" in ql:
        return "this week"
    if "this year" in ql:
        return "this year"
    if "last year" in ql:
        return "last year"
    if "last 30" in ql or "30 day" in ql:
        return "in the last 30 days"
    if "last 90" in ql or "90 day" in ql:
        return "in the last 90 days"
    window = resolve_period(question, 30)
    return window.label


def build_focused_subquestion(question: str, topic: str) -> str:
    """Narrow a multi-topic question to one civic topic (keeps time range)."""
    period = _period_suffix(question)
    if topic == "311":
        return f"How many 311 service requests in Dorchester {period}?"
    if topic == "911":
        return f"How many 911 emergency calls in Dorchester {period}?"
    if topic == "shootings":
        return f"How many shootings in Dorchester {period}?"
    if topic == "homicide":
        return f"How many homicides in Dorchester {period}?"
    if topic == "crime":
        return f"How many crime incidents in Dorchester {period}?"
    if topic == "events":
        return f"What events are happening in Dorchester {period}?"
    return question


def decompose_civic_question(question: str) -> List[CivicPart]:
    """Return focused sub-questions when multiple civic topics are requested."""
    q = _q(question)
    if not q:
        return []

    topics: List[str] = []
    if _mentions_311(q):
        topics.append("311")
    if _mentions_911_dispatch(q):
        topics.append("911")
    if _mentions_shootings(q):
        topics.append("shootings")
    if _mentions_homicide(q):
        topics.append("homicide")
    if _mentions_crime_general(q):
        topics.append("crime")
    if _mentions_events(q) and any(h in q for h in ("how many", "count", "number of", "list", "what")):
        topics.append("events")

    if len(topics) < 2:
        return []

    return [
        CivicPart(topic=topic, sub_question=build_focused_subquestion(question, topic))
        for topic in topics
    ]
