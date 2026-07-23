# Phase 5 local / staging operations

This package completes product operations for On the Porch without claiming a
public production deploy. Remote staging hosts remain operator-provisioned.

## Authoritative contracts

- MySQL owns source registry, document/chunk metadata, ingestion runs, and audit.
- Chroma/Qdrant is a rebuildable semantic index only.
- `CHAT_VECTOR_WRITES_ENABLED=false` and `DYNAMIC_SQL_ENABLED=false`.
- Authoritative resident answers use `MODEL_LED_ORCHESTRATOR_MODE=authoritative`.

## Bootstrap (local demo / staging prep)

```bash
# Apply migrations + seed registry + catalog existing Chroma + ingest local corpus
PYTHONPATH=.:api:on_the_porch \
  /path/to/.venv/bin/python scripts/otp_ops.py bootstrap

# Optional: also write vectors (requires ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED for API path)
PYTHONPATH=.:api:on_the_porch \
  /path/to/.venv/bin/python scripts/otp_ops.py bootstrap --write-vectors
```

CLI helpers:

```bash
python scripts/otp_ops.py sources
python scripts/otp_ops.py runs
python scripts/otp_ops.py health
python scripts/otp_ops.py ingest-local boston.policy_corpus
python scripts/otp_ops.py set-enabled dorchester.local_news --disable
```

Admin UI: `public/admin.html` → **Sources** tab (admin role required).

## Staging checklist (reproducible)

1. Copy `example_env.txt` → staging `.env` with staging-only secrets.
2. Create staging MySQL schema and least-privilege user (never production data).
3. Run `./start_api.sh` or gunicorn via `api/gunicorn.conf.py` with `GUNICORN_WORKERS=2`.
4. Serve `public/` over HTTPS reverse proxy; set `ALLOWED_ORIGINS` and secure cookies.
5. Run `scripts/otp_ops.py bootstrap` against the staging DB.
6. Verify `GET /health` and admin `GET /admin/health/ready`.
7. Smoke guest conversation flows (events, 311, documents, emergency).

See also: `docs/staging/runbook.md`, `docs/staging/environment_contract.md`.

## Backup / restore / rollback

### MySQL backup

```bash
mysqldump -h "$MYSQL_HOST" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" \
  --single-transaction --routines "$MYSQL_DB" > backup-$(date +%Y%m%d).sql
```

### Restore into a separate test database

```bash
mysql -h "$MYSQL_HOST" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DB_RESTORE" \
  < backup-YYYYMMDD.sql
```

### Vector rebuild

Vector data is rebuildable from MySQL catalog + local/source files:

```bash
python scripts/otp_ops.py bootstrap --write-vectors
# or historical builders under on_the_porch/rag\ stuff/
```

### Application rollback

1. Stop API workers.
2. `git checkout <previous-merge-commit>`.
3. Restart gunicorn with the prior `.env`.
4. Keep MySQL backups; do not drop `source_registry` unless intentionally rolling schema back.

Phase 4 merge commit reference: `152dcc75`. Phase 5 merge commit: `4c9c6092`.

## Privacy notes

- Do not log resident message bodies by default.
- Operator audit stores action metadata only.
- Connector HTTP fetches are allowlisted to official Boston hosts.
- Public clients cannot trigger unrestricted server-side URL fetches.

## Provenance and resident eligibility (public beta)

Resident retrieval excludes `DEMO_FIXTURE`, `TEST_ONLY`, and `INTERNAL_ANALYSIS`.
Local policy/zoning staging files are `OFFICIAL_SECONDARY` curated references — never
presented as primary City/BPDA/ISD legal text. Fixture digests live under
`data/phase5_corpus/fixtures/` and are not ingested for residents.

`source_registry.enabled` and provenance class gate document retrieval. Disabling a
source stops new evidence selection without requiring manual vector edits.

## Model SDK policy

Resident-facing Gemini calls use the maintained `google-genai` SDK (planner provider and
plain-text synthesis helper). Legacy `google.generativeai` remains only in offline/RAG
tooling pending migration.

## Guest rate limits and audit mode

- Guest chat: `RATE_LIMIT_GUEST_CHAT_PER_SESSION` (default 180/hour)
- Authenticated chat: `RATE_LIMIT_CHAT_PER_SESSION` (default 120/hour)
- Audit mode: `AUDIT_RATE_LIMIT_MODE=true` (non-production only) plus
  `X-On-The-Porch-Audit-Token` matching `AUDIT_RATE_LIMIT_SECRET`

## Backup / restore operator dependency

Application DB users must remain least-privilege. Full isolated restore requires a separate
DBA role. Run `scripts/staging/backup_restore_drill.sh` after exporting `OTP_DBA_*`.
