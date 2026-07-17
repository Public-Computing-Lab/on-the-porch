#!/usr/bin/env python3
"""Seed/cleanup synthetic staging fixtures. Refuses unsafe database names.

Requires --i-am-staging and staging-looking MYSQL_DB. Never prints passwords.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]

# Never allow these identities to be treated as staging targets.
FORBIDDEN_DB_NAMES = {
    "",
    "mysql",
    "information_schema",
    "performance_schema",
    "sys",
}


def _ny_today():
    return datetime.now(ZoneInfo("America/New_York")).date()


def _assert_safe_target(db_name: str) -> None:
    name = (db_name or "").strip().lower()
    if name in FORBIDDEN_DB_NAMES:
        raise SystemExit(f"Refusing database name {db_name!r}")
    if "prod" in name and "staging" not in name:
        raise SystemExit(
            f"Refusing database name {db_name!r}: looks like production. "
            "Use an explicitly staging-named schema."
        )


def _connect():
    import mysql.connector

    host = os.getenv("MYSQL_HOST", "")
    user = os.getenv("MYSQL_USER", "")
    db = os.getenv("MYSQL_DB", "")
    _assert_safe_target(db)
    return mysql.connector.connect(
        host=host,
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=user,
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=db,
    )


def seed(conn) -> None:
    start = _ny_today() + timedelta(days=7)
    marker = uuid.uuid4().hex[:10]
    name = f"OTP STAGING FIXTURE {marker}"
    event_date = f"{start.strftime('%A')}, {start.strftime('%B')} {start.day}"
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO weekly_events (
              source_pdf, page_number, event_name, event_date,
              start_date, end_date, start_time, end_time, raw_text,
              category, normalized_name
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                "otp-staging-fixture.pdf",
                1,
                name,
                event_date,
                start,
                start,
                "10:00:00",
                "11:00:00",
                f"Synthetic staging fixture only ({name}). Not official civic data.",
                "community",
                name.lower(),
            ),
        )
        event_id = int(cur.lastrowid)
        try:
            cur.execute(
                """
                INSERT INTO events_materialized (
                  id, event_name, event_date, start_date, end_date,
                  start_time, end_time, raw_text, source_pdf, event_url
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE event_name=VALUES(event_name)
                """,
                (
                    event_id,
                    name,
                    event_date,
                    start,
                    start,
                    "10:00:00",
                    "11:00:00",
                    f"Synthetic staging fixture only ({name}).",
                    "otp-staging-fixture.pdf",
                    None,
                ),
            )
        except Exception:
            pass
        conn.commit()
        print(f"seeded_event_id={event_id}")
        print(f"seeded_event_name={name}")
        print(f"seeded_start_date={start}")
    finally:
        cur.close()


def cleanup(conn) -> None:
    cur = conn.cursor()
    try:
        try:
            cur.execute(
                "DELETE FROM events_materialized WHERE event_name LIKE 'OTP STAGING FIXTURE%'"
            )
        except Exception:
            pass
        cur.execute(
            "DELETE FROM weekly_events WHERE event_name LIKE 'OTP STAGING FIXTURE%'"
        )
        conn.commit()
        print(f"cleanup_weekly_events_rows={cur.rowcount}")
    finally:
        cur.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Seed fixtures")
    parser.add_argument("--cleanup", action="store_true", help="Remove staging fixtures")
    parser.add_argument(
        "--i-am-staging",
        action="store_true",
        help="Required acknowledgement that MYSQL_* points at staging",
    )
    args = parser.parse_args()
    if not args.i_am_staging:
        raise SystemExit("Refusing: pass --i-am-staging after verifying MYSQL_DB is staging-only")
    if not args.apply and not args.cleanup:
        raise SystemExit("Specify --apply and/or --cleanup")

    # Load env without printing
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass

    db = os.getenv("MYSQL_DB", "")
    host = os.getenv("MYSQL_HOST", "")
    print(f"target_host={host}")
    print(f"target_db={db}")
    _assert_safe_target(db)

    conn = _connect()
    try:
        if args.cleanup:
            cleanup(conn)
        if args.apply:
            seed(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
