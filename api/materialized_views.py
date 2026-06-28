"""Refresh pre-aggregated MySQL summary tables (Iteration 5)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

_DORCHESTER_311 = (
    "(`neighborhood` = 'Dorchester' OR `police_district` = 'C11')"
)
_DORCHESTER_CRIME = "`district` = 'C11'"
_EVENTS_DAYS_AHEAD = max(7, min(int(os.getenv("EVENTS_MATERIALIZED_DAYS", "90")), 180))
_311_ISSUE_EXPR = "COALESCE(NULLIF(`service_name`, ''), NULLIF(`case_topic`, ''))"
_OFFENSE_EXPR = (
    "COALESCE(NULLIF(`offense_code_group`, ''), NULLIF(`offense_description`, ''))"
)


def is_materialized_views_enabled() -> bool:
    return os.getenv("MATERIALIZED_VIEWS_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _extract_event_url(raw_text: Optional[str]) -> Optional[str]:
    if not raw_text:
        return None
    match = re.search(r"URL:\s*(https?://\S+)", raw_text, flags=re.IGNORECASE)
    return match.group(1).rstrip(").,]") if match else None


def refresh_events_materialized(conn) -> int:
    """Rebuild upcoming events snapshot from weekly_events."""
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM events_materialized")
        cursor.execute(
            f"""
            INSERT INTO events_materialized (
                id, event_name, event_date, start_date, end_date,
                start_time, end_time, raw_text, source_pdf, event_url, refreshed_at
            )
            SELECT
                id, event_name, event_date, start_date, end_date,
                start_time, end_time, raw_text, source_pdf, NULL,
                UTC_TIMESTAMP()
            FROM weekly_events
            WHERE start_date >= CURDATE()
              AND start_date <= DATE_ADD(CURDATE(), INTERVAL {_EVENTS_DAYS_AHEAD} DAY)
            """
        )
        updated = cursor.rowcount or 0
        if updated:
            cursor.execute(
                "SELECT id, raw_text FROM events_materialized WHERE event_url IS NULL"
            )
            for row_id, raw_text in cursor.fetchall():
                url = _extract_event_url(raw_text)
                if url:
                    cursor.execute(
                        "UPDATE events_materialized SET event_url = %s WHERE id = %s",
                        (url, row_id),
                    )
        conn.commit()
        return int(updated)
    finally:
        cursor.close()


def refresh_mv_311_weekly(conn, weeks: int = 12) -> int:
    weeks = max(1, min(weeks, 52))
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM mv_311_weekly")
        cursor.execute(
            f"""
            INSERT INTO mv_311_weekly (week_start, issue_type, request_count, refreshed_at)
            SELECT
                DATE_SUB(DATE(`open_date`), INTERVAL WEEKDAY(`open_date`) DAY) AS week_start,
                {_311_ISSUE_EXPR} AS issue_type,
                COUNT(*) AS request_count,
                UTC_TIMESTAMP()
            FROM service_requests_311
            WHERE {_DORCHESTER_311}
              AND `open_date` >= DATE_SUB(CURDATE(), INTERVAL {weeks * 7} DAY)
              AND {_311_ISSUE_EXPR} IS NOT NULL
            GROUP BY week_start, issue_type
            """
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        cursor.close()


def refresh_mv_crime_monthly(conn, months: int = 12) -> int:
    months = max(1, min(months, 24))
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM mv_crime_monthly")
        cursor.execute(
            f"""
            INSERT INTO mv_crime_monthly (
                month_start, offense_group, incident_count, refreshed_at
            )
            SELECT
                DATE_FORMAT(`occurred_on_date`, '%Y-%m-01') AS month_start,
                {_OFFENSE_EXPR} AS offense_group,
                COUNT(*) AS incident_count,
                UTC_TIMESTAMP()
            FROM crime_incident_reports
            WHERE {_DORCHESTER_CRIME}
              AND `occurred_on_date` >= DATE_SUB(CURDATE(), INTERVAL {months} MONTH)
              AND {_OFFENSE_EXPR} IS NOT NULL
              AND {_OFFENSE_EXPR} <> ''
            GROUP BY month_start, offense_group
            """
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        cursor.close()


def refresh_all_materialized_views(conn) -> Dict[str, int]:
    stats = {
        "events_materialized": refresh_events_materialized(conn),
        "mv_311_weekly": refresh_mv_311_weekly(conn),
        "mv_crime_monthly": refresh_mv_crime_monthly(conn),
    }
    return stats


def table_exists(conn, table_name: str) -> bool:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
            LIMIT 1
            """,
            (table_name,),
        )
        return cursor.fetchone() is not None
    finally:
        cursor.close()


def events_query_source(conn) -> str:
    """Return table name for the Events API read path."""
    if is_materialized_views_enabled() and table_exists(conn, "events_materialized"):
        return "events_materialized"
    return "weekly_events"


def run_refresh(connection_factory: Callable[[], Any]) -> Dict[str, Any]:
    if not is_materialized_views_enabled():
        return {"enabled": False, "refreshed": {}}
    conn = connection_factory()
    try:
        stats = refresh_all_materialized_views(conn)
        return {
            "enabled": True,
            "refreshed": stats,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()
