#!/usr/bin/env python3
"""Bootstrap civic_source_status from existing local MySQL tables.

Honest basis: LATEST_RECORD_ONLY when only MAX(record date) is available.
Does not claim SOURCE_AS_OF. Safe for local demo only.

By default, does not overwrite stronger SOURCE_AS_OF / INGEST_METADATA rows.
Use --force to replace an existing status deliberately.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "on_the_porch"))

import mysql.connector

from source_freshness import (  # noqa: E402
    SOURCE_311,
    SOURCE_CRIME,
    FreshnessBasis,
    SourceStatusRecord,
    load_source_status,
    upsert_source_status,
)

_STRONGER_BASIS = frozenset(
    {
        FreshnessBasis.SOURCE_AS_OF,
        FreshnessBasis.INGEST_METADATA,
    }
)


def _conn():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "sentiment_demo"),
    )


def _max_date(conn, sql: str):
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()


def _count(conn, sql: str) -> int:
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        row = cursor.fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        cursor.close()


def bootstrap(*, claim_ingest_metadata: bool, force: bool = False) -> None:
    conn = _conn()
    try:
        try:
            from db_migrations import run_migrations

            run_migrations(_conn)
        except Exception as exc:
            print(f"migration note: {exc}")

        crime_latest = _max_date(
            conn,
            "SELECT DATE(MAX(`occurred_on_date`)) FROM crime_incident_reports",
        )
        crime_count = _count(conn, "SELECT COUNT(*) FROM crime_incident_reports")
        s311_latest = _max_date(
            conn,
            "SELECT DATE(MAX(`open_date`)) FROM service_requests_311",
        )
        s311_count = _count(conn, "SELECT COUNT(*) FROM service_requests_311")

        basis = (
            FreshnessBasis.INGEST_METADATA
            if claim_ingest_metadata
            else FreshnessBasis.LATEST_RECORD_ONLY
        )
        now = datetime.now(timezone.utc)
        for key, latest, count in (
            (SOURCE_CRIME, crime_latest, crime_count),
            (SOURCE_311, s311_latest, s311_count),
        ):
            existing = load_source_status(_conn, key)
            if (
                existing is not None
                and existing.freshness_basis in _STRONGER_BASIS
                and not force
                and not claim_ingest_metadata
            ):
                print(
                    f"{key}: skip bootstrap — existing basis "
                    f"{existing.freshness_basis.value} is stronger than "
                    f"{basis.value} (pass --force to overwrite)"
                )
                continue
            # LATEST_RECORD_ONLY: coverage mirrors latest record; basis stays honest.
            # Do not invent last_successful_ingest_at unless explicitly claiming ingest.
            record = SourceStatusRecord(
                source_key=key,
                last_successful_ingest_at=now if claim_ingest_metadata else None,
                data_through_date=latest,
                latest_record_date=latest,
                freshness_basis=basis,
                record_count=count,
            )
            ok = upsert_source_status(_conn, record)
            print(f"{key}: latest={latest} count={count} basis={basis.value} ok={ok}")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--claim-ingest-metadata",
        action="store_true",
        help="Mark basis as INGEST_METADATA (only when an ingest just succeeded).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing SOURCE_AS_OF / INGEST_METADATA rows.",
    )
    args = parser.parse_args()
    bootstrap(
        claim_ingest_metadata=args.claim_ingest_metadata,
        force=args.force,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
