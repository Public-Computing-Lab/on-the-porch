# Staging deployment and rollback runbook

**Scope:** staging preparation and future staging deployment only.
**Status:** remote staging resources are not assumed available. Do not treat this
document as evidence that staging was validated.

Every destructive or remote step below lists prerequisites, expected result,
recovery, and stop condition. **Never put real secrets in commands or tickets.**

---

## 1. Resource identity verification

- **Prerequisites:** operator checklist filled (`operator_prerequisites.md`).
- **Expected:** staging FQDN ≠ production; app path ≠ production path.
- **Recovery:** stop; do not continue.
- **Stop if:** any production identity is uncertain.

## 2. Staging database verification

- **Prerequisites:** staging MySQL host/db/user confirmed staging-only.
- **Expected:** `MYSQL_DB` matches staging contract; not `sentiment_demo` / production.
- **Recovery:** refuse mutation (`OTP_STAGING_MUTATION_OK` unset).
- **Stop if:** DB identity uncertain.

## 3. Repository commit verification

```bash
cd "$STAGING_APP_DIR"
git fetch origin
git checkout <intended-sha>
git rev-parse HEAD
git status -sb
```

- **Expected:** clean tree; SHA matches intended merge/main commit.
- **Stop if:** unexpected secrets or uncommitted dumps present.

## 4. Backup before migration

```bash
# Interactive password prompt — do not embed passwords in shell history.
mysqldump --single-transaction --routines --triggers \
  -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p \
  "$MYSQL_DB" | gzip > "$BACKUP_DIR/staging-${MYSQL_DB}-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
sha256sum "$BACKUP_DIR"/staging-*.sql.gz | tee "$BACKUP_DIR/SHA256SUMS"
chmod 600 "$BACKUP_DIR"/*
```

- **Expected:** nonzero gzip; checksum recorded; metadata without credentials.
- **Recovery:** do not migrate until backup succeeds.
- **Stop if:** backup path world-readable or dump fails.

## 5. Dependency installation

```bash
source .venv/bin/activate   # or venv
pip install -r requirements.txt
```

- **Expected:** install completes.
- **Stop if:** wrong Python / missing gunicorn.

## 6. Migration

```bash
PYTHONPATH=api:. python - <<'PY'
from api.db_migrations import run_migrations
# Use the staging app's connection factory only.
from api.api_v2 import get_db_connection
run_migrations(get_db_connection)
PY
```

- **Expected:** `schema_migrations` includes through `011_conversation_turns.sql`.
- **Recovery:** restore from backup; do not invent DROP scripts casually.
- **Stop if:** migration errors or wrong DB.

## 7. Environment validation

```bash
PRODUCTION_MODE=true PYTHONPATH=api python -c \
  'from launch_runtime import assert_launch_runtime; assert_launch_runtime(production_mode=True)'
```

- **Expected:** exit 0; category errors only on failure (no secret values).
- **Stop if:** validation fails.

## 8. Gunicorn startup

```bash
PRODUCTION_MODE=true ./start_api.sh
```

- **Prerequisites:** steps 1–7; TLS terminator ready.
- **Expected:** bind behind proxy; launch-runtime ok.
- **Recovery:** stop staging process only; fix env; restart.
- **Stop if:** Passenger/legacy path is the only option without written exception.

## 9. HTTPS health check

```bash
curl -fsS "https://$STAGING_HOST/health"
```

- **Expected:** minimal `status` (+ optional `version`); no internals.
- **Stop if:** HTTP-only or leaked fields.

## 10. Admin readiness check

```bash
curl -sS -o /dev/null -w "%{http_code}\n" "https://$STAGING_HOST/admin/health/ready"
# anonymous → 401/403
# staging admin → success per admin contract
```

- **Stop if:** publicly accessible without auth.

## 11. SSE validation

Exercise deterministic event + emergency + disconnect/replay with same `client_turn_id`
through the **real** staging proxy. Confirm no buffering until completion; one message pair.

- **Stop if:** SSE unusable or duplicates.

## 12. Session and CSRF validation

Guest + authenticated flows over HTTPS; secure cookies; CSRF functional.

- **Stop if:** cookies set without Secure or CSRF bypassed.

## 13. Smoke dialogues

Use staging fixtures only (synthetic markers). Cover events, active event, emergency,
freshness, idempotent replay. No production resident text.

## 14. Structured log review

Sample `request_completion` lines: one per request; no resident/answer/emergency text;
no tokens/CSRF/`client_turn_id`.

## 15. Connection-budget measurement

Fill `connection_budget.md` from staging MySQL permissions. Prefer conservative workers/pool.

- **Stop if:** limit UNKNOWN without a conservative soft cap.

## 16. Cleanup dry run

```bash
python scripts/cleanup_conversation_turns.py   # dry-run default
# Fixture cleanup:
OTP_STAGING_MUTATION_OK=1 OTP_STAGING_MYSQL_DB="$MYSQL_DB" \
  python scripts/staging/seed_fixtures.py --cleanup
```

- **Stop if:** would touch non-fixture / non-staging data.

## 17. Backup restore test

Restore into disposable staging schema when permitted; verify schema + startup against copy.

- **Stop / mark BLOCKED** if restore permissions unavailable (blocks production approval).

## 18. Rollback rehearsal

Deploy current → smoke → prior SHA → smoke → restore current → smoke. Staging only.

## 19. Promotion decision

Staging validation is successful only when blockers in `docs/staging/README.md` are cleared.
**This preparation PR does not authorize production.**

## 20. Process shutdown where required

Stop staging Gunicorn when the slice is complete and the service is not needed.
Never stop unrelated operator processes (e.g. local `:8888`).
