# Final hardening backlog

Single backlog for nonblocking polish after Phase 4. Do not scatter TODO files.

## Semantic edge cases
- Rare paraphrases that still ask for an unnecessary time period after a broad safety overview.
- Occasional planner `DOMAIN_ONLY` / `USER_MEANING` clarifications on broad “safety concerns” or “summarize recent crime” wording; clearer crime-category asks already succeed.
- Multi-entity names that partially overlap multiple profiles and need better disambiguation ranking.
- Cross-domain asks that currently clarify “which domain first” instead of combining evidence when both are clearly requested.
- Crime GROUP currently surfaces many `Unknown` offense labels when `offense_code_group` is sparse; reuse the richer prepared-SQL offense expression.

## UI polish
- Final citation chip visual design and accessibility contrast pass.
- Long multi-source answers: collapsible source groups and clearer source-type legend.
- Event sidebar spacing when Phase 3 source chips are present.
- Final visual redesign / brand pass (explicitly deferred past Phase 4).

## Ranking refinements
- Document passage ranking across POLICY / TRANSCRIPT / NEWS / COMMUNITY when several corpora return hits.
- Crime offense-group display names for uncommon BPD labels.

## Optional performance optimization
- Shared connection reuse for crime freshness + aggregate queries.
- Avoid loading retrieval stack for pure entity-profile turns beyond the current short-circuit.

## Source-quality improvements (Phase 5)
- Populate and maintain the local document/source index (empty index currently returns safe limitations).
- Expand verified civic entity profiles beyond the initial seed set.
- Stronger meeting-transcript metadata (speaker, meeting date) when ingestion provides it.
- Stale/invalid source exclusion during ingestion.

## Final accessibility work
- Source chip keyboard focus order and screen-reader labels.
- Ensure limitation text remains visible when citations wrap on narrow screens.

## Rollback / legacy retirement (post Phase 4)
- Delete deprecated `isolated_canary` and `shadow_or_offline_replay` mode names after no callers remain.
- Further delete or gate `unified_chatbot` / `intent_router` / phrase planners once MODE=off rollback is no longer required.
- Migrate remaining offline ingest scripts from `google.generativeai` to `google-genai` when Phase 5 touches them.

## Final release testing
- Full semantic holdout / external audit after Phase 5 ingestion and UI completion.
- Load and latency soak under multi-worker authoritative mode.
- Design QA for the end-of-product visual pass.
- Public deployment checklist (not started in Phase 4).
