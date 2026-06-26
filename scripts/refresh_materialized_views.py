#!/usr/bin/env python3
"""Refresh MySQL materialized summary tables (Iteration 5)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_API_DIR = Path(__file__).resolve().parent.parent / "api"
_ROOT = _API_DIR.parent
load_dotenv(_ROOT / ".env")

if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from materialized_views import run_refresh  # noqa: E402


def _connection_factory():
    import mysql.connector

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "sentiment_demo"),
    )


def main() -> int:
    result = run_refresh(_connection_factory)
    print(result)
    if not result.get("enabled", True):
        return 0
    refreshed = result.get("refreshed") or {}
    return 0 if result.get("enabled") is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
