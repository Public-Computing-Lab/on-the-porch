# Conversation State Version 2

Conversation state uses three independent version concepts:

| Concept | Field | Meaning |
|---|---|---|
| Envelope format | `version` | Outer `thread_state_json` namespace format. Remains `1`. |
| State schema | `conversation_state.schema_version` | Canonical semantic-state schema. Version `2` is current. |
| Optimistic revision | `conversation_state.revision` | Incremented only by semantic state transitions and used for conflict detection. |

`state_version` in the version-1 state schema was the optimistic revision despite its
name. Version-2 code provides a read-only compatibility property with that name, but
new code uses `revision`.

## Migration Table

| Stored form | In-memory result | Write behavior |
|---|---|---|
| Empty or null | Envelope v1, empty state v2, caller-provided empty cache | No write on load. |
| Legacy top-level retrieval cache | Envelope v1, unchanged cache, empty state v2 | Next successful turn serializes the envelope and state v2. |
| Envelope v1 with `retrieval_cache` only | Unchanged cache, empty state v2 | Next successful turn adds state v2. |
| Envelope v1 with legacy `state_version` state | `state_version` becomes `revision`; a valid active event survives; unsupported legacy topics become null | Next successful turn serializes state v2. |
| Envelope v1 with state v2 | Strict structure; malformed optional dimensions are dropped during persisted-state migration | Next successful turn writes normalized state v2. |

Loading is read-only. It never rewrites `thread_state_json` merely to migrate it.
Migration does not consume UI metadata, shadow relationship diagnostics, shadow
dimension diagnostics, answer prose, or retrieval evidence.

## Canonical State

```json
{
  "schema_version": 2,
  "revision": 7,
  "active_topic": "EVENTS",
  "active_entity": {
    "type": "event",
    "id": "435",
    "source_ref": "event:435"
  },
  "location": null,
  "time_range": null,
  "filters": {},
  "previous_route": "sql",
  "last_substantive_message_id": "message-id"
}
```

`UNKNOWN` is never durable. Unknown or untrusted topics normalize to null during
migration. The only durable entity type is an authoritative event ID. Locations must
come from the server-owned geography registry. Time ranges use half-open ISO date
intervals in `America/New_York` and may span at most 366 days. Event filters are sparse
and are cleared when the active topic is not `EVENTS`.

## Size And Concurrency

The canonical conversation state has an independent 8 KiB serialized limit. The full
envelope retains its existing 1 MiB compatibility limit so a larger retrieval cache
does not consume the semantic-state budget.

Only `revision` participates in optimistic conflict detection. Envelope and schema
versions do not. The API reloads and locks the thread row before inserting messages or
writing state, so a stale semantic writer receives `conversation_conflict` and its
transaction persists neither messages nor state.
