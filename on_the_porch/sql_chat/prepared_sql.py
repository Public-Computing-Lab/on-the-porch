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


def _days_from_question(question: str, default: int = 7) -> int:
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
        return 60
    if "30 day" in q or "last 30" in q:
        return 30
    if "90 day" in q or "last 90" in q:
        return 90
    return default


def _format_count_answer(
    question: str,
    result: Dict[str, Any],
    *,
    label: str,
    period: str,
) -> str:
    rows = result.get("rows") or []
    count = 0
    if rows and isinstance(rows[0], dict):
        count = rows[0].get("cnt") or rows[0].get("count") or 0
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    if count == 0:
        return (
            f"I didn't find any {label} in Dorchester for {period}. "
            "Try a wider date range or check back after the next data sync."
        )
    return (
        f"There were **{count:,}** {label} in Dorchester for {period} "
        "(based on the latest synced Boston Open Data)."
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


def _events_by_source_sql(source: str, days: int) -> str:
    days = max(1, min(days, 90))
    like = source.lower()
    return (
        "SELECT `event_name`, `event_date`, `start_date`, `start_time`, "
        "`raw_text`, `source_pdf` "
        "FROM `weekly_events` "
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


def _match_311(question: str) -> Optional[PreparedMatch]:
    q = (question or "").lower()
    if not any(h in q for h in ("311", "service request", "service requests", "city complaint")):
        return None
    days = _days_from_question(question, 7)
    period = f"the last {days} days"

    if any(h in q for h in ("how many", "count", "number of", "total")):
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
            answer_fn=lambda question, result: _format_recent_rows(
                question, result, title="open 311 cases",
                fields=["open_date", "issue_type", "street_name", "case_status"],
            ),
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
        "shooting", "theft", "burglary", "offense", "911", "police report",
    )
    if not any(h in q for h in crime_hints):
        return None
    days = _days_from_question(question, 30)
    period = f"the last {days} days"

    if any(h in q for h in ("how many", "count", "number of", "total", "stats")):
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
        _match_event_by_name,
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
