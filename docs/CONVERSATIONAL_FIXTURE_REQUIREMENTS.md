# Conversational Fixture Requirements

## Purpose

Tier 2 scenarios depend on stable civic records. They must be release gates only
against controlled fixtures. Execution against changing production data is
advisory and must not be compared with fixture-bound exact values.

All fixture records need a stable source ID, canonical source category, source
URL or fixture URI, publication or occurrence time, ingestion time, geography,
and deterministic display text. Fixture clocks must be frozen.

## Minimum Inventory

| Dataset | Category | Minimum records | Date range and geography | Required fields | Representative queries | Freshness strategy |
|---|---|---:|---|---|---|---|
| Events | `event_listing` | 24 | Frozen six-week window; Dorchester sub-neighborhoods plus Boston-wide controls | stable event ID, title, description, start/end, venue, address, neighborhood, cost, accessibility, registration, status, source URL | weekend events, named event, free/family/accessibility filters, cancellation, registration | Relative dates resolve from a frozen clock; include active, cancelled, sparse, and duplicate-title records |
| Boston 311 | `official_data` | 120 | Frozen 90 days; Dorchester, Codman Square, Fields Corner, Ashmont, and outside-area controls | case ID, opened/closed time, status, subject, reason, type, department, location, neighborhood, coordinates, source | counts, trends, top types, geography correction, pagination, no-match, source explanation | Immutable snapshot plus precomputed aggregates with the same cutoff |
| Crime | `official_data` | 80 | Frozen 12 months with two comparison windows; Dorchester districts and outside controls | incident ID, occurred/report dates, offense group/description, district, reporting area, generalized location, source | counts, trend, offense filter, time-window correction, no-confirmation | Immutable public-record snapshot; record publication lag in fixture metadata |
| Public safety notices | `official_document` | 12 | Frozen 60 days; Dorchester and citywide | notice ID, title, issued/updated time, status, area, body, official URL | active notice, resolved notice, no official confirmation | Explicit `as_of` and active/resolved state |
| Policy | `official_document` | 20 | Frozen current and historical policy set | document ID, title, body, proposal/approval status, effective date, agency, geography, source URL | proposal versus approval, policy identity correction, resident impact | Versioned documents with superseded links |
| Budget | `official_document` | 16 | Two fiscal years and one proposed year | document ID, fiscal year, status, amount, purpose, department, approval date, source URL | amount, purpose, approval state, current versus proposed | Frozen fiscal calendar and status transitions |
| Meeting transcripts | `meeting_transcript` | 16 meetings / 80 chunks | Frozen 90 days; at least four neighborhoods | meeting ID, title, date, organization, agenda item, speaker role or anonymous label, chunk ID, transcript text, source URL | concern summary, named meeting, speaker attribution, official/community distinction | Immutable transcript fixture; no generated speaker identity |
| Newsletters | `community_material` | 20 | Frozen 90 days; neighborhood and organization coverage | document ID, title, publisher, publication date, body/chunks, neighborhood, source URL | newsletter updates, earlier-topic follow-up, source-type distinction | Versioned publication snapshot |
| Community notes | `community_material` | 30 | Frozen 90 days; at least five neighborhoods | note ID, meeting/event linkage, date, organization, topic tags, text, verification label, source URI | resident concerns, neighborhood summary, no-discussion case | Explicitly synthetic or licensed fixture text with verification state |
| Local news | `local_journalism` | 30 | Frozen 120 days; policy, events, safety, development, utilities | article ID, headline, publisher, publication/update date, body/chunks, topic, geography, source URL | latest news, news versus primary record, related-topic follow-up | Snapshot with publication cutoff; no live assertions in CI |
| Organizations | `organization` | 20 | Dorchester-serving and Boston agencies, plus ambiguous-name controls | organization ID, canonical/alternate names, acronym, type, description, address/service area, contact, official URL | identity, acronym clarification, contact, correction | Versioned directory with explicit verified date |
| Utilities and transit | `official_document` | 16 | Frozen service notices and static service-area records | notice/entity ID, provider, service type, issued/updated time, status, area, body, source URL | provider identity, outage limitation, transit role, no real-time claim | Static snapshots only; live outage or vehicle status remains unsupported |

## Fixture Relationships

- Event list, selected event, and follow-up records share the same stable event
  ID.
- Pagination scenarios use deterministic order keys and non-overlapping pages.
- 311 raw rows and aggregates share one cutoff and reconcile exactly.
- Community, transcript, journalism, and official records use distinct source
  categories even when they discuss the same topic.
- Named organizations include aliases, punctuation variants, and ambiguous
  acronyms.
- No-evidence scenarios include explicit negative controls rather than relying
  on accidental absence.

## Tier 2 Execution Contract

- Fixture mode is deterministic and may enforce fixture-bound values.
- Live mode reports retrieval and answer quality but does not gate exact counts,
  dates, or current status.
- Missing fixture inventory is `BLOCKED_BY_FIXTURE_OR_ENVIRONMENT`, not a
  chatbot pass or failure.
- Production records are never copied into committed fixtures unless separately
  reviewed, sanitized, and licensed.
- Stage 2.5 defines these requirements only; it does not rebuild the demo
  database or modify ingestion.
