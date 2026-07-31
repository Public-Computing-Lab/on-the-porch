# Request Budget and Deadlines

Each new logical turn owner receives one `TurnBudget` after authentication, validation,
and persistent reservation, but before history loading or expensive execution. The budget
uses a monotonic clock. Duplicate attachments retain the owner's deadline and completed
replays do not create a generation budget.

The development default is 25 seconds with a 2-second finalization reserve. Expensive work
uses the working deadline, while atomic persistence and terminal delivery may use the
reserved interval. Default caps are 12 seconds for model calls, 8 seconds for embeddings,
10 seconds for heavy retrieval, 8 seconds for dynamic/prepared SQL, and 3 seconds for
enrichment. Every cap is reduced to the remaining working budget.

The same budget is propagated with `ContextVar` copying into executor work, SQL/RAG hybrid
branches, bounded provider calls, and the SSE producer. Optional work is skipped when its
minimum execution window no longer fits. Skipping optional work is not recorded as a turn
timeout; attempting required work without enough time is.

Python cannot kill an already-running provider thread. The request returns at its deadline,
but that operation retains its concurrency permit until it exits. This prevents timed-out
calls from accumulating without limit. Pending futures are canceled where possible, and
executor shutdown never waits beyond the request boundary.

Pooled MySQL connections check the active budget before checkout and after reconnect. During
a turn, each checkout installs a session `MAX_EXECUTION_TIME` derived from the same remaining
deadline. Reconnect attempts are limited to one per checked-out connection; final persistence
explicitly uses the reserved absolute-deadline window. Connector-level socket interruption is
still limited by the configured MySQL connection timeout and must be reviewed on the target
host.

Timeout uses Stage 3's terminal `FAILED` state with normalized `turn_timeout` metadata.
The same client turn ID replays that safe failure rather than regenerating. A resident may
retry with a new turn ID.

The 25-second value is conservative relative to the Stage 3 local p95 of 2.6 seconds and
8.0-second maximum. It is a development default, not a production conclusion. DreamHost
proxy, worker, database, and SSE behavior must be audited before deployment.
