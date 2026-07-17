#!/usr/bin/env python3
"""Seed/cleanup synthetic staging fixtures with fail-closed identity guards.

Requires:
  OTP_STAGING_MUTATION_OK=1
  MYSQL_DB matching staging allowlist (or OTP_STAGING_MYSQL_DB contract)
  --apply and/or --cleanup

Never prints passwords. Fixture names always include OTP STAGING FIXTURE.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from identity import assert_staging_mutation_allowed  # noqa: E402

FIXTURE_PREFIX = "OTP STAGING FIXTURE "


def _ny_today():
    return datetime.now(ZoneInfo("America/New_York")).date()


def _connect(db_name: str):
    import mysql.connector

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", ""),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=db_name,
    )


def seed(conn) -> str:
    start = _ny_today() + timedelta(days=7)
    marker = uuid.uuid4().hex[:10]
    name = f"{FIXTURE_PREFIX}{marker}"
    event_date = f"{start.strftime('%A')}, {start.strftime('%B')} {start.day}"
    cur = conn.cursor()
    try:
        # Idempotent-ish: remove any prior row with same marker pattern not needed;
        # each seed creates a unique marker. Cleanup deletes by prefix.
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
                (
                    f"SYNTHETIC/TEST ONLY ({name}). Not official civic data. "
                    "Do not treat as current civic truth."
                ),
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
                ON DUPLICATE KEY UPDATE
                  event_name=VALUES(event_name),
                  raw_text=VALUES(raw_text)
                """,
                (
                    event_id,
                    name,
                    event_date,
                    start,
                    start,
                    "10:00:00",
                    "11:00:00",
                    f"SYNTHETIC/TEST ONLY ({name}).",
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
        return name
    finally:
        cur.close()


def cleanup(conn) -> int:
    cur = conn.cursor()
    try:
        deleted = 0
        try:
            cur.execute(
                "DELETE FROM events_materialized WHERE event_name LIKE %s",
                (f"{FIXTURE_PREFIX}%",),
            )
            deleted += cur.rowcount or 0
        except Exception:
            pass
        cur.execute(
            "DELETE FROM weekly_events WHERE event_name LIKE %s",
            (f"{FIXTURE_PREFIX}%",),
        )
        deleted += cur.rowcount or 0
        conn.commit()
        print(f"cleanup_fixture_rows={deleted}")
        return deleted
    finally:
        cur.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Staging fixture seed/cleanup (fail-closed identity guards)."
    )
    parser.add_argument("--apply", action="store_true", help="Seed fixtures")
    parser.add_argument("--cleanup", action="store_true", help="Remove fixture-owned rows only")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate identity guards and print target; do not mutate",
    )
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass

    try:
        db = assert_staging_mutation_allowed()
    except RuntimeError as exc:
        print(f"REFUSED: {exc}")
        return 2

    host = os.getenv("MYSQL_HOST", "")
    print(f"target_host={host}")
    print(f"target_db={db}")
    print("mutation_ok=OTP_STAGING_MUTATION_OK")

    if args.dry_run:
        print("dry_run=True (no mutation)")
        return 0

    if not args.apply and not args.cleanup:
        print("REFUSED: specify --apply and/or --cleanup (or --dry-run)")
        return 2

    conn = _connect(db)
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
