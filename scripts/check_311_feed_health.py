#!/usr/bin/env python3
"""Diagnose Boston 311 Creatio vs legacy-2026 row balance in MySQL."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DORCHESTER = "(`neighborhood` = 'Dorchester' OR `police_district` = 'C11')"


def main() -> int:
    conn = mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "rethink_ai_boston"),
    )
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) AS c FROM service_requests_311")
    total = int(cur.fetchone()["c"])

    cur.execute(
        f"""
        SELECT
          CASE WHEN case_id REGEXP '^[0-9]+$' THEN 'legacy_2026' ELSE 'creatio_bcs' END AS feed,
          COUNT(*) AS n
        FROM service_requests_311
        GROUP BY feed
        """
    )
    split = {row["feed"]: int(row["n"]) for row in cur.fetchall()}

    cur.execute(
        f"""
        SELECT COUNT(*) AS c FROM service_requests_311
        WHERE {DORCHESTER}
          AND open_date >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
          AND open_date < DATE_FORMAT(CURDATE(), '%Y-%m-01')
        """
    )
    last_month = int(cur.fetchone()["c"])

    cur.execute(
        f"""
        SELECT
          CASE WHEN case_id REGEXP '^[0-9]+$' THEN 'legacy_2026' ELSE 'creatio_bcs' END AS feed,
          COUNT(*) AS n
        FROM service_requests_311
        WHERE {DORCHESTER}
          AND open_date >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
          AND open_date < DATE_FORMAT(CURDATE(), '%Y-%m-01')
        GROUP BY feed
        """
    )
    last_month_split = {row["feed"]: int(row["n"]) for row in cur.fetchall()}

    cur.execute(
        "SELECT COUNT(*) AS c FROM service_requests_311 WHERE open_date IS NULL"
    )
    null_dates = int(cur.fetchone()["c"])

    cur.close()
    conn.close()

    legacy = split.get("legacy_2026", 0)
    creatio = split.get("creatio_bcs", 0)
    lm_legacy = last_month_split.get("legacy_2026", 0)
    lm_creatio = last_month_split.get("creatio_bcs", 0)

    print("311 feed health")
    print("=" * 50)
    print(f"Total rows in service_requests_311: {total:,}")
    print(f"  legacy_2026 (numeric IDs): {legacy:,}")
    print(f"  creatio_bcs (BCS-* IDs):   {creatio:,}")
    print(f"  rows with NULL open_date:   {null_dates:,}")
    print()
    print(f"Dorchester last calendar month: {last_month:,}")
    print(f"  legacy_2026: {lm_legacy:,}")
    print(f"  creatio_bcs: {lm_creatio:,}")
    print()

    ok = True
    if total > 0 and legacy == 0:
        print("⚠️  MISSING legacy_2026 feed — expect ~2,500+/month Dorchester, not ~474.")
        print("   Fix: git pull, then run:")
        print("     python on_the_porch/data_ingestion/boston_data_sync/boston_data_sync.py \\")
        print("       --dataset 311_service_requests_2026_legacy --full")
        ok = False
    elif last_month > 0 and lm_legacy == 0 and lm_creatio > 0:
        print("⚠️  Last month is Creatio-only — chat totals will undercount (~474 vs ~3,100).")
        ok = False
    else:
        print("✓ Both feeds appear present.")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
