#!/usr/bin/env python3
"""Tests for 911 question classification and routing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from sql_chat.prepared_sql import (  # noqa: E402
    classify_911_question,
    match_prepared_sql,
)


def test_colloquial_stats_is_crime_redirect():
    assert classify_911_question("what are the 911 stats in my neighbourhood?") == "crime_redirect"


def test_dispatch_calls_unavailable():
    assert classify_911_question("how many 911 calls last month?") == "dispatch_unavailable"


def test_advisory():
    assert classify_911_question("should i call 911 or 311 for a pothole?") == "advisory"


def test_explicit_crime_not_classified():
    assert classify_911_question("911 crime incidents last month") == "none"


def test_colloquial_matches_crime_sql():
    hit = match_prepared_sql("911 safety stats in Dorchester last month")
    assert hit is not None
    assert hit.intent.startswith("crime")


def main() -> int:
    tests = [
        test_colloquial_stats_is_crime_redirect,
        test_dispatch_calls_unavailable,
        test_advisory,
        test_explicit_crime_not_classified,
        test_colloquial_matches_crime_sql,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
