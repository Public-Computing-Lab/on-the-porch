# Staging deployment and rollback runbook

## 1. Pre-deployment checks

- [ ] Staging FQDN + TLS confirmed
- [ ] Staging MySQL identity confirmed ≠ production and ≠ local demo
- [ ] Secrets present on host only (mode 600)
- [ ] `git rev-parse HEAD` matches intended commit
- [ ] Backup location writable
- [ ] Operator approval for staging-only mutation

## 2. Repository revision verification

```bash
cd "$STAGING_APP_DIR"
git fetch origin
git checkout <intended-sha>
git rev-parse HEAD
git status -sb   # must be clean of secret files
```

## 3. Backup

```bash
# Example shape — substitute staging identity; never log passwords.
mysqldump --single-transaction --routines --triggers \
  -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p \
  "$MYSQL_DB" | gzip > "$BACKUP_DIR/staging-${MYSQL_DB}-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
sha256sum "$BACKUP_DIR"/staging-*.sql.gz | tee "$BACKUP_DIR/SHA256SUMS"
chmod 600 "$BACKUP_DIR"/*
```

Record: timestamp, DB name (not password), file size, checksum, path, retention (suggest 14 days staging).

## 4. Dependency installation

```bash
source venv/bin/activate   # or .venv
pip install -r requirements.txt
```

## 5. Migration

```bash
PYTHONPATH=api:. python - <<'PY'
from api.db_migrations import run_migrations
from api.api_v2 import get_db_connection  # or staging connection factory
run_migrations(get_db_connection)
PY
```

Verify `schema_migrations` contains through `011_conversation_turns.sql` (and peers).

## 6. Environment validation

```bash
PRODUCTION_MODE=true PYTHONPATH=api python -c \
  'from launch_runtime import assert_launch_runtime; assert_launch_runtime(production_mode=True)'
```

## 7. Process restart

Stop previous staging Gunicorn **only** (never production). Start:

```bash
PRODUCTION_MODE=true ./start_api.sh
```

## 8. Health verification

```bash
curl -fsS "https://$STAGING_HOST/health"
# expect: {"status":"ok",...} minimal fields only
```

## 9. Smoke tests

```bash
python scripts/staging/smoke_https.py --base-url "https://$STAGING_HOST"
python scripts/staging/validate_startup_failures.py  # local synthetic; optional on host
```

## 10. Log verification

Inspect Gunicorn error log for `request_completion` JSON lines; confirm no resident text.

## 11. Rollback criteria

Roll back if: startup fail-closed broken, health leaks internals, SSE unusable,
duplicate turns under retry, MySQL connection budget exceeded, uncontrolled 5xx,
secrets in logs, or staging points at production data.

## 12. Rollback steps (application)

```bash
git checkout <previous-known-good-sha>
# re-validate env
PRODUCTION_MODE=true ./start_api.sh
curl -fsS "https://$STAGING_HOST/health"
```

## 13. Database rollback considerations

Additive migrations (`civic_source_status`, `conversation_turns`, …) are generally
**forward-compatible** for application rollback, but **not automatically reversible**.
Do not DROP staging tables without an explicit restore from backup.
Document whether the previous app revision can run against the newer schema (usually yes
for additive columns/tables).

## 14. Post-rollback verification

Health, one guest conversation JSON, one SSE deterministic path, admin readiness 401 for guest.

## Rollback rehearsal (required before production approval)

When staging host exists:

1. Deploy current staging revision → smoke.
2. Roll back to prior known-good → smoke.
3. Restore current revision → smoke.

**This rehearsal is BLOCKED until staging SSH/process control is available.**
