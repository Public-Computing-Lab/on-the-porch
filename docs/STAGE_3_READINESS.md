# Stage 3 Readiness

## Decision

Local readiness: **ready**.

Production audit: **partially blocked by access**.

Stage 3 has not started. Stage 2.5 contains no Stage 3 runtime implementation.

## Trusted Quality Gate

The 84 scenarios are assigned exactly once:

- Tier 1 conversational invariants: 29;
- Tier 2 data-dependent product scenarios: 32;
- Tier 3 exploratory quality scenarios: 23.

Tier 1 dispositions:

- hard-gate passing baseline: 9;
- known baseline defects: 5;
- blocked by fixture or environment: 15.

Future changes must produce zero new critical failures, preserve every hard
gate, avoid worsening known defects, and remain within configured source
relevance, naturalness, resident-language, domain, and clarification
tolerances.

## Baseline

- Raw frozen Stage 2: 13/84 passed, 71 failed, 100 failed critical checks.
- Calibrated deterministic: 27/84 passed, 57 failed, 86 failed critical checks.
- Every original failed scenario and critical check has a traceable
  classification.
- `ENGINEER_REVIEW_BY_CODEX` covers all 43 multi-turn and all 57
  critical-failure scenarios, 66 unique scenarios total.

## Known Defects

- `event-010`: unanchored relative date handling;
- `community-006`: repeated continuation material;
- `entity-006`: confident expansion of an ambiguous acronym;
- `cross-006`: failure to return to the earlier cleanup result;
- `uncertain-005`: near-me request answered without location context.

These are not converted to passing tests. They may remain temporarily but may
not worsen.

## Remaining Limitations

- Controlled Tier 2 fixture inventory does not yet exist.
- Stage 2 capture omitted some correction metadata.
- Preferred-source presence is advisory; source relevance still needs
  structured review.
- Deterministic phrase checks cannot fully judge nuanced synthesis.
- Production process, proxy, database, cache, vector, model, and schedule facts
  are `BLOCKED_BY_ACCESS`.

## Conversational Components To Protect

- raw conversation history and natural follow-up interpretation;
- explicit correction, date, geography, source-type, and topic-switch handling;
- willingness to answer clear resident questions without generic clarification;
- hybrid structured-data and community-context synthesis;
- official versus journalism versus community source distinction;
- evidence restraint and resident-friendly failure language;
- current answer voice and response schema.

## Required Stage 3 Checks

1. Run schema, harness, observability, calibration, tier, triage, and report
   reproducibility tests.
2. Run all Tier 1 hard gates with zero regressions.
3. Compare with the committed calibrated baseline.
4. Produce zero new critical failures.
5. Show no worsening of known defects.
6. Keep naturalness, resident-friendly language, source relevance, domain
   averages, and unnecessary clarification within configured tolerance.
7. Treat Tier 2 as advisory until controlled fixtures exist.
8. Include structured Tier 3 review for broad synthesis changes.

## Stage 3 Priorities

1. Dynamic SQL safety.
2. Client turn identity.
3. Idempotent reservation and replay.
4. Unified JSON/SSE turn execution.
5. Atomic conversation and state persistence.
6. Global request deadline in a later stage.
7. Bounded retries and concurrency in a later stage.

No priority above is implemented by Stage 2.5.

## Status

`STAGE 2.5 COMPLETE — PRODUCTION AUDIT PARTIALLY BLOCKED, LOCAL STAGE 3 READY`
