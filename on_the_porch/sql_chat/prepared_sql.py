"""Prepared SQL catalog — answer common civic questions without LLM SQL generation.

Iteration 1: rule-matched intents run fixed queries and template (or optional LLM)
summaries. Returns None when no catalog entry matches so the caller can fall back
to the full LLM SQL pipeline.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from query_plan import days_from_question as _days_from_question
from query_plan import period_from_question as _period_from_question
from query_plan import resolve_period as _resolve_period
from query_plan import PeriodWindow

from civic_decompose import CivicPart, decompose_civic_question

# Dorchester scope (matches app4 post-processing conventions).
_DORCHESTER_311 = (
    "(`neighborhood` = 'Dorchester' OR `police_district` = 'C11')"
)
_DORCHESTER_CRIME = "`district` = 'C11'"
_OFFENSE_EXPR = (
    "COALESCE(NULLIF(`offense_code_group`, ''), NULLIF(`offense_description`, ''))"
)


@dataclass
class PreparedMatch:
    intent: str
    sql: str
    answer_fn: Callable[[str, Dict[str, Any]], str]
    supplemental_sql: Optional[str] = None


def _data_source_note() -> str:
    return "based on the latest synced Boston Open Data"


def _period_preposition(period: str) -> str:
    """'for last month (May 2026)' vs 'in May 2026' vs 'for the last 7 days'."""
    lower = period.lower()
    if lower.startswith(("last month", "this month", "last week", "this week")):
        return "for"
    if re.match(r"^[A-Z][a-z]+ \d{4}", period):
        return "in"
    if period.startswith("this year") or period.startswith("last year"):
        return "for"
    return "for"


def _date_column_filter(column: str, window: PeriodWindow) -> str:
    if window.calendar_ym:
        return f"DATE_FORMAT(`{column}`, '%Y-%m') = '{window.calendar_ym}'"
    days = max(1, min(window.days, 365))
    return f"`{column}` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY)"


def _format_count_answer(
    question: str,
    result: Dict[str, Any],
    *,
    label: str,
    period: str,
    definition: Optional[str] = None,
    zero_hint: Optional[str] = None,
) -> str:
    rows = result.get("rows") or []
    count = 0
    if rows and isinstance(rows[0], dict):
        count = rows[0].get("cnt") or rows[0].get("count") or 0
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    prep = _period_preposition(period)
    defn = f" ({definition})" if definition else ""
    if count == 0:
        hint = zero_hint or "Try a wider date range or check again after the next data sync."
        return f"I found **0** {label} in Dorchester {prep} {period}{defn}. {hint}"
    return f"There were **{count:,}** {label} in Dorchester {prep} {period}{defn}."


def _format_grouped_answer(
    question: str,
    result: Dict[str, Any],
    *,
    label: str,
    key_field: str,
    period: str,
    limit: int = 10,
) -> str:
    rows = result.get("rows") or []
    if not rows:
        return f"I didn't find {label} data in Dorchester for {period}."
    lines = [f"Top {label} in Dorchester for {period}:\n"]
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        name = row.get(key_field) or "Unknown"
        cnt = row.get("cnt") or row.get("count") or 0
        lines.append(f"- **{name}**: {cnt:,}")
    return "\n".join(lines)


def _format_recent_rows(
    question: str,
    result: Dict[str, Any],
    *,
    title: str,
    fields: List[str],
) -> str:
    rows = result.get("rows") or []
    if not rows:
        return f"No recent {title} found in Dorchester."
    lines = [f"Recent {title} in Dorchester:\n"]
    for row in rows[:15]:
        if not isinstance(row, dict):
            continue
        parts = [str(row.get(f) or "") for f in fields if row.get(f)]
        label = " — ".join(parts) if parts else "Record"
        lines.append(f"- {label}")
    return "\n".join(lines)


def _format_issue_recent_answer(
    question: str,
    result: Dict[str, Any],
    *,
    issue_label: str,
    period: str,
) -> str:
    rows = result.get("rows") or []
    if not rows:
        return (
            f"I didn't find recent **{issue_label}** 311 reports in Dorchester for {period}. "
            "You can try a wider window — for example, 'pothole reports last month'."
        )
    lines = [f"Recent **{issue_label}** 311 reports in Dorchester for {period}:\n"]
    for row in rows[:15]:
        if not isinstance(row, dict):
            continue
        parts = [
            str(row.get("open_date") or ""),
            str(row.get("issue_type") or ""),
            str(row.get("street_name") or ""),
            str(row.get("case_status") or ""),
        ]
        label = " — ".join(p for p in parts if p)
        lines.append(f"- {label}")
    return "\n".join(lines)


def _events_source_table() -> str:
    if os.getenv("MATERIALIZED_VIEWS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    ):
        return "events_materialized"
    return "weekly_events"


def _sql_events_upcoming(days: int, *, weekend_only: bool = False) -> str:
    days = max(1, min(days, 90))
    table = _events_source_table()
    weekend_clause = ""
    if weekend_only:
        weekend_clause = (
            " AND (DAYOFWEEK(`start_date`) IN (1, 7) "
            " OR LOWER(`event_date`) LIKE '%saturday%' "
            " OR LOWER(`event_date`) LIKE '%sunday%') "
        )
    return (
        f"SELECT `event_name`, `event_date`, `start_date`, `end_date`, "
        f"`start_time`, `end_time`, `raw_text`, `source_pdf` "
        f"FROM `{table}` "
        "WHERE `start_date` >= CURDATE() "
        f"AND `start_date` <= DATE_ADD(CURDATE(), INTERVAL {days} DAY) "
        f"{weekend_clause}"
        "ORDER BY `start_date` ASC, `start_time` ASC "
        "LIMIT 30"
    )


def _events_by_source_sql(source: str, days: int) -> str:
    days = max(1, min(days, 90))
    like = source.lower()
    table = _events_source_table()
    return (
        "SELECT `event_name`, `event_date`, `start_date`, `start_time`, "
        "`raw_text`, `source_pdf` "
        f"FROM `{table}` "
        f"WHERE LOWER(`source_pdf`) LIKE '%{like}%' "
        "AND `start_date` >= CURDATE() "
        f"AND `start_date` <= DATE_ADD(CURDATE(), INTERVAL {days} DAY) "
        "ORDER BY `start_date` ASC LIMIT 30"
    )


def _format_events_rows(question: str, result: Dict[str, Any]) -> str:
    from sql_chat import app4  # noqa: WPS433

    return app4._format_events_answer_fallback(question, result)


def _sql_escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("'", "''")


def _normalize_event_name(text: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        (text or "").replace("–", "-").replace("—", "-").strip().lower(),
    )


def _extract_event_name(question: str) -> Optional[str]:
    """Pull a specific event title from quoted or 'tell me about X event' phrasing."""
    q = (question or "").strip()
    if not q:
        return None
    quoted = re.search(r"['\"]([^'\"]{4,})['\"]", q)
    if quoted:
        return quoted.group(1).strip()
    for pattern in (
        r"(?:tell me more about|more about|details about|what is|what's)\s+(.+?)\s+event\b",
        r"(?:tell me more about|more about|details about)\s+(.+?)\s+happening\b",
    ):
        match = re.search(pattern, q, flags=re.IGNORECASE)
        if match:
            name = match.group(1).strip(" '\".,?")
            if len(name) >= 4:
                return name
    return None


def _event_name_where_clause(name: str) -> str:
    norm = _normalize_event_name(name)
    escaped = _sql_escape(norm)
    return (
        "LOWER(REPLACE(REPLACE(`event_name`, '–', '-'), '—', '-')) "
        f"LIKE '%{escaped}%'"
    )


def _event_by_name_sql(
    name: str,
    days_window: int = 90,
    on_date=None,
) -> str:
    days_window = max(7, min(days_window, 180))
    date_clause = ""
    if on_date is not None:
        date_clause = f"AND `start_date` = '{on_date.isoformat()}' "
    return (
        "SELECT `event_name`, `event_date`, `start_date`, `end_date`, "
        "`start_time`, `end_time`, `raw_text`, `source_pdf` "
        "FROM `weekly_events` "
        f"WHERE {_event_name_where_clause(name)} "
        "AND `start_date` >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) "
        f"AND `start_date` <= DATE_ADD(CURDATE(), INTERVAL {days_window} DAY) "
        f"{date_clause}"
        "ORDER BY `start_date` ASC, `start_time` ASC "
        "LIMIT 3"
    )


def _parse_mentioned_event_date(question: str):
    """Best-effort parse of 'Jun 25' / 'June 25' from the user question."""
    from datetime import date

    q = (question or "").lower()
    month_map = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    match = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\s+(\d{1,2})\b",
        q,
    )
    if not match:
        return None
    month = month_map.get(match.group(1)[:3], month_map.get(match.group(1)))
    if not month:
        return None
    day = int(match.group(2))
    year = date.today().year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _fmt_clock(value) -> str:
    if not value:
        return ""
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) < 2:
        return text
    try:
        hour = int(parts[0])
        minute = parts[1][:2]
        suffix = "AM" if hour < 12 else "PM"
        hour12 = hour % 12 or 12
        return f"{hour12}:{minute} {suffix}"
    except ValueError:
        return text


def _extract_event_url(raw_text: str) -> str:
    match = re.search(r"URL:\s*(https?://\S+)", raw_text or "", flags=re.IGNORECASE)
    return match.group(1).rstrip(").,]") if match else ""


_EVENT_SOURCE_LABELS = {
    "codman": "Codman Square Health Center",
    "csndc": "CSNDC",
    "dotnews": "Dorchester Reporter",
}


def _event_source_label(source_pdf: str) -> str:
    key = (source_pdf or "").strip().lower()
    if key in _EVENT_SOURCE_LABELS:
        return _EVENT_SOURCE_LABELS[key]
    for token, label in _EVENT_SOURCE_LABELS.items():
        if token in key:
            return label
    return key.title() if key else "Community calendar"


def build_event_sources(
    rows: List[Dict[str, Any]],
    question: str = "",
) -> List[Dict[str, str]]:
    """Build citation objects for weekly_events rows (org label + event URL)."""
    if not rows:
        return []
    picked = _pick_event_rows(question, rows) if question else rows[:1]
    row = picked[0] if picked else rows[0]
    if not isinstance(row, dict):
        return []
    raw = str(row.get("raw_text") or "")
    url = _extract_event_url(raw)
    label = _event_source_label(str(row.get("source_pdf") or ""))
    return [{"type": "event", "source": label, "path": url}]


def _clean_event_body(raw_text: str, name: str) -> str:
    body = (raw_text or "").strip()
    if not body:
        return ""
    norm_name = _normalize_event_name(name)
    first_line = body.splitlines()[0].strip() if body else ""
    if _normalize_event_name(first_line) == norm_name:
        body = "\n".join(body.splitlines()[1:]).strip()
    # Drop structured footer lines we render separately.
    kept: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("url:") or lower.startswith("location:"):
            continue
        kept.append(stripped)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    # Single-paragraph Tribe descriptions embed LOCATION:/CONTACT: inline.
    body = re.split(r"\bLOCATION:\s*", body, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    body = re.split(r"\bCONTACT:\s*", body, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return body


def _pick_event_rows(question: str, rows: List[dict]) -> List[dict]:
    target = _parse_mentioned_event_date(question)
    if target is not None:
        filtered = []
        for row in rows:
            sd = row.get("start_date")
            if sd is None:
                continue
            try:
                if hasattr(sd, "year"):
                    row_date = sd
                else:
                    from datetime import date

                    row_date = date.fromisoformat(str(sd)[:10])
                if row_date == target:
                    filtered.append(row)
            except (TypeError, ValueError):
                continue
        if filtered:
            return filtered[:1]
    return rows[:1]


def _format_event_detail_answer(question: str, result: Dict[str, Any]) -> str:
    rows = result.get("rows") or []
    if not rows:
        return (
            "I didn't find that event in the community calendar. "
            "It may have passed or not been synced yet — try the Events panel or ask about this week's events."
        )

    row = _pick_event_rows(question, rows)[0]
    name = row.get("event_name") or "Event"
    start_date = row.get("start_date") or row.get("event_date") or ""
    start_time = _fmt_clock(row.get("start_time"))
    end_time = _fmt_clock(row.get("end_time"))
    when_parts = [str(start_date)] if start_date else []
    if start_time:
        when_parts.append(
            f"{start_time} – {end_time}" if end_time else start_time
        )
    when_label = " · ".join(when_parts)

    body = _clean_event_body(row.get("raw_text") or "", name)
    url = _extract_event_url(row.get("raw_text") or "")

    lines = [f"**{name}**"]
    if when_label:
        lines.append(f"**When:** {when_label}")
    if body:
        lines.append("")
        lines.append(body)
    loc_match = re.search(
        r"LOCATION:\s*(.+?)(?:\n|CONTACT:|$)",
        row.get("raw_text") or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    if loc_match:
        lines.append("")
        lines.append(f"**Location:** {loc_match.group(1).strip()}")
    contact_match = re.search(
        r"CONTACT:\s*(.+?)(?:\n|WALK-INS|$)",
        row.get("raw_text") or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    if contact_match:
        lines.append("")
        lines.append(f"**Contact:** {contact_match.group(1).strip()}")
    if url:
        lines.append("")
        lines.append(f"[Event details]({url})")
    return "\n".join(lines).strip()


def _match_event_by_name(question: str) -> Optional[PreparedMatch]:
    q = (question or "").lower()
    if "event" not in q and "happening" not in q:
        return None
    detail_phrases = (
        "tell me more about",
        "more about",
        "details about",
        "what is the",
        "what's the",
        "when is",
        "where is",
    )
    has_quotes = '"' in (question or "") or "'" in (question or "")
    if not has_quotes and not any(p in q for p in detail_phrases):
        return None
    name = _extract_event_name(question)
    if not name:
        return None
    days = max(_days_from_question(question, 30), 30)
    on_date = _parse_mentioned_event_date(question)
    return PreparedMatch(
        intent="event_by_name",
        sql=_event_by_name_sql(name, days_window=days, on_date=on_date),
        answer_fn=_format_event_detail_answer,
    )


def _sql_311_weekly_count(window: PeriodWindow) -> str:
    return (
        "SELECT COUNT(*) AS cnt FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND {_date_column_filter('open_date', window)}"
    )


def _sql_311_top_types_live(window: PeriodWindow) -> str:
    """Top 311 issue types from the live table (bypasses mv_311_weekly)."""
    issue = "COALESCE(NULLIF(`service_name`, ''), NULLIF(`case_topic`, ''))"
    return (
        f"SELECT {issue} AS issue_type, COUNT(*) AS cnt "
        "FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND {_date_column_filter('open_date', window)} "
        f"AND {issue} IS NOT NULL AND {issue} <> '' "
        f"GROUP BY {issue} ORDER BY cnt DESC LIMIT 10"
    )


def _materialized_311_views_enabled() -> bool:
    return os.getenv("MATERIALIZED_VIEWS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _sql_uses_mv_311_weekly(sql: str) -> bool:
    return "mv_311_weekly" in (sql or "")


def _sql_311_top_types(window: PeriodWindow) -> str:
    if window.calendar_ym and _materialized_311_views_enabled():
        year, month = window.calendar_ym.split("-")
        next_y, next_m = int(year), int(month) + 1
        if next_m > 12:
            next_m, next_y = 1, next_y + 1
        end_ym = f"{next_y:04d}-{next_m:02d}"
        return (
            "SELECT issue_type, SUM(request_count) AS cnt "
            "FROM `mv_311_weekly` "
            f"WHERE `week_start` >= '{window.calendar_ym}-01' "
            f"AND `week_start` < '{end_ym}-01' "
            "AND issue_type IS NOT NULL AND issue_type <> '' "
            "GROUP BY issue_type ORDER BY cnt DESC LIMIT 10"
        )
    days = max(1, min(window.days, 365))
    if _materialized_311_views_enabled():
        weeks = max(1, min((days + 6) // 7, 52))
        return (
            "SELECT issue_type, SUM(request_count) AS cnt "
            "FROM `mv_311_weekly` "
            f"WHERE `week_start` >= DATE_SUB(CURDATE(), INTERVAL {weeks * 7} DAY) "
            "AND issue_type IS NOT NULL AND issue_type <> '' "
            "GROUP BY issue_type ORDER BY cnt DESC LIMIT 10"
        )
    return _sql_311_top_types_live(window)


def _maybe_fallback_mv_311_sql(
    sql: str,
    question: str,
) -> Optional[str]:
    """When mv_311_weekly is empty, query service_requests_311 instead."""
    if not _sql_uses_mv_311_weekly(sql):
        return None
    window = _resolve_period(question, 7)
    return _sql_311_top_types_live(window)


def _execute_sql_with_mv_311_fallback(
    execute_sql: Callable[[str], Dict[str, Any]],
    sql: str,
    question: str,
) -> tuple[Dict[str, Any], str]:
    result = execute_sql(sql)
    rows = result.get("rows") or []
    if rows or not _sql_uses_mv_311_weekly(sql):
        return result, sql
    fallback_sql = _maybe_fallback_mv_311_sql(sql, question)
    if not fallback_sql or fallback_sql == sql:
        return result, sql
    print(
        "\n[Prepared SQL — mv_311_weekly empty, falling back to service_requests_311]\n"
        f"{fallback_sql}\n"
    )
    return execute_sql(fallback_sql), fallback_sql


def _sql_311_issue_recent(terms: tuple, window: PeriodWindow) -> str:
    """Recent 311 rows filtered to a specific issue type (pothole, trash, etc.)."""
    parts: List[str] = []
    for term in terms:
        esc = term.replace("'", "''")
        parts.append(f"LOWER(COALESCE(`service_name`, '')) LIKE '%{esc}%'")
        parts.append(f"LOWER(COALESCE(`case_topic`, '')) LIKE '%{esc}%'")
    issue_clause = "(" + " OR ".join(parts) + ")"
    issue = "COALESCE(NULLIF(`service_name`, ''), NULLIF(`case_topic`, ''))"
    return (
        f"SELECT `open_date`, {issue} AS issue_type, `street_name`, `case_status` "
        "FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND {_date_column_filter('open_date', window)} "
        f"AND {issue_clause} "
        "ORDER BY `open_date` DESC LIMIT 15"
    )


def _sql_311_recent(window: PeriodWindow) -> str:
    days = max(1, min(window.days, 90))
    if window.calendar_ym:
        window = PeriodWindow(days=days, label=window.label, calendar_ym=window.calendar_ym)
    issue = "COALESCE(NULLIF(`service_name`, ''), NULLIF(`case_topic`, ''))"
    return (
        f"SELECT `open_date`, {issue} AS issue_type, `street_name`, `case_status` "
        "FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND {_date_column_filter('open_date', window)} "
        "ORDER BY `open_date` DESC LIMIT 20"
    )


def _sql_311_open(window: PeriodWindow) -> str:
    issue = "COALESCE(NULLIF(`service_name`, ''), NULLIF(`case_topic`, ''))"
    open_status = (
        "`case_status` IS NOT NULL AND `case_status` <> '' "
        "AND `case_status` NOT IN ('Closed')"
    )
    return (
        f"SELECT `open_date`, {issue} AS issue_type, `street_name`, `case_status` "
        "FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND {open_status} "
        f"AND {_date_column_filter('open_date', window)} "
        "ORDER BY `open_date` DESC LIMIT 20"
    )


def _sql_crime_monthly_count(window: PeriodWindow) -> str:
    if window.calendar_ym and os.getenv("MATERIALIZED_VIEWS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    ):
        return (
            "SELECT SUM(incident_count) AS cnt "
            "FROM `mv_crime_monthly` "
            f"WHERE DATE_FORMAT(`month_start`, '%Y-%m') = '{window.calendar_ym}'"
        )
    days = max(1, min(window.days, 365))
    if os.getenv("MATERIALIZED_VIEWS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    ):
        months = max(1, min((days + 29) // 30, 24))
        return (
            "SELECT SUM(incident_count) AS cnt "
            "FROM `mv_crime_monthly` "
            f"WHERE `month_start` >= DATE_SUB(CURDATE(), INTERVAL {months} MONTH)"
        )
    return (
        "SELECT COUNT(*) AS cnt FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} "
        f"AND {_date_column_filter('occurred_on_date', window)}"
    )


def _sql_crime_homicide_date_clause(window: PeriodWindow, *, calendar_year: bool = False) -> str:
    if calendar_year:
        return "YEAR(`occurred_on_date`) = YEAR(CURDATE())"
    return _date_column_filter("occurred_on_date", window)


def _sql_crime_homicide_labeled_expr() -> str:
    return (
        "LOWER(COALESCE(`offense_description`, '')) LIKE '%homicide%' "
        "OR LOWER(COALESCE(`offense_code_group`, '')) LIKE '%homicide%' "
        "OR LOWER(COALESCE(`offense_description`, '')) LIKE '%murder%'"
    )


def _sql_crime_homicide_proxy_expr() -> str:
    return (
        "LOWER(COALESCE(`offense_description`, '')) LIKE '%death investigation%' "
        "AND (`shooting` = 1 OR `shooting` = '1' OR UPPER(COALESCE(`shooting`, '')) = 'Y')"
    )


def _sql_crime_homicide_tiered(window: PeriodWindow, *, calendar_year: bool = False) -> str:
    date_clause = _sql_crime_homicide_date_clause(window, calendar_year=calendar_year)
    labeled = _sql_crime_homicide_labeled_expr()
    proxy = _sql_crime_homicide_proxy_expr()
    return (
        "SELECT "
        f"SUM(CASE WHEN ({labeled}) THEN 1 ELSE 0 END) AS labeled_cnt, "
        f"SUM(CASE WHEN ({proxy}) THEN 1 ELSE 0 END) AS proxy_cnt "
        "FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} AND {date_clause}"
    )


def _format_homicide_answer(
    question: str,
    result: Dict[str, Any],
    *,
    period: str,
) -> str:
    rows = result.get("rows") or []
    labeled = proxy = 0
    if rows and isinstance(rows[0], dict):
        labeled = int(rows[0].get("labeled_cnt") or 0)
        proxy = int(rows[0].get("proxy_cnt") or 0)
    labeled_def = "offense labeled homicide or murder in Boston Open Data"
    proxy_def = "death investigations with a shooting flag in Boston Open Data"
    baseline_note = _bpd_fatal_shootings_baseline_note()

    if labeled > 0:
        answer = (
            f"There were **{labeled:,}** homicides in Dorchester for {period} "
            f"({labeled_def}; {_data_source_note()})."
        )
        return answer + baseline_note

    if proxy > 0:
        answer = (
            f"I found **0** homicides explicitly labeled in Boston Open Data for {period}, "
            f"but **{proxy:,}** fatal-shooting death investigations in Dorchester "
            f"({proxy_def}). Official BPD homicide counts may differ because rulings "
            f"and offense labels are updated separately from this feed."
        )
        return answer + baseline_note

    answer = (
        f"I found **0** homicides in Dorchester for {period} using both labeled homicides "
        f"and fatal-shooting death investigations in Boston Open Data ({_data_source_note()}). "
        "Recent fatal shootings may not appear yet, or may be coded under other offense types "
        "until BPD updates the record."
    )
    return answer + baseline_note


def _bpd_fatal_shootings_baseline_note() -> str:
    """Optional cross-check from BPD shootings dashboard baseline file."""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "data_ingestion"
        / "boston_data_sync"
        / "bpd_homicide_baseline.json"
    )
    if not path.exists():
        return ""
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        fatal = int(data.get("fatal_shootings_ytd") or 0)
        as_of = data.get("as_of", "")[:10]
        if fatal <= 0:
            return ""
        return (
            f"\n\nFor context, the BPD shootings dashboard reports **{fatal:,}** fatal "
            f"shootings in Dorchester (C11) year-to-date"
            + (f" as of {as_of}" if as_of else "")
            + ". That count uses a different methodology than labeled homicides above."
        )
    except Exception:
        return ""


def _sql_crime_top_offenses(window: PeriodWindow) -> str:
    if window.calendar_ym and os.getenv("MATERIALIZED_VIEWS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    ):
        return (
            "SELECT offense_group, SUM(incident_count) AS cnt "
            "FROM `mv_crime_monthly` "
            f"WHERE DATE_FORMAT(`month_start`, '%Y-%m') = '{window.calendar_ym}' "
            "AND offense_group IS NOT NULL AND offense_group <> '' "
            "GROUP BY offense_group ORDER BY cnt DESC LIMIT 10"
        )
    days = max(1, min(window.days, 365))
    if os.getenv("MATERIALIZED_VIEWS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    ):
        months = max(1, min((days + 29) // 30, 24))
        return (
            "SELECT offense_group, SUM(incident_count) AS cnt "
            "FROM `mv_crime_monthly` "
            f"WHERE `month_start` >= DATE_SUB(CURDATE(), INTERVAL {months} MONTH) "
            "AND offense_group IS NOT NULL AND offense_group <> '' "
            "GROUP BY offense_group ORDER BY cnt DESC LIMIT 10"
        )
    return (
        f"SELECT {_OFFENSE_EXPR} AS offense_group, COUNT(*) AS cnt "
        "FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} "
        f"AND {_date_column_filter('occurred_on_date', window)} "
        f"AND {_OFFENSE_EXPR} IS NOT NULL AND {_OFFENSE_EXPR} <> '' "
        f"GROUP BY {_OFFENSE_EXPR} ORDER BY cnt DESC LIMIT 10"
    )


def _format_open_311_answer(question: str, result: Dict[str, Any], *, period: str) -> str:
    rows = result.get("rows") or []
    if not rows:
        return f"I didn't find any open 311 cases in Dorchester for {period}."
    lines = [f"Open 311 cases in Dorchester for {period}:\n"]
    for row in rows[:15]:
        if not isinstance(row, dict):
            continue
        parts = [
            str(row.get("open_date") or ""),
            str(row.get("issue_type") or ""),
            str(row.get("street_name") or ""),
            str(row.get("case_status") or ""),
        ]
        label = " — ".join(p for p in parts if p)
        lines.append(f"- {label}")
    return "\n".join(lines)


def _match_events_list(question: str) -> Optional[PreparedMatch]:
    from sql_chat import app4  # noqa: WPS433

    if not app4.is_generic_events_list_question(question):
        return None
    q = (question or "").lower()
    weekend_only = "weekend" in q
    days = 7 if weekend_only else _days_from_question(question, 14)
    intent = "events_weekend" if weekend_only else "events_list"
    period = "this weekend" if weekend_only else f"the next {days} days"
    return PreparedMatch(
        intent=intent,
        sql=_sql_events_upcoming(days, weekend_only=weekend_only),
        answer_fn=lambda question, result: _format_events_list_answer(
            question, result, period=period,
        ),
    )


def _format_events_list_answer(
    question: str,
    result: Dict[str, Any],
    *,
    period: str,
) -> str:
    from sql_chat import app4  # noqa: WPS433

    rows = result.get("rows") or []
    if not rows:
        return (
            f"I didn't find any upcoming events in Dorchester for {period}. "
            "Try a wider date range or check back later."
        )
    return app4._format_events_answer_fallback(question, result)


def _match_events_by_source(question: str) -> Optional[PreparedMatch]:
    q = (question or "").lower()
    sources = (
        ("csndc", ("csndc", "codman square neighborhood")),
        ("codman", ("codman", "codman square")),
        ("dotnews", ("dotnews", "dot news", "dorchester reporter")),
    )
    if "event" not in q and "happening" not in q and "calendar" not in q:
        return None
    for key, hints in sources:
        if any(h in q for h in hints):
            days = _days_from_question(question, 30)
            return PreparedMatch(
                intent=f"events_by_source:{key}",
                sql=_events_by_source_sql(key, days),
                answer_fn=_format_events_rows,
            )
    return None


def _sql_311_issue_count(terms: tuple, window: PeriodWindow) -> str:
    """Count 311 requests matching issue-type keywords (pothole, trash, etc.)."""
    parts: List[str] = []
    for term in terms:
        esc = term.replace("'", "''")
        parts.append(f"LOWER(COALESCE(`service_name`, '')) LIKE '%{esc}%'")
        parts.append(f"LOWER(COALESCE(`case_topic`, '')) LIKE '%{esc}%'")
    issue_clause = "(" + " OR ".join(parts) + ")"
    return (
        "SELECT COUNT(*) AS cnt FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND {_date_column_filter('open_date', window)} "
        f"AND {issue_clause}"
    )


_311_ISSUE_ALIASES: Dict[str, tuple] = {
    "pothole": ("pothole",),
    "trash": ("trash", "missed trash", "garbage", "recycling"),
    "graffiti": ("graffiti",),
    "rodent": ("rodent", "rat", "rats"),
    "snow": ("snow", "ice", "plow"),
    "street_light": ("street light", "streetlight"),
    "illegal_dumping": ("illegal dumping", "dumping"),
}


def _issue_search_terms(question: str) -> Optional[tuple]:
    q = (question or "").lower()
    for _key, hints in _311_ISSUE_ALIASES.items():
        for hint in hints:
            if " " in hint:
                if hint in q:
                    return hints
            elif re.search(rf"\b{re.escape(hint)}\b", q):
                return hints
    return None


_311_DOMAIN_HINTS = (
    "311", "service request", "service requests", "city complaint",
    "complaint", "complaints", "pothole", "potholes", "trash", "graffiti",
    "rodent", "rats", "snow", "streetlight", "street light", "missed pickup",
    "illegal dumping",
)

_311_ADVISORY_HINTS = (
    "should i report", "should i call", "should i use",
    "can i report", "can i call", "do i report", "do i call",
    "how do i report", "how do i call", "how to report", "how to call",
    "what can i report", "what should i report", "what do i report",
    "what is 311", "what's 311", "how does 311", "how do 311",
    "when should i call", "when do i call", "is 311 for",
)


def is_311_advisory_question(question: str) -> bool:
    """Resident guidance about when/how to use 311 — not a stats lookup."""
    q = (question or "").lower()
    if not any(h in q for h in _311_DOMAIN_HINTS):
        return False
    return any(h in q for h in _311_ADVISORY_HINTS)

_STATS_COUNT_HINTS = (
    "how many", "how much", "count", "number of", "total",
    "a lot of", "are there a lot", "are there many", "how bad", "how common",
)

_BREAKDOWN_HINTS = (
    "what were they", "what were these", "what were those", "what kind",
    "what kinds", "what type", "what types", "breakdown", "categories",
    "based on", "about what", "for what", "mostly about", "common types",
    "what issues", "what were the", "what are the", "what were those about",
)


def _wants_issue_breakdown(q: str) -> bool:
    return any(h in q for h in _BREAKDOWN_HINTS)


def wants_issue_breakdown(question: str) -> bool:
    return _wants_issue_breakdown(_q(question))


def include_311_types_by_default() -> bool:
    return os.getenv("PREPARED_SQL_311_INCLUDE_TYPES", "true").strip().lower() in (
        "1", "true", "yes",
    )


_311_BREAKDOWN_INTENTS = frozenset({"311_weekly_count", "311_count_with_types"})


def should_include_311_breakdown(question: str, intent: str) -> bool:
    """Fetch top issue types alongside generic 311 counts."""
    if intent not in _311_BREAKDOWN_INTENTS:
        return False
    if wants_issue_breakdown(question):
        return True
    return include_311_types_by_default()


def analyze_311_report_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate row-level 311 data for conversational synthesis."""
    clean: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        for key in ("cnt", "count"):
            if key in item and item[key] is not None:
                try:
                    item[key] = int(item[key])
                except (TypeError, ValueError):
                    pass
        clean.append(item)
    if not clean:
        return {"total": 0}
    open_cnt = 0
    streets: Dict[str, int] = {}
    for row in clean:
        status = str(row.get("case_status") or "").lower()
        if status and status != "closed":
            open_cnt += 1
        street = str(row.get("street_name") or "").strip()
        if street:
            streets[street] = streets.get(street, 0) + 1
    dates = [str(r["open_date"]) for r in clean if r.get("open_date")]
    return {
        "total": len(clean),
        "open_count": open_cnt,
        "closed_count": len(clean) - open_cnt,
        "top_streets": sorted(streets.items(), key=lambda x: -x[1])[:4],
        "newest_date": max(dates) if dates else None,
        "oldest_date": min(dates) if dates else None,
    }


def build_synthesis_canonical(
    intent: str,
    question: str,
    result: Dict[str, Any],
    *,
    issue_label: Optional[str] = None,
    period: Optional[str] = None,
) -> str:
    """Compact facts for the LLM — not list-shaped templates."""
    rows = result.get("rows") or []
    period_phrase = f" for {period}" if period else ""

    if intent == "311_top_types":
        if not rows:
            return f"No 311 issue-type breakdown is available{period_phrase}."
        parts = []
        for row in rows[:10]:
            name = row.get("issue_type") or "Unknown"
            cnt = int(row.get("cnt") or row.get("count") or 0)
            parts.append(f"{name}: **{cnt:,}**")
        return f"311 issue-type breakdown in Dorchester{period_phrase}: " + "; ".join(parts)

    if intent in ("311_recent", "311_issue_recent", "311_open"):
        analysis = analyze_311_report_rows(rows)
        label = issue_label or "311"
        if analysis["total"] == 0:
            return f"No recent **{label}** reports in Dorchester{period_phrase}."
        return (
            f"**{analysis['total']}** recent **{label}** reports{period_phrase} "
            f"(**{analysis['open_count']}** open, **{analysis['closed_count']}** closed)."
        )

    if intent == "crime_by_offense":
        if not rows:
            return f"No crime breakdown available{period_phrase}."
        parts = []
        for row in rows[:8]:
            name = row.get("offense_group") or "Unknown"
            cnt = int(row.get("cnt") or row.get("count") or 0)
            parts.append(f"{name}: **{cnt:,}**")
        return f"Crime types in Dorchester{period_phrase}: " + "; ".join(parts)

    count = _first_count_from_result(result)
    if count is not None:
        return f"Count: **{count:,}**{period_phrase}."
    return ""


def build_synthesis_payload(
    intent: str,
    question: str,
    result: Dict[str, Any],
    *,
    canonical_facts: str,
    supplemental_result: Optional[Dict[str, Any]] = None,
    issue_label: Optional[str] = None,
) -> Dict[str, Any]:
    rows = result.get("rows") or []
    payload: Dict[str, Any] = {
        "intent": intent,
        "canonical_facts": canonical_facts,
        "row_count": len(rows),
    }
    if intent in DETAIL_CIVIC_INTENTS or intent in ("crime_by_offense",):
        payload["row_analysis"] = analyze_311_report_rows(rows) if "311" in intent else None
        payload["sample_rows"] = rows[:8]
    elif rows:
        payload["sample_rows"] = rows[:5]
    if supplemental_result and supplemental_result.get("rows"):
        payload["issue_type_breakdown"] = (supplemental_result.get("rows") or [])[:10]
    if issue_label:
        payload["issue_label"] = issue_label
    return payload


# Re-export for answer_voice
DETAIL_CIVIC_INTENTS = frozenset({
    "311_top_types", "311_recent", "311_issue_recent", "311_open",
    "events_list", "events_by_source", "crime_by_offense",
})

COUNT_CIVIC_INTENTS = frozenset({
    "311_weekly_count",
    "311_issue_count",
    "311_count_with_types",
    "crime_monthly_count",
    "crime_shooting_count",
    "crime_homicide_count",
})


def format_issue_type_summary(
    rows: List[Dict[str, Any]],
    *,
    limit: int = 5,
) -> str:
    """Short bullet list of top 311 issue types for answers and fallbacks."""
    if not rows:
        return ""
    lines = ["Most common issues residents reported:"]
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        name = row.get("issue_type") or "Unknown"
        cnt = row.get("cnt") or row.get("count") or 0
        try:
            cnt = int(cnt)
        except (TypeError, ValueError):
            cnt = 0
        lines.append(f"- **{name}**: {cnt:,}")
    return "\n".join(lines)


def sql_311_top_types(window: PeriodWindow) -> str:
    return _sql_311_top_types(window)


def _match_911(question: str) -> Optional[PreparedMatch]:
    """Boston 911 dispatch data is not in our database — return a clear static answer."""
    q = (question or "").lower()
    if "911" not in q:
        return None
    if any(h in q for h in ("police report", "crime report", "incident report", "bpd")):
        return None
    if not any(h in q for h in ("call", "calls", "dispatch", "emergency", "how many", "count", "number of", "total")):
        return None

    def _answer(_question: str, _result: Dict[str, Any]) -> str:
        return (
            "We don't have Boston **911 emergency dispatch call** data in this app. "
            "911 call logs are separate from BPD crime incident reports and 311 service requests.\n\n"
            "For **crime statistics** in Dorchester, ask about crime incidents or shootings. "
            "For **non-emergency city services** (potholes, trash, lights), ask about **311 requests**."
        )

    return PreparedMatch(intent="911_unavailable", sql="SELECT 0 AS cnt", answer_fn=_answer)


def _sql_crime_shooting_count(window: PeriodWindow) -> str:
    return (
        "SELECT COUNT(*) AS cnt FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} "
        f"AND {_date_column_filter('occurred_on_date', window)} "
        "AND (`shooting` = 1 OR `shooting` = '1' OR UPPER(COALESCE(`shooting`, '')) = 'Y')"
    )


def _match_311(question: str) -> Optional[PreparedMatch]:
    q = (question or "").lower()
    issue_terms = _issue_search_terms(question)
    if not any(h in q for h in _311_DOMAIN_HINTS) and not issue_terms:
        return None
    if is_311_advisory_question(question):
        return None
    window = _resolve_period(question, 7)
    period = window.label

    if any(h in q for h in _STATS_COUNT_HINTS):
        generic_311 = any(
            h in q for h in ("311", "service request", "service requests", "city complaint")
        )
        if _wants_issue_breakdown(q) and (generic_311 or issue_terms):
            return PreparedMatch(
                intent="311_count_with_types",
                sql=_sql_311_weekly_count(window),
                supplemental_sql=_sql_311_top_types(window),
                answer_fn=lambda question, result: _format_count_answer(
                    question, result, label="311 service requests", period=period,
                ),
            )
        if issue_terms and not generic_311:
            label = issue_terms[0].replace("_", " ") + " 311 requests"
            return PreparedMatch(
                intent="311_issue_count",
                sql=_sql_311_issue_count(issue_terms, window),
                answer_fn=lambda question, result: _format_count_answer(
                    question, result, label=label, period=period,
                ),
            )
        if generic_311 or issue_terms:
            return PreparedMatch(
                intent="311_weekly_count",
                sql=_sql_311_weekly_count(window),
                answer_fn=lambda question, result: _format_count_answer(
                    question, result, label="311 service requests", period=period,
                ),
            )
    if issue_terms and any(h in q for h in ("problem", "issues", "bad", "worse", "common")):
        label = issue_terms[0].replace("_", " ") + " 311 requests"
        return PreparedMatch(
            intent="311_issue_count",
            sql=_sql_311_issue_count(issue_terms, window),
            answer_fn=lambda question, result: _format_count_answer(
                question, result, label=label, period=period,
            ),
        )
    generic_311 = any(
        h in q for h in ("311", "service request", "service requests", "city complaint")
    )
    exploratory = any(
        phrase in q
        for phrase in (
            "tell me about", "about the", "what are the", "what's going on",
            "whats going on", "give me an overview", "overview of",
        )
    )
    if exploratory and generic_311 and any(
        h in q for h in ("issue", "issues", "request", "requests", "complaint", "complaints")
    ):
        return PreparedMatch(
            intent="311_count_with_types",
            sql=_sql_311_weekly_count(window),
            supplemental_sql=_sql_311_top_types(window),
            answer_fn=lambda question, result: _format_count_answer(
                question, result, label="311 service requests", period=period,
            ),
        )
    if any(h in q for h in (
        "top", "most common", "common", "breakdown", "types",
        "what kind", "what kinds", "what type", "what types",
        "what issues", "what were the", "what are the",
    )):
        return PreparedMatch(
            intent="311_top_types",
            sql=_sql_311_top_types(window),
            answer_fn=lambda question, result: _format_grouped_answer(
                question, result, label="311 issue types",
                key_field="issue_type", period=period,
            ),
        )
    if "open" in q and any(h in q for h in ("case", "cases", "request", "requests")):
        return PreparedMatch(
            intent="311_open",
            sql=_sql_311_open(window),
            answer_fn=lambda question, result: _format_open_311_answer(question, result, period=period),
        )
    if issue_terms and any(
        h in q for h in ("recent", "latest", "last few", "list", "reports", "any ")
    ):
        issue_window = _resolve_period(question, 30)
        issue_period = issue_window.label
        issue_label = issue_terms[0]
        return PreparedMatch(
            intent="311_issue_recent",
            sql=_sql_311_issue_recent(issue_terms, issue_window),
            answer_fn=lambda question, result, lbl=issue_label, per=issue_period: _format_issue_recent_answer(
                question, result, issue_label=lbl, period=per,
            ),
        )
    if any(h in q for h in ("recent", "latest", "last few", "open requests", "list")):
        return PreparedMatch(
            intent="311_recent",
            sql=_sql_311_recent(window),
            answer_fn=lambda question, result: _format_recent_rows(
                question, result, title="311 requests",
                fields=["open_date", "issue_type", "street_name", "case_status"],
            ),
        )
    return None


def _match_crime(question: str) -> Optional[PreparedMatch]:
    q = (question or "").lower()
    crime_hints = (
        "crime", "crimes", "incident", "incidents", "assault", "robbery",
        "shooting", "shootings", "shots fired", "gun violence", "theft", "burglary",
        "offense", "police report",
        "homicide", "homicides", "murder", "murders",
    )
    if not any(h in q for h in crime_hints):
        return None
    window = _resolve_period(question, 30)
    period = window.label
    calendar_year = "this year" in q

    stats_asked = any(h in q for h in _STATS_COUNT_HINTS) or any(
        h in q for h in ("recently", "lately", "these days")
    )
    if stats_asked:
        if any(h in q for h in ("shooting", "shootings", "shots fired", "gun violence")):
            shoot_def = "incidents flagged shooting=1 in BPD crime reports"
            return PreparedMatch(
                intent="crime_shooting_count",
                sql=_sql_crime_shooting_count(window),
                answer_fn=lambda question, result: _format_count_answer(
                    question,
                    result,
                    label="shootings",
                    period=period,
                    definition=shoot_def,
                ),
            )
        if any(h in q for h in ("homicide", "homicides", "murder", "murders")):
            return PreparedMatch(
                intent="crime_homicide_count",
                sql=_sql_crime_homicide_tiered(
                    window, calendar_year=calendar_year,
                ),
                answer_fn=lambda question, result: _format_homicide_answer(
                    question, result, period=period,
                ),
            )
        return PreparedMatch(
            intent="crime_monthly_count",
            sql=_sql_crime_monthly_count(window),
            answer_fn=lambda question, result: _format_count_answer(
                question, result, label="crime incidents", period=period,
            ),
        )
    if any(h in q for h in ("top", "most common", "breakdown", "by type", "types")):
        return PreparedMatch(
            intent="crime_by_offense",
            sql=_sql_crime_top_offenses(window),
            answer_fn=lambda question, result: _format_grouped_answer(
                question, result, label="crime types",
                key_field="offense_group", period=period,
            ),
        )
    return None


def is_named_event_detail(question: str) -> bool:
    """True when the user asks about one specific calendar event by name."""
    hit = match_prepared_sql(question)
    return hit is not None and hit.intent == "event_by_name"


def match_prepared_sql(question: str) -> Optional[PreparedMatch]:
    """Return the first matching prepared-SQL intent, or None."""
    if not question or not question.strip():
        return None
    for matcher in (
        _match_911,
        _match_event_by_name,
        _match_events_list,
        _match_events_by_source,
        _match_311,
        _match_crime,
    ):
        hit = matcher(question)
        if hit is not None:
            return hit
    return None


_COUNT_INTENTS = frozenset({
    "311_weekly_count",
    "311_issue_count",
    "311_count_with_types",
    "311_top_types",
    "311_open",
    "311_recent",
    "311_issue_recent",
    "crime_monthly_count",
    "crime_shooting_count",
    "crime_homicide_count",
    "crime_by_offense",
    "911_unavailable",
})

_OPINION_MARKERS = (
    "what do people", "what do residents", "how do people", "how do residents",
    "what are people saying", "residents say", "residents raise", "what concerns",
    "concerns do", "community feel", "opinion", "perspective",
)


def has_opinion_angle(question: str) -> bool:
    q = (question or "").lower()
    return any(h in q for h in _OPINION_MARKERS)


def is_factual_db_question(question: str) -> bool:
    """Questions that must be answered from executed SQL, never invented by an LLM."""
    if decompose_civic_question(question):
        return True
    if match_prepared_sql(question):
        return True
    if has_opinion_angle(question):
        return False
    q = (question or "").lower()
    civic = any(h in q for h in _311_DOMAIN_HINTS) or any(
        h in q
        for h in (
            "crime", "crimes", "shooting", "shootings", "gun violence", "homicide",
            "incident", "offense", "assault", "robbery", "911",
        )
    )
    stats = any(h in q for h in _STATS_COUNT_HINTS)
    return civic and stats


def factual_no_match_message(question: str) -> str:
    return (
        "I can answer that from synced Boston Open Data, but I need a clearer civic "
        "topic or time range. For example: 'How many pothole complaints in Dorchester "
        "this month?' or 'How many shootings in Dorchester in the last 30 days?'"
    )


def validate_sql_numeric_answer(answer: str, result: Dict[str, Any]) -> str:
    """Align any SQL-backed answer's lead count with query results."""
    count = _first_count_from_result(result)
    if count is None:
        return answer
    bold = _bold_counts_in_answer(answer)
    if bold and bold[0] == count:
        return answer
    if not bold:
        return answer
    return (
        f"The database shows **{count:,}** matching records in Dorchester "
        "(based on the latest synced Boston Open Data)."
    )


def is_prepared_sql_enabled() -> bool:
    return os.getenv("PREPARED_SQL_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _conversational_llm_enabled() -> bool:
    return os.getenv("PREPARED_SQL_CONVERSATIONAL", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _prepared_should_use_llm(hit: PreparedMatch) -> bool:
    if hit.intent == "event_by_name":
        return True
    if hit.intent in ("crime_homicide_count", "911_unavailable"):
        return False
    if _conversational_llm_enabled() and hit.intent in _COUNT_INTENTS:
        return True
    if hit.intent not in _COUNT_INTENTS and os.getenv(
        "PREPARED_SQL_USE_LLM_SUMMARY", "false",
    ).strip().lower() in ("1", "true", "yes"):
        return True
    return False


def _llm_render_prepared_answer(
    question: str,
    hit: PreparedMatch,
    result: Dict[str, Any],
    *,
    supplemental_result: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Rewrite DB-backed facts into a conversational reply; numbers stay validated after."""
    from answer_voice import (  # noqa: WPS433
        classify_from_intent,
        format_conversation_prefix,
        resident_question_block,
        system_prompt,
    )
    from sql_chat import app4  # noqa: WPS433

    window = _resolve_period(question, 7)
    terms = _issue_search_terms(question)
    issue_label = terms[0] if terms else None
    if hit.intent == "911_unavailable":
        canonical = hit.answer_fn(question, result)
    else:
        canonical = build_synthesis_canonical(
            hit.intent, question, result, issue_label=issue_label, period=window.label,
        )
    if not canonical:
        canonical = hit.answer_fn(question, result)

    payload = build_synthesis_payload(
        hit.intent,
        question,
        result,
        canonical_facts=canonical,
        supplemental_result=supplemental_result,
        issue_label=issue_label,
    )
    kind = classify_from_intent(hit.intent)
    full_prompt = system_prompt(kind, conversation_history) + "\n\n"
    full_prompt += format_conversation_prefix(conversation_history)
    full_prompt += resident_question_block(question)
    full_prompt += (
        f"\nVerified facts (JSON):\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )

    model = os.getenv("GEMINI_SUMMARY_MODEL", app4.GEMINI_SUMMARY_MODEL)
    return app4._call_gemini_with_logging(model, full_prompt, temperature=0.35).strip()


def _format_compound_fallback(
    question: str,
    count_result: Dict[str, Any],
    types_result: Dict[str, Any],
    *,
    period: str,
) -> str:
    count_line = _format_count_answer(
        question, count_result, label="311 service requests", period=period,
    )
    breakdown = _format_grouped_answer(
        question,
        types_result,
        label="311 issue types",
        key_field="issue_type",
        period=period,
    )
    return f"{count_line}\n\n{breakdown}"


def _multi_part_enabled() -> bool:
    return os.getenv("PREPARED_SQL_MULTI_PART", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _execute_prepared_hit(
    hit: PreparedMatch,
    question: str,
    execute_sql: Callable[[str], Dict[str, Any]],
    *,
    supplemental_sql: Optional[str] = None,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    result, _sql_used = _execute_sql_with_mv_311_fallback(execute_sql, hit.sql, question)
    supplemental_result: Optional[Dict[str, Any]] = None
    extra_sql = supplemental_sql or hit.supplemental_sql
    if extra_sql:
        supplemental_result, _ = _execute_sql_with_mv_311_fallback(
            execute_sql, extra_sql, question,
        )
    return result, supplemental_result


def execute_prepared_facts(
    question: str,
    hit: PreparedMatch,
    execute_sql: Callable[[str], Dict[str, Any]],
    *,
    supplemental_sql: Optional[str] = None,
) -> Dict[str, Any]:
    """Run prepared SQL and return canonical facts only (no LLM synthesis)."""
    print(f"\n[Prepared SQL — {hit.intent}]\n{hit.sql}\n")
    result, supplemental_result = _execute_prepared_hit(
        hit, question, execute_sql, supplemental_sql=supplemental_sql,
    )
    window = _resolve_period(question, 7)
    period = window.label
    issue_label: Optional[str] = None
    terms = _issue_search_terms(question)
    if terms:
        issue_label = terms[0]

    if hit.intent == "311_count_with_types" and supplemental_result:
        count_line = build_synthesis_canonical(
            "311_weekly_count", question, result, period=period,
        )
        types_line = build_synthesis_canonical(
            "311_top_types", question, supplemental_result, period=period,
        )
        canonical = f"{count_line} {types_line}"
    elif hit.intent in DETAIL_CIVIC_INTENTS or hit.intent in (
        "311_top_types", "crime_by_offense",
    ):
        canonical = build_synthesis_canonical(
            hit.intent, question, result, issue_label=issue_label, period=period,
        )
    elif hit.intent in _311_BREAKDOWN_INTENTS and supplemental_result:
        canonical = build_synthesis_canonical(
            "311_weekly_count", question, result, period=period,
        )
        canonical += " " + build_synthesis_canonical(
            "311_top_types", question, supplemental_result, period=period,
        )
    elif hit.intent == "911_unavailable":
        canonical = hit.answer_fn(question, result)
    elif hit.intent in COUNT_CIVIC_INTENTS or hit.intent.startswith("crime"):
        canonical = build_synthesis_canonical(
            hit.intent, question, result, issue_label=issue_label, period=period,
        )
    else:
        canonical = hit.answer_fn(question, result)
    sql_text = hit.sql
    extra_sql = supplemental_sql or hit.supplemental_sql
    if extra_sql:
        sql_text = f"{sql_text}\n---\n{extra_sql}"

    row_analysis = None
    if hit.intent in DETAIL_CIVIC_INTENTS:
        row_analysis = analyze_311_report_rows(result.get("rows") or [])

    return {
        "intent": hit.intent,
        "canonical_facts": canonical,
        "result": result,
        "supplemental_result": supplemental_result,
        "sql": sql_text,
        "issue_label": issue_label,
        "row_analysis": row_analysis,
    }


def _llm_render_compound_answer(
    question: str,
    blocks: List[Dict[str, Any]],
    *,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    from answer_voice import (  # noqa: WPS433
        format_conversation_prefix,
        resident_question_block,
        system_prompt,
    )
    from sql_chat import app4  # noqa: WPS433

    full_prompt = system_prompt("multi_part", conversation_history) + "\n\n"
    full_prompt += format_conversation_prefix(conversation_history)
    full_prompt += resident_question_block(question)
    full_prompt += (
        f"\nVerified facts by topic (JSON):\n"
        f"{json.dumps(blocks, ensure_ascii=False, default=str)}"
    )
    model = os.getenv("GEMINI_SUMMARY_MODEL", app4.GEMINI_SUMMARY_MODEL)
    return app4._call_gemini_with_logging(model, full_prompt, temperature=0.35).strip()


def _enforce_compound_canonical_facts(answer: str, blocks: List[Dict[str, Any]]) -> str:
    """Ensure each block's lead fact appears if the LLM dropped it."""
    out = (answer or "").strip()
    for block in blocks:
        canonical = (block.get("canonical_facts") or "").strip()
        if not canonical:
            continue
        if block.get("intent") == "911_unavailable":
            continue
        count = _first_count_from_result(block.get("result") or {})
        if count is not None:
            if f"**{count:,}**" in out or f"**{count}**" in out:
                continue
        canonical_bold = _bold_counts_in_answer(canonical)
        if canonical_bold:
            answer_bold = _bold_counts_in_answer(out)
            if answer_bold and answer_bold[0] == canonical_bold[0]:
                continue
        if canonical.lower() not in out.lower():
            out = f"{out}\n\n{canonical}" if out else canonical
    return out.strip()


def enforce_compound_canonical_facts(answer: str, blocks: List[Dict[str, Any]]) -> str:
    return _enforce_compound_canonical_facts(answer, blocks)


def _try_run_compound_prepared_sql(
    question: str,
    execute_sql: Callable[[str], Dict[str, Any]],
    *,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    if not _multi_part_enabled():
        return None

    parts = decompose_civic_question(question)
    if len(parts) < 2:
        return None

    blocks: List[Dict[str, Any]] = []
    sql_parts: List[str] = []
    primary_result: Optional[Dict[str, Any]] = None
    window = _resolve_period(question, 7)

    for part in parts:
        sub_q = part.sub_question
        hit = match_prepared_sql(sub_q)
        if hit is None:
            blocks.append({
                "topic": part.topic,
                "sub_question": sub_q,
                "canonical_facts": (
                    f"I couldn't run a prepared query for the **{part.topic}** part of your question."
                ),
                "result": {"columns": [], "rows": []},
            })
            continue

        supplemental_sql = hit.supplemental_sql
        if part.topic == "311" and should_include_311_breakdown(question, hit.intent):
            supplemental_sql = supplemental_sql or _sql_311_top_types(window)

        print(f"\n[Prepared SQL — compound/{part.topic}: {hit.intent}]\n{hit.sql}\n")
        try:
            result, supplemental_result = _execute_prepared_hit(
                hit, sub_q, execute_sql, supplemental_sql=supplemental_sql,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ Compound prepared SQL failed ({part.topic}): {exc}")
            continue

        canonical = hit.answer_fn(sub_q, result)
        if hit.intent == "311_count_with_types" and supplemental_result:
            canonical = _format_compound_fallback(
                sub_q, result, supplemental_result, period=window.label,
            )

        block: Dict[str, Any] = {
            "topic": part.topic,
            "sub_question": sub_q,
            "intent": hit.intent,
            "canonical_facts": canonical,
            "result": result,
        }
        if hit.intent == "911_unavailable":
            block["data_unavailable"] = True
            block["path"] = "guardrail"
        if supplemental_result and supplemental_result.get("rows"):
            block["issue_type_breakdown"] = supplemental_result.get("rows")[:10]

        blocks.append(block)
        sql_parts.append(hit.sql)
        if supplemental_sql:
            sql_parts.append(supplemental_sql)
        if primary_result is None:
            primary_result = result

    if len(blocks) < 2:
        return None

    answer = ""
    if _conversational_llm_enabled():
        try:
            answer = _llm_render_compound_answer(
                question, blocks, conversation_history=conversation_history,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ Compound LLM answer failed: {exc}")

    if not answer.strip():
        answer = "\n\n".join(
            b["canonical_facts"] for b in blocks if b.get("canonical_facts")
        )

    answer = _enforce_compound_canonical_facts(answer, blocks)
    for block in blocks:
        intent = block.get("intent")
        if intent in ("911_unavailable",):
            continue
        if intent and block.get("result"):
            answer = validate_prepared_answer(
                answer, block["result"], prepared_intent=str(intent),
            )

    from answer_voice import polish_conversational_answer  # noqa: WPS433

    answer = polish_conversational_answer(
        answer.strip(),
        conversation_history,
        synthesis_blocks=blocks,
        prepared_intent=f"compound:{','.join(p.topic for p in parts)}",
    )

    return {
        "answer": answer.strip(),
        "sql": "\n---\n".join(sql_parts),
        "result": primary_result or {"columns": [], "rows": []},
        "prepared_intent": f"compound:{','.join(p.topic for p in parts)}",
        "db_backed": True,
    }


def _q(question: str) -> str:
    return (question or "").lower().strip()


def run_prepared_sql(
    question: str,
    execute_sql: Callable[[str], Dict[str, Any]],
    *,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Execute a catalog query if matched. Returns None when no match."""
    if not is_prepared_sql_enabled():
        return None

    compound = _try_run_compound_prepared_sql(
        question, execute_sql, conversation_history=conversation_history,
    )
    if compound is not None:
        return compound

    hit = match_prepared_sql(question)
    if hit is None:
        return None

    print(f"\n[Prepared SQL — {hit.intent}]\n{hit.sql}\n")
    supplemental_sql = hit.supplemental_sql
    if supplemental_sql is None and should_include_311_breakdown(question, hit.intent):
        supplemental_sql = _sql_311_top_types(_resolve_period(question, 7))
    try:
        result, supplemental_result = _execute_prepared_hit(
            hit, question, execute_sql, supplemental_sql=supplemental_sql,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ Prepared SQL failed ({hit.intent}): {exc}")
        return None

    use_llm = _prepared_should_use_llm(hit)
    answer = ""
    if use_llm:
        try:
            if hit.intent == "event_by_name":
                from sql_chat import app4  # noqa: WPS433

                answer = app4._llm_generate_answer(
                    question,
                    hit.sql,
                    result,
                    os.getenv("GEMINI_SUMMARY_MODEL", app4.GEMINI_SUMMARY_MODEL),
                    conversation_history,
                )
            else:
                answer = _llm_render_prepared_answer(
                    question,
                    hit,
                    result,
                    supplemental_result=supplemental_result,
                    conversation_history=conversation_history,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ Prepared SQL LLM summary failed: {exc}")

    if not (answer or "").strip():
        window = _resolve_period(question, 7)
        terms = _issue_search_terms(question)
        issue_label = terms[0] if terms else None
        if supplemental_result and hit.intent in _311_BREAKDOWN_INTENTS:
            count_line = build_synthesis_canonical(
                "311_weekly_count", question, result, period=window.label,
            )
            types_line = build_synthesis_canonical(
                "311_top_types", question, supplemental_result, period=window.label,
            )
            answer = f"{count_line} {types_line}"
        elif hit.intent in DETAIL_CIVIC_INTENTS or hit.intent in COUNT_CIVIC_INTENTS:
            answer = build_synthesis_canonical(
                hit.intent, question, result, issue_label=issue_label, period=window.label,
            )
        else:
            answer = hit.answer_fn(question, result)

    answer = validate_prepared_answer(
        (answer or "").strip(),
        result,
        prepared_intent=hit.intent,
    )
    from answer_voice import polish_conversational_answer  # noqa: WPS433

    answer = polish_conversational_answer(
        answer,
        conversation_history,
        prepared_intent=hit.intent,
    )

    out: Dict[str, Any] = {
        "answer": answer,
        "sql": hit.sql,
        "result": result,
        "prepared_intent": hit.intent,
        "db_backed": True,
    }
    if hit.intent == "event_by_name":
        out["event_sources"] = build_event_sources(result.get("rows") or [], question)
    return out


def _first_count_from_result(result: Dict[str, Any]) -> Optional[int]:
    rows = result.get("rows") or []
    if not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    for key in ("cnt", "count", "labeled_cnt", "proxy_cnt"):
        if key in row and row[key] is not None:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                continue
    return None


def _bold_counts_in_answer(answer: str) -> list[int]:
    return [int(m.replace(",", "")) for m in re.findall(r"\*\*(\d[\d,]*)\*\*", answer or "")]


def validate_prepared_answer(
    answer: str,
    result: Dict[str, Any],
    *,
    prepared_intent: Optional[str] = None,
) -> str:
    """Ensure prepared-SQL numeric answers match query results."""
    if not prepared_intent or not answer:
        return answer

    sql_count = _first_count_from_result(result)
    bold = _bold_counts_in_answer(answer)

    if prepared_intent == "crime_homicide_count":
        row = (result.get("rows") or [{}])[0] if result.get("rows") else {}
        labeled = int((row or {}).get("labeled_cnt") or 0)
        proxy = int((row or {}).get("proxy_cnt") or 0)
        if labeled > 0 and (not bold or bold[0] != labeled):
            return (
                f"There were **{labeled:,}** homicides in Dorchester "
                f"(offense labeled homicide or murder in Boston Open Data)."
            )
        if labeled == 0 and proxy > 0 and (not bold or proxy not in bold):
            return (
                f"I found **0** homicides explicitly labeled in Boston Open Data, "
                f"but **{proxy:,}** fatal-shooting death investigations in Dorchester."
            )
        return answer

    if sql_count is None or not bold or bold[0] == sql_count:
        return answer

    period_match = re.search(
        r"for (the last \d+ days|this year[^.]*|this weekend|in [A-Z][a-z]+ \d{4})",
        answer,
        re.I,
    )
    period = period_match.group(1) if period_match else "the selected period"
    prep = "in" if period.lower().startswith("in ") else "for"
    if prep == "in":
        period = period[3:]
    label = prepared_intent.replace("_", " ").replace("crime ", "").replace("311 ", "311 ")
    return f"There were **{sql_count:,}** {label} in Dorchester {prep} {period}."
