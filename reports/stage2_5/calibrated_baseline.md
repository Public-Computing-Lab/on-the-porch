# Stage 2.5 Calibrated Baseline

The original Stage 2 result is preserved and reproduced separately from the calibrated profile.

## Scores

- Raw original: **13/84 passed**, **71 failed**, **100 critical checks**.
- Raw reproduction matches: **true**.
- Calibrated deterministic: **27/84 passed**, **57 failed**, **86 critical checks**.

The calibrated phrase-only score is not a chatbot success rate. Real defects, data limitations, evaluator limitations, and exploratory variation remain separated in the triage.

## Classification Totals

| Classification | Primary scenario count |
|---|---|
| DATA_COVERAGE_FAILURE | 6 |
| DATA_FRESHNESS_FAILURE | 3 |
| ENVIRONMENT_MISMATCH | 2 |
| EVALUATOR_FALSE_POSITIVE | 7 |
| NONDETERMINISTIC_ACCEPTABLE_VARIATION | 3 |
| REAL_CONVERSATIONAL_DEFECT | 18 |
| REAL_GROUNDING_DEFECT | 8 |
| REAL_RETRIEVAL_DEFECT | 6 |
| REAL_STRUCTURED_DATA_DEFECT | 9 |
| SCENARIO_SPECIFICATION_DEFECT | 5 |
| TEST_HARNESS_DEFECT | 2 |
| UNSUPPORTED_CAPABILITY | 2 |

## Test Tiers

| Tier | Scenario count |
|---|---|
| tier1 | 29 |
| tier2 | 32 |
| tier3 | 23 |

## Tier 1 Dispositions

| Disposition | Scenario count |
|---|---|
| hard_gate_passing_baseline | 9 |
| known_baseline_defect | 5 |
| blocked_by_fixture_or_environment | 15 |

## Gate Interpretation

- Tier 1 hard gates must continue to pass.
- Known baseline defects may not gain failed checks or new critical failures.
- Blocked Tier 1 scenarios become hard gates only after their named controlled fixtures exist.
- Tier 2 live results are advisory until controlled fixtures are available.
- Tier 3 uses deterministic safety checks plus structured review trends.
