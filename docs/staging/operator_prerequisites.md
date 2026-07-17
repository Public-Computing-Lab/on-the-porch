# Staging operator prerequisites (fill out-of-band)

**Do not commit credentials. Do not paste credentials in tickets or chat.
Do not use production database credentials or production secrets.
Provide secrets only through the approved host-side secret channel.**

This checklist is for a **staging** environment only. Completing it does **not**
mean staging has been validated.

| Category | Value / notes (no secrets) | Status |
|---|---|---|
| Staging FQDN | | MISSING |
| TLS status | | MISSING |
| SSH hostname | | MISSING |
| SSH username | | MISSING |
| Staging application path | | MISSING |
| Supported Python version | | AVAILABLE_BUT_UNVERIFIED |
| Gunicorn / process-control mechanism | | MISSING |
| Staging MySQL hostname | | MISSING |
| Staging MySQL database name | must be staging-only | MISSING |
| Staging MySQL username | | MISSING |
| Staging MySQL privilege scope | least privilege | MISSING |
| Staging log path | | MISSING |
| Staging backup path | | MISSING |
| Staging administrator identity | for `/admin/health/ready` | MISSING |
| Staging CORS origin | HTTPS only | MISSING |
| Rate-limit policy | `RATE_LIMIT_ENABLED` explicit | MISSING |
| Method for storing staging secrets | host file mode 600 / panel | MISSING |
| Method for restarting the application | | MISSING |

Status values: `AVAILABLE` | `AVAILABLE_BUT_UNVERIFIED` | `MISSING` |
`BLOCKED_BY_CREDENTIALS` | `BLOCKED_BY_HOSTING_LIMITATION`

## Host capabilities the staging design expects (unverified until provisioned)

The supported app path (`PRODUCTION_MODE=true ./start_api.sh`) expects the host to
support — **do not assume these exist**:

- A persistent Gunicorn (or equivalent WSGI) process
- Reverse-proxy configuration suitable for SSE (no response buffering)
- A way to restart the process
- Environment variables / host-local secret file
- A staging-only MySQL database

If any capability is missing, mark `BLOCKED_BY_HOSTING_LIMITATION` and stop.

## Affirmative mutation markers (scripts)

Fixture/mutation scripts require:

- `OTP_STAGING_MUTATION_OK=1`
- `MYSQL_DB` matching the staging allowlist **or** equal to `OTP_STAGING_MYSQL_DB`

See `scripts/staging/identity.py`.
