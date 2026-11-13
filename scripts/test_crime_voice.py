#!/usr/bin/env python3
"""Tests for respectful crime answer phrasing."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from sql_chat.prepared_sql import (  # noqa: E402
    _format_crime_breakdown_answer,
    _format_crime_count_answer,
    _is_non_crime_offense,
    _sql_crime_top_offenses,
    crime_data_clarification_answer,
    is_crime_data_clarification_question,
    match_prepared_sql,
)
from query_plan import resolve_period  # noqa: E402


def _has_bold_number(text: str) -> bool:
    return bool(re.search(r"\*\*\d[\d,]*\*\*", text))


def test_breakdown_avoids_top_crimes_language():
    answer = _format_crime_breakdown_answer(
        "what crimes are most frequently reported?",
        {"rows": [
            {"offense_group": "Larceny", "cnt": 142},
            {"offense_group": "Simple Assault", "cnt": 89},
        ]},
        period="the last 30 days",
    )
    low = answer.lower()
    assert "top crime" not in low
    assert "scorecard" in low or "patterns" in low
    assert "larceny" in low
    assert not _has_bold_number(answer)
    assert "142" not in answer


def test_breakdown_excludes_non_crime_categories():
    answer = _format_crime_breakdown_answer(
        "what crimes are most frequently reported?",
        {"rows": [
            {"offense_group": "INVESTIGATE PERSON", "cnt": 50},
            {"offense_group": "SICK ASSIST", "cnt": 40},
            {"offense_group": "LARCENY SHOPLIFTING", "cnt": 12},
        ]},
        period="the last 30 days",
    )
    low = answer.lower()
    assert "shoplifting" in low
    assert "sick assist" not in low
    assert "investigating persons" not in low


def test_breakdown_includes_counts_when_asked():
    answer = _format_crime_breakdown_answer(
        "how many of each crime type last month?",
        {"rows": [
            {"offense_group": "Larceny", "cnt": 142},
            {"offense_group": "Simple Assault", "cnt": 89},
        ]},
        period="the last 30 days",
    )
    assert "142" in answer
    assert "89" in answer


def test_how_many_of_each_routes_to_breakdown():
    question = "how many of each crime type in Dorchester last month?"
    hit = match_prepared_sql(question)
    assert hit is not None
    assert hit.intent == "crime_by_offense"


def test_count_avoids_there_were_crime_incidents():
    answer = _format_crime_count_answer(
        "how many crime incidents last month?",
        {"rows": [{"cnt": 653}]},
        period="the last 30 days",
    )
    low = answer.lower()
    assert not low.startswith("there were")
    assert "653" in answer
    assert "whole story" in low or "context" in low


def test_911_reportings_breakdown_tone():
    hit = match_prepared_sql("what are the most comon 911 reportings in Dorchester?")
    assert hit is not None
    answer = hit.answer_fn(
        "what are the most comon 911 reportings in Dorchester?",
        {"rows": [{"offense_group": "Larceny", "cnt": 10}]},
    )
    assert "top crime" not in answer.lower()
    assert "10" not in answer


def test_frequently_reported_crimes_routes_to_breakdown():
    question = "what crimes are most frequently reported in Dorchester?"
    hit = match_prepared_sql(question)
    assert hit is not None
    assert hit.intent == "crime_by_offense"
    answer = hit.answer_fn(
        question,
        {"rows": [{"offense_group": "Larceny", "cnt": 142}]},
    )
    assert "top crime" not in answer.lower()
    assert "larceny" in answer.lower()
    assert "142" not in answer


def test_sql_excludes_sick_assist():
    sql = _sql_crime_top_offenses(resolve_period("last month", 30)).lower()
    assert "sick assist" in sql
    assert "not like" in sql


def test_non_crime_offense_classifier():
    assert _is_non_crime_offense("SICK ASSIST")
    assert _is_non_crime_offense("INVESTIGATE PERSON")
    assert not _is_non_crime_offense("LARCENY SHOPLIFTING")


def test_crime_clarification_question():
    assert is_crime_data_clarification_question("are these really crimes?")
    assert "sick assist" in crime_data_clarification_answer().lower()


def test_enforce_does_not_append_crime_breakdown_footer():
    from sql_chat.prepared_sql import enforce_compound_canonical_facts  # noqa: WPS433

    prose = (
        "For criminal offense categories in Dorchester for the last 30 days, "
        "**shoplifting** and **simple assaults** are among those recorded most often."
    )
    machine = (
        "BPD offense categories recorded most often in Dorchester for the last 7 days: "
        "LARCENY SHOPLIFTING, ASSAULT - SIMPLE"
    )
    out = enforce_compound_canonical_facts(
        prose,
        [{"intent": "crime_by_offense", "canonical_facts": machine, "result": {"rows": []}}],
    )
    assert out == prose


def test_ensure_crime_facts_when_llm_omits_categories():
    from sql_chat.prepared_sql import _ensure_crime_breakdown_facts  # noqa: WPS433

    vague = "Safety is a concern for many residents and the city tracks various reports."
    rows = {"rows": [
        {"offense_group": "LARCENY SHOPLIFTING", "cnt": 12},
        {"offense_group": "VANDALISM", "cnt": 8},
        {"offense_group": "ASSAULT - SIMPLE", "cnt": 5},
    ]}
    out = _ensure_crime_breakdown_facts(vague, rows)
    assert "shoplifting" in out.lower()
    assert "vandalism" in out.lower()


def test_ensure_crime_facts_skips_when_categories_present():
    from sql_chat.prepared_sql import _ensure_crime_breakdown_facts  # noqa: WPS433

    good = (
        "Over the last 30 days, shoplifting and vandalism were among the filings "
        "in Dorchester, along with simple assaults."
    )
    rows = {"rows": [
        {"offense_group": "LARCENY SHOPLIFTING", "cnt": 12},
        {"offense_group": "VANDALISM", "cnt": 8},
        {"offense_group": "ASSAULT - SIMPLE", "cnt": 5},
    ]}
    assert _ensure_crime_breakdown_facts(good, rows) == good


def test_polish_crime_strips_scoreboard():
    from answer_voice import polish_crime_answer  # noqa: WPS433

    scoreboard = (
        "Hey there! It's good to connect. Looking at the BPD safety statistics "
        "for Dorchester over the last 7 days:\n"
        "Shoplifting: **31** reports.\n"
        "Vandalism: **25** reports.\n"
        "Simple assaults: **22** reports."
    )
    out = polish_crime_answer(scoreboard, "what crimes are most frequently reported?")
    assert "hey there" not in out.lower()
    assert "**31**" not in out
    assert "shoplifting" in out.lower()


def test_frequently_reported_routes_sql():
    from intent_router import match_rules  # noqa: WPS433
    from sql_chat.prepared_sql import crime_routing_mode  # noqa: WPS433

    q = "what crimes are most frequently reported in Dorchester?"
    assert crime_routing_mode(q) == "sql"
    plan = match_rules(q)
    assert plan is not None
    assert plan.get("mode") == "sql"
    assert plan.get("intent") == "crime_stats"


def test_subjective_safety_routes_hybrid():
    from intent_router import match_rules  # noqa: WPS433
    from sql_chat.prepared_sql import crime_routing_mode  # noqa: WPS433

    q = "do a lot of crimes occur in this neighbourhood. is it a safe neighbourhood?"
    assert crime_routing_mode(q) == "hybrid"
    plan = match_rules(q)
    assert plan is not None
    assert plan.get("mode") == "hybrid"
    assert plan.get("intent") == "crime_safety_context"


def test_factual_and_subjective_differ():
    from sql_chat.prepared_sql import crime_routing_mode  # noqa: WPS433

    assert crime_routing_mode(
        "what crimes are most frequently reported in Dorchester?",
    ) == "sql"
    assert crime_routing_mode(
        "is Dorchester a safe neighbourhood to raise a family?",
    ) == "hybrid"


def test_subjective_safety_skips_count_enforcement():
    from sql_chat.prepared_sql import validate_prepared_answer  # noqa: WPS433

    narrative = (
        "Dorchester is a place where neighbors know each other. Like any urban area, "
        "BPD filings reflect real concerns, but they don't tell you what daily life feels like."
    )
    out = validate_prepared_answer(
        narrative,
        {"rows": [{"cnt": 633}]},
        prepared_intent="crime_monthly_count",
        question="is it a safe neighbourhood?",
    )
    assert out == narrative
    assert "633" not in out


def main() -> int:
    tests = [
        test_breakdown_avoids_top_crimes_language,
        test_breakdown_excludes_non_crime_categories,
        test_breakdown_includes_counts_when_asked,
        test_how_many_of_each_routes_to_breakdown,
        test_count_avoids_there_were_crime_incidents,
        test_911_reportings_breakdown_tone,
        test_frequently_reported_crimes_routes_to_breakdown,
        test_sql_excludes_sick_assist,
        test_non_crime_offense_classifier,
        test_crime_clarification_question,
        test_enforce_does_not_append_crime_breakdown_footer,
        test_ensure_crime_facts_when_llm_omits_categories,
        test_ensure_crime_facts_skips_when_categories_present,
        test_polish_crime_strips_scoreboard,
        test_frequently_reported_routes_sql,
        test_subjective_safety_routes_hybrid,
        test_factual_and_subjective_differ,
        test_subjective_safety_skips_count_enforcement,
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
