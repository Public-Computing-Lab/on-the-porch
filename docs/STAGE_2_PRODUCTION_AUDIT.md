# Stage 2 Production Audit

## Audit Boundary

The expected DreamHost deployment path is
`/home/pclabdev/source/on-the-porch`, with expected deployed commit
`840ec54dd1265b4f2f42cc18b66b54cbf2a99b89`.

No DreamHost SSH target or credential is configured in the authoritative local
environment. The only configured SSH host is unrelated to this project.
Production commands were therefore not attempted.

DreamHost was not modified. Production MySQL, Redis, vector storage, cron,
processes, proxy configuration, logs, and chatbot endpoints were not accessed.

## Runtime

| Finding | Status | Evidence |
|---|---|---|
| Deployed commit | `BLOCKED_BY_ACCESS` | Expected SHA is documented, but remote `git rev-parse` could not be run. |
| Actual Gunicorn command and uptime | `BLOCKED_BY_ACCESS` | No configured DreamHost access. |
| Worker class/count and thread count | `BLOCKED_BY_ACCESS` | Repository defaults are not proof of active production values. |
| Timeout, graceful timeout, keepalive, preload | `BLOCKED_BY_ACCESS` | Active process/environment unavailable. |
| Process and total application memory | `BLOCKED_BY_ACCESS` | Process list unavailable. |
| Host CPU and memory limits | `BLOCKED_BY_ACCESS` | Host access unavailable. |
| Supervisor/restart mechanism | `UNKNOWN` | Legacy docs discuss Passenger; current active mechanism cannot be confirmed. |
| Repository Gunicorn defaults | `CONFIRMED` | Static code defaults to `gthread`, 3 workers, 8 threads, 180s timeout, 30s graceful timeout, 5s keepalive, and `preload_app=False`. These are not claimed as active production settings. |

## Proxy and SSE

| Finding | Status | Evidence |
|---|---|---|
| Reverse proxy | `EXTERNAL_CONFIGURATION_REQUIRED` | DreamHost panel/proxy configuration is unavailable locally. |
| Proxy read/send timeout | `EXTERNAL_CONFIGURATION_REQUIRED` | Not represented authoritatively in the repository. |
| Proxy buffering and SSE rules | `EXTERNAL_CONFIGURATION_REQUIRED` | API sends `X-Accel-Buffering: no`, but external proxy behavior is unconfirmed. |
| Connection limits | `EXTERNAL_CONFIGURATION_REQUIRED` | Host/panel configuration required. |
| Static frontend serving mechanism | `UNKNOWN` | Legacy Passenger documentation may not describe the active deployment. |

## Data Services

| Finding | Status | Evidence |
|---|---|---|
| Production MySQL host category | `BLOCKED_BY_ACCESS` | Production environment values unavailable. |
| Connection-pool settings | `BLOCKED_BY_ACCESS` | Active environment override unavailable. |
| Database grants | `BLOCKED_BY_ACCESS` | No production database connection was attempted. |
| Civic table sizes and indexes | `BLOCKED_BY_ACCESS` | No production metadata queries were run. |
| Slow-query logging | `BLOCKED_BY_ACCESS` | Database status unavailable. |
| Redis state and host category | `BLOCKED_BY_ACCESS` | Active environment and service unavailable. |
| Vector provider, endpoint/path, and size | `BLOCKED_BY_ACCESS` | Active environment and collection unavailable. |
| Embedding provider and Gemini model names | `BLOCKED_BY_ACCESS` | Active environment values unavailable. |
| Ingestion schedules and overlap | `BLOCKED_BY_ACCESS` | Remote cron could not be listed. |

## Static Inferences Requiring Production Confirmation

- `INFERRED`: the API supports MySQL, in-memory, and Redis-backed caches, but the
  active production backend is unknown.
- `INFERRED`: the API supports Chroma or Qdrant and local or Gemini embeddings,
  but active production selections are unknown.
- `INFERRED`: legacy DreamHost scripts refer to Passenger and a daily ingestion
  cron. Those scripts are explicitly described as potentially outdated and are
  not evidence of active configuration.

## Operator Follow-up

When existing read-only DreamHost access becomes available, repeat the audit
without invoking the chatbot or mutating any service. Confirm the deployed SHA,
process command line and resource use, proxy/SSE settings, environment variable
names and nonsecret operational values, MySQL grants and metadata, cache/vector
providers, and cron overlap. Findings must replace the blocked labels rather
than being inferred from repository defaults.
