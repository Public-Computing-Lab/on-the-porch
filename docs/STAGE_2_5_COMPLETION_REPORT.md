# Stage 2.5 Completion Report

## Scope

Stage 2.5 calibrates the frozen Stage 2 conversational baseline, classifies all
failures, establishes three scenario tiers and an executable future quality
gate, defines controlled fixture requirements, and prepares a read-only
DreamHost audit.

No runtime conversational semantics changed. No Stage 3 feature was started.

## Results

| Measure | Result |
|---|---:|
| Scenarios | 84 |
| Multi-turn scenarios | 43 |
| Captured turns | 135 |
| Original pass/fail | 13 / 71 |
| Original failed critical checks | 100 |
| Calibrated pass/fail | 27 / 57 |
| Calibrated failed critical checks | 86 |
| Unique scenarios reviewed by Codex | 66 |
| Tier 1 / Tier 2 / Tier 3 | 29 / 32 / 23 |
| Tier 1 hard / known / blocked | 9 / 5 / 15 |

Primary scenario classifications:

| Classification | Count |
|---|---:|
| Real conversational defect | 18 |
| Real grounding defect | 8 |
| Real retrieval defect | 6 |
| Real structured-data defect | 9 |
| Data coverage failure | 6 |
| Data freshness failure | 3 |
| Evaluator false positive | 7 |
| Scenario specification defect | 5 |
| Unsupported capability | 2 |
| Environment mismatch | 2 |
| Nondeterministic acceptable variation | 3 |
| Test harness defect | 2 |

## Artifacts

- `reports/stage2_5/failure_triage.*`;
- `reports/stage2_5/calibrated_baseline.*`;
- `reports/stage2_5/engineer_review_by_codex.*`;
- `tests/conversation_regression/quality_gate.yaml`;
- `tests/conversation_regression/stage2_5_decisions.json`;
- `scripts/build_stage2_5_reports.py`;
- Stage 2.5 calibration, fixture, audit, readiness, and completion documents.

## Evaluator Changes

Calibration is opt-in and versioned. It adds narrow semantic aliases,
punctuation and `&` normalization, source-category aliases, corrected raw-SQL
detection, evidence-limitation aliases, advisory handling for preferred source
categories, and explicit handling of capture metadata that was not observable.

No scenario expectation changed. The original Stage 2 evaluator output remains
preserved and exactly reproducible.

## Production Audit

DreamHost inspection is `BLOCKED_BY_ACCESS`. No production command was run, no
secret was requested or exposed, and no production data or service was
modified. A redaction-aware read-only operator checklist is provided.

## Verification

| Check | Result |
|---|---|
| Corpus and quality-gate validation | 84 scenarios; 43 multi-turn; tiers 29/32/23 |
| Stage 1, Stage 2, and Stage 2.5 unit tests | 56/56 passed |
| Fixture replay | 8/8 supplied passed; 76 explicitly skipped |
| Frozen captured-baseline comparison gate | Passed; 84 evaluated |
| 911 routing | 5/5 passed |
| Execution planning | 8/8 passed |
| Follow-up gate | 23/23 passed |
| Conversation accuracy | 13/13 passed |
| Crime voice | 18/18 passed |
| Golden facts | 3/4; prepared SQL passed, local vector snapshot remained 2 chunks versus minimum 50 |
| Report reproducibility | Passed |
| Python compilation | Passed |
| JSON-compatible YAML and report JSON parsing | Passed |
| Whitespace check | Passed |

The golden vector-readiness result is retained as a known fixture/data-coverage
failure and was not weakened or reported as passing.

## Stage Boundary

Stage 3 has not started. This stage adds no SQL sandbox, client turn ID,
reservation, replay, JSON/SSE execution unification, atomic persistence,
deadline, retry, concurrency, worker, ingestion, retrieval, prompt, routing, or
conversation-state change.

Final integration and post-merge verification are recorded by the Stage 2.5
merge commit and test log.
