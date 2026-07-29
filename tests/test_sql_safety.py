from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
SQL_CHAT = ROOT / "on_the_porch" / "sql_chat"
ON_THE_PORCH = ROOT / "on_the_porch"
for path in (SQL_CHAT, ON_THE_PORCH):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from sql_safety import (  # noqa: E402
    SQLSafetyError,
    fetch_bounded_rows,
    safe_sql_observability_payload,
    validate_dynamic_sql,
)


ALLOWED = {
    "weekly_events",
    "events_materialized",
    "service_requests_311",
    "mv_311_weekly",
    "crime_incident_reports",
    "mv_crime_monthly",
    "shootings",
}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT event_name, start_date FROM weekly_events",
        "SELECT reason, COUNT(*) AS count FROM service_requests_311 GROUP BY reason ORDER BY count DESC",
        "SELECT e.event_name, c.offense_description FROM weekly_events e JOIN crime_incident_reports c ON 1 = 0",
        "SELECT * FROM weekly_events WHERE id IN (SELECT id FROM weekly_events)",
        "WITH recent AS (SELECT * FROM service_requests_311 WHERE open_date >= CURRENT_DATE - INTERVAL 7 DAY) SELECT COUNT(*) FROM recent",
        "SELECT DATE(open_date), COUNT(*) FROM service_requests_311 GROUP BY DATE(open_date)",
        "SELECT REPLACE(event_name, 'Parade', 'Procession') FROM weekly_events",
        "SELECT 'DROP TABLE users' AS harmless_text FROM weekly_events",
        "SELECT 'Bring a rag -- and gloves' AS note FROM weekly_events",
        "SeLeCt COUNT(*) FrOm weekly_events",
    ],
)
def test_valid_civic_queries_are_accepted(sql: str) -> None:
    result = validate_dynamic_sql(sql, tables=ALLOWED, row_limit=20)
    assert result.tables
    assert result.fingerprint
    assert "LIMIT 21" in result.sql.upper()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO weekly_events (event_name) VALUES ('x')",
        "UPDATE weekly_events SET event_name = 'x'",
        "DELETE FROM weekly_events",
        "REPLACE INTO weekly_events (event_name) VALUES ('x')",
        "MERGE INTO weekly_events USING service_requests_311 ON 1=1 WHEN MATCHED THEN DELETE",
        "DROP TABLE weekly_events",
        "ALTER TABLE weekly_events ADD COLUMN x INT",
        "TRUNCATE TABLE weekly_events",
        "CREATE TABLE x (id INT)",
        "RENAME TABLE weekly_events TO x",
        "GRANT SELECT ON *.* TO x",
        "REVOKE SELECT ON *.* FROM x",
        "CALL civic_proc()",
        "EXECUTE stmt",
        "PREPARE stmt FROM 'SELECT 1'",
        "DEALLOCATE PREPARE stmt",
        "SET @x = 1",
        "USE mysql",
        "START TRANSACTION",
        "COMMIT",
        "ROLLBACK",
        "LOCK TABLES weekly_events READ",
        "LOAD DATA INFILE '/tmp/x' INTO TABLE weekly_events",
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT * FROM weekly_events INTO OUTFILE '/tmp/x'",
        "SELECT * FROM weekly_events INTO DUMPFILE '/tmp/x'",
        "SELECT @x := 1",
        "SELECT SLEEP(10)",
        "SHOW TABLES",
        "SELECT * FROM weekly_events FOR UPDATE",
    ],
)
def test_unsafe_statement_families_are_rejected(sql: str) -> None:
    with pytest.raises(SQLSafetyError):
        validate_dynamic_sql(sql, tables=ALLOWED)


@pytest.mark.parametrize(
    ("sql", "category"),
    [
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("SELECT * FROM weekly_events -- trailing", "comments_not_allowed"),
        ("SELECT * FROM weekly_events # trailing", "comments_not_allowed"),
        ("SELECT /* hidden */ * FROM weekly_events", "comments_not_allowed"),
        ("SELECT * FROM mysql.user", "system_schema"),
        ("SELECT * FROM information_schema.tables", "system_schema"),
        ("SELECT * FROM performance_schema.threads", "system_schema"),
        ("SELECT * FROM sys.user_summary", "system_schema"),
        ("SELECT * FROM users", "table_not_allowed"),
        ("SELECT * FROM conversation_messages", "table_not_allowed"),
        ("SELECT * FROM admin_knowledge", "table_not_allowed"),
        ("SELECT * FROM schema_migrations", "table_not_allowed"),
        ("SELECT * FROM other_schema.weekly_events", "cross_schema_reference"),
        ("WITH users AS (SELECT * FROM users) SELECT * FROM users", "table_not_allowed"),
        ("SELECT password_hash FROM weekly_events", "sensitive_column"),
        ("SELECT FROM", "parse_error"),
    ],
)
def test_bypass_and_private_data_attempts_are_rejected(sql: str, category: str) -> None:
    with pytest.raises(SQLSafetyError) as caught:
        validate_dynamic_sql(
            sql,
            tables=ALLOWED,
            current_database="sentiment_demo",
        )
    assert caught.value.category == category


def test_current_database_qualification_is_allowed() -> None:
    result = validate_dynamic_sql(
        "SELECT * FROM sentiment_demo.weekly_events",
        tables=ALLOWED,
        current_database="sentiment_demo",
    )
    assert result.tables == ("weekly_events",)


def test_existing_smaller_limit_is_preserved() -> None:
    result = validate_dynamic_sql(
        "SELECT * FROM weekly_events LIMIT 5",
        tables=ALLOWED,
        row_limit=100,
    )
    assert "LIMIT 5" in result.sql.upper()


def test_selected_column_limit_is_enforced() -> None:
    with pytest.raises(SQLSafetyError, match="too_many_columns"):
        validate_dynamic_sql(
            "SELECT event_name, event_date, start_date FROM weekly_events",
            tables=ALLOWED,
            column_limit=2,
        )


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.fetchmany_calls = []

    def fetchmany(self, amount):
        self.fetchmany_calls.append(amount)
        return self.rows[:amount]


def test_row_limit_fetches_only_limit_plus_one() -> None:
    cursor = FakeCursor([(index,) for index in range(20)])
    result = fetch_bounded_rows(
        cursor,
        columns=("value",),
        row_limit=5,
        payload_limit_bytes=10_000,
    )
    assert cursor.fetchmany_calls == [6]
    assert len(result.rows) == 5
    assert result.truncated is True


def test_payload_limit_truncates_without_loading_more_rows() -> None:
    cursor = FakeCursor([("x" * 500,), ("y" * 500,)])
    result = fetch_bounded_rows(
        cursor,
        columns=("value",),
        row_limit=10,
        payload_limit_bytes=200,
    )
    assert result.rows == ()
    assert result.truncated is True
    assert result.payload_bytes <= 200


def test_observability_payload_contains_no_raw_sql() -> None:
    secret_literal = "resident-private-literal"
    validated = validate_dynamic_sql(
        f"SELECT * FROM weekly_events WHERE event_name = '{secret_literal}'",
        tables=ALLOWED,
    )
    payload = safe_sql_observability_payload(validated=validated, accepted=True)
    serialized = json.dumps(payload)
    assert secret_literal not in serialized
    assert "SELECT" not in serialized
    assert payload["tables"] == ["weekly_events"]


def test_production_without_read_only_config_fails_closed(monkeypatch) -> None:
    from sql_safety import require_dynamic_sql_runtime

    for key in (
        "DYNAMIC_SQL_RO_HOST",
        "DYNAMIC_SQL_RO_USER",
        "DYNAMIC_SQL_RO_DB",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setenv("DYNAMIC_SQL_ALLOW_APP_CONNECTION", "false")
    with pytest.raises(SQLSafetyError, match="readonly_connection_required"):
        require_dynamic_sql_runtime()


def test_rejected_dynamic_sql_never_reaches_connector(monkeypatch) -> None:
    import app4

    called = {"connection": 0}

    def fail_if_called():
        called["connection"] += 1
        raise AssertionError("connector must not be reached")

    monkeypatch.setattr(app4, "_get_dynamic_db_connection", fail_if_called)
    with pytest.raises(SQLSafetyError):
        app4._execute_sql("DELETE FROM weekly_events")
    assert called["connection"] == 0


class TrustedCursor:
    description = (("count",),)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql):
        return None

    def fetchall(self):
        return [(3,)]


class TrustedConnection:
    def cursor(self):
        return TrustedCursor()


def test_trusted_prepared_execution_remains_available(monkeypatch) -> None:
    import app4

    monkeypatch.setattr(app4, "_get_db_connection", lambda: TrustedConnection())
    result = app4._execute_sql(
        "SELECT COUNT(*) AS count FROM weekly_events",
        trusted=True,
    )
    assert result == {"columns": ["count"], "rows": [{"count": 3}]}


def test_app4_imports_through_sql_chat_package() -> None:
    import importlib

    module = importlib.import_module("sql_chat.app4")
    assert callable(module.validate_dynamic_sql)
