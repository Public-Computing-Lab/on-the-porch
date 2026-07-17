# Staging hosting and deployment audit

## SUPPORTED application startup (preferred for staging and production-mode hosts)

```text
PRODUCTION_MODE=true ./start_api.sh
  → Gunicorn (api/gunicorn.conf.py)
  → launch-runtime validation before bind (+ when_ready)
  → api_v2:app
```

Why this is the supported path:

1. Rejects Redis backends at shell and validator layers.
2. Fail-closed `assert_launch_runtime(production_mode=True)` before accepting traffic.
3. Matches PR #18 launch-runtime hardening.
4. Compatible with reverse-proxy SSE headers emitted by the app.

**Operators must not treat any other path as preferred for staging or production.**

## LEGACY / UNVALIDATED — DreamHost Passenger materials

The following remain in-tree for historical reference. They are **not** the preferred
staging or production startup path. Compatibility with current Gunicorn + launch-runtime
validation is **unverified** and must be validated before any use.

| Artifact | Label | Guidance |
|---|---|---|
| `scripts/dreamhost/deploy.sh` | **LEGACY** | Generates Passenger WSGI; **refuses to run** unless `OTP_ALLOW_LEGACY_PASSENGER_DEPLOY=1` |
| `scripts/dreamhost/DEPLOY_QUICKSTART.md` | **LEGACY** | Passenger panel / stale DB notes |
| `scripts/dreamhost/README.md` | **LEGACY** (banner) | Points here for supported path |
| `scripts/dreamhost/setup.sh` / `database_setup.sh` | **LEGACY / REVIEW** | Venv/DB helpers only; prefer `api/db_migrations.py` |
| Host `passenger_wsgi.py` if generated | **LEGACY** | Bypasses `start_api.sh` pre-bind gate unless duplicated |

Do **not** delete these scripts merely to hide deployment history.

## Host capabilities required by the supported path (do not assume)

Until operators confirm, treat each as **UNKNOWN**:

- Persistent Gunicorn (or equivalent) process
- Reverse-proxy SSE without full-buffering
- Process restart mechanism
- Environment variables / secret file
- Staging-only MySQL

## Reverse-proxy notes (when host exists)

- Trust forwarded scheme/client IP only from the approved hop
- Disable SSE response buffering
- Idle timeouts ≥ Gunicorn timeout with headroom

## Accidental production targeting

Refuse deploys that reuse production directory names, production `.env`, or production
`MYSQL_*` without an explicit staging identity contract (`scripts/staging/identity.py`).
