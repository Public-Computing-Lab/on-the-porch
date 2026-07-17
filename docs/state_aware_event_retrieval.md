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
  Reporter PDF editions (`REP-*`).
- Structured columns include event ID, title, display date, start/end dates,
  start/end times, source, category, normalized name, raw text, and ingestion time.
- Location, URL, description, modality, contact, registration, and cost are embedded
  in labeled or prose content in `raw_text`.

Because the table is not Dorchester-scoped and has no authoritative neighborhood
column, date filtering uses bound SQL parameters and Dorchester relevance uses a
bounded deterministic classifier (`event_relevance.py`).

## Natural-language time

Relative phrases resolve in America/New_York to half-open ranges. Examples at
Friday 2026-07-17:

| Phrase | Kind | Effective upcoming range |
|---|---|---|
| next week | `NEXT_CALENDAR_WEEK` | [2026-07-20, 2026-07-27) |
| over the next week / next seven days | `ROLLING_DAYS` | [2026-07-17, 2026-07-24) |
| next 14 days | `ROLLING_DAYS` | [2026-07-17, 2026-07-31) |
| this week (upcoming discovery) | `THIS_CALENDAR_WEEK` semantic; floor applied | [2026-07-17, 2026-07-20) |

“Next week” and “next seven days” are distinct. Semantic calendar-week state is
preserved while upcoming retrieval applies today’s floor. Historical phrases such as
“what happened on July 16” do not apply the upcoming floor.

## Dorchester relevance

Default generic event discovery uses geography `dorchester` with no subarea.

Qualifying kinds for the Dorchester default (chat and sidebar):

- confirmed physical Dorchester addresses / vetted address registry;
- vetted Dorchester subarea addresses (Codman Square, Fields Corner);
- vetted Dorchester-serving virtual programs (bounded allowlist; labeled Virtual).

Excluded from the default:

- physical outside-Dorchester venues (for example Randolph);
- unvetted virtual listings;
- unknown / unlabeled locations;
- relevance inferred only from title, URL, organizer address, or source name.

Virtual modality is supported as an explicit filter. Cost, time-of-day, hybrid, and
registration filters remain unsupported.

## Sidebar contract

The default sidebar title is **Upcoming Dorchester Events**. Date-range and count
controls affect only `GET /events`. They never alter the chat event query plan.

## Cache contract

Deterministic event-list cache entries use a five-minute TTL and an exact signature:

```json
{
  "topic": "EVENTS",
  "start_date": "2026-07-20",
  "end_date_exclusive": "2026-07-27",
  "location_id": "dorchester",
  "subarea_id": null,
  "modality": null,
  "filters": {}
}
```

The signature contains no resident text. Effective upcoming dates are used for
SQL/cache execution; semantic label/dates remain on the plan for state.
