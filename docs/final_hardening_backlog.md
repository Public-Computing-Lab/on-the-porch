# Final hardening backlog

Single backlog for nonblocking polish after Phase 3. Do not scatter TODO files.

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

## Ranking refinements
- Document passage ranking across POLICY / TRANSCRIPT / NEWS / COMMUNITY when several corpora return hits.
- Crime offense-group display names for uncommon BPD labels.

## Optional performance optimization
- Shared connection reuse for crime freshness + aggregate queries.
- Avoid loading retrieval stack for pure entity-profile turns beyond the current short-circuit.

## Source-quality improvements
- Expand verified civic entity profiles beyond the initial seed set.
- Stronger meeting-transcript metadata (speaker, meeting date) when ingestion provides it.
- Phase 5 ingestion improvements for stale/invalid source exclusion.

## Final accessibility work
- Source chip keyboard focus order and screen-reader labels.
- Ensure limitation text remains visible when citations wrap on narrow screens.

## Final release testing
- Full semantic holdout / external audit after Phases 4–5 are complete.
- Load and latency soak under multi-worker authoritative mode.
- Design QA for the end-of-product visual pass.
