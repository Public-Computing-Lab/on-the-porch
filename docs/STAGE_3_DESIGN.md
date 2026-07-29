# Stage 3 Design: Safe SQL and Reliable Turn Execution

## Scope

Stage 3 wraps the existing conversational core with two infrastructure controls:

1. an AST-validated, bounded execution boundary for model-generated MySQL; and
2. a persistent, transport-neutral turn lifecycle shared by JSON and SSE.

It does not change prompts, routing, retrieval, model selection, answer synthesis,
history formatting, or resident-facing answer semantics. It does not add a global
request deadline, retry tuning, admission control, or worker tuning.

## Existing Turn Lifecycle

Both message routes currently implement their own lifecycle:

1. authenticate and verify thread ownership;
2. load the last 20 messages and `thread_state_json`;
3. close the database connection;
4. run the conversational core;
5. reopen a database connection;
6. independently insert a user message and assistant message;
7. overwrite `thread_state_json`;
8. write an interaction log;
9. commit and return a transport-specific response.

The SSE route additionally streams deltas and the browser retries a failed stream
through the JSON route. Because neither request carries a stable logical turn ID,
that fallback can execute the model and persist messages a second time. Two
different requests for one thread can also load the same state and overwrite one
another.

## Proposed Turn Lifecycle

The shared lifecycle is:

1. route-level authentication, CSRF, rate limiting, and message validation;
2. validate or generate `client_turn_id`;
3. verify thread ownership;
4. calculate a request fingerprint from owner, thread, normalized message, and
   generation-affecting request context;
5. atomically reserve the logical turn and the thread's active-turn slot;
6. classify the reservation as execution owner, completed replay, running
   existing, failed replay, fingerprint conflict, or ownership failure;
7. only the execution owner loads history and thread state;
8. transition the turn from `RESERVED` to `RUNNING`;
9. run the existing conversational core outside a database transaction;
10. atomically persist the user message, assistant message, thread state, state
    version, replay payload, interaction reference, and `COMPLETED` turn;
11. clear the thread's active-turn slot in that same transaction;
12. deliver the same authoritative result through JSON or SSE.

JSON and SSE use one `ConversationTurnService`. Delivery adapters differ only in
whether deltas are emitted while the existing producer runs.

## SQL Trust Boundary

`on_the_porch/sql_chat/sql_safety.py` is the authoritative boundary for
model-generated SQL. `app4._execute_sql()` invokes it before a cursor sees dynamic
SQL. Trusted prepared SQL remains on its existing prepared path.

The guard uses `sqlglot` with the MySQL dialect. It requires exactly one parsed
read-only query, rejects ambiguous comments and dangerous AST nodes/functions,
resolves CTE aliases separately from physical tables, blocks system schemas, and
enforces an explicit civic-table allowlist. It adds or caps a server query limit
and fetches at most the configured row limit plus one detection row.

Execution also enforces configurable statement time, selected-column, row, and
serialized-payload limits. Observability records a non-reversible query
fingerprint, validation outcome, rejection category, allowed tables, elapsed
time, row count, truncation, and timeout status. Raw generated SQL and literals
are not logged by the safety layer.

Production dynamic SQL fails closed unless a dedicated read-only MySQL identity is
configured. Conversation/authentication persistence continues using the existing
application connection. Local development may explicitly permit the application
connection for demo-data tests.

## Turn Data Model

Migration `010_conversation_turns.sql` adds:

- `conversation_turns`: internal ID, owner, thread, client ID, request
  fingerprint, transport origin, client-idempotency marker, status, lease owner
  and expiry, message references, replay payload, normalized error category,
  observability trace ID, state-version snapshot, and lifecycle timestamps;
- `conversation_threads.active_turn_id`: the database-backed same-thread
  execution slot; and
- `conversation_threads.thread_state_version`: optimistic state revision.

Uniqueness is `(thread_id, client_turn_id)`. Ownership is still independently
verified by `user_id`; a turn status or replay is never loaded without both.

## Turn States

Allowed transitions are:

- new request: no row -> `RESERVED`;
- execution ownership: `RESERVED` -> `RUNNING`;
- successful atomic persistence: `RUNNING` -> `COMPLETED`;
- terminal generation/persistence failure: `RESERVED|RUNNING` -> `FAILED`.

A completed or failed turn is terminal for that client ID. A resident retry after
failure uses a new client ID. An expired lease permits recovery of the thread's
active slot but does not silently rerun the same failed logical turn.

## Request Hashing

The SHA-256 fingerprint covers:

- owner ID;
- thread ID;
- whitespace-normalized message (case preserved);
- relevant UI/entity request context when present; and
- explicit generation-affecting options.

It excludes credentials, headers, cookies, database configuration, prompts, and
logs. Reusing an ID with a different fingerprint is an HTTP 409 conflict.

## Replay Rules

- `COMPLETED`: return the stored response payload or its persisted message
  references without model, SQL, RAG, embedding, or enrichment work.
- `RUNNING` or `RESERVED` with an active lease: return a stable in-progress
  result; never create a second execution owner.
- `FAILED`: replay the normalized failure; never rerun with that ID.
- absent client ID: generate a server ID for backward compatibility and mark the
  request non-client-idempotent in observability and response metadata.

Replay retention is 30 days for completed turns and 72 hours for failed/abandoned
turns. Cleanup is documented and can be scheduled separately.

## Same-Thread Concurrency

Reservation locks the owned `conversation_threads` row briefly. A new turn may
claim `active_turn_id` only when no live active turn exists. A second different
turn receives `turn_in_progress`; turns in different threads proceed
independently. No database transaction remains open during Gemini, SQL, or RAG.

The final transaction compares `thread_state_version` with the version captured
at execution start. A mismatch never overwrites newer state. The message pair and
completed replay remain authoritative; optional cache state is conservatively
preserved and the conflict is recorded.

## Transaction Boundaries

Reservation, transition-to-running, completion, and failure each use short MySQL
transactions. The completion transaction:

1. locks the turn and thread;
2. revalidates turn ownership, lease ownership, active turn, and fingerprint;
3. inserts exactly one user/assistant pair;
4. conditionally updates thread state using compare-and-swap;
5. updates title and last-message metadata;
6. stores the exact replay payload and message references;
7. marks the turn `COMPLETED`;
8. clears `active_turn_id`;
9. commits.

Any exception rolls back the complete message pair and completion state.

## JSON Behavior

New turns preserve the current HTTP 201 body and add a backward-compatible
`turn` object. Completed replay returns the same persisted body with
`turn.replayed=true`. Active turns return HTTP 202 with `turn_in_progress`.
Fingerprint conflicts return HTTP 409. Failed replay returns the normalized
terminal failure without execution.

## SSE Behavior

New turns preserve current delta/correction/final events. The final event contains
the persisted authoritative payload and turn metadata. Completed replay emits one
final event without token replay. An active turn returns a machine-readable
in-progress response before streaming begins. Disconnecting a client does not
create another execution owner; the same ID can be replayed through JSON or SSE.

## Frontend Behavior

Each logical send creates one UUID using `crypto.randomUUID()` with a
cryptographically strong fallback. The optimistic user bubble and assistant
placeholder retain this ID. The SSE request, JSON fallback, status lookup, and
replay all reuse it. Authoritative messages replace local placeholders by ID, and
stale completions from another active thread are ignored. Existing message,
source-chip, and answer rendering remain unchanged.

## Crash and Retry Behavior

- crash before reservation: no turn exists; the same request may reserve;
- crash after reservation: the lease expires and frees the thread, while the same
  nonterminal ID reports its state rather than silently regenerating;
- generation failure: mark `FAILED`, clear the active slot, and replay the
  normalized failure;
- rollback during final persistence: no message pair or completed turn remains;
- crash after commit but before response/final SSE: retry replays `COMPLETED`;
- browser SSE fallback: same ID reaches replay/running status, never a second run.

## Migration Strategy and Backward Compatibility

Migration numbering follows current dev (`010`). It is applied only to local/demo
schemas during Stage 3 verification. Existing rows receive state version `0` and
no active turn. Existing APIs still accept requests without `client_turn_id`;
modern browser requests always send it. Existing messages, thread-state JSON, and
response fields remain readable.

Rollback is manual and ordered: remove active-turn/state-version columns after
dropping their foreign key/indexes, then drop `conversation_turns`. Application
rollback must precede schema rollback. No production account or grant is changed
by migrations.

## Failure Handling

Resident responses use bounded public codes and messages. They never include raw
SQL, parser details, table names, stack traces, credentials, fingerprints, lease
owners, provider payloads, or internal intent/tool names. Developer logs retain
normalized categories, internal turn IDs, state transitions, and trace IDs.

## Observability Integration

Stage 2 traces gain bounded fields for client/internal turn IDs, reservation
outcome, replay, fingerprint conflict, state transition, execution ownership,
same-thread contention, persistence duration, version conflict, replay source,
duplicate suppression, and dynamic-SQL safety metrics. Observability remains
nonfatal and does not log full resident text or raw generated SQL.

## Protected Conversational Interfaces

The service calls the existing `_execute_agent_response`,
`_iter_stream_agent_with_keepalive`, source extraction, fallback, and cache
builders without changing their prompts, routing, retrieval thresholds, model
choices, history length, or answer formatting. Rebuild planner/compiler,
model-led service, strict semantic dimensions, and deterministic answer
compilation are not imported.
