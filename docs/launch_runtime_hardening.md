# Launch runtime hardening (staging preparation)

This document covers production startup guardrails, privacy-safe observability,
and MySQL pool sizing for On the Porch. It does **not** claim staging or
production has been validated yet.

## Architecture constraints

- MySQL is the only persistent and cross-worker shared application store.
- Redis is unsupported and unavailable. Do not set Redis backends.
- Process-local counters/caches are **non-authoritative diagnostics only**.

## Required production environment categories

When `PRODUCTION_MODE=true`, startup fails closed (before bind) unless:

| Category | Requirement |
|---|---|
| Flask/session secret | Non-empty, not a repository default/example, ≥24 chars, adequate entropy |
| Token pepper | Distinct non-default `TOKEN_PEPPER` |
| Debug/testing | `FLASK_DEBUG` and `TESTING` disabled |
| Cookies | `FLASK_SESSION_COOKIE_SECURE=true` |
| Cache | `CACHE_BACKEND=mysql`, `SEMANTIC_CACHE_BACKEND=mysql` |
| SQL / vectors | `DYNAMIC_SQL_ENABLED=false`, `CHAT_VECTOR_WRITES_ENABLED=false` |
| Fault injection | `OTP_TURN_IDEMPOTENCY_FAULTS` must not be `1` |
| MySQL | Host, database, and user present |
| Rate limits | `RATE_LIMIT_ENABLED` set explicitly |
| CORS | `ALLOWED_ORIGINS` set explicitly (no wildcards) |
| Legacy chat | `LEGACY_CHAT_ENABLED` defaults false (warns if enabled) |

Authoritative validator: `api/launch_runtime.py` (`validate_launch_runtime` /
`assert_launch_runtime`). Called from:

- Flask import path (`api/api_v2.py` when `PRODUCTION_MODE=true`)
- `start_api.sh` before gunicorn
- Gunicorn `when_ready` hook

Do not duplicate validation logic in ad-hoc scripts.

### Prohibited default secrets

Examples that fail production validation (never use in production):

- `agent-api-secret-2025`
- `change-me-in-production`
- `changeme` / `secret` / `password` / short or low-entropy values

Errors name the **setting category** only — secret values are never printed.

## Modern conversation contract

- Public web client must use `/conversations/<id>/messages` (JSON) or
  `/conversations/<id>/messages/stream` (SSE).
- In production, `client_turn_id` is required (`REQUIRE_CLIENT_TURN_ID` defaults
  true when `PRODUCTION_MODE=true`).
- Missing `client_turn_id` returns a controlled `missing_client_turn_id` error.
- No silent server-side turn ID generation for modern production submissions.
- Development may omit `client_turn_id` unless `REQUIRE_CLIENT_TURN_ID=true`.

## Legacy `/chat`

- Default **off** in production (`LEGACY_CHAT_ENABLED=false`).
- Opt-in only via explicit `LEGACY_CHAT_ENABLED=true`.
- Unsupported for the public web client even when enabled.
- Emergency detection remains message-local and privacy-safe when compatibility
  mode is deliberately enabled.

## Fault injection

- Guarded by `OTP_TURN_IDEMPOTENCY_FAULTS=1` and allowlisted points only.
- Disabled by default; **impossible** to activate under `PRODUCTION_MODE=true`.
- Production startup fails if the flag is enabled.
- `X-OTP-Test-Fault` headers are ignored when faults are disabled.
- No arbitrary code execution path; fault detail does not leak into resident
  responses.

## Public vs internal health

- `GET /health` — public liveness only, e.g. `{"status":"ok","version":"..."}`.
- Must not expose paths, models, cache backends, DB hosts, pool sizes, ingestion
  internals, Redis references, or exception details.
- `GET /admin/health/ready` — detailed readiness behind admin auth.
- Health checks must not run expensive civic/vector table scans.

## Request ID behavior

- One `request_id` per HTTP request (JSON and SSE share it for that transport).
- Accepts a bounded incoming `X-Request-ID` (`[A-Za-z0-9_-]{8,64}`); otherwise
  generates a server-owned UUID.
- Returned on `X-Request-ID` response header.
- Not used for authentication or idempotency.

## Structured completion log fields

One privacy-safe `request_completion` event per modern conversation request:

- `event_name`, `request_id`, `endpoint_family`, `route`, `http_status`,
  `completion_status`, `latency_ms`, `worker_pid`, `authenticated_or_guest`,
  `idempotency_outcome`, `mysql_pool_wait_bucket`, `llm_calls`,
  `embedding_calls`, optional `source_freshness_status`, optional `error_code`,
  `counters_authoritative=false`

**Never log:** resident query/answer/emergency text, tokens, cookies, CSRF,
fingerprints, `client_turn_id`, lease tokens, selected-event payloads, source
excerpts, full conversation state, raw SQL, or stack traces in resident
responses.

### Idempotency outcomes

`NEW_TURN`, `COMPLETED_REPLAY`, `TURN_IN_PROGRESS`, `IDEMPOTENCY_KEY_REUSED`,
`EXPIRED_LEASE_RECLAIMED`, `FAILED_ATTEMPT_RETRIED`, `DEADLOCK_MAPPED_TO_RETRY`,
`POOL_PRESSURE_MAPPED_TO_RETRY`, `PERSISTENCE_COMPLETED`, `PERSISTENCE_FAILED`.

Retries are distinguishable from new turns. Correctness must not depend on
in-process counters.

## MySQL pool SLO and connection budgeting

Per-process settings (defaults are starting points, not DreamHost guarantees):

| Setting | Role |
|---|---|
| `MYSQL_POOL_SIZE` | Max connections **per worker process** |
| `MYSQL_CONNECT_TIMEOUT` | TCP connect timeout |
| `MYSQL_POOL_CHECKOUT_TIMEOUT_SECONDS` | Documented checkout/ping budget |
| `MYSQL_POOL_RECYCLE` | Soft lifetime hint |
| `TURN_DUPLICATE_WAIT_SECONDS` | Bounded duplicate poll window |

mysql-connector uses **non-blocking** pool acquisition: exhausted pools raise
`PoolError` immediately (no indefinite wait). Pool pressure maps to controlled
`turn_in_progress` where appropriate. Interaction logging is best-effort and
must not fail a committed resident turn.

### Connection budget worksheet

```
total potential app connections ≈
    GUNICORN_WORKERS × MYSQL_POOL_SIZE
  + ingestion/admin processes
  + migration / ops headroom

available ≈ MYSQL max_connections − safety_reserve

Require: total potential ≤ available
```

Example (illustrative only — verify DreamHost limits during staging):

- 2 Gunicorn workers × 4 threads
- `MYSQL_POOL_SIZE=6` → web capacity `2 × 6 = 12`
- ingestion/admin headroom `10`
- safety reserve `10`
- If MySQL `max_connections=50` → available `40`; total potential `22` fits

Do **not** treat a local pool size of 8–16 as a universal production number.
Do **not** claim configured pool size equals total database connections.

Helper: `launch_runtime.pool_sizing_worksheet(...)`.

## Turn-record cleanup

```bash
python scripts/cleanup_conversation_turns.py              # dry-run
python scripts/cleanup_conversation_turns.py --apply      # delete eligible rows
```

Defaults: COMPLETED 30d, FAILED 7d, abandoned IN_PROGRESS 7d after lease expiry.
Batched deletes; active leases retained; counts only; no credentials printed.
No automatic production cron is installed by this change — schedule manually
after staging validation.

**Retention tradeoff:** 30 days for COMPLETED covers slow transport retries and
support investigation; shorten only after confirming client retry horizons.

**Operational consequence after deletion:** If a client retries with the same
`client_turn_id` after the turn row has been deleted past retention, the server
treats it as a **new** reservation (no COMPLETED replay). That can create a
duplicate resident-visible turn. Keep retention ≥ the documented client retry
horizon; do not clean up inside that window.

## Retryable `turn_in_progress`

Identical in-flight turns return HTTP 409 with `code=turn_in_progress` and
`Retry-After`. Bounded client retries must not be treated as unlimited
generation bypasses. Completed idempotent replay is not a new expensive
generation for observability. Emergency routing must not be blocked by
unnecessary internal retries. Health remains lightly rate-policy appropriate.

## Rollback considerations

- Feature flags: set `PRODUCTION_MODE=false` only for local recovery (never as a
  production bypass).
- Legacy chat can be temporarily enabled with `LEGACY_CHAT_ENABLED=true` if an
  integration requires it — still unsupported for the public web client.
- Observability is log-only; disabling `REQUEST_METRICS_ENABLED` reduces noise
  without changing correctness.
- Turn cleanup is offline-only; skipping it retains rows (safe).

## No Redis / no silent memory authority

Unsupported Redis env vars must not activate Redis. Production cannot silently
fall back to process-local authoritative cache/state.

`CACHE_BACKEND=mysql` and `SEMANTIC_CACHE_BACKEND=mysql` **fail closed** if MySQL
is unavailable (startup / builder raises; no silent switch to memory).

Process-local memory is an explicit, single-process, non-authoritative
development opt-in only:

```
ALLOW_SINGLE_PROCESS_MEMORY_BACKEND=true
CACHE_BACKEND=memory
GUNICORN_WORKERS=1
PRODUCTION_MODE=false
```

Memory mode is rejected when `PRODUCTION_MODE=true` or `GUNICORN_WORKERS>1`.
Shared rate limiting with MySQL also fails closed (no allow-on-error).
