#!/usr/bin/env python3
"""Bounded cleanup for conversation_turns retention (dry-run by default).

Recommended defaults:
  - COMPLETED: 30 days (covers realistic web-client transport retries; tradeoff:
    longer retention increases table size; shorter than ~7 days risks losing
    replay for slow client retries after network interruptions)
  - FAILED: 7 days
  - abandoned IN_PROGRESS (lease expired): 7 days after lease expiry

Does not delete rows with active (unexpired) leases.
Does not inspect message bodies.
Safe to re-run. Requires --apply to delete.

Usage:
  python scripts/cleanup_conversation_turns.py
  python scripts/cleanup_conversation_turns.py --apply --batch-size 500
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))


def _connect():
    import mysql.connector

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "rethink_ai_boston"),
        connection_timeout=int(os.getenv("MYSQL_CONNECT_TIMEOUT", "10")),
    )


def _count(cur, sql: str, params: tuple) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0] if row else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete eligible rows (default is dry-run)",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--completed-days", type=int, default=30)
    parser.add_argument("--failed-days", type=int, default=7)
    parser.add_argument("--abandoned-days", type=int, default=7)
    parser.add_argument("--max-batches", type=int, default=100)
    args = parser.parse_args()

    batch_size = max(1, min(5000, int(args.batch_size)))
    completed_days = max(1, int(args.completed_days))
    failed_days = max(1, int(args.failed_days))
    abandoned_days = max(1, int(args.abandoned_days))
    max_batches = max(1, int(args.max_batches))

    # Load .env without printing values.
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass

    conn = _connect()
    cur = conn.cursor()
    try:
        eligible_completed = _count(
            cur,
            """
            SELECT COUNT(*) FROM conversation_turns
            WHERE status = 'COMPLETED'
              AND updated_at < (UTC_TIMESTAMP() - INTERVAL %s DAY)
            """,
            (completed_days,),
        )
        eligible_failed = _count(
            cur,
            """
            SELECT COUNT(*) FROM conversation_turns
            WHERE status = 'FAILED'
              AND updated_at < (UTC_TIMESTAMP() - INTERVAL %s DAY)
            """,
            (failed_days,),
        )
        eligible_abandoned = _count(
            cur,
            """
            SELECT COUNT(*) FROM conversation_turns
            WHERE status = 'IN_PROGRESS'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < (UTC_TIMESTAMP() - INTERVAL %s DAY)
            """,
            (abandoned_days,),
        )
        print(
            "cleanup_conversation_turns "
            f"dry_run={not args.apply} "
            f"eligible_completed={eligible_completed} "
            f"eligible_failed={eligible_failed} "
            f"eligible_abandoned={eligible_abandoned} "
            f"batch_size={batch_size}"
        )
        if not args.apply:
            print("Dry-run only; pass --apply to delete.")
            return 0

        deleted_total = 0
        statements = [
            (
                "COMPLETED",
                """
                DELETE FROM conversation_turns
                WHERE status = 'COMPLETED'
                  AND updated_at < (UTC_TIMESTAMP() - INTERVAL %s DAY)
                LIMIT %s
                """,
                (completed_days, batch_size),
            ),
            (
                "FAILED",
                """
                DELETE FROM conversation_turns
                WHERE status = 'FAILED'
                  AND updated_at < (UTC_TIMESTAMP() - INTERVAL %s DAY)
                LIMIT %s
                """,
                (failed_days, batch_size),
            ),
            (
                "ABANDONED_IN_PROGRESS",
                """
                DELETE FROM conversation_turns
                WHERE status = 'IN_PROGRESS'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < (UTC_TIMESTAMP() - INTERVAL %s DAY)
                LIMIT %s
                """,
                (abandoned_days, batch_size),
            ),
        ]
        for label, sql, params in statements:
            for batch_i in range(max_batches):
                cur.execute(sql, params)
                deleted = cur.rowcount
                conn.commit()
                deleted_total += max(0, deleted)
                print(f"deleted_{label.lower()}_batch={batch_i + 1} rows={deleted}")
                if deleted < batch_size:
                    break
        print(f"deleted_total={deleted_total}")
        return 0
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
