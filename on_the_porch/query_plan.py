"""Canonical normalization for vague civic questions (Phase 1).

Maps natural language to structured period/metric hints used by the router
and prepared SQL catalog. No LLM calls.
"""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import date
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


@dataclass
class PeriodWindow:
    """Resolved time window for prepared SQL and answer phrasing."""

    days: int
    label: str
    calendar_ym: Optional[str] = None  # YYYY-MM for calendar-month questions


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return year, month


def _calendar_month_window(offset: int, *, month_to_date: bool = False) -> PeriodWindow:
    """offset 0 = this month, -1 = last month."""
    today = date.today()
    year, month = _shift_month(today.year, today.month, offset)
    ym = f"{year:04d}-{month:02d}"
    month_name = calendar.month_name[month]
    days_in_month = calendar.monthrange(year, month)[1]
    if month_to_date and offset == 0:
        label = f"{month_name} {year} (month to date)"
        days = max(1, today.day)
    else:
        label = f"{month_name} {year}"
        days = days_in_month
    return PeriodWindow(days=days, label=label, calendar_ym=ym)


def resolve_period(question: str, default: int = 7) -> PeriodWindow:
    """Map natural-language time phrases to SQL + user-facing period labels."""
    q = (question or "").lower()
    if "last month" in q:
        window = _calendar_month_window(-1)
        return PeriodWindow(
            days=window.days,
            label=f"last month ({window.label})",
            calendar_ym=window.calendar_ym,
        )
    if "this month" in q:
        window = _calendar_month_window(0, month_to_date=True)
        return PeriodWindow(
            days=window.days,
            label=f"this month ({window.label})",
            calendar_ym=window.calendar_ym,
        )
    if "this year" in q:
        return PeriodWindow(days=365, label="this year (calendar year to date)")
    if "last year" in q:
        return PeriodWindow(days=730, label="last year")
    if "last week" in q:
        return PeriodWindow(days=14, label="the last 14 days")
    if "this week" in q:
        return PeriodWindow(days=7, label="the last 7 days")
    if "this weekend" in q or ("weekend" in q and "last" not in q):
        return PeriodWindow(days=7, label="this weekend")
    if "today" in q:
        return PeriodWindow(days=1, label="today")
    if "yesterday" in q:
        return PeriodWindow(days=2, label="yesterday")
    days = days_from_question(question, default)
    return PeriodWindow(days=days, label=f"the last {days} days")


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
        return max(1, date.today().day)
    if "last month" in q:
        today = date.today()
        year, month = _shift_month(today.year, today.month, -1)
        return calendar.monthrange(year, month)[1]
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
    window = resolve_period(question, default)
    return window.days, window.label


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
    window = resolve_period(question, default_days)
    q = (question or "").lower()
    return QueryPlan(
        period_days=window.days,
        period_label=window.label,
        calendar_year="this year" in q,
        metric=infer_metric(question),
        sources=None,
    )
