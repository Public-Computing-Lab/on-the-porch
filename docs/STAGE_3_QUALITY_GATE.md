# Stage 3 Quality Gate

## Deterministic Gate

The frozen Stage 2 captured response set was reevaluated with the calibrated
Stage 2.5 gate after Stage 3A and after complete Stage 3:

- scenarios: 84
- multi-turn scenarios: 43
- passed: 27
- failed baseline scenarios: 57
- skipped: 0
- Tier 1 hard gates preserved: 9 of 9
- new protected critical failures: 0
- known Tier 1 defects worsened: 0
- result: PASS

This is the authoritative semantic regression check because Stage 3 changes the
execution envelope, not prompts, routing, retrieval, or answer composition.

## Full Live Comparison

A fresh run used the loopback API, local `sentiment_demo` database, local Chroma
snapshot, local embeddings, and configured provider for synthetic corpus
questions only.

| Metric | Stage 2 | Stage 3 |
|---|---:|---:|
| Scenarios | 84 | 84 |
| Turns | 135 | 135 |
| Blocked | 0 | 0 |
| Raw deterministic pass | 13 | 14 |
| Raw deterministic fail | 71 | 70 |
| Critical checks | 100 | 99 |
| p50 | 1780.8 ms | 1678.1 ms |
| p95 | 3438.8 ms | 2604.3 ms |
| max | 10978.4 ms | 8025.0 ms |

The first calibrated live comparison identified six scenario-level variations.
One limited rerun resolved four; a final limited rerun left two Tier 2 advisory
flags:

- `event-006`: the accessibility follow-up correctly stated that accessibility
  was unknown, but live retrieval attached community/transcript sources instead
  of the controlled event-listing fixture required by the evaluator.
- `uncertain-003`: the answer explicitly said real-time Red Line delay data could
  not be retrieved, but the deterministic phrase matcher did not recognize
  "couldn't pull up ... real-time delay information" as its expected evidence
  limitation wording.

`quality_gate.yaml` labels both scenarios Tier 2 and their live execution
advisory-only until controlled fixtures exist. Engineering review found no
Stage 3 causal path: no conversational prompt, route, retrieval rule, history
format, or synthesis code changed. No gate was weakened and no production rule
was added to target these phrases.

## Decision

Stage 3 passes the protected calibrated gate. The full live run is retained as
an advisory provider/data-variance characterization. There is no real protected
conversational regression, and the raw live comparison improved by one scenario
and one critical check.
