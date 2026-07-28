# Stage 2.5 Production Audit

## Status

Overall status: `BLOCKED_BY_ACCESS`.

The expected DreamHost path is `/home/pclabdev/source/on-the-porch`, and the
expected deployed production commit is
`840ec54dd1265b4f2f42cc18b66b54cbf2a99b89`. No existing noninteractive
DreamHost access was available in the authoritative local environment.
Production commands were not attempted.

DreamHost, production MySQL, Redis, vector storage, cron, processes, proxy
configuration, logs, and chatbot endpoints were not modified or invoked.

## Findings

| Area | Finding | Status |
|---|---|---|
| Deployed commit | Expected commit is documented; active checkout was not inspected | `BLOCKED_BY_ACCESS` |
| Gunicorn command | Active command, worker class/count, threads, timeout, graceful timeout, keepalive, and preload are unknown | `BLOCKED_BY_ACCESS` |
| Host resources | CPU count, memory, uptime, process RSS/CPU, and restart mechanism are unknown | `BLOCKED_BY_ACCESS` |
| Ports and proxy | Active listeners, reverse proxy, SSE buffering, and proxy timeouts are unknown | `BLOCKED_BY_ACCESS` |
| Static frontend | Active serving mechanism is unknown | `UNKNOWN` |
| MySQL | Host category, grants, pool overrides, table estimates, indexes, and server limits are unknown | `BLOCKED_BY_ACCESS` |
| Cache | Redis enabled state and active cache mode are unknown | `BLOCKED_BY_ACCESS` |
| Vector and embeddings | Active vector provider, collection/path, embedding provider, and model are unknown | `BLOCKED_BY_ACCESS` |
| Generation models | Active Gemini model names and nonsecret timeout settings are unknown | `BLOCKED_BY_ACCESS` |
| Scheduling | Cron jobs and ingestion overlap risk are unknown | `BLOCKED_BY_ACCESS` |

## Confirmed Repository Facts

These facts are confirmed from versioned files and are not claims about active
production:

- the application supports configurable MySQL, cache, vector, embedding, and
  Gemini backends;
- repository Gunicorn defaults exist, but environment and process-manager
  overrides can replace them;
- the API emits an SSE anti-buffering header, but external proxy behavior is
  outside the repository;
- legacy DreamHost scripts mention Passenger and scheduled ingestion, but they
  are not evidence of the active deployment.

## Audit Boundary

The owner can execute the safe checklist in
`docs/DREAMHOST_READ_ONLY_AUDIT_CHECKLIST.md` after access is available.
Outputs must be redacted before sharing. Findings should replace blocked labels
only when directly supported by read-only evidence.
