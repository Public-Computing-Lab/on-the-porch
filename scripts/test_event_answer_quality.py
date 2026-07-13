#!/usr/bin/env python3
"""Focused regressions for event-detail lookup and resident-safe failures."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

import answer_synthesizer  # noqa: E402
import intent_router  # noqa: E402
import plan_executor  # noqa: E402
from execution_plan import ExecutionPlan, FactBundle, FactPart, PlanPart  # noqa: E402
from plan_builder import build_execution_plan_rules  # noqa: E402
from resident_response import internal_terms, sanitize_degraded_answer  # noqa: E402
from sql_chat import app4, prepared_sql  # noqa: E402


SCREENSHOT_QUERY = (
    'Tell me more about "CREW & CADET STREET PARADE" event happening on Mon, Jul 13.'
)


class SkipTest(Exception):
    pass


def _assert_rejected(sql: str) -> None:
    try:
        app4._validate_select_only_sql(sql)
    except ValueError:
        return
    raise AssertionError(f"expected SQL to be rejected: {sql}")


def test_replace_function_is_allowed_in_select() -> None:
    sql = (
        "SELECT REPLACE(`event_name`, '&', 'and') AS `name` "
        "FROM `weekly_events`"
    )
    validated = app4._validate_select_only_sql(sql)
    assert validated.startswith("SELECT REPLACE(")
    assert "LIMIT" in validated


def test_replace_function_is_allowed_in_cte_select() -> None:
    sql = (
        "WITH `x` AS (SELECT `event_name` FROM `weekly_events`) "
        "SELECT REPLACE(`event_name`, '&', 'and') FROM `x`"
    )
    validated = app4._validate_select_only_sql(sql)
    assert validated.startswith("WITH `x` AS")
    assert "LIMIT" in validated


def test_write_and_chained_statements_are_rejected() -> None:
    rejected = (
        "REPLACE INTO `weekly_events` (`id`) VALUES (1)",
        "SELECT `event_name` FROM `weekly_events`; DELETE FROM `weekly_events`",
        "  /* leading comment */  REPLACE INTO `weekly_events` (`id`) VALUES (1)",
        "\n-- leading comment\n  uPdAtE `weekly_events` SET `event_name` = 'x'",
        "SeLeCt `event_name` FROM `weekly_events` WHERE 1 = 1; DrOp TABLE `weekly_events`",
    )
    for sql in rejected:
        _assert_rejected(sql)


def test_all_prohibited_statement_commands_are_rejected() -> None:
    rejected = (
        "INSERT INTO `weekly_events` (`id`) VALUES (1)",
        "UPDATE `weekly_events` SET `event_name` = 'x'",
        "DELETE FROM `weekly_events`",
        "DROP TABLE `weekly_events`",
        "ALTER TABLE `weekly_events` ADD COLUMN `x` INT",
        "CREATE TABLE `x` (`id` INT)",
        "TRUNCATE TABLE `weekly_events`",
        "GRANT SELECT ON `weekly_events` TO 'x'@'localhost'",
        "CALL `refresh_events`()",
        "LOAD DATA INFILE '/tmp/x' INTO TABLE `weekly_events`",
        "WITH `x` AS (SELECT `id` FROM `weekly_events`) DELETE FROM `weekly_events`",
    )
    for sql in rejected:
        _assert_rejected(sql)


def test_exact_event_detail_plan_stays_sql_with_resident_topic() -> None:
    routing = intent_router.route_with_rules(SCREENSHOT_QUERY)
    assert routing is not None
    plan = build_execution_plan_rules(SCREENSHOT_QUERY, routing)
    assert plan is not None
    assert plan.legacy_mode() == "sql"
    assert len(plan.parts) == 1
    assert plan.parts[0].intent == "event_by_name"
    assert plan.parts[0].topic == "event details"


def test_exact_event_detail_returns_confirmed_title_and_date() -> None:
    routing = intent_router.route_with_rules(SCREENSHOT_QUERY)
    assert routing is not None
    plan = build_execution_plan_rules(SCREENSHOT_QUERY, routing)
    assert plan is not None

    def _event_result(_sql: str):
        return {
            "columns": ["id", "event_name", "start_date", "raw_text", "source_pdf"],
            "rows": [{
                "id": 417,
                "event_name": "CREW & CADET STREET PARADE",
                "event_date": "Mon, Jul 13",
                "start_date": date(2026, 7, 13),
                "start_time": None,
                "end_time": None,
                "raw_text": "CREW & CADET STREET PARADE",
                "source_pdf": "community calendar",
            }],
        }

    bundle = plan_executor.execute_plan(
        plan,
        SCREENSHOT_QUERY,
        execute_sql=_event_result,
        retrieve_rag=lambda _q, _p: ([], []),
    )
    answer = answer_synthesizer.synthesize_and_validate(SCREENSHOT_QUERY, bundle)

    assert bundle.prepared_intent == "event_by_name"
    assert bundle.parts[0].result["rows"][0]["id"] == 417
    assert "CREW & CADET STREET PARADE" in answer
    assert "Monday, July 13" in answer
    assert "detailed description" in answer
    assert "location" in answer
    assert answer.startswith("**CREW & CADET STREET PARADE** is scheduled")
    assert not internal_terms(answer), answer


def test_partial_event_record_names_missing_fields_without_total_failure() -> None:
    result = {
        "columns": ["event_name", "start_date"],
        "rows": [{
            "event_name": "CREW & CADET STREET PARADE",
            "start_date": date(2026, 7, 13),
            "raw_text": "",
        }],
    }
    answer = prepared_sql._format_event_detail_answer(SCREENSHOT_QUERY, result)
    assert "CREW & CADET STREET PARADE" in answer
    assert "Monday, July 13" in answer
    assert "detailed description" in answer
    assert "location" in answer
    assert "didn't find that event" not in answer.lower()


def test_event_execution_error_never_exposes_internal_intent() -> None:
    part = PlanPart(
        path="sql",
        intent="event_by_name",
        topic="event details",
        sub_question=SCREENSHOT_QUERY,
    )
    plan = ExecutionPlan(
        question=SCREENSHOT_QUERY,
        parts=[part],
        routing_mode="sql",
    )

    def _fail(_sql: str):
        raise RuntimeError("SQL execution retrieval failure in event_by_name")

    bundle = plan_executor.execute_plan(
        plan,
        SCREENSHOT_QUERY,
        execute_sql=_fail,
        retrieve_rag=lambda _q, _p: ([], []),
    )
    assert bundle.parts[0].error
    fallback = bundle.parts[0].canonical_facts
    assert fallback == (
        "I found the event listing, but I could not load additional details right now."
    )

    leaked = FactBundle(
        question=SCREENSHOT_QUERY,
        parts=[FactPart(
            path="sql",
            intent="event_by_name",
            topic="event details",
            sub_question=SCREENSHOT_QUERY,
            canonical_facts=fallback,
            error="forced_test_error",
        )],
        prepared_intent="event_by_name",
    )
    answer = answer_synthesizer.validate_answer(
        "event_by_name retrieval failure caused RuntimeError during SQL execution",
        leaked,
        question=SCREENSHOT_QUERY,
    )
    assert answer == fallback
    assert not internal_terms(answer), answer

    factual = "The parade is scheduled for Monday, July 13."
    assert sanitize_degraded_answer(factual, fallback=fallback) == factual


def test_exact_event_id_from_demo_db_when_requested() -> None:
    if os.getenv("RUN_EVENT_DETAIL_DB_TEST", "false").lower() not in ("1", "true", "yes"):
        raise SkipTest("set RUN_EVENT_DETAIL_DB_TEST=true to verify local demo row 417")
    hit = prepared_sql.match_prepared_sql(SCREENSHOT_QUERY)
    assert hit is not None and hit.intent == "event_by_name"
    result = app4._execute_sql(hit.sql)
    matching = [row for row in result.get("rows") or [] if int(row.get("id") or 0) == 417]
    assert matching, f"event 417 not returned: {result.get('rows')}"
    answer = hit.answer_fn(SCREENSHOT_QUERY, result)
    assert "CREW & CADET STREET PARADE" in answer
    assert "Monday, July 13" in answer
    assert "detailed description" in answer
    assert "location" in answer


def main() -> int:
    tests = [
        test_replace_function_is_allowed_in_select,
        test_replace_function_is_allowed_in_cte_select,
        test_write_and_chained_statements_are_rejected,
        test_all_prohibited_statement_commands_are_rejected,
        test_exact_event_detail_plan_stays_sql_with_resident_topic,
        test_exact_event_detail_returns_confirmed_title_and_date,
        test_partial_event_record_names_missing_fields_without_total_failure,
        test_event_execution_error_never_exposes_internal_intent,
        test_exact_event_id_from_demo_db_when_requested,
    ]
    passed = 0
    skipped = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print(f"OK   {fn.__name__}")
        except SkipTest as exc:
            skipped += 1
            print(f"SKIP {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
