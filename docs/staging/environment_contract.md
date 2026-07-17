# Staging environment contract (categories only)

Copy categories into a **host-local staging** secret file (e.g. `/path/to/staging/.env`)
with mode `600`. **Never commit real values.** This contract describes a **staging**
environment that uses production-mode hardening; it does **not** claim staging has been
validated or that DreamHost limits are known.

## Required (fail closed when `PRODUCTION_MODE=true`)

| Category | Required setting | Notes |
|---|---|---|
| Mode | `PRODUCTION_MODE=true` | Staging uses production hardening |
| Cache | `CACHE_BACKEND=mysql` | No Redis, no silent memory |
| Semantic cache | `SEMANTIC_CACHE_BACKEND=mysql` | |
| Memory opt-in | `ALLOW_SINGLE_PROCESS_MEMORY_BACKEND=false` | Must stay false on staging |
| Dynamic SQL | `DYNAMIC_SQL_ENABLED=false` | |
| Vector writes | `CHAT_VECTOR_WRITES_ENABLED=false` | |
| Fault injection | `OTP_TURN_IDEMPOTENCY_FAULTS=0` | Never `1` |
| Legacy chat | `LEGACY_CHAT_ENABLED=false` | Default-off under production policy |
| Client turn id | `REQUIRE_CLIENT_TURN_ID=true` (or rely on production default) | Modern endpoints |
| Debug | `FLASK_DEBUG=false` | |
| Testing | `TESTING=false` | |
| Secure cookies | `FLASK_SESSION_COOKIE_SECURE=true` | HTTPS only |
| Rate limits | `RATE_LIMIT_ENABLED` set explicitly (`true` recommended) | Bucket vars required when enabled |
| CORS | `ALLOWED_ORIGINS` = staging HTTPS origin(s) only | No `*` |
| Flask secret | `FLASK_SECRET_KEY` | Strong, staging-only, not example defaults |
| Token pepper | `TOKEN_PEPPER` | Distinct from Flask secret; staging-only |
| MySQL | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB` | Staging schema only |
| Metrics | `REQUEST_METRICS_ENABLED=true` | Privacy-safe completion logs |

## Forbidden

- Example/default secrets (`change-me-in-production`, `agent-api-secret-2025`, …)
- Production secret reuse
- Production database credentials or hostname
- Uploading local developer `.env` unchanged
- Credentials in Git, logs, HTML, or JS
- `CACHE_BACKEND=redis` / `SEMANTIC_CACHE_BACKEND=redis`
- `CACHE_BACKEND=memory` on staging

## Gunicorn (initial conservative placeholders — replace after measuring host limits)

Document measured values in `connection_budget.md` before raising.

| Category | Suggested starting point until measured | Must verify |
|---|---|---|
| Workers | `GUNICORN_WORKERS=2` | Against MySQL `max_connections` |
| Threads | `GUNICORN_THREADS=2` | |
| Pool | `MYSQL_POOL_SIZE` ≤ conservative per-process budget | |
| Recycle | `GUNICORN_MAX_REQUESTS` high enough for soak, not zero forever | |
| Bind | Staging-only bind behind TLS terminator | |

## File permissions (host)

```bash
chmod 600 .env
chmod 700 logs backup   # if used
# do not world-read secret files
```

## Startup gate

```bash
PRODUCTION_MODE=true ./start_api.sh
# or: PYTHONPATH=api python -c 'from launch_runtime import assert_launch_runtime; assert_launch_runtime(production_mode=True)'
```

Invalid configurations must fail **before** accepting traffic with category errors only (no secret values).
