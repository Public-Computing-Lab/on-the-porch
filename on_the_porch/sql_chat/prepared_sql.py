"""Prepared SQL catalog — answer common civic questions without LLM SQL generation.

Iteration 1: rule-matched intents run fixed queries and template (or optional LLM)
summaries. Returns None when no catalog entry matches so the caller can fall back
to the full LLM SQL pipeline.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from query_plan import days_from_question as _days_from_question
from query_plan import period_from_question as _period_from_question

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


def _data_source_note() -> str:
    return "based on the latest synced Boston Open Data"


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
    defn = f" ({definition})" if definition else ""
    if count == 0:
        hint = zero_hint or (
            "Boston Open Data may label these incidents differently than BPD homicide statistics. "
            "Try a wider date range or check back after the next data sync."
        )
        return (
            f"I found **0** {label} in Dorchester for {period}{defn} "
            f"({_data_source_note()}). {hint}"
        )
    return (
        f"There were **{count:,}** {label} in Dorchester for {period}{defn} "
        f"({_data_source_note()})."
    )


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


def _sql_311_weekly_count(days: int) -> str:
    days = max(1, min(days, 365))
    return (
        "SELECT COUNT(*) AS cnt FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND `open_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY)"
    )


def _sql_311_top_types(days: int) -> str:
    days = max(1, min(days, 365))
    if os.getenv("MATERIALIZED_VIEWS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    ):
        weeks = max(1, min((days + 6) // 7, 52))
        return (
            "SELECT issue_type, SUM(request_count) AS cnt "
            "FROM `mv_311_weekly` "
            f"WHERE `week_start` >= DATE_SUB(CURDATE(), INTERVAL {weeks * 7} DAY) "
            "AND issue_type IS NOT NULL AND issue_type <> '' "
            "GROUP BY issue_type ORDER BY cnt DESC LIMIT 10"
        )
    issue = "COALESCE(NULLIF(`service_name`, ''), NULLIF(`case_topic`, ''))"
    return (
        f"SELECT {issue} AS issue_type, COUNT(*) AS cnt "
        "FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND `open_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY) "
        f"AND {issue} IS NOT NULL AND {issue} <> '' "
        f"GROUP BY {issue} ORDER BY cnt DESC LIMIT 10"
    )


def _sql_311_recent(days: int) -> str:
    days = max(1, min(days, 90))
    issue = "COALESCE(NULLIF(`service_name`, ''), NULLIF(`case_topic`, ''))"
    return (
        f"SELECT `open_date`, {issue} AS issue_type, `street_name`, `case_status` "
        "FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND `open_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY) "
        "ORDER BY `open_date` DESC LIMIT 20"
    )


def _sql_311_open(days: int) -> str:
    days = max(1, min(days, 90))
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
        f"AND `open_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY) "
        "ORDER BY `open_date` DESC LIMIT 20"
    )


def _sql_crime_monthly_count(days: int) -> str:
    days = max(1, min(days, 365))
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
        f"AND `occurred_on_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY)"
    )


def _sql_crime_homicide_date_clause(days: int, *, calendar_year: bool = False) -> str:
    if calendar_year:
        return "YEAR(`occurred_on_date`) = YEAR(CURDATE())"
    days = max(1, min(days, 365))
    return f"`occurred_on_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY)"


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


def _sql_crime_homicide_tiered(days: int, *, calendar_year: bool = False) -> str:
    date_clause = _sql_crime_homicide_date_clause(days, calendar_year=calendar_year)
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


def _sql_crime_top_offenses(days: int) -> str:
    days = max(1, min(days, 365))
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
        f"AND `occurred_on_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY) "
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


def _sql_311_issue_count(terms: tuple, days: int) -> str:
    """Count 311 requests matching issue-type keywords (pothole, trash, etc.)."""
    days = max(1, min(days, 365))
    parts: List[str] = []
    for term in terms:
        esc = term.replace("'", "''")
        parts.append(f"LOWER(COALESCE(`service_name`, '')) LIKE '%{esc}%'")
        parts.append(f"LOWER(COALESCE(`case_topic`, '')) LIKE '%{esc}%'")
    issue_clause = "(" + " OR ".join(parts) + ")"
    return (
        "SELECT COUNT(*) AS cnt FROM `service_requests_311` "
        f"WHERE {_DORCHESTER_311} "
        f"AND `open_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY) "
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


def _sql_crime_shooting_count(days: int) -> str:
    days = max(1, min(days, 365))
    return (
        "SELECT COUNT(*) AS cnt FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} "
        f"AND `occurred_on_date` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY) "
        "AND (`shooting` = 1 OR `shooting` = '1' OR UPPER(COALESCE(`shooting`, '')) = 'Y')"
    )


def _match_311(question: str) -> Optional[PreparedMatch]:
    q = (question or "").lower()
    if not any(h in q for h in _311_DOMAIN_HINTS):
        return None
    days, period = _period_from_question(question, 7)

    if any(h in q for h in ("how many", "count", "number of", "total")):
        generic_311 = any(
            h in q for h in ("311", "service request", "service requests", "city complaint")
        )
        issue_terms = _issue_search_terms(question)
        if issue_terms and not generic_311:
            label = issue_terms[0].replace("_", " ") + " 311 requests"
            return PreparedMatch(
                intent="311_issue_count",
                sql=_sql_311_issue_count(issue_terms, days),
                answer_fn=lambda question, result: _format_count_answer(
                    question, result, label=label, period=period,
                ),
            )
        if generic_311 or issue_terms:
            return PreparedMatch(
                intent="311_weekly_count",
                sql=_sql_311_weekly_count(days),
                answer_fn=lambda question, result: _format_count_answer(
                    question, result, label="311 service requests", period=period,
                ),
            )
    if any(h in q for h in ("top", "most common", "common", "breakdown", "types", "issues")):
        return PreparedMatch(
            intent="311_top_types",
            sql=_sql_311_top_types(days),
            answer_fn=lambda question, result: _format_grouped_answer(
                question, result, label="311 issue types",
                key_field="issue_type", period=period,
            ),
        )
    if "open" in q and any(h in q for h in ("case", "cases", "request", "requests")):
        return PreparedMatch(
            intent="311_open",
            sql=_sql_311_open(days),
            answer_fn=lambda question, result: _format_open_311_answer(question, result, period=period),
        )
    if any(h in q for h in ("recent", "latest", "last few", "open requests", "list")):
        return PreparedMatch(
            intent="311_recent",
            sql=_sql_311_recent(days),
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
    days, period = _period_from_question(question, 30)
    calendar_year = "this year" in q

    if any(h in q for h in ("how many", "count", "number of", "total", "stats")):
        if any(h in q for h in ("shooting", "shootings", "shots fired", "gun violence")):
            shoot_def = "incidents flagged shooting=1 in BPD crime reports"
            return PreparedMatch(
                intent="crime_shooting_count",
                sql=_sql_crime_shooting_count(days),
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
                    days, calendar_year=calendar_year,
                ),
                answer_fn=lambda question, result: _format_homicide_answer(
                    question, result, period=period,
                ),
            )
        return PreparedMatch(
            intent="crime_monthly_count",
            sql=_sql_crime_monthly_count(days),
            answer_fn=lambda question, result: _format_count_answer(
                question, result, label="crime incidents", period=period,
            ),
        )
    if any(h in q for h in ("top", "most common", "breakdown", "by type", "types")):
        return PreparedMatch(
            intent="crime_by_offense",
            sql=_sql_crime_top_offenses(days),
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


def is_prepared_sql_enabled() -> bool:
    return os.getenv("PREPARED_SQL_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def run_prepared_sql(
    question: str,
    execute_sql: Callable[[str], Dict[str, Any]],
    *,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Execute a catalog query if matched. Returns None when no match."""
    if not is_prepared_sql_enabled():
        return None

    hit = match_prepared_sql(question)
    if hit is None:
        return None

    print(f"\n[Prepared SQL — {hit.intent}]\n{hit.sql}\n")
    try:
        result = execute_sql(hit.sql)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ Prepared SQL failed ({hit.intent}): {exc}")
        return None

    use_llm = os.getenv("PREPARED_SQL_USE_LLM_SUMMARY", "false").strip().lower() in (
        "1", "true", "yes",
    ) or hit.intent == "event_by_name"
    answer = ""
    if use_llm:
        try:
            from sql_chat import app4  # noqa: WPS433

            answer = app4._llm_generate_answer(
                question,
                hit.sql,
                result,
                os.getenv("GEMINI_SUMMARY_MODEL", app4.GEMINI_SUMMARY_MODEL),
                conversation_history,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ Prepared SQL LLM summary failed: {exc}")

    if not (answer or "").strip():
        answer = hit.answer_fn(question, result)

    out: Dict[str, Any] = {
        "answer": (answer or "").strip(),
        "sql": hit.sql,
        "result": result,
        "prepared_intent": hit.intent,
    }
    if hit.intent == "event_by_name":
        out["event_sources"] = build_event_sources(result.get("rows") or [], question)
    return out
