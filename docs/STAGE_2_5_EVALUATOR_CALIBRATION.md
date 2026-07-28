# Stage 2.5 Evaluator Calibration

## Purpose

Stage 2.5 calibrates the deterministic conversation evaluator against the
frozen Stage 2 capture. It does not change chatbot prompts, routing, retrieval,
SQL, history, answer synthesis, frontend behavior, or production configuration.

The original evaluator remains the default. The calibrated profile is opt-in,
versioned as `CALIBRATION_PROFILE_VERSION = 1`, and can be selected with
`--calibrated` or by the quality-gate runner.

## Preserved Baseline

| Profile | Passed | Failed | Failed critical checks |
|---|---:|---:|---:|
| Original Stage 2 | 13 | 71 | 100 |
| Original evaluator reproduced | 13 | 71 | 100 |
| Calibrated evaluator | 27 | 57 | 86 |

The calibrated result is not a chatbot success rate. The failure triage keeps
real conversational, grounding, retrieval, and structured-data defects separate
from data limitations and evaluator defects.

## Changes

### Semantic phrase aliases

The calibrated profile recognizes narrow equivalence groups supported by the
captured answers:

- `good morning`, `hello`, `hi`, and `good to see you`;
- `most common`, `most frequent`, `top`, and `highest count`;
- common evidence-limitation wording such as `couldn't find`, `wasn't able to
  find`, `doesn't specify`, and `no exact information`;
- `event listing` and `calendar listing`;
- `official data`, `official records`, and `city data`;
- `not official`, `community evidence`, and `meeting notes`;
- direct correction acknowledgements such as `you're right` and `thanks for
  correcting`.

This corrected brittle wording failures including `greet-002`, `311-004`,
`event-013`, `safety-006`, and `safety-011`. The concepts themselves remain
required.

### Text normalization

Calibrated matching:

- case-folds text;
- normalizes punctuation and whitespace;
- normalizes curly apostrophes;
- treats `&` and `and` as equivalent.

This does not add fuzzy matching or broad stemming.

### Internal SQL detection

The original raw-SQL pattern could match ordinary prose such as `Real Estate
Update ... from`. The calibrated pattern only matches an answer that begins
like an SQL statement or SQL code block. Internal identifiers, prepared SQL
terms, vector-provider names, and tool language remain critical failures.

This correction affects `cross-006` and does not permit actual resident-facing
SQL.

### Source-category aliases

The calibrated profile maps narrow capture aliases to the authored taxonomy:

- city or official records to `official_data`;
- calendar events to `event_listing`;
- news to `local_journalism`;
- transcripts and meeting notes to `meeting_transcript`;
- community notes to `community_material`;
- nonprofit records to `organization`.

Required source categories remain critical. For `source_requirement:
preferred`, a missing preferred category becomes advisory rather than making an
otherwise acceptable answer fail. This separates source presence from source
relevance; it does not make an unsupported civic claim acceptable.

### Unobservable correction metadata

The Stage 2 capture did not persist the message-level conversation metadata
needed by several correction assertions. In calibrated mode, a metadata
assertion is marked unobservable only when its top-level field is absent from
the capture. If the field is present with the wrong value, the assertion still
fails.

This is a harness calibration, not proof that correction state was correct.
Those scenarios remain classified and fixture-blocked where appropriate.

## Scenario Expectations

No scenario expectation changed in Stage 2.5. No required concept, forbidden
concept, correction expectation, source requirement, or uncertainty requirement
was removed. Tier assignments and baseline dispositions are stored separately
in `quality_gate.yaml`.

## Traceability

`reports/stage2_5/failure_triage.json` retains, for every original failed
scenario:

- the original failed checks and critical checks;
- observed answers, sources, and execution modes;
- primary and secondary failure classifications;
- evidence, confidence, tier, and future disposition.

`scripts/build_stage2_5_reports.py --check` reproduces the original totals,
calibrated totals, triage, and engineering review from the frozen Stage 2
capture.

## Why The Gate Is Not Weaker

- Original results are never overwritten.
- Calibration is explicit and versioned.
- Required sources remain required.
- Actual internal leakage remains critical.
- Present-but-wrong metadata still fails.
- Real defects remain recorded even when a brittle phrase check is corrected.
- The future gate rejects new critical failures, hard-gate regressions, known
  defect worsening, unnecessary clarification growth, and configured score
  declines.
