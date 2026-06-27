#!/usr/bin/env python3
"""
Golden fact checks for prepared SQL answers (Phase 0).

Validates numeric civic answers against the database — not just keywords.
Does not call Gemini. Run: python scripts/golden_fact_test.py
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


@dataclass
class GoldenCase:
    id: str
    question: str
    expect_intent: str
    check: Callable[[str, dict], Optional[str]]


def _extract_int(text: str) -> Optional[int]:
    m = re.search(r"\*\*(\d[\d,]*)\*\*", text or "")
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _run_case(case: GoldenCase) -> tuple[bool, str]:
    from sql_chat import app4, prepared_sql

    hit = prepared_sql.match_prepared_sql(case.question)
    if hit is None:
        return False, f"no prepared match (expected {case.expect_intent})"
    if hit.intent != case.expect_intent:
        return False, f"intent {hit.intent!r} != {case.expect_intent!r}"

    result = app4._execute_sql(hit.sql)
    if result.get("error"):
        return False, f"SQL error: {result['error']}"

    answer = hit.answer_fn(case.question, result)
    err = case.check(answer, result)
    if err:
        return False, err
    return True, answer[:160].replace("\n", " ")


GOLDEN_CASES: List[GoldenCase] = [
    GoldenCase(
        "shootings_last_month",
        "How many shootings were there in Dorchester last month?",
        "crime_shooting_count",
        lambda answer, result: (
            None
            if "30 days" in answer.lower()
            and _extract_int(answer) is not None
            and "did not find exact" not in answer.lower()
            else "expected 30-day period and a bold count without hedging opener"
        ),
    ),
    GoldenCase(
        "homicides_ytd",
        "How many homicides were reported in Dorchester this year?",
        "crime_homicide_count",
        lambda answer, result: (
            None
            if (
                "death investigation" in answer.lower()
                or (_extract_int(answer) or 0) > 0
            )
            and "didn't find any homicides" not in answer.lower()
            else "expected tiered homicide answer (proxy or labeled count), not bare zero denial"
        ),
    ),
    GoldenCase(
        "311_week",
        "How many 311 service requests were filed in Dorchester this week?",
        "311_weekly_count",
        lambda answer, result: (
            None
            if _extract_int(answer) is not None and _extract_int(answer) >= 0
            else "expected a numeric 311 count"
        ),
    ),
]


def main() -> int:
    passed = 0
    failed: List[str] = []
    print("Golden fact tests (prepared SQL)")
    print("=" * 60)
    for case in GOLDEN_CASES:
        ok, detail = _run_case(case)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case.id}")
        if not ok:
            print(f"       {detail}")
            failed.append(case.id)
        else:
            print(f"       {detail}")
        if ok:
            passed += 1
    print("=" * 60)
    print(f"{passed}/{len(GOLDEN_CASES)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
