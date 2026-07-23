# Final hardening backlog

Nonblocking work after public-beta readiness. Do not scatter TODO files.

## Semantic quality
- Rare paraphrases that still ask for an unnecessary time period after a broad safety overview.
- Occasional entity-profile asks that clarify instead of resolving a seeded profile.
- Cross-domain asks that clarify “which domain first” instead of combining evidence.
- News follow-ups when local-news coverage is intentionally INCOMPLETE.

## Source quality
- Ingest allowlisted Dorchester journalism RSS so news can leave INCOMPLETE.
- Ingest boston.city_announcements through the allowlisted HTTP connector.
- Expand verified civic entity profiles.
- Stronger meeting-transcript speaker/date metadata when available.

## Ranking / retrieval
- Passage ranking across POLICY / TRANSCRIPT / NEWS / COMMUNITY.
- Crime offense-group display names when `offense_code_group` is sparse.

## UI polish
- Final citation chip visual design and source-type legend.
- Collapsible multi-source groups.
- Final brand / visual redesign (deferred).

## Accessibility
- Additional screen-reader polish for streaming deltas.
- Limitation text visibility when many citation chips wrap.

## Mobile
- Conversation list gesture polish.
- Composer/keyboard avoidance tuning on iOS Safari.

## Performance / load
- Multi-worker soak and latency percentiles under production-like concurrent guests.
- Shared connection reuse for crime freshness + aggregate queries.

## Security / privacy
- Final review of operator audit retention windows.
- Optional redaction policy for admin interaction viewers.

## Operations
- Remote staging host provisioning (FQDN, TLS, staging DB).
- Automate process-supervisor restart verification on a real staging host.
- Complete DBA restore drill after operator grants are available.
- Migrate remaining offline ingest/RAG scripts from `google.generativeai` to `google-genai`.

## Release evaluation
- Full semantic holdout / external audit after staging host is complete.
- Design QA for the end-of-product visual pass.

## Deployment
- Public production deploy checklist (not authorized until explicitly approved).
- Production secret management path confirmation.

## Documentation
- Keep architecture + provenance + operations docs consolidated; avoid overlapping manuals.
