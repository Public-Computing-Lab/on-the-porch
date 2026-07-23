# Public-beta readiness checklist

Controlled public beta only after every item is checked. Do not publicly deploy without
explicit authorization.

## Environment
- [ ] Staging or private beta host provisioned (FQDN, TLS, reverse proxy)
- [ ] `MODEL_LED_ORCHESTRATOR_MODE=authoritative`
- [ ] `GUNICORN_WORKERS=2` (or approved production value)
- [ ] `EMBED_PROVIDER` set to maintained provider or approved local model
- [ ] `ANSWER_SYNTHESIS_ENABLED` intentional
- [ ] `PRODUCTION_MODE=true` only on the authorized public host
- [ ] `AUDIT_RATE_LIMIT_MODE=false` in production
- [ ] `CHAT_VECTOR_WRITES_ENABLED=false`
- [ ] `DYNAMIC_SQL_ENABLED=false`
- [ ] Redis absent

## Secrets
- [ ] Distinct staging secrets (never production resident credentials)
- [ ] Gemini API key present
- [ ] Flask secret / session signing key rotated for staging
- [ ] Audit token unset in production

## Database
- [ ] Migrations through `013_source_provenance.sql` applied
- [ ] Application DB user least-privilege (no CREATE DATABASE)
- [ ] Separate DBA role for backup/restore (`scripts/staging/backup_restore_drill.sh`)

## Source eligibility
- [ ] `scripts/otp_ops.py bootstrap` completed against staging DB
- [ ] DEMO_FIXTURE / INTERNAL_ANALYSIS / TEST_ONLY excluded from resident retrieval
- [ ] `boston.city_announcements` is NOT_CONFIGURED or properly ingested
- [ ] News/community status is INCOMPLETE when coverage is thin (not falsely healthy)
- [ ] Disabled sources return no new resident evidence

## Corpus / index
- [ ] Resident-eligible corpus only under `data/phase5_corpus/{policy,zoning,meetings,news,community}`
- [ ] `data/phase5_corpus/fixtures/` never ingested for residents
- [ ] Vector index rebuilt; MySQL/vector parity OK
- [ ] Quarantine of contaminated analysis/digest chunks verified

## Rate limits
- [ ] Guest chat ceiling supports normal multi-turn sessions
- [ ] Authenticated ceiling higher than guest when configured
- [ ] 429 responses include Retry-After and resident-visible explanation
- [ ] Audit mode requires non-production + secret header

## Health
- [ ] `GET /health` ok
- [ ] Admin readiness includes source registry / catalog / parity
- [ ] No secrets in health payloads

## Backup / rollback
- [ ] Backup dump succeeds with DBA role
- [ ] Isolated restore drill succeeds or the single external DBA grant gap is documented
- [ ] Application rollback to previous merge commit documented and rehearsed

## Privacy / admin
- [ ] Admin UI/API require admin auth
- [ ] Resident logs do not store raw message bodies by default
- [ ] Connector HTTP allowlist intact

## Smoke tests
- [ ] Greeting / events / 311 / safety overview / entity / policy / zoning / meeting / community
- [ ] Emergency interception
- [ ] Idempotent replay
- [ ] Guest isolation
- [ ] Mobile chat-first viewport (375px / 430px)
- [ ] Desktop layout intact
