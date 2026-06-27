#!/usr/bin/env python3
"""Refresh Dorchester (C11) fatal-shooting YTD baseline from Boston Open Data.

Writes on_the_porch/data_ingestion/boston_data_sync/bpd_homicide_baseline.json
for homicide answer cross-checks. Uses local `shootings` table when present,
otherwise queries the CKAN API directly.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "on_the_porch"))

OUT_PATH = (
    ROOT
    / "on_the_porch"
    / "data_ingestion"
    / "boston_data_sync"
    / "bpd_homicide_baseline.json"
)
SHOOTINGS_RESOURCE = "73c7e069-701f-4910-986d-b950f46c91a1"
CKAN_SQL_URL = "https://data.boston.gov/api/3/action/datastore_search_sql"


def _from_mysql() -> int | None:
    try:
        import pymysql  # noqa: WPS433

        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DB", "sentiment_demo"),
            charset="utf8mb4",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM shootings "
                "WHERE district = 'C11' "
                "AND shooting_type_v2 = 'Fatal' "
                "AND YEAR(shooting_date) = YEAR(CURDATE())"
            )
            row = cur.fetchone()
        conn.close()
        return int(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001
        print(f"MySQL baseline query skipped: {exc}")
        return None


def _from_ckan() -> int:
    year = datetime.now(timezone.utc).year
    sql = (
        f'SELECT COUNT(*) AS cnt FROM "{SHOOTINGS_RESOURCE}" '
        "WHERE district = 'C11' "
        "AND shooting_type_v2 = 'Fatal' "
        f"AND shooting_date >= '{year}-01-01'"
    )
    for method, kwargs in (
        ("post", {"json": {"sql": sql}}),
        ("get", {"params": {"sql": sql}}),
    ):
        try:
            resp = getattr(requests, method)(CKAN_SQL_URL, timeout=60, **kwargs)
            if resp.status_code == 403:
                continue
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success"):
                continue
            records = payload.get("result", {}).get("records") or []
            if records:
                return int(records[0].get("cnt") or 0)
        except Exception:
            continue

    # Fallback: paginate datastore_search (no raw SQL)
    total = 0
    offset = 0
    page = 500
    while True:
        resp = requests.get(
            "https://data.boston.gov/api/3/action/datastore_search",
            params={
                "resource_id": SHOOTINGS_RESOURCE,
                "filters": json.dumps({
                    "district": "C11",
                    "shooting_type_v2": "Fatal",
                }),
                "limit": page,
                "offset": offset,
            },
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        records = payload.get("result", {}).get("records") or []
        for row in records:
            date_val = str(row.get("shooting_date") or "")
            if date_val.startswith(str(year)):
                total += 1
        if len(records) < page:
            break
        offset += page
    return total


def main() -> int:
    fatal_ytd = _from_mysql()
    source = "local shootings table"
    if fatal_ytd is None:
        fatal_ytd = _from_ckan()
        source = "Boston Open Data shootings dashboard (CKAN)"

    baseline = {
        "district": "C11",
        "neighborhood": "Dorchester",
        "fatal_shootings_ytd": fatal_ytd,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "note": (
            "BPD homicide rulings may differ from fatal shootings in the dashboard; "
            "use alongside labeled homicide counts in crime incident reports."
        ),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH} — C11 fatal shootings YTD: {fatal_ytd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
