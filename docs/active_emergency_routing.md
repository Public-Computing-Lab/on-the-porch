# Active Emergency Routing

Clear descriptions of current or imminent life-threatening danger are handled by
`on_the_porch/emergency_routing.py` before event resolution, conversation classifiers,
cache-answer logic, civic routing, SQL, document retrieval, embeddings, or model calls.

The detector is rule-only and consumes only the current validated message. Its bounded
decision contains a category, reason code, rule decision source, and confidence. It
does not contain resident text, names, addresses, history, or client metadata.

The resident response is intentionally short and begins with `Call 911 now.` The
application does not ask the resident to send a location to the app, does not dispatch
responders, and does not claim to contact emergency services.

## Conversation persistence

Modern endpoints persist the user and assistant messages in the resident-owned MySQL
conversation thread. Emergency handling preserves the existing semantic topic, active
event, canonical location/time/filters, retrieval cache, semantic revision, and last
substantive civic message.

Same-thread emergency races use the thread's loaded `last_message_at` as an additional
one-winner check because emergency turns intentionally do not increment the semantic
revision.

## Privacy and telemetry

Emergency messages remain visible in their owner’s conversation transcript under the
existing product contract. They are not copied into `interaction_log`, and emergency
handling does not acquire the separate interaction-log connection.

Operational telemetry contains only the emergency route, bounded category and reason
code, success/failure, detector/endpoint latency, request/thread identifiers already
used by existing metrics, and zero dependency-call counters. Logs never include the
resident message or emergency answer.

## Storage

The feature uses existing MySQL conversation persistence and introduces no new
persistent store, cache backend, lock service, or schema migration.
