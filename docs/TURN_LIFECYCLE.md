# Reliable Conversation Turn Lifecycle

## Contract

Modern browser sends create one `client_turn_id` and reuse it for SSE, JSON
fallback, status lookup, and replay. The identifier is scoped to its conversation;
the request fingerprint additionally binds it to the owner, thread, normalized
message, bounded UI context, and generation-affecting options.

The response keeps the existing thread and message objects and adds:

```json
{
  "turn": {
    "client_turn_id": "uuid",
    "turn_id": "server uuid",
    "status": "completed",
    "replayed": false,
    "client_idempotent": true
  }
}
```

Legacy clients that omit the ID receive a server-generated ID marked
`client_idempotent: false`.

## State Machine

```text
NEW -> RESERVED -> RUNNING -> COMPLETED
                         \-> FAILED
```

`COMPLETED` and `FAILED` are terminal for a client turn ID. A completed duplicate
returns the stored payload. A failed duplicate returns the same normalized
failure. Reusing an ID with different logical inputs returns
`client_turn_id_conflict`.

## Reservation

`TurnRepository.reserve()` briefly locks the owned conversation row and uses
MySQL as the cross-thread and cross-worker coordinator. It returns one of:

- `new_execution_owner`
- `completed_replay`
- `running_existing`
- `failed_replay`
- `fingerprint_conflict`
- `thread_busy`
- `ownership_failure`

Only `new_execution_owner` invokes the conversational core. A live
`active_turn_id` prevents another different turn from reading and later
overwriting the same conversation state. Different conversations remain
independent.

## Shared Execution

`ConversationTurnService` owns reservation, context loading, running-state
transition, generation, finalization, replay, and failure normalization. JSON and
SSE provide different producers/delivery adapters but use this same lifecycle.

The model, SQL, and RAG work run without an open database transaction. SSE uses a
persistently reserved owner in a worker thread, so client disconnect does not
create another execution owner. Reconnecting or falling back through JSON with
the same ID either observes `in_progress` or replays `completed`.

## Atomic Completion

The short completion transaction:

1. locks and verifies the owned thread and active turn;
2. inserts the user and assistant messages;
3. compares `thread_state_version`;
4. writes state only when the captured version is current;
5. writes the interaction log and assistant metadata;
6. stores the exact replay payload and message IDs;
7. marks the turn `COMPLETED`;
8. clears `active_turn_id`;
9. commits.

An exception rolls back the complete message pair, state update, log, and turn
completion. On a state-version conflict, newer state is preserved; the correct
message pair and replay result still commit, and observability records the
conflict.

## Status and Replay

`GET /conversations/<thread_id>/turns/<client_turn_id>` is ownership protected.
It returns reserved, running, completed, or failed state. Completed responses
include the stored final result and require no model, embedding, SQL, RAG, or web
work.

## Lease Recovery

The default reservation lease is 180 seconds and may be configured with
`TURN_LEASE_SECONDS` from 30 to 900 seconds. It is only abandoned-turn recovery;
it is not a request deadline. An expired same-ID turn becomes deterministic
failure and frees the active slot. It is never regenerated silently.

## Retention

Recommended operational retention is 30 days for completed replay payloads and
72 hours for failed turns. Cleanup is intentionally deferred to an operator job;
Stage 3 does not introduce an ingestion scheduler or background queue.
