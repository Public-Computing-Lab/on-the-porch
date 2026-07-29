# Dynamic SQL Security

## Trust Boundary

All model-generated MySQL reaches
`on_the_porch/sql_chat/sql_safety.py::validate_dynamic_sql` before a database
cursor sees it. `app4._execute_sql(..., trusted=False)` is the authoritative
generated-SQL executor. The existing prepared-query implementation remains a
separate trusted path.

The validator uses `sqlglot==27.20.0` with the MySQL dialect. Regex and string
checks are used only to reject ambiguous comments; statement type, functions,
tables, CTEs, columns, and limits are decided from the parsed AST.

## Allowed Queries

One read-only query is allowed, including SELECT, WITH/CTEs, joins, subqueries,
grouping, aggregation, ordering, date expressions, and scalar functions that are
not on the dangerous-function list.

The default physical-table allowlist is:

- `weekly_events`
- `events_materialized`
- `service_requests_311`
- `mv_311_weekly`
- `crime_incident_reports`
- `mv_crime_monthly`
- `shootings`

`DYNAMIC_SQL_ALLOWED_TABLES` may replace this list with an explicit comma-
separated allowlist. Invalid entries are ignored and an empty resulting override
fails closed. It never means "all tables."

## Rejected Queries

The guard rejects multiple statements, ambiguous comments, DML, DDL,
administrative commands, transaction/locking commands, prepared-statement
commands, user/session variables, filesystem functions and output clauses,
dangerous delay/lock functions, malformed SQL, cross-catalog access, unauthorized
schemas/tables, system schemas, sensitive columns, and oversized select lists.

System schemas are always blocked:

- `mysql`
- `information_schema`
- `performance_schema`
- `sys`

Authentication, session, conversation, interaction, administration, migration,
cache, ingestion, and turn-coordination tables are absent from the allowlist.
CTE aliases do not conceal physical table references.

## Limits

Defaults:

- statement execution: `DYNAMIC_SQL_TIMEOUT_MS=8000`
- returned rows: `DYNAMIC_SQL_MAX_ROWS=500`
- selected columns per SELECT: `DYNAMIC_SQL_MAX_COLUMNS=40`
- serialized payload: `DYNAMIC_SQL_MAX_PAYLOAD_BYTES=1000000`

The AST receives a limit of at most the row limit plus one detection row. The
cursor uses `fetchmany(limit + 1)`, and serialization stops before the payload
limit. Internal results include `truncated` and `payload_bytes` without changing
the public response schema.

## Production Read-Only Account

Production dynamic SQL requires a dedicated account configured with:

```text
DYNAMIC_SQL_RO_HOST
DYNAMIC_SQL_RO_PORT
DYNAMIC_SQL_RO_USER
DYNAMIC_SQL_RO_PASSWORD
DYNAMIC_SQL_RO_DB
DYNAMIC_SQL_ALLOW_APP_CONNECTION=false
```

The application does not create database users or modify grants. An operator
should grant only `SELECT` on the explicit civic tables/views and no global,
schema-administration, file, routine, temporary-table, or write privileges.

Example operator-managed grants:

```sql
CREATE USER 'otp_civic_reader'@'app-host' IDENTIFIED BY '<managed secret>';
GRANT SELECT ON civic_db.weekly_events TO 'otp_civic_reader'@'app-host';
GRANT SELECT ON civic_db.events_materialized TO 'otp_civic_reader'@'app-host';
GRANT SELECT ON civic_db.service_requests_311 TO 'otp_civic_reader'@'app-host';
GRANT SELECT ON civic_db.mv_311_weekly TO 'otp_civic_reader'@'app-host';
GRANT SELECT ON civic_db.crime_incident_reports TO 'otp_civic_reader'@'app-host';
GRANT SELECT ON civic_db.mv_crime_monthly TO 'otp_civic_reader'@'app-host';
GRANT SELECT ON civic_db.shootings TO 'otp_civic_reader'@'app-host';
```

Local demo tests may explicitly use the application connection. Production fails
closed without the dedicated configuration.

## Logging and Resident Errors

Observability records validation outcome, rejection category, physical tables,
literal-redacted fingerprint, row count, truncation, execution time, and timeout
status. It does not record raw generated SQL or literal values. Raw SQL console
logging is disabled unless `DYNAMIC_SQL_LOG_RAW_SQL=true`, which is for isolated
diagnostics only.

Residents receive a bounded civic-data-unavailable result through the existing
fallback path. Parser details, model SQL, table names, database exceptions, and
stack traces are not included.

