"""Canonical normalization for vague civic questions (Phase 1).

Maps natural language to structured period/metric hints used by the router
and prepared SQL catalog. No LLM calls.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
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
    # Half-open [start_date, end_date_exclusive) when an explicit range is parsed.
    start_date: Optional[date] = None
    end_date_exclusive: Optional[date] = None


_MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _parse_explicit_range(question: str, *, today: Optional[date] = None) -> Optional[PeriodWindow]:
    """Parse 'from June 1 through June 20' into a half-open PeriodWindow."""
    q = (question or "").lower()
    local_today = today or date.today()
    pattern = re.compile(
        r"from\s+([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?"
        r"\s+(?:through|to|until|-)\s+"
        r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?",
        re.I,
    )
    match = pattern.search(q)
    if not match:
        return None
    m1, d1, y1, m2, d2, y2 = match.groups()
    month1 = _MONTH_NAMES.get(m1.lower())
    month2 = _MONTH_NAMES.get(m2.lower())
    if not month1 or not month2:
        return None
    year2 = int(y2) if y2 else local_today.year
    year1 = int(y1) if y1 else year2
    try:
        start = date(year1, month1, int(d1))
        end_inclusive = date(year2, month2, int(d2))
    except ValueError:
        return None
    end_exclusive = end_inclusive + timedelta(days=1)
    days = max(1, (end_exclusive - start).days)
    label = (
        f"{start.strftime('%B')} {start.day} through "
        f"{end_inclusive.strftime('%B')} {end_inclusive.day}, {end_inclusive.year}"
    )
    return PeriodWindow(
        days=days,
        label=label,
        start_date=start,
        end_date_exclusive=end_exclusive,
    )


def _named_month_window(question: str, *, today: Optional[date] = None) -> Optional[PeriodWindow]:
    """Parse bare 'in June' / 'during June 2026' as a full calendar month."""
    q = (question or "").lower()
    local_today = today or date.today()
    # Avoid stealing "from June 1 through ..." (handled by explicit range).
    if re.search(r"from\s+[a-z]+\s+\d{1,2}", q):
        return None
    match = re.search(
        r"\b(?:in|during|for)\s+(january|february|march|april|may|june|july|"
        r"august|september|october|november|december)(?:\s+(\d{4}))?\b",
        q,
    )
    if not match:
        return None
    month = _MONTH_NAMES[match.group(1)]
    year = int(match.group(2)) if match.group(2) else local_today.year
    ym = f"{year:04d}-{month:02d}"
    days_in_month = calendar.monthrange(year, month)[1]
    return PeriodWindow(
        days=days_in_month,
        label=f"{calendar.month_name[month]} {year}",
        calendar_ym=ym,
        start_date=date(year, month, 1),
        end_date_exclusive=(
            date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        ),
    )


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
    explicit = _parse_explicit_range(question)
    if explicit is not None:
        return explicit
    named_month = _named_month_window(question)
    if named_month is not None:
        return named_month
    if "last month" in q:
        window = _calendar_month_window(-1)
        year, month = int(window.calendar_ym[:4]), int(window.calendar_ym[5:7])
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return PeriodWindow(
            days=window.days,
            label=f"last month ({window.label})",
            calendar_ym=window.calendar_ym,
            start_date=start,
            end_date_exclusive=end,
        )
    if "this month" in q:
        window = _calendar_month_window(0, month_to_date=True)
        year, month = int(window.calendar_ym[:4]), int(window.calendar_ym[5:7])
        today = date.today()
        start = date(year, month, 1)
        # Month-to-date: half-open through tomorrow so today is included.
        end = today + timedelta(days=1)
        return PeriodWindow(
            days=window.days,
            label=f"this month ({window.label})",
            calendar_ym=window.calendar_ym,
            start_date=start,
            end_date_exclusive=end,
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
    """Compatibility horizon derived from canonical event-time semantics."""
    from conversation_dimensions import (  # noqa: WPS433
        resolve_app_now,
        resolve_canonical_time_expression,
    )

    now = resolve_app_now()
    resolved, _reason, clarification = resolve_canonical_time_expression(
        question,
        now=now,
        resolve_yearless_named_dates=True,
    )
    if resolved is None or clarification:
        return default
    end_exclusive = date.fromisoformat(str(resolved.end_date_exclusive))
    return max(1, (end_exclusive - now.date()).days)


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
