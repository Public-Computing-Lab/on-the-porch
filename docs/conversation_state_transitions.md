# Conversation State Transitions

Conversation state changes are planned after the existing answer path completes and are
committed with both messages under the existing thread-row lock. The planner receives
canonical server-owned state, rule-resolved relationship and dimension results,
authoritative event identity, route metadata, and completion status. It never receives
client state, SQL rows, RAG chunks, or answer prose.

## Trust policy

- Only rule-resolved relationship decisions authorize general durable changes.
- Only explicit rule-resolved dimensions, deterministic references to current trusted
  state, and authoritative server event resolution may enter durable state.
- LLM and fallback classifier output remains diagnostic-only.
- Unknown or ambiguous locations, `UNKNOWN` topics, unsupported filters, result sets,
  and answer-derived facts are not persisted.

## Completion mapping

| Status | Existing result signals | State behavior |
|---|---|---|
| `ACCEPTABLE` | Non-empty answer; route completed without timeout, dependency, or part errors. An authoritative empty result is allowed. | May commit if route-compatible. |
| `PARTIAL` | SSE emitted only a partial answer after generation failure. | Preserve state/cache. |
| `DEGRADED` | Request budget timeout, `execution_degraded`, or SQL/RAG/plan part error. | Preserve state/cache. |
| `FAILED` | Missing/malformed result or empty answer. | Preserve state/cache. |
| `CONFLICTED` | Thread revision changed before persistence. | Transaction rolls back messages, state, and cache. |

An HTTP success code alone is not an acceptable-completion signal.

## Route compatibility

Durable changes are rejected when the completed answer route is materially incompatible
with the proposed active topic. In particular, public-safety context requires the hybrid
safety path, community opinion requires RAG/hybrid evidence, and a history-only response
cannot authorize a new event filter or location. This prevents state from claiming more
understanding than the current answer demonstrated.

The transition engine can deterministically resolve references such as `there`, but the
current API reports that reference context as unapplied because this slice does not rewrite
queries. Such a turn is retained as a diagnostic proposal only and does not advance durable
state until the corresponding retrieval path consumes canonical context.

## Cache policy

The retrieval-cache format is retained for compatibility and does not store a semantic
signature yet. The planner compares a deterministic bounded signature in memory using
topic, authoritative entity reference, canonical location, canonical time bounds,
supported filters, and route family.

Any factual dimension or substantive route-family change invalidates the cache by writing
the canonical empty cache in the same transaction as the state revision. For small talk
and exact active-event property follow-ups, the existing cache is retained unchanged.
For normalized equivalent context, `PRESERVE` means the transition engine does not force
cache clearing; the current same-context answer path may still refresh compatible cache
contents. None of these equivalent-context cases increments the semantic revision.

## Persistence diagnostics

Bounded transition diagnostics are stored only in user-message metadata. They contain
action, relationship, cache action, changed field names, reason codes, completion status,
and commit flags. They exclude previous/next state, message text, answer prose, SQL,
retrieval content, raw model output, and exception details. Normal history serialization
does not expose this metadata.

This slice updates state only. It does not rewrite the current question from canonical
location/time/filter state and does not add geographic safety/311 refinement or event
free-only SQL filtering.
