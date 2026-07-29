"""AST-based safety boundary for model-generated MySQL queries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope


DEFAULT_ALLOWED_CIVIC_TABLES = frozenset(
    {
        "crime_incident_reports",
        "events_materialized",
        "mv_311_weekly",
        "mv_crime_monthly",
        "service_requests_311",
        "shootings",
        "weekly_events",
    }
)
BLOCKED_SCHEMAS = frozenset(
    {"information_schema", "mysql", "performance_schema", "sys"}
)
BLOCKED_COLUMNS = frozenset(
    {
        "api_key",
        "csrf_token_hash",
        "password",
        "password_hash",
        "session_token_hash",
        "token",
        "token_hash",
    }
)
BLOCKED_FUNCTIONS = frozenset(
    {
        "benchmark",
        "get_lock",
        "is_free_lock",
        "is_used_lock",
        "load_file",
        "master_pos_wait",
        "name_const",
        "release_lock",
        "sleep",
        "sys_exec",
        "sys_eval",
    }
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def allowed_civic_tables() -> frozenset[str]:
    raw = os.getenv("DYNAMIC_SQL_ALLOWED_TABLES", "")
    if not raw.strip():
        return DEFAULT_ALLOWED_CIVIC_TABLES
    values = {
        item.strip().lower()
        for item in raw.split(",")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", item.strip())
    }
    return frozenset(values)


def dynamic_sql_row_limit() -> int:
    return _env_int("DYNAMIC_SQL_MAX_ROWS", 500, 1, 5_000)


def dynamic_sql_payload_limit() -> int:
    return _env_int("DYNAMIC_SQL_MAX_PAYLOAD_BYTES", 1_000_000, 1_024, 5_000_000)


def dynamic_sql_column_limit() -> int:
    return _env_int("DYNAMIC_SQL_MAX_COLUMNS", 40, 1, 200)


def dynamic_sql_timeout_ms() -> int:
    return _env_int("DYNAMIC_SQL_TIMEOUT_MS", 8_000, 100, 60_000)


def _production_mode() -> bool:
    return _env_bool("PRODUCTION_MODE", False)


def dynamic_sql_read_only_configured() -> bool:
    return bool(
        os.getenv("DYNAMIC_SQL_RO_HOST")
        and os.getenv("DYNAMIC_SQL_RO_USER")
        and os.getenv("DYNAMIC_SQL_RO_DB")
    )


def dynamic_sql_app_connection_allowed() -> bool:
    return _env_bool("DYNAMIC_SQL_ALLOW_APP_CONNECTION", not _production_mode())


def require_dynamic_sql_runtime() -> None:
    """Fail closed when production lacks a dedicated civic read-only identity."""
    if dynamic_sql_read_only_configured():
        return
    if dynamic_sql_app_connection_allowed() and not _production_mode():
        return
    raise SQLSafetyError(
        "readonly_connection_required",
        "Dynamic civic data lookup is temporarily unavailable.",
    )


class SQLSafetyError(ValueError):
    """A normalized rejection that is safe for the conversational fallback."""

    def __init__(self, category: str, public_message: str = "The civic data query was not available."):
        super().__init__(category)
        self.category = category
        self.public_message = public_message


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    fingerprint: str
    tables: tuple[str, ...]
    row_limit: int
    payload_limit_bytes: int
    column_limit: int
    timeout_ms: int


@dataclass(frozen=True)
class BoundedRows:
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    truncated: bool
    payload_bytes: int


def _contains_comment_outside_literals(sql: str) -> bool:
    """Reject MySQL comments without mistaking markers inside quoted literals."""
    state: Optional[str] = None
    escaped = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if state is not None:
            if escaped:
                escaped = False
            elif char == "\\" and state in {"'", '"'}:
                escaped = True
            elif char == state:
                if index + 1 < len(sql) and sql[index + 1] == state:
                    index += 1
                else:
                    state = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            state = char
            index += 1
            continue
        if char == "#":
            return True
        if char == "/" and index + 1 < len(sql) and sql[index + 1] == "*":
            return True
        if char == "-" and index + 1 < len(sql) and sql[index + 1] == "-":
            following = sql[index + 2 : index + 3]
            if not following or following.isspace():
                return True
        index += 1
    return False


def _dangerous_expression_types() -> tuple[type[exp.Expression], ...]:
    names = (
        "Alter", "Analyze", "Attach", "Call", "Command", "Commit", "Copy",
        "Create", "Delete", "Detach", "Drop", "Execute", "Grant", "Insert",
        "Kill", "Lock", "Merge", "Pragma", "Rename", "Revoke", "Rollback",
        "Set", "Show", "Transaction", "TruncateTable", "Unload", "Update", "Use",
    )
    return tuple(
        value
        for name in names
        if isinstance((value := getattr(exp, name, None)), type)
    )


def _physical_tables(
    statement: exp.Expression,
    *,
    allowed_tables: frozenset[str],
    current_database: Optional[str],
) -> tuple[str, ...]:
    found: set[str] = set()
    current_db = (
        current_database
        or os.getenv("DYNAMIC_SQL_RO_DB")
        or os.getenv("MYSQL_DB")
        or ""
    ).lower()
    physical_nodes: list[exp.Table] = []
    for scope in traverse_scope(statement):
        physical_nodes.extend(
            source
            for source in scope.sources.values()
            if isinstance(source, exp.Table)
        )
    for table in physical_nodes:
        name = (table.name or "").lower()
        schema = (table.db or "").lower()
        catalog = (table.catalog or "").lower()
        if not name:
            raise SQLSafetyError("invalid_table_reference")
        if schema in BLOCKED_SCHEMAS or catalog in BLOCKED_SCHEMAS:
            raise SQLSafetyError("system_schema")
        if catalog:
            raise SQLSafetyError("cross_catalog_reference")
        if schema and (not current_db or schema != current_db):
            raise SQLSafetyError("cross_schema_reference")
        if name not in allowed_tables:
            raise SQLSafetyError("table_not_allowed")
        found.add(name)
    return tuple(sorted(found))


def _validate_functions_and_variables(statement: exp.Expression) -> None:
    variable_types = tuple(
        value
        for name in ("Parameter", "PropertyEQ", "SessionParameter")
        if isinstance((value := getattr(exp, name, None)), type)
    )
    if variable_types and any(isinstance(node, variable_types) for node in statement.walk()):
        raise SQLSafetyError("variables_not_allowed")
    for function in statement.find_all(exp.Func):
        name = (function.sql_name() or function.name or "").lower()
        if name in BLOCKED_FUNCTIONS:
            raise SQLSafetyError("function_not_allowed")
    for anonymous in statement.find_all(exp.Anonymous):
        if (anonymous.name or "").lower() in BLOCKED_FUNCTIONS:
            raise SQLSafetyError("function_not_allowed")


def _validate_columns(statement: exp.Expression, *, maximum: int) -> None:
    for select in statement.find_all(exp.Select):
        if len(select.expressions) > maximum:
            raise SQLSafetyError("too_many_columns")
    for column in statement.find_all(exp.Column):
        if (column.name or "").lower() in BLOCKED_COLUMNS:
            raise SQLSafetyError("sensitive_column")


def _apply_limit(statement: exp.Expression, limit: int) -> exp.Expression:
    existing = statement.args.get("limit")
    existing_value: Optional[int] = None
    if existing is not None:
        expression = existing.args.get("expression")
        if isinstance(expression, exp.Literal) and not expression.is_string:
            try:
                existing_value = int(expression.this)
            except (TypeError, ValueError):
                existing_value = None
    effective = min(existing_value, limit) if existing_value is not None else limit
    return statement.limit(effective, copy=True)


def validate_dynamic_sql(
    sql: str,
    *,
    current_database: Optional[str] = None,
    tables: Optional[Iterable[str]] = None,
    row_limit: Optional[int] = None,
    payload_limit_bytes: Optional[int] = None,
    column_limit: Optional[int] = None,
    timeout_ms: Optional[int] = None,
) -> ValidatedSQL:
    raw = (sql or "").strip()
    if not raw:
        raise SQLSafetyError("empty_sql")
    if _contains_comment_outside_literals(raw):
        raise SQLSafetyError("comments_not_allowed")
    try:
        statements = sqlglot.parse(raw, read="mysql")
    except ParseError as exc:
        raise SQLSafetyError("parse_error") from exc
    if len(statements) != 1 or statements[0] is None:
        raise SQLSafetyError("multiple_statements")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise SQLSafetyError("statement_not_read_only")
    dangerous_types = _dangerous_expression_types()
    if dangerous_types and any(isinstance(node, dangerous_types) for node in statement.walk()):
        raise SQLSafetyError("statement_not_read_only")
    if statement.args.get("locks"):
        raise SQLSafetyError("locking_not_allowed")
    _validate_functions_and_variables(statement)
    maximum_columns = column_limit or dynamic_sql_column_limit()
    _validate_columns(statement, maximum=maximum_columns)
    allowed = frozenset(item.lower() for item in (tables or allowed_civic_tables()))
    if not allowed:
        raise SQLSafetyError("empty_table_allowlist")
    physical_tables = _physical_tables(
        statement,
        allowed_tables=allowed,
        current_database=current_database,
    )
    maximum_rows = row_limit or dynamic_sql_row_limit()
    bounded_statement = _apply_limit(statement, maximum_rows + 1)
    normalized = bounded_statement.sql(dialect="mysql", pretty=False)
    fingerprint_source = bounded_statement.transform(
        lambda node: exp.Placeholder() if isinstance(node, exp.Literal) else node
    ).sql(dialect="mysql", pretty=False)
    fingerprint = hashlib.sha256(fingerprint_source.lower().encode("utf-8")).hexdigest()[:24]
    return ValidatedSQL(
        sql=normalized,
        fingerprint=fingerprint,
        tables=physical_tables,
        row_limit=maximum_rows,
        payload_limit_bytes=payload_limit_bytes or dynamic_sql_payload_limit(),
        column_limit=maximum_columns,
        timeout_ms=timeout_ms or dynamic_sql_timeout_ms(),
    )


def fetch_bounded_rows(
    cursor: Any,
    *,
    columns: Sequence[str],
    row_limit: int,
    payload_limit_bytes: int,
) -> BoundedRows:
    raw_rows = cursor.fetchmany(row_limit + 1)
    truncated = len(raw_rows) > row_limit
    result: list[dict[str, Any]] = []
    payload_bytes = 2
    for raw_row in raw_rows[:row_limit]:
        item = {columns[index]: raw_row[index] for index in range(len(columns))}
        encoded = json.dumps(
            item, ensure_ascii=True, default=str, separators=(",", ":")
        ).encode("utf-8")
        separator_bytes = 1 if result else 0
        if payload_bytes + separator_bytes + len(encoded) > payload_limit_bytes:
            truncated = True
            break
        payload_bytes += separator_bytes + len(encoded)
        result.append(item)
    return BoundedRows(
        columns=tuple(columns),
        rows=tuple(result),
        truncated=truncated,
        payload_bytes=payload_bytes,
    )


def execute_validated_query(
    conn: Any,
    validated: ValidatedSQL,
) -> tuple[BoundedRows, float]:
    started = time.perf_counter()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(validated.timeout_ms)}")
        cursor.execute(validated.sql)
        columns = tuple(description[0] for description in (cursor.description or ()))
        if len(columns) > validated.column_limit:
            raise SQLSafetyError("too_many_columns")
        result = fetch_bounded_rows(
            cursor,
            columns=columns,
            row_limit=validated.row_limit,
            payload_limit_bytes=validated.payload_limit_bytes,
        )
        return result, (time.perf_counter() - started) * 1000.0
    finally:
        cursor.close()


def read_only_connection_kwargs() -> Optional[dict[str, Any]]:
    if not dynamic_sql_read_only_configured():
        return None
    return {
        "host": os.environ["DYNAMIC_SQL_RO_HOST"],
        "port": _env_int("DYNAMIC_SQL_RO_PORT", 3306, 1, 65_535),
        "user": os.environ["DYNAMIC_SQL_RO_USER"],
        "password": os.getenv("DYNAMIC_SQL_RO_PASSWORD", ""),
        "database": os.environ["DYNAMIC_SQL_RO_DB"],
        "connection_timeout": _env_int("MYSQL_CONNECT_TIMEOUT", 10, 1, 60),
        "autocommit": True,
    }


def safe_sql_observability_payload(
    *,
    validated: Optional[ValidatedSQL] = None,
    accepted: bool,
    rejection_category: Optional[str] = None,
    row_count: int = 0,
    truncated: bool = False,
    execution_ms: Optional[float] = None,
    timed_out: bool = False,
) -> Mapping[str, Any]:
    return {
        "accepted": bool(accepted),
        "rejection_category": (rejection_category or "")[:64] or None,
        "fingerprint": validated.fingerprint if validated else None,
        "tables": list(validated.tables) if validated else [],
        "row_count": max(0, int(row_count)),
        "truncated": bool(truncated),
        "execution_ms": round(float(execution_ms), 3) if execution_ms is not None else None,
        "timed_out": bool(timed_out),
    }
