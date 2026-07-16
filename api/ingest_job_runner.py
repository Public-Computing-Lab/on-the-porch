#!/usr/bin/env python3
"""CLI helpers for cron_ingest.sh job tracking (Iteration 5)."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_API_DIR = Path(__file__).resolve().parent
_ROOT = _API_DIR.parent
load_dotenv(_ROOT / ".env")

if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

_STATE_FILE = _ROOT / "logs" / ".ingest_job_state.json"


def _load_state() -> dict:
    import json

    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    import json

    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _mysql_conn():
    import mysql.connector

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "sentiment_demo"),
    )


def cmd_start(job_name: str) -> int:
    from ingest_queue import is_ingest_queue_enabled, record_job_start

    started_wall = time.time()
    mysql_conn = None
    if is_ingest_queue_enabled():
        try:
            mysql_conn = _mysql_conn()
        except Exception:
            mysql_conn = None
    started = record_job_start(job_name, mysql_conn=mysql_conn)
    state = _load_state()
    state[job_name] = {
        "run_id": started.get("run_id"),
        "mysql_id": started.get("mysql_id"),
        "started_at": started_wall,
    }
    _save_state(state)
    if mysql_conn:
        mysql_conn.close()
    print(started.get("run_id") or "")
    return 0


def cmd_finish(job_name: str, status: str, error: str = "") -> int:
    from ingest_queue import is_ingest_queue_enabled, record_job_finish

    state = _load_state()
    entry = state.get(job_name, {})
    started_at = entry.get("started_at")
    mysql_conn = None
    if is_ingest_queue_enabled():
        try:
            mysql_conn = _mysql_conn()
        except Exception:
            mysql_conn = None
    record_job_finish(
        job_name,
        status=status,
        error=error or None,
        started_at=started_at,
        mysql_conn=mysql_conn,
        mysql_row_id=entry.get("mysql_id"),
    )
    state.pop(job_name, None)
    _save_state(state)
    if mysql_conn:
        mysql_conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Track cron ingest job status")
    sub = parser.add_subparsers(dest="command", required=True)
    start_p = sub.add_parser("start")
    start_p.add_argument("job_name")
    fin_p = sub.add_parser("finish")
    fin_p.add_argument("job_name")
    fin_p.add_argument("status", choices=["success", "failed"])
    fin_p.add_argument("--error", default="")
    args = parser.parse_args()
    if args.command == "start":
        return cmd_start(args.job_name)
    return cmd_finish(args.job_name, args.status, args.error)


if __name__ == "__main__":
    raise SystemExit(main())
