#!/usr/bin/env python3
"""Unit tests for execution pipeline plan builders (no DB or Gemini)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from plan_builder import build_execution_plan_rules  # noqa: E402


def _routing(mode: str, intent: str, **kwargs):
    base = {"mode": mode, "intent": intent, "confidence": 0.9, "k": 5}
    base.update(kwargs)
    return base


def test_compound_311_911_calls_dispatch_guardrail():
    q = "How many 311 and 911 calls in Dorchester last month?"
    plan = build_execution_plan_rules(q, _routing("sql", "311_stats"))
    assert plan is not None
    assert len(plan.parts) >= 2
    topics = {p.topic for p in plan.parts}
    assert "311" in topics
    assert "911" in topics
    assert plan.tier == "A"
    part_911 = next(p for p in plan.parts if p.topic == "911")
    assert part_911.path == "guardrail"
    assert part_911.intent == "911_unavailable"


def test_compound_311_911_colloquial_uses_crime():
    q = "How many 311 and 911 in Dorchester last month?"
    plan = build_execution_plan_rules(q, _routing("sql", "311_stats"))
    assert plan is not None
    topics = {p.topic for p in plan.parts}
    assert "311" in topics
    assert "crime" in topics
    assert "911" not in topics
    part_crime = next(p for p in plan.parts if p.topic == "crime")
    assert part_crime.path == "sql"
    assert part_crime.intent == "crime_monthly_count"


def test_single_311_count():
    q = "How many 311 requests in Dorchester last month?"
    plan = build_execution_plan_rules(q, _routing("sql", "311_stats"))
    assert plan is not None
    assert len(plan.parts) == 1
    assert plan.parts[0].path == "sql"
    assert plan.parts[0].intent.startswith("311")
    assert plan.parts[0].supplemental_intent == "311_top_types"


def test_311_with_breakdown():
    q = "How many 311 requests last month and what were they about?"
    plan = build_execution_plan_rules(q, _routing("sql", "311_stats"))
    assert plan is not None
    assert plan.parts[0].supplemental_intent == "311_top_types"


def test_hybrid_crime_opinion():
    q = "How many shootings last month and what do residents say about safety?"
    plan = build_execution_plan_rules(
        q,
        _routing(
            "hybrid",
            "crime_hybrid",
            transcript_tags=["safety", "violence"],
        ),
    )
    assert plan is not None
    paths = {p.path for p in plan.parts}
    assert "sql" in paths
    assert "rag" in paths


def test_compound_311_includes_types():
    q = "How many 311 and 911 calls in Dorchester last month?"
    plan = build_execution_plan_rules(q, _routing("sql", "311_stats"))
    assert plan is not None
    part_311 = next(p for p in plan.parts if p.topic == "311")
    assert part_311.supplemental_intent == "311_top_types"


def test_rag_transcript():
    q = "What did residents say about displacement in meetings?"
    plan = build_execution_plan_rules(
        q,
        _routing("rag", "transcript", transcript_tags=["displacement"]),
    )
    assert plan is not None
    assert plan.parts[0].path == "rag"


def test_911_routes_guardrail_not_sql():
    q = "How many 911 emergency calls in Dorchester this month?"
    plan = build_execution_plan_rules(q, _routing("sql", "911_unavailable"))
    assert plan is not None
    assert plan.parts[0].path == "guardrail"
    assert plan.parts[0].intent == "911_unavailable"
    assert "don't have" in (plan.parts[0].guardrail_message or "").lower()


def main() -> int:
    tests = [
        test_compound_311_911_calls_dispatch_guardrail,
        test_compound_311_911_colloquial_uses_crime,
        test_compound_311_includes_types,
        test_single_311_count,
        test_311_with_breakdown,
        test_hybrid_crime_opinion,
        test_rag_transcript,
        test_911_routes_guardrail_not_sql,
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
