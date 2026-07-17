# Staging fixtures (synthetic only)

## Rules

- No production resident messages, sessions, interaction logs, or emergency text.
- Label all fixtures `OTP STAGING FIXTURE` / `staging-test@`.
- Event dates relative to America/New_York “today”.
- Crime/311 rows must use synthetic provenance and `civic_source_status` metadata
  that never claims official live civic truth.
- Document cleanup; prefer delete-by-label.

## Minimum set (when staging DB exists)

1. Guest auth path (API creates guests — no seed required).
2. Optional password user `staging-admin@example.test` (staging-only) for readiness.
3. One conversation thread with synthetic messages.
4. One future weekly event (+ materialized mirror if MVs enabled).
5. Synthetic 311 / crime aggregates **or** rely on empty tables with freshness
   status rows stating `test` provenance.
6. `civic_source_status` rows for staging sources with explicit test labels.

## Cleanup

```bash
OTP_STAGING_MUTATION_OK=1 OTP_STAGING_MYSQL_DB="$MYSQL_DB" \
  python scripts/staging/seed_fixtures.py --cleanup
```

Also dry-run identity only:

```bash
OTP_STAGING_MUTATION_OK=1 OTP_STAGING_MYSQL_DB="$MYSQL_DB" \
  python scripts/staging/seed_fixtures.py --dry-run
```

Ordinary cleanup deletes only rows whose `event_name` starts with `OTP STAGING FIXTURE `.

## Local disposable prep note

Creating a separate MySQL schema may be unavailable under least-privilege demo users.
Local fixture exercises may use the dual gate
`OTP_STAGING_ALLOW_FORBIDDEN_DB_FOR_LOCAL_PREP=1` **together with**
`OTP_STAGING_MYSQL_DB` + `OTP_STAGING_MUTATION_OK=1` so only prefix-labeled rows are
touched. That gate is for local preparation tests only and does **not** mean remote
staging validation occurred.
