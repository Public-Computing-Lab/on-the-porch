"""Canonical normalization for vague civic questions (Phase 1).

Maps natural language to structured period/metric hints used by the router
and prepared SQL catalog. No LLM calls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class QueryPlan:
    period_days: int
    period_label: str
    calendar_year: bool = False
    metric: Optional[str] = None
    sources: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def days_from_question(question: str, default: int = 7) -> int:
    q = (question or "").lower()
    if "today" in q:
        return 1
    if "yesterday" in q:
        return 2
    if "this weekend" in q or ("weekend" in q and "last" not in q):
        return 7
    if "this week" in q:
        return 7
    if "last week" in q:
        return 14
    if "next week" in q:
        return 14
    if "this month" in q:
        return 30
    if "last month" in q:
        return 30
    if "this year" in q:
        return 365
    if "last year" in q:
        return 730
    if "30 day" in q or "last 30" in q:
        return 30
    if "90 day" in q or "last 90" in q:
        return 90
    return default


def period_from_question(question: str, default: int = 7) -> tuple[int, str]:
    """Map vague time phrases to (lookback_days, human-readable period label)."""
    days = days_from_question(question, default)
    q = (question or "").lower()
    if "this year" in q:
        return days, "this year (calendar year to date)"
    if "last year" in q:
        return days, "last year"
    if "last month" in q:
        return 30, "the last 30 days"
    if "this month" in q:
        return 30, "the last 30 days"
    if "last week" in q:
        return 14, "the last 14 days"
    if "this week" in q:
        return 7, "the last 7 days"
    if "this weekend" in q or ("weekend" in q and "last" not in q):
        return 7, "this weekend"
    if "today" in q:
        return 1, "today"
    if "yesterday" in q:
        return 2, "yesterday"
    return days, f"the last {days} days"


def infer_metric(question: str) -> Optional[str]:
    q = (question or "").lower()
    if any(h in q for h in ("homicide", "homicides", "murder", "murders")):
        return "homicides"
    if any(h in q for h in ("shooting", "shootings", "shots fired", "gun violence")):
        return "shootings"
    if "311" in q or "service request" in q or "pothole" in q:
        return "311"
    if any(h in q for h in ("event", "events", "calendar", "weekend")):
        return "events"
    if any(h in q for h in ("crime", "incident", "offense", "assault", "robbery")):
        return "crime"
    if "transcript" in q or ("meeting" in q and "resident" in q):
        return "transcripts"
    if any(h in q for h in ("policy", "plan", "ordinance", "zoning")):
        return "policy"
    return None


def days_ahead_from_question(question: str, default: int = 14) -> int:
    """Forward-looking window for upcoming events/calendar queries."""
    q = (question or "").lower()
    if "today" in q:
        return 1
    if "tomorrow" in q:
        return 2
    if "this weekend" in q or ("weekend" in q and "next" not in q):
        return 7
    if "this week" in q:
        return 7
    if "next week" in q:
        return 14
    if "this month" in q:
        return 30
    if "next month" in q:
        return 60
    return default


def build_query_plan(question: str, *, mode: str = "sql", default_days: int = 7) -> QueryPlan:
    days, label = period_from_question(question, default_days)
    q = (question or "").lower()
    return QueryPlan(
        period_days=days,
        period_label=label,
        calendar_year="this year" in q,
        metric=infer_metric(question),
        sources=None,
    )
