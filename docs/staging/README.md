# Staging deployment validation — prerequisite checklist

This document is the gate for a **staging-only** deployment of On the Porch after
PR #18 (launch-runtime hardening). It does **not** authorize production deploy.

**Status of this slice (as of branch creation):** remote staging resources are
**not provisioned / not accessible from this workstation**. Local preparation
artifacts are committed; **no remote mutation** was performed.

## Architecture contract (non-negotiable)

- MySQL is the only persistent / cross-worker shared application store.
- Redis and Redis-compatible services are unsupported.
- No process-local authoritative state or distributed locks.
- Process-local metrics/caches are diagnostics or bounded optimizations only.

## Required staging resources

| # | Resource | Status | Notes |
|---|---|---|---|
| 1 | Dedicated staging hostname/subdomain | **MISSING** | No staging FQDN configured in repo or local SSH |
| 2 | HTTPS/TLS for that hostname | **MISSING** | Depends on (1) |
| 3 | Staging-only MySQL database | **MISSING** | Local `.env` points at localhost demo DB only — must not be used as “staging” |
| 4 | Staging-only MySQL least-privilege user | **MISSING** | |
| 5 | Staging application directory on host | **MISSING** | DreamHost docs use placeholder `~/ml-misi-community-sentiment` |
| 6 | Supported Python runtime on host | **AVAILABLE_BUT_UNVERIFIED** | DreamHost historically supports Python 3.11+; not verified remotely |
| 7 | Writable application log location | **MISSING** | Host path unknown |
| 8 | Deployment / process-start mechanism | **AVAILABLE_BUT_UNVERIFIED** | Repo supports `./start_api.sh` + Gunicorn; DreamHost Passenger scripts are **stale** |
| 9 | Way to restart Gunicorn/app | **MISSING** | No staging SSH/process manager access from this workstation |
| 10 | Staging access restriction | **MISSING** | Basic-auth / IP allowlist / VPN not configured |
| 11 | Staging administrator identity | **MISSING** | No staging admin fixture provisioned remotely |
| 12 | Staging secret-management method | **MISSING** | No vault/panel path documented for staging-only secrets |
| 13 | Staging backup location | **MISSING** | |
| 14 | Rollback mechanism | **AVAILABLE_BUT_UNVERIFIED** | Git revision rollback documented in runbook; host process restart unverified |

### Access blockers

| Item | Status |
|---|---|
| Staging SSH host in `~/.ssh/config` | **MISSING** (only unrelated academic host present) |
| Staging panel / DreamHost credentials in agent environment | **BLOCKED_BY_CREDENTIALS** |
| Approved staging hostname from operators | **MISSING** |
| Permission to create DreamHost subdomain/DB | **BLOCKED_BY_CREDENTIALS** / **UNKNOWN** |

## Exact checklist before any remote mutation

Operators must provide (out of band — never commit values):

1. Staging FQDN (e.g. `staging.<domain>`) with TLS.
2. SSH (or equivalent) to a staging app directory **separate** from production.
3. Staging MySQL host + schema + user **distinct** from production and from local `sentiment_demo`.
4. Confirmation that staging will **not** mount production data volumes or replicas of resident conversations.
5. Staging-only secrets: `FLASK_SECRET_KEY`, `TOKEN_PEPPER`, MySQL password, optional Gemini key for smoke.
6. Log directory path and rotation policy.
7. Process supervision: how Gunicorn is started/stopped (systemd, Passenger custom, `screen`, panel).
8. Staging admin email for `/admin/health/ready`.
9. Backup filesystem path with chmod-restricted access.
10. Written approval that this host is staging-only.

Until every item above is **AVAILABLE**, staging validation remains **not successful**.

## What this branch provides locally

- Environment **category** contract (no secret values): `docs/staging/environment_contract.md`
- Deployment / rollback runbook: `docs/staging/runbook.md`
- Hosting audit vs Passenger: `docs/staging/hosting_audit.md`
- Connection-budget worksheet template: `docs/staging/connection_budget.md`
- Fixture + cleanup procedure: `docs/staging/fixtures.md`
- Local/CI-safe validators and smoke harnesses under `scripts/staging/` (require `--base-url` for remote; refuse production DB names)

## Explicit non-actions in this slice

- No production deployment.
- No use of production resident data.
- No pointing any process at the production database.
- No stop/restart of the operator’s local API on `:8888`.
- No mutation of historical event worktrees or external backups.
