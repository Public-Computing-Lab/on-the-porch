"""Detect when a civic question asks about multiple topics and build focused sub-questions.

Used by prepared SQL to run one query per topic (e.g. 311 AND crime) and merge
answers conversationally. Rule-based only — no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

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


def _classify_911(question: str) -> str:
    try:
        from sql_chat.prepared_sql import classify_911_question  # noqa: WPS433

        return classify_911_question(question)
    except Exception:
        if "911" not in _q(question):
            return "none"
        return "crime_redirect"


def _mentions_911_dispatch(q: str, question: str) -> bool:
    return _classify_911(question) == "dispatch_unavailable"


def _mentions_911_crime(q: str, question: str) -> bool:
    return _classify_911(question) == "crime_redirect"


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


def _crime_topic_for_911(q: str) -> str:
    if _mentions_shootings(q):
        return "shootings"
    if _mentions_homicide(q):
        return "homicide"
    return "crime"


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
    if _mentions_911_dispatch(q, question):
        topics.append("911")
    elif _mentions_911_crime(q, question):
        crime_topic = _crime_topic_for_911(q)
        if crime_topic not in topics:
            topics.append(crime_topic)
    if _mentions_shootings(q) and "shootings" not in topics:
        topics.append("shootings")
    if _mentions_homicide(q) and "homicide" not in topics:
        topics.append("homicide")
    if _mentions_crime_general(q) and "crime" not in topics:
        topics.append("crime")
    if _mentions_events(q) and any(h in q for h in ("how many", "count", "number of", "list", "what")):
        topics.append("events")

    if len(topics) < 2:
        return []

    return [
        CivicPart(topic=topic, sub_question=build_focused_subquestion(question, topic))
        for topic in topics
    ]
