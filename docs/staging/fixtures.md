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

```sql
-- Illustrative; run only on staging schema after verifying MYSQL_DB name.
DELETE FROM weekly_events WHERE event_name LIKE 'OTP STAGING FIXTURE%';
DELETE FROM events_materialized WHERE event_name LIKE 'OTP STAGING FIXTURE%';
-- users/sessions: delete by staging email domain only after confirming schema
```

Also: `scripts/staging/seed_fixtures.py --apply` / `--cleanup` (refuses forbidden DB names).

## Reset procedure

1. Confirm `MYSQL_DB` is staging.
2. Backup.
3. `--cleanup` then `--apply`.
4. Re-run smoke.
