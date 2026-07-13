#!/usr/bin/env python3
"""Focused regressions for event-detail lookup and resident-safe failures."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

import answer_synthesizer  # noqa: E402
import intent_router  # noqa: E402
import plan_executor  # noqa: E402
import plan_builder  # noqa: E402
import unified_chatbot  # noqa: E402
from execution_plan import ExecutionPlan, FactBundle, FactPart, PlanPart  # noqa: E402
from plan_builder import build_execution_plan_rules  # noqa: E402
from resident_response import (  # noqa: E402
    execution_failure_message,
    internal_terms,
    sanitize_degraded_answer,
)
from query_plan import PeriodWindow  # noqa: E402
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


def test_schema_allowlist_and_file_operations_are_rejected() -> None:
    rejected = (
        "WITH `user` AS (SELECT * FROM `mysql`.`user`) SELECT * FROM `user`",
        "SELECT * FROM `information_schema`.`tables`",
        "SELECT * FROM `performance_schema`.`threads`",
        "SELECT * FROM `sys`.`version`",
        "SELECT * FROM `other_schema`.`weekly_events`",
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT `LOAD_FILE`('/etc/passwd')",
        "SELECT 1 INTO DUMPFILE '/tmp/x'",
        "SELECT `event_name` FROM `weekly_events` INTO OUTFILE '/tmp/x'",
        "SeLeCt LoAd_FiLe/**/('/etc/passwd')",
        "SELECT 1 InTo/**/DuMpFiLe '/tmp/x'",
        "SELECT `event_name` FROM `weekly_events` InTo /* blocked */ OuTfIlE '/tmp/x'",
    )
    for sql in rejected:
        _assert_rejected(sql)


def test_table_query_expressions_are_rejected() -> None:
    rejected = (
        "TABLE weekly_events",
        "WITH x AS (TABLE weekly_events) SELECT * FROM x",
        "WITH x AS (TABLE mysql.user) SELECT * FROM x",
        "SELECT * FROM (TABLE weekly_events) AS x",
        "WiTh x AS (TaBlE mysql.user) SeLeCt * FrOm x",
        "WITH x AS (TABLE/**/mysql.user) SELECT * FROM x",
        "WITH x AS (/* source */ TABLE mysql.user) SELECT * FROM x",
    )
    for sql in rejected:
        _assert_rejected(sql)


def test_table_word_inside_literals_and_comments_is_allowed() -> None:
    literal = (
        "SELECT 'The community table is available' AS note "
        "FROM weekly_events"
    )
    quoted_description = (
        'SELECT "Bring food to the welcome table" AS description '
        "FROM weekly_events"
    )
    commented = (
        "SELECT event_name FROM weekly_events "
        "/* TABLE mysql.user is documentation, not executable */"
    )
    for sql in (literal, quoted_description, commented):
        assert app4._validate_select_only_sql(sql).startswith("SELECT")


def test_straight_join_bypasses_are_rejected() -> None:
    rejected = (
        (
            "SELECT w.id FROM weekly_events w "
            "STRAIGHT_JOIN events_materialized e ON 1=1"
        ),
        "SELECT w.id FROM weekly_events w STRAIGHT_JOIN mysql.user u ON 1=1",
        "SELECT w.id FROM weekly_events w STRAIGHT_JOIN other_schema.secret s ON 1=1",
        (
            "WITH x AS (SELECT w.id FROM weekly_events w "
            "STRAIGHT_JOIN mysql.user u ON 1=1) SELECT * FROM x"
        ),
        (
            "SELECT * FROM (SELECT w.id FROM weekly_events w "
            "STRAIGHT_JOIN mysql.user u ON 1=1) AS x"
        ),
        "SELECT w.id FROM weekly_events w StRaIgHt_JoIn mysql.user u ON 1=1",
        "SELECT w.id FROM weekly_events w STRAIGHT_JOIN/**/mysql.user u ON 1=1",
    )
    for sql in rejected:
        _assert_rejected(sql)


def test_supported_table_source_subset_extracts_every_physical_table() -> None:
    accepted = (
        "SELECT event_name FROM weekly_events",
        (
            "SELECT w.event_name, e.event_name FROM weekly_events w, "
            "events_materialized e"
        ),
        (
            "SELECT w.event_name FROM weekly_events w "
            "JOIN events_materialized e ON e.event_name = w.event_name"
        ),
        (
            "SELECT w.event_name FROM weekly_events w "
            "INNER JOIN events_materialized e ON e.event_name = w.event_name"
        ),
        (
            "SELECT w.event_name FROM weekly_events w "
            "LEFT OUTER JOIN events_materialized e ON e.event_name = w.event_name"
        ),
        (
            "SELECT w.event_name FROM weekly_events w "
            "RIGHT JOIN events_materialized e ON e.event_name = w.event_name"
        ),
        "SELECT w.event_name FROM weekly_events w CROSS JOIN events_materialized e",
        (
            "WITH x AS (SELECT event_name FROM weekly_events) "
            "SELECT x.event_name FROM x"
        ),
        (
            "WITH weekly_events AS (SELECT event_name FROM events_materialized) "
            "SELECT event_name FROM weekly_events"
        ),
        (
            "SELECT x.event_name FROM "
            "(SELECT event_name FROM weekly_events) AS x"
        ),
        (
            "SELECT event_name FROM weekly_events WHERE id IN "
            "(SELECT id FROM events_materialized)"
        ),
    )
    for sql in accepted:
        validated = app4._validate_select_only_sql(sql)
        assert validated.startswith(("SELECT", "WITH")), validated

    rejected = (
        "SELECT * FROM weekly_events, mysql.user",
        "SELECT * FROM weekly_events, other_schema.secret",
        "SELECT * FROM weekly_events JOIN mysql.user ON 1=1",
        "SELECT * FROM weekly_events LEFT JOIN other_schema.secret ON 1=1",
        "WITH x AS (SELECT * FROM mysql.user) SELECT * FROM x",
        (
            "WITH weekly_events AS (SELECT * FROM mysql.user) "
            "SELECT * FROM weekly_events"
        ),
        "SELECT * FROM (SELECT * FROM other_schema.secret) AS x",
        "SELECT * FROM weekly_events WHERE id IN (SELECT id FROM mysql.user)",
    )
    for sql in rejected:
        _assert_rejected(sql)


def test_unsupported_table_source_extensions_fail_closed() -> None:
    groups = {
        "natural": (
            "SELECT * FROM weekly_events NATURAL JOIN events_materialized",
            "SELECT * FROM weekly_events NATURAL JOIN mysql.user",
            "SELECT * FROM weekly_events NATURAL LEFT JOIN other_schema.secret",
        ),
        "lateral": (
            (
                "SELECT * FROM weekly_events w JOIN LATERAL "
                "(SELECT * FROM events_materialized) x ON TRUE"
            ),
            (
                "SELECT * FROM weekly_events w JOIN LATERAL "
                "(SELECT * FROM mysql.user) x ON TRUE"
            ),
            (
                "SELECT * FROM weekly_events w JOIN LATERAL "
                "(SELECT * FROM other_schema.secret) x ON TRUE"
            ),
        ),
        "json_table": (
            (
                "SELECT * FROM weekly_events w JOIN "
                "JSON_TABLE(w.raw_text, '$[*]' COLUMNS(v VARCHAR(20) PATH '$')) j"
            ),
            "SELECT * FROM mysql.user u JOIN JSON_TABLE('[]', '$[*]' COLUMNS(v INT PATH '$')) j",
            (
                "SELECT * FROM other_schema.secret s JOIN "
                "JSON_TABLE('[]', '$[*]' COLUMNS(v INT PATH '$')) j"
            ),
        ),
        "parenthesized_join": (
            "SELECT * FROM (weekly_events JOIN events_materialized ON 1=1) x",
            "SELECT * FROM (weekly_events JOIN mysql.user ON 1=1) x",
            "SELECT * FROM (weekly_events JOIN other_schema.secret ON 1=1) x",
        ),
        "odbc_escape": (
            "SELECT * FROM { OJ weekly_events LEFT JOIN events_materialized ON 1=1 }",
            "SELECT * FROM { OJ weekly_events LEFT JOIN mysql.user ON 1=1 }",
            "SELECT * FROM { OJ weekly_events LEFT JOIN other_schema.secret ON 1=1 }",
        ),
        "index_hint": (
            "SELECT * FROM weekly_events USE INDEX (`PRIMARY`)",
            "SELECT * FROM mysql.user FORCE KEY (`PRIMARY`)",
            "SELECT * FROM other_schema.secret IGNORE INDEX (`idx`)",
        ),
        "partition": (
            "SELECT * FROM weekly_events PARTITION (p0)",
            "SELECT * FROM mysql.user PARTITION (p0)",
            "SELECT * FROM other_schema.secret PARTITION (p0)",
        ),
    }
    for examples in groups.values():
        for sql in examples:
            _assert_rejected(sql)


def test_table_source_words_in_literals_and_normal_comments_are_allowed() -> None:
    sql = (
        "SELECT 'STRAIGHT_JOIN LATERAL JSON_TABLE PARTITION SLEEP' AS note "
        "FROM weekly_events "
        "/* NATURAL JOIN mysql.user; USE INDEX; { OJ other_schema.secret } */"
    )
    assert app4._validate_select_only_sql(sql).startswith("SELECT")

    quoted_description = (
        'SELECT "Bring a TABLE to the NATURAL history display" AS description '
        "FROM weekly_events -- STRAIGHT_JOIN mysql.user\n"
    )
    assert app4._validate_select_only_sql(quoted_description).startswith("SELECT")


def test_executable_mysql_comments_and_non_mysql_dash_comments_are_rejected() -> None:
    rejected = (
        "SELECT * FROM weekly_events /*! STRAIGHT_JOIN mysql.user ON 1=1 */",
        "SELECT * FROM weekly_events /*+ JOIN_ORDER(weekly_events) */",
        "SELECT 1--x FROM mysql.user",
    )
    for sql in rejected:
        _assert_rejected(sql)


def test_current_prepared_query_corpus_remains_valid() -> None:
    rolling = PeriodWindow(days=30, label="the last 30 days")
    calendar = PeriodWindow(days=31, label="July 2026", calendar_ym="2026-07")
    original_mv = os.environ.get("MATERIALIZED_VIEWS_ENABLED")
    queries = ["SELECT 0 AS cnt", app4._fast_weekly_events_sql(14)]
    try:
        for enabled in ("true", "false"):
            os.environ["MATERIALIZED_VIEWS_ENABLED"] = enabled
            queries.extend((
                prepared_sql._sql_events_upcoming(14),
                prepared_sql._sql_events_upcoming(7, weekend_only=True),
                prepared_sql._events_by_source_sql("dotnews", 30),
                prepared_sql._event_by_name_sql("Crew & Cadet Street Parade", 90),
                prepared_sql._sql_311_weekly_count(rolling),
                prepared_sql._sql_311_top_types_live(rolling),
                prepared_sql._sql_311_top_types(rolling),
                prepared_sql._sql_311_top_types(calendar),
                prepared_sql._sql_311_issue_count(("pothole",), rolling),
                prepared_sql._sql_311_issue_recent(("pothole",), rolling),
                prepared_sql._sql_311_recent(rolling),
                prepared_sql._sql_311_open(rolling),
                prepared_sql._sql_crime_monthly_count(rolling),
                prepared_sql._sql_crime_monthly_count(calendar),
                prepared_sql._sql_crime_homicide_tiered(rolling),
                prepared_sql._sql_crime_top_offenses(rolling),
                prepared_sql._sql_crime_top_offenses(calendar),
                prepared_sql._sql_crime_shooting_count(rolling),
                prepared_sql.sql_311_top_types(rolling),
            ))
    finally:
        if original_mv is None:
            os.environ.pop("MATERIALIZED_VIEWS_ENABLED", None)
        else:
            os.environ["MATERIALIZED_VIEWS_ENABLED"] = original_mv

    for sql in queries:
        validated = app4._validate_select_only_sql(sql)
        assert validated.startswith(("SELECT", "WITH")), sql


def test_resource_control_functions_are_rejected() -> None:
    rejected = (
        "SELECT SLEEP(5)",
        "SELECT BENCHMARK(1000, SHA2('x', 256))",
        "SELECT GET_LOCK('porch', 5)",
        "SELECT RELEASE_LOCK('porch')",
        "SELECT IS_FREE_LOCK('porch')",
        "SELECT IS_USED_LOCK('porch')",
        "SELECT RELEASE_ALL_LOCKS()",
        "SELECT sLeEp (5)",
        "SELECT GeT_LoCk /* blocked */ ('porch', 5)",
    )
    for sql in rejected:
        _assert_rejected(sql)


def test_resource_control_words_inside_literals_and_comments_are_allowed() -> None:
    sql = (
        "SELECT 'SLEEP(5) GET_LOCK(\"porch\", 5)' AS note "
        "FROM weekly_events /* BENCHMARK(1000, 1) */"
    )
    assert app4._validate_select_only_sql(sql).startswith("SELECT")


def test_prohibited_words_inside_literals_are_allowed() -> None:
    sql = (
        "SELECT REPLACE('LOAD_FILE INTO DUMPFILE REPLACE INTO; DELETE', "
        "'DELETE', 'safe') AS `label` FROM `weekly_events`"
    )
    validated = app4._validate_select_only_sql(sql)
    assert validated.startswith("SELECT REPLACE(")
    assert "LIMIT" in validated

    semicolon_literal = (
        "SELECT 'value; DELETE FROM mysql.user -- not executable' AS `note` "
        "FROM `weekly_events`"
    )
    assert app4._validate_select_only_sql(semicolon_literal).startswith("SELECT '")


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


def test_failure_categories_never_expose_raw_intents() -> None:
    cases = (
        ("crime_monthly_count", "public-safety information"),
        ("311_issue_count", "city-service information"),
        ("safety_overview", "public-safety information"),
        ("transcript", "community information"),
        ("opinion", "community information"),
        ("unknown_synthetic_intent", "general information"),
    )
    for intent, expected in cases:
        message = execution_failure_message(path="sql", intent=intent)
        assert expected in message.lower(), message
        assert intent not in message
        assert not internal_terms(message), message


def test_untrusted_llm_plan_fields_never_become_resident_text() -> None:
    cases = (
        ("event_by_name", "event details"),
        ("crime_monthly_count", "public-safety information"),
        ("unknown_internal_route", "general information"),
        ("local news", "local news"),
    )
    for topic, expected_category in cases:
        raw_guardrail = "The RAG pipeline failed with ChromaTimeoutError."
        content = json.dumps({
            "routing_mode": "hybrid",
            "routing_intent": "synthetic_review",
            "parts": [{
                "path": "guardrail",
                "intent": "unknown_synthetic_intent",
                "sub_question": "What information is available?",
                "topic": topic,
                "guardrail_message": raw_guardrail,
            }],
        })
        plan = plan_builder._parse_llm_plan_json(content, "What information is available?")
        assert plan is not None
        part = plan.parts[0]
        assert part.intent == "unknown_synthetic_intent"
        assert part.topic == expected_category
        assert part.guardrail_message != raw_guardrail
        assert not internal_terms(part.guardrail_message or "")

        bundle = plan_executor.execute_plan(
            plan,
            "What information is available?",
            execute_sql=lambda _sql: {"columns": [], "rows": []},
            retrieve_rag=lambda _q, _p: ([], []),
        )
        answer = bundle.parts[0].canonical_facts
        assert not internal_terms(answer), answer
        assert raw_guardrail not in answer
        assert expected_category in answer.lower()

        old_synthesis = os.environ.get("ANSWER_SYNTHESIS_ENABLED")
        os.environ["ANSWER_SYNTHESIS_ENABLED"] = "false"
        try:
            final_answer = answer_synthesizer.synthesize_and_validate(
                "What information is available?",
                bundle,
            )
        finally:
            if old_synthesis is None:
                os.environ.pop("ANSWER_SYNTHESIS_ENABLED", None)
            else:
                os.environ["ANSWER_SYNTHESIS_ENABLED"] = old_synthesis
        assert raw_guardrail not in final_answer
        assert not internal_terms(final_answer), final_answer

        safe_blocks = answer_synthesizer._resident_prompt_blocks(
            answer_synthesizer._blocks_from_bundle(bundle)
        )
        serialized = json.dumps(safe_blocks)
        assert topic not in serialized or topic == expected_category
        assert raw_guardrail not in serialized
        assert "unknown_synthetic_intent" not in serialized
        assert safe_blocks[0]["category"] == expected_category


def test_internal_guard_is_narrow_and_catches_identifiers() -> None:
    fallback = "Community information is temporarily unavailable."
    ordinary = "Bring a rag and gloves to the neighborhood cleanup."
    assert sanitize_degraded_answer(ordinary, fallback=fallback) == ordinary

    safe_words = "The event registration page includes neighborhood details."
    assert sanitize_degraded_answer(safe_words, fallback=fallback) == safe_words

    rejected = (
        "The RAG pipeline failed.",
        "I could not load event_by_name data.",
        "I could not load crime_monthly_count data.",
    )
    for answer in rejected:
        assert sanitize_degraded_answer(answer, fallback=fallback) == fallback


def test_degraded_hybrid_preserves_completed_fact_with_safe_category() -> None:
    answer = unified_chatbot._degraded_hybrid_answer(
        {
            "answer": "BPD's open data shows 42 incident reports in the last 30 days.",
            "result": {"rows": [{"cnt": 42}]},
            "prepared_intent": "crime_monthly_count",
            "db_backed": True,
        },
        {
            "answer": "The RAG pipeline failed for safety_overview.",
            "chunks": [],
            "metadata": [],
            "error": "ChromaTimeoutError",
        },
        {"intent": "safety_overview"},
    )
    assert answer is not None
    assert "42 incident reports" in answer
    assert "Public-safety information is temporarily unavailable." in answer
    assert not internal_terms(answer), answer


def test_title_and_date_metadata_are_not_duplicated() -> None:
    rows = (
        {
            "event_name": "CREW & CADET STREET PARADE",
            "event_date": "Mon, Jul 13",
            "start_date": date(2026, 7, 13),
            "raw_text": "CREW & CADET STREET PARADE Mon, Jul 13",
        },
        {
            "event_name": "CREW & CADET STREET PARADE",
            "event_date": "Mon, Jul 13",
            "start_date": date(2026, 7, 13),
            "raw_text": (
                "CREW & CADET STREET PARADE\nMon, Jul 13\n"
                "A neighborhood parade for local youth groups."
            ),
        },
    )
    for row in rows:
        answer = prepared_sql._format_event_detail_answer(SCREENSHOT_QUERY, {"rows": [row]})
        assert answer.count("CREW & CADET STREET PARADE") == 1, answer
        assert answer.count("Monday, July 13") == 1, answer
        assert "Mon, Jul 13" not in answer, answer


def test_event_prose_location_uses_precise_missing_field_wording() -> None:
    result = {
        "rows": [{
            "event_name": "Community Cleanup",
            "start_date": date(2026, 7, 18),
            "raw_text": "Community Cleanup\nJoin us at Codman Square.",
        }],
    }
    answer = prepared_sql._format_event_detail_answer(
        "Tell me about the Community Cleanup event", result,
    )
    assert "Join us at Codman Square." in answer
    assert "does not provide a separate location field" in answer
    assert "does not include a location" not in answer


def test_structured_event_location_is_rendered_once() -> None:
    result = {
        "rows": [{
            "event_name": "Community Cleanup",
            "start_date": date(2026, 7, 18),
            "raw_text": (
                "Community Cleanup\nHelp clean neighborhood streets.\n"
                "LOCATION: Codman Square"
            ),
        }],
    }
    answer = prepared_sql._format_event_detail_answer(
        "Tell me about the Community Cleanup event", result,
    )
    assert "Help clean neighborhood streets." in answer
    assert "**Location:** Codman Square" in answer
    assert answer.count("Codman Square") == 1
    assert "separate location field" not in answer


def test_location_mentioned_in_prose_and_structured_field_is_not_duplicated() -> None:
    result = {
        "rows": [{
            "event_name": "Community Cleanup",
            "start_date": date(2026, 7, 18),
            "raw_text": (
                "Community Cleanup\nJoin us at Codman Square.\n"
                "LOCATION: Codman Square"
            ),
        }],
    }
    answer = prepared_sql._format_event_detail_answer(
        "Tell me about the Community Cleanup event", result,
    )
    assert answer.count("Codman Square") == 1, answer
    assert "separate location field" not in answer


def test_title_date_only_event_uses_precise_missing_fields_message() -> None:
    result = {
        "rows": [{
            "event_name": "CREW & CADET STREET PARADE",
            "start_date": date(2026, 7, 13),
            "raw_text": "CREW & CADET STREET PARADE",
        }],
    }
    answer = prepared_sql._format_event_detail_answer(SCREENSHOT_QUERY, result)
    assert answer == (
        "**CREW & CADET STREET PARADE** is scheduled for **Monday, July 13**.\n\n"
        "The current listing does not include a detailed description and does not "
        "provide a separate location field."
    )


def test_inline_event_fields_have_clean_boundaries() -> None:
    url = "https://example.org/parade"
    result = {
        "rows": [{
            "event_name": "CREW & CADET STREET PARADE",
            "event_date": "Mon, Jul 13",
            "start_date": date(2026, 7, 13),
            "start_time": "13:00:00",
            "end_time": "15:00:00",
            "raw_text": (
                "CREW & CADET STREET PARADE Mon, Jul 13 "
                "A neighborhood parade for local youth groups. "
                "LOCATION: Codman Square CONTACT: Jane Doe "
                f"URL: {url} COST: Free REGISTRATION: Required"
            ),
        }],
    }
    answer = prepared_sql._format_event_detail_answer(SCREENSHOT_QUERY, result)
    assert "A neighborhood parade for local youth groups." in answer
    assert "**Time:** 1:00 PM – 3:00 PM" in answer
    assert "**Location:** Codman Square" in answer
    assert "**Contact:** Jane Doe" in answer
    assert "**Cost:** Free" in answer
    assert "**Registration:** Required" in answer
    assert answer.count(url) == 1, answer
    assert "Jane Doe URL" not in answer


def test_registration_url_is_not_duplicated() -> None:
    url = "https://example.org/register"
    result = {
        "rows": [{
            "event_name": "Community Cleanup",
            "start_date": date(2026, 7, 18),
            "raw_text": (
                "Community Cleanup\nHelp clean neighborhood streets.\n"
                "LOCATION: Codman Square\n"
                f"REGISTRATION: {url}\nURL: {url}"
            ),
        }],
    }
    answer = prepared_sql._format_event_detail_answer("Tell me about Community Cleanup", result)
    assert answer.count(url) == 1, answer
    assert "**Registration:** [Registration link]" in answer


def test_normal_event_list_output_is_unchanged() -> None:
    question = "What events are happening this weekend?"
    hit = prepared_sql.match_prepared_sql(question)
    assert hit is not None and hit.intent == "events_weekend"
    result = {
        "columns": ["event_name", "event_date", "start_date", "start_time"],
        "rows": [{
            "event_name": "Neighborhood Cleanup",
            "event_date": "Sat, Jul 18",
            "start_date": date(2026, 7, 18),
            "start_time": "10:00:00",
        }],
    }
    expected = app4._format_events_answer_fallback(question, result)
    assert hit.answer_fn(question, result) == expected


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
        test_schema_allowlist_and_file_operations_are_rejected,
        test_table_query_expressions_are_rejected,
        test_table_word_inside_literals_and_comments_is_allowed,
        test_straight_join_bypasses_are_rejected,
        test_supported_table_source_subset_extracts_every_physical_table,
        test_unsupported_table_source_extensions_fail_closed,
        test_table_source_words_in_literals_and_normal_comments_are_allowed,
        test_executable_mysql_comments_and_non_mysql_dash_comments_are_rejected,
        test_current_prepared_query_corpus_remains_valid,
        test_resource_control_functions_are_rejected,
        test_resource_control_words_inside_literals_and_comments_are_allowed,
        test_prohibited_words_inside_literals_are_allowed,
        test_exact_event_detail_plan_stays_sql_with_resident_topic,
        test_exact_event_detail_returns_confirmed_title_and_date,
        test_partial_event_record_names_missing_fields_without_total_failure,
        test_event_execution_error_never_exposes_internal_intent,
        test_failure_categories_never_expose_raw_intents,
        test_untrusted_llm_plan_fields_never_become_resident_text,
        test_internal_guard_is_narrow_and_catches_identifiers,
        test_degraded_hybrid_preserves_completed_fact_with_safe_category,
        test_title_and_date_metadata_are_not_duplicated,
        test_event_prose_location_uses_precise_missing_field_wording,
        test_structured_event_location_is_rendered_once,
        test_location_mentioned_in_prose_and_structured_field_is_not_duplicated,
        test_title_date_only_event_uses_precise_missing_fields_message,
        test_inline_event_fields_have_clean_boundaries,
        test_registration_url_is_not_duplicated,
        test_normal_event_list_output_is_unchanged,
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
