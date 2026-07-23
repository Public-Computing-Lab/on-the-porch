# Final hardening backlog

Single backlog for nonblocking polish after Phase 5. Do not scatter TODO files.

## Semantic quality
- Rare paraphrases that still ask for an unnecessary time period after a broad safety overview.
- Occasional planner clarifications on broad “safety concerns” wording; clearer crime-category asks succeed.
- Occasional entity-profile asks that clarify instead of resolving a seeded profile.
- Cross-domain asks that clarify “which domain first” instead of combining evidence.

## Source quality
- Expand verified civic entity profiles beyond the current seed set.
- Stronger meeting-transcript metadata (speaker, meeting date) when ingestion provides it.
- Broader allowlisted official announcement pages beyond the initial Boston.gov hosts.
- Stronger stale/invalid source exclusion during incremental refresh.

## Ranking / retrieval
- Document passage ranking across POLICY / TRANSCRIPT / NEWS / COMMUNITY when several corpora return hits.
- Crime offense-group display names for uncommon BPD labels when `offense_code_group` is sparse.

## UI polish
- Final citation chip visual design and source-type legend.
- Long multi-source answers: collapsible source groups.
- Event sidebar spacing with source chips present.
- Final brand / visual redesign (explicitly deferred).

## Accessibility
- Additional screen-reader polish for streaming deltas.
- Ensure limitation text remains visible when many citation chips wrap.

## Mobile
- Further events-panel compaction on very small phones.
- Conversation list gesture polish.

## Performance
- Shared connection reuse for crime freshness + aggregate queries.
- Avoid loading retrieval stack for pure entity-profile turns beyond the current short-circuit.

## Load
- Multi-worker soak and latency percentiles under production-like concurrent guests.

## Security / privacy
- Final review of operator audit retention windows.
- Optional redaction policy for admin interaction viewers.

## Operations
- Remote staging host provisioning (FQDN, TLS, staging DB) still operator-blocked.
- Automate DreamHost/process-supervisor restart verification on a real staging host.
- Migrate remaining offline ingest scripts from `google.generativeai` to `google-genai`.

## Release evaluation
- Full semantic holdout / external audit after UI and staging host are complete.
- Design QA for the end-of-product visual pass.

## Deployment
- Public production deploy checklist (not authorized in Phase 5).
- Production secret management path confirmation.

## Documentation
- Keep a single architecture doc (`docs/answering_architecture.md`) and operations pack (`docs/phase5_operations.md`); avoid overlapping manuals.
