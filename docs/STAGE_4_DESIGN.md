# Stage 4: Bounded Latency and Load

## Scope and protected interfaces

Stage 4 changes the execution shell around the existing conversational core. It does not
change prompts, raw history, routing semantics, retrieval ranking, prepared SQL meaning,
answer voice, source admission, or response formatting. Stage 3 turn identity, ownership,
replay, atomic persistence, state versioning, and SQL validation remain authoritative.

## Current risks

The current system has finite but independent Gemini, MySQL, Chroma, executor, and
enrichment timeouts. Several layers retry independently, so their elapsed times can add.
Hybrid executors wait for every branch, provider concurrency is not bounded by operation
class, and SSE keepalives are not governed by one logical-turn deadline. Gunicorn's worker
timeout is a process safeguard, not a resident-facing service budget.

## Authoritative turn budget

Every new execution owner receives one `TurnBudget` after reservation and before history
loading or other expensive work. The budget uses `time.monotonic()` and contains the
logical turn ID, start, absolute deadline, total duration, finalization reserve, exhaustion
category, and cancellation state. It is installed in a `ContextVar`; existing context-copying
executor submission therefore propagates the same object and deadline to child threads.

Completed and failed replay do not create a generation budget or enter expensive gates.
A duplicate attachment never changes the owner's deadline. Replay delivery remains bounded
by the HTTP server and database operation timeouts.

The development default is 25 seconds, justified by the Stage 3 live p95 of 2.6 seconds,
8.0-second maximum, and sub-10-ms persistence overhead. A 2-second finalization reserve
protects synthesis fallback, atomic persistence, terminal state, and final transport
delivery. Deployment must review these values rather than treating them as host-optimal.

## Stage deadlines and finalization

All optional/expensive work uses the working deadline: absolute deadline minus the
finalization reserve. `timeout_for()` returns the minimum of a stage cap and remaining
working time. Stages decline to start when their configured minimum cannot fit. Final
persistence uses the full absolute deadline and never holds a transaction during model or
retrieval work.

Initial caps are configurable: model 12 seconds, embedding 8 seconds, heavy retrieval 10
seconds, dynamic SQL 8 seconds, enrichment 3 seconds, and queue wait 750 ms. No cap can
extend the turn deadline.

## Retry policy

One centralized policy classifies transient 429, 5xx, connection reset/network failures,
and short provider timeouts. It permits one initial attempt and at most one retry. Permanent
4xx, authentication/ownership errors, SQL-safety rejection, invalid input, parser errors,
capacity rejection, and deadline exhaustion are not retried. Backoff is bounded, jittered
deterministically when requested, and skipped unless the next attempt can fit while keeping
the finalization reserve. Existing fallback-model behavior counts as the second attempt.

Dynamic SQL semantic repair remains distinct from transport retry but is limited to one
repair and must pass the same budget checks.

## Concurrency and overload

Named process-local gates bound LLM generation, embedding, heavy RAG, dynamic SQL, and
Boston.gov enrichment separately. Acquisition is FIFO, tracks active and waiting counts,
and waits no longer than the smaller of the configured queue cap and working deadline.
Permit release is guaranteed in `finally`. A noncancelable operation that outlives its
request retains its permit until its worker actually exits, preventing orphan accumulation.

Capacity exhaustion produces a normalized `capacity_unavailable` terminal failure, a safe
resident message, and `Retry-After`. The turn is failed once and the same client turn ID
replays that terminal result. Limits are per process; effective host-wide concurrency is
approximately per-worker limit multiplied by worker count.

## Parallel and partial work

SQL and RAG branches share the same budget. Collection stops at the working deadline,
pending futures are canceled where possible, and executor shutdown never waits past the
request boundary. Completed evidence is retained in plan order. Existing synthesis may use
one successful branch when enough synthesis budget remains; it must not claim evidence from
the failed branch. All-path failure uses the existing resident-safe failure language.

## SSE and persistence

The budget is bound inside the SSE worker and remains active after headers are emitted.
Keepalive polling checks exhaustion. A terminal final or controlled error is authoritative;
disconnect does not create another execution, and same-ID JSON/SSE replay does not
regenerate. Timeout and overload use the existing `FAILED` state with normalized categories,
so no schema change is required. Raw provider errors are never persisted or returned.

## Observability

Traces add total/remaining budget, finalization reserve, exhaustion stage, requested external
timeout, retry decision/reason/attempt/backoff, gate active/waiting counts, queue duration,
acquisition result, overload category, partial completion, canceled futures, noncancelable
work, and completion relative to budget. Existing redaction and fail-open emission remain.
No prompts, responses, SQL, credentials, cookies, or source documents are recorded.

## Load characterization

The deterministic harness uses fake providers and a seeded 40/30/15/10/5 mix of prepared
SQL, RAG, hybrid, follow-up, and dynamic SQL work over JSON, SSE, replay, duplicates,
timeouts, retries, and overload. It exercises concurrency 1, 5, 10, 20, and 40 and compares
2x4, 3x4, and 2x8 worker/thread configurations without changing production defaults. It
records throughput, latency percentiles, queue metrics, active maxima, retries, replay,
persistence, RSS, threads, and unfinished calls, and always stops child processes.

## Production boundary and limitations

Process-local gates are not cross-worker distributed limits. Python cannot forcibly stop an
already-running thread, so such work holds its gate until exit while the request returns at
its budget. DreamHost proxy/SSE behavior, worker count, MySQL pool compatibility, migration
010, dedicated `DYNAMIC_SQL_RO_*` credentials, and production budget/concurrency values
must be audited before deployment. Stage 4 does not deploy or begin Stage 5.
