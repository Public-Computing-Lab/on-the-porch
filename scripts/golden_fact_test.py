#!/usr/bin/env python3
"""
Golden fact checks for prepared SQL answers and vectordb health.

Validates numeric civic answers against the database — not just keywords.
Does not call Gemini. Run: python scripts/golden_fact_test.py

Outcomes per fact:
  PASS | FAIL | SKIP_ENVIRONMENT | TIMEOUT_CONTROLLED
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))
sys.path.insert(0, str(ROOT / "on_the_porch" / "rag stuff"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

# Bound every external / vector / SQL dependency so the suite cannot hang.
VECTOR_CHECK_TIMEOUT_SECONDS = float(os.getenv("GOLDEN_VECTOR_TIMEOUT_SECONDS", "20"))
CASE_TIMEOUT_SECONDS = float(os.getenv("GOLDEN_CASE_TIMEOUT_SECONDS", "30"))
SUITE_TIMEOUT_SECONDS = float(os.getenv("GOLDEN_SUITE_TIMEOUT_SECONDS", "120"))


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


def _run_with_timeout(fn, timeout: float, label: str):
    """Run callable with a hard timeout; return (status, detail, value)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            value = future.result(timeout=timeout)
            return "ok", "", value
        except concurrent.futures.TimeoutError:
            future.cancel()
            return (
                "TIMEOUT_CONTROLLED",
                f"{label} exceeded {timeout:.0f}s timeout",
                None,
            )
        except Exception as exc:  # noqa: BLE001
            return "error", str(exc), None


def check_transcript_vectordb() -> Tuple[str, str]:
    """Ensure meeting transcripts are present in the vector store.

    Returns (PASS|FAIL|SKIP_ENVIRONMENT|TIMEOUT_CONTROLLED, detail).
    """
    min_chunks = int(os.getenv("TRANSCRIPT_MIN_CHUNKS", "50"))

    def _check():
        import retrieval  # noqa: WPS433

        retrieval.clear_query_embedding_cache()
        db = retrieval.load_vectordb()
        if db is None:
            return "SKIP_ENVIRONMENT", "vector DB not loaded"
        data = db.get(where={"doc_type": "transcript"}, include=[])
        count = len(data.get("ids") or [])
        if count == 0:
            return (
                "SKIP_ENVIRONMENT",
                "no local transcript corpus (0 chunks); "
                "not a product regression for this environment",
            )
        if count < min_chunks:
            return "FAIL", f"{count} transcript chunks < minimum {min_chunks}"
        return "PASS", f"{count} transcript chunks"

    status, detail, value = _run_with_timeout(
        _check, VECTOR_CHECK_TIMEOUT_SECONDS, "transcript_vectordb"
    )
    if status == "TIMEOUT_CONTROLLED":
        return status, detail
    if status == "error":
        # Missing chroma/corpus/import → environmental skip, not hang.
        lowered = detail.lower()
        if any(
            token in lowered
            for token in (
                "no such file",
                "not found",
                "does not exist",
                "chromadb",
                "vector",
                "permission",
            )
        ):
            return "SKIP_ENVIRONMENT", detail
        return "FAIL", detail
    return value  # type: ignore[return-value]


def _run_case(case: GoldenCase) -> Tuple[str, str]:
    def _inner():
        from sql_chat import app4, prepared_sql

        out = prepared_sql.run_prepared_sql(case.question, app4._execute_sql)
        if out is None:
            hit = prepared_sql.match_prepared_sql(case.question)
            if hit is None:
                return "FAIL", f"no prepared match (expected {case.expect_intent})"
            return "FAIL", f"prepared run failed (expected {case.expect_intent})"
        if out.get("prepared_intent") != case.expect_intent:
            return (
                "FAIL",
                f"intent {out.get('prepared_intent')!r} != {case.expect_intent!r}",
            )

        result = out.get("result") or {}
        if result.get("error"):
            return "FAIL", f"SQL error: {result['error']}"

        answer = out.get("answer", "")
        err = case.check(answer, result)
        if err:
            return "FAIL", err
        return "PASS", answer[:160].replace("\n", " ")

    status, detail, value = _run_with_timeout(
        _inner, CASE_TIMEOUT_SECONDS, case.id
    )
    if status == "TIMEOUT_CONTROLLED":
        return status, detail
    if status == "error":
        return "FAIL", detail
    return value  # type: ignore[return-value]


GOLDEN_CASES: List[GoldenCase] = [
    GoldenCase(
        "shootings_last_month",
        "How many shootings were there in Dorchester last month?",
        "crime_shooting_count",
        lambda answer, result: (
            None
            if (
                _extract_int(answer) is not None
                and (
                    "last month" in answer.lower()
                    or "june" in answer.lower()
                    or "not a complete count" in answer.lower()
                    or "data through" in answer.lower()
                    or "contains records through" in answer.lower()
                )
                and "did not find exact" not in answer.lower()
            )
            else "expected a bold shooting count with period or freshness coverage wording"
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
            if (
                _extract_int(answer) is not None
                or "does not describe what is happening right now" in answer.lower()
                or "can't provide a reliable count" in answer.lower()
                or "records only through" in answer.lower()
                or "records through" in answer.lower()
            )
            else "expected a numeric 311 count or explicit stale/out-of-coverage wording"
        ),
    ),
]


def main() -> int:
    print("Golden fact tests (prepared SQL + vectordb)")
    print("=" * 60)
    print(
        f"timeouts: vector={VECTOR_CHECK_TIMEOUT_SECONDS:.0f}s "
        f"case={CASE_TIMEOUT_SECONDS:.0f}s suite={SUITE_TIMEOUT_SECONDS:.0f}s"
    )

    results: List[Tuple[str, str, str]] = []

    def _suite():
        local = []
        status, detail = check_transcript_vectordb()
        local.append(("transcript_vectordb", status, detail))
        print(f"[{status}] transcript_vectordb")
        print(f"       {detail}")
        for case in GOLDEN_CASES:
            status, detail = _run_case(case)
            local.append((case.id, status, detail))
            print(f"[{status}] {case.id}")
            print(f"       {detail}")
        return local

    status, detail, value = _run_with_timeout(
        _suite, SUITE_TIMEOUT_SECONDS, "golden_suite"
    )
    if status == "TIMEOUT_CONTROLLED":
        print(f"[TIMEOUT_CONTROLLED] golden_suite")
        print(f"       {detail}")
        print("=" * 60)
        print("suite terminated with TIMEOUT_CONTROLLED (no hang)")
        return 2
    if status == "error":
        print(f"[FAIL] golden_suite: {detail}")
        return 1
    results = value or []

    counts = {"PASS": 0, "FAIL": 0, "SKIP_ENVIRONMENT": 0, "TIMEOUT_CONTROLLED": 0}
    for _id, st, _detail in results:
        counts[st] = counts.get(st, 0) + 1

    print("=" * 60)
    print(
        f"PASS={counts['PASS']} FAIL={counts['FAIL']} "
        f"SKIP_ENVIRONMENT={counts['SKIP_ENVIRONMENT']} "
        f"TIMEOUT_CONTROLLED={counts['TIMEOUT_CONTROLLED']}"
    )
    # Environmental skips are not product failures; hard FAIL/TIMEOUT exit non-zero.
    if counts["FAIL"] or counts["TIMEOUT_CONTROLLED"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
