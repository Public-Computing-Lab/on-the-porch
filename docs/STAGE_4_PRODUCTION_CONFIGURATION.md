# Stage 4 Production Configuration Review

Stage 4 does not change production configuration. Before deployment, review these values
against the actual DreamHost host, proxy, worker count, memory, MySQL pool, and provider
quotas:

| Setting | Development default | Review guidance |
|---|---:|---|
| `TURN_TOTAL_BUDGET_MS` | 25000 | Start in the 20-30 second range; verify proxy/SSE timeouts exceed it. |
| `TURN_FINALIZATION_RESERVE_MS` | 2000 | Preserve enough time for atomic persistence and terminal delivery. |
| `LLM_MAX_CONCURRENCY` | 2 | Multiply by worker count; reconcile provider quota and memory. |
| `EMBEDDING_MAX_CONCURRENCY` | 2 | Review local model/GPU or API capacity. |
| `HEAVY_RAG_MAX_CONCURRENCY` | 4 | Review vector-store and executor capacity. |
| `DYNAMIC_SQL_MAX_CONCURRENCY` | 2 | Review dedicated read-only pool capacity. |
| `ENRICHMENT_MAX_CONCURRENCY` | 2 | Review Boston.gov/network behavior. |
| `EXPENSIVE_QUEUE_MAX_WAIT_MS` | 750 | Keep well below the turn budget. |

Deployment prerequisites remain: audit DreamHost runtime and proxy behavior; verify
migration 010; configure dedicated `DYNAMIC_SQL_RO_*` credentials; confirm MySQL pool size
against worker count and ensure `MYSQL_CONNECT_TIMEOUT` fits the turn budget; validate
Redis/vector availability; verify SSE disconnect and replay;
and run a canary before resident traffic.

Process-local gates are not host-global. Python threads that cannot be canceled retain
permits until exit. Monitor queue rejections, timeout stage, noncancelable-running calls,
worker RSS, MySQL waits, provider 429/5xx, replay latency, and completion relative to budget.

Do not copy local worker-matrix results directly into DreamHost. No production system,
credential, data store, cron, or deployment process was accessed during Stage 4.
