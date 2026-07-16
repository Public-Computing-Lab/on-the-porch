# State-aware event retrieval

This feature applies trusted canonical conversation context only to deterministic
event-list searches. It does not rewrite safety, crime, 311, opinion, meeting, news,
or generic RAG queries.

## Local dataset audit

The audit used the local `sentiment_demo.weekly_events` table on 2026-07-15. The
production database was not accessed.

- `weekly_events` contained 482 rows and 59 current/future rows.
- The dataset is not entirely Dorchester-scoped. Current/future listings include
  locations outside Dorchester, including Randolph, Newton, Brookline, Jamaica Plain,
  Mattapan, Allston-Brighton, and Roslindale.
- Sources represented were `codman`, `csndc`, a community email source, and Dorchester
  Reporter PDF editions (`REP-*`). Current/future rows came from `codman`, `csndc`,
  `REP-19_26-web.pdf`, `REP-22_26web.pdf`, and `REP-25_26web.pdf`.
- Structured columns include event ID, title, display date, start/end dates,
  start/end times, source, category, normalized name, raw text, and ingestion time.
- Location, URL, description, modality, contact, registration, and cost are embedded
  in labeled or prose content in `raw_text`. The prepared event materialization extracts
  an event URL but does not add a structured neighborhood field.
- Of 59 current/future rows, 42 had a labeled location and 29 were explicitly virtual.
- Four physical rows could be canonicalized with the vetted exact-location registry:
  three in Codman Square and one in broader Dorchester. No current/future row could be
  confirmed for Fields Corner.
- Eleven rows had an address without a vetted supported-neighborhood mapping. Other
  rows had unknown or unsupported physical location metadata.

Because the table is not Dorchester-scoped and has no authoritative neighborhood
column, date filtering is performed with bound SQL parameters and location filtering
is performed deterministically after retrieval. Only explicit labeled locations and a
small server-owned exact registry are accepted. Source name, organizer, title, URL,
and incidental description mentions never establish physical location.

## Safe defaults

- Relative dates are constructed from America/New_York server time and represented as
  half-open ranges. SQL never uses `CURDATE()` or `NOW()` for this path.
- A date-less event-list request uses a documented 14-day upcoming window.
- Because the dataset is not Dorchester-scoped, a request without an explicit location
  is retrieved with a Dorchester location scope. That retrieval default is not treated
  as an explicit resident location and is not persisted by itself.
- Unknown and virtual locations are excluded from physical location results.
- Missing `there` or `that day` antecedents produce deterministic clarification and no
  query, model call, state transition, or cache invalidation.
- Cost, time-of-day, modality, and registration filters are explicitly unsupported in
  this slice. They produce a deterministic limitation response and preserve prior state
  and cache.

## Cache contract

Deterministic event-list cache entries use a five-minute TTL and an exact canonical
signature containing only topic, half-open dates, canonical location ID, and an empty
filters object. The signature contains no resident text, answer prose, SQL, excerpts,
or identity data. A date or location change requires a fresh retrieval.
