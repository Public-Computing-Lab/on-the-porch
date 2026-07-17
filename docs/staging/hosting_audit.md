# Staging hosting and deployment audit

## Single supported staging startup path

**Supported:** `./start_api.sh` → Gunicorn (`api/gunicorn.conf.py`) → `api_v2:app`

Why valid:

1. Loads `.env` then rejects Redis backends.
2. When `PRODUCTION_MODE=true`, runs `assert_launch_runtime(production_mode=True)` **before** bind.
3. Gunicorn `when_ready` re-validates the same contract.
4. Flask import path also validates under `PRODUCTION_MODE=true`.
5. Aligns with PR #18 fail-closed launch hardening.

**Not supported as primary staging path:** DreamHost Passenger via
`scripts/dreamhost/deploy.sh` generating `passenger_wsgi.py` + `.htaccess`.

## Stale / conflicting DreamHost materials

| Artifact | Issue | Staging guidance |
|---|---|---|
| `scripts/dreamhost/deploy.sh` | Generates Passenger WSGI; does not run launch-runtime validator; uses `pymysql` ad-hoc connect; hard-codes project dir name | Do **not** use for staging until rewritten for Gunicorn |
| `scripts/dreamhost/DEPLOY_QUICKSTART.md` | Mentions Passenger panel, `python api_v2.py` for test, PostgreSQL `DATABASE_URL` | Stale relative to MySQL-only + Gunicorn contract |
| `scripts/dreamhost/README.md` | Passenger-centric | Treat as historical |
| `scripts/dreamhost/setup.sh` / `database_setup.sh` | Useful patterns for venv/DB bootstrap but not PR #18-aware | Review before reuse; prefer `api/db_migrations.py` |
| Generated `passenger_wsgi.py` (on host) | Would bypass `start_api.sh` pre-bind validation unless duplicated | Prefer Gunicorn under a process supervisor |

## Reverse-proxy assumptions

Staging must terminate TLS at the edge and forward to Gunicorn with:

- Correct `X-Forwarded-Proto` / scheme trust **only** from the approved proxy hop
- SSE-friendly buffering disabled (`X-Accel-Buffering: no` already set by the app for SSE)
- Idle timeouts ≥ Gunicorn timeout (default 75s) with headroom

Exact proxy product (nginx, Apache+Passenger, DreamHost panel) is **UNKNOWN** until operators confirm the staging host.

## Scripts that could accidentally target production

- Any deploy using `$HOME/ml-misi-community-sentiment` without a **staging-specific** directory name
- Copying production `.env` into staging
- Running migrations against production `MYSQL_*` values
- Enabling Passenger on the production domain while testing staging scripts

**Mitigation:** require distinct staging directory, hostname, and MySQL schema; refuse known production DB names in `scripts/staging/*`.

## Smallest safe strategy (when host exists)

1. Separate staging directory + git checkout of validated commit.
2. Staging-only `.env` (mode 600) per `environment_contract.md`.
3. `python -m` / `api/db_migrations.py` against staging MySQL after backup.
4. Start with `PRODUCTION_MODE=true ./start_api.sh` behind HTTPS terminator.
5. Smoke via `scripts/staging/smoke_https.py --base-url https://<staging>`.
6. Do not enable Passenger for this slice unless Gunicorn cannot run and a written exception is approved.
