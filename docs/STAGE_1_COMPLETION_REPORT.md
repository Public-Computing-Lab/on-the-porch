# Stage 1 Completion Report

## Scope

Stage 1 freezes conversational behavior. It adds only a local regression corpus,
deterministic evaluator, runner, synthetic fixtures, tests, and documentation.
It does not change the application runtime.

## Protected baseline

- Baseline branch: `main`
- Baseline commit: `840ec54dd1265b4f2f42cc18b66b54cbf2a99b89`
- Historical infrastructure donor: `rebuild`
- Rebuild commit: `d11077d8d778d672bd4ef96d8957352a2596173a`
- Dev starting commit: `840ec54dd1265b4f2f42cc18b66b54cbf2a99b89`
- Temporary branch: `stage1/conversational-baseline`
- Dev integration: performed with a non-fast-forward merge; the authoritative
  final SHA is recorded in the Stage 1 operator report and Git history after the
  merge. A commit cannot embed its own final hash in its tree.

## Branch cleanup

The remote branch namespace was audited before development. The original `dev`
branch was an unrelated root commit
`5a09399974ad52c3e139443084d027e62ec21fce` and was not reachable from `main`
or `rebuild`. It was preserved with the annotated tag
`archive/remote-branch/dev-20260728`, then removed.

`dev` was recreated from the exact protected `main` commit and pushed. All other
obsolete remote feature, fix, redesign, refactor, and chore branches had no unique
commits outside `main` or `rebuild` and were removed without archival tags.

The final active remote branch set is:

- `main`
- `rebuild`
- `dev`

No tags were deleted. Local branches checked out by other worktrees were not
deleted or modified.

## Files added

- `tests/conversation_regression/__init__.py`
- `tests/conversation_regression/README.md`
- `tests/conversation_regression/scenario_schema.json`
- `tests/conversation_regression/scenarios.yaml`
- `tests/conversation_regression/evaluators.py`
- `tests/conversation_regression/reporting.py`
- `tests/conversation_regression/run_regression.py`
- `tests/conversation_regression/test_harness.py`
- `tests/conversation_regression/fixtures/baseline_responses.json`
- `tests/conversation_regression/reports/.gitignore`
- `docs/CONVERSATIONAL_BASELINE.md`
- `docs/STAGE_1_COMPLETION_REPORT.md`

No pre-existing source file was modified.

## Scenario corpus

- Total scenarios: **84**
- Multi-turn scenarios: **43**

Coverage tags overlap intentionally:

| Required coverage | Count |
|---|---:|
| Greetings | 5 |
| Events | 21 |
| Boston 311 | 15 |
| Crime and public safety | 13 |
| Policy, budgets, zoning, and development | 13 |
| Meetings, news, newsletters, and community concerns | 21 |
| Entities and organizations | 9 |
| Cross-domain synthesis | 7 |
| No-evidence and uncertainty | 13 |
| General conversational mechanics | 45 |

The corpus includes direct follow-ups, corrections, date-only changes,
geography-only changes, source-type changes, continuation, ordinal references,
pronouns, topic switches, topic returns, broad synthesis, source distinctions,
no-evidence source changes, disagreement, answer-preferred ambiguity, and
clarification-required ambiguity.

## Deterministic evaluator

The evaluator covers:

- required concepts using normalized aliases or regular expressions;
- forbidden concepts;
- answer-empty failures;
- required and forbidden source categories;
- source-presence requirements;
- correction and context metadata assertions;
- required and unnecessary clarification;
- uncertainty disclosure and overconfidence;
- repeated-answer similarity;
- repeated stable result IDs;
- internal SQL, route, tool, and vector-store terminology;
- per-turn dimension scores, critical failures, and scenario results;
- JSON and Markdown aggregation by domain, conversation category, and dimension.

The 18 dimensions are intent understanding, context retention, correction
handling, continuation handling, topic-switch handling, reference and pronoun
resolution, completeness, naturalness, resident-friendly language, grounding,
source relevance, source-type distinction, uncertainty and restraint,
unnecessary clarification, internal implementation leakage, repetition,
contradiction with earlier turns, and failure quality.

The optional qualitative evaluator is an interface only and is disabled by
default.

## Tests run

Passed:

```text
python3 tests/conversation_regression/run_regression.py validate
  84 scenarios validated; 43 multi-turn

python3 -m unittest tests/conversation_regression/test_harness.py
  13/13 passed

python3 tests/conversation_regression/run_regression.py fixture \
  --output-dir /private/tmp/on-the-porch-stage1-reports \
  --stem fixture-replay
  8/8 evaluated fixtures passed; 76 scenarios transparently skipped

.venv/bin/python -c <Draft 2020-12 schema validation>
  schema and corpus passed

PYTHONPYCACHEPREFIX=/private/tmp/on-the-porch-stage1-pycache \
  python3 -m compileall -q tests/conversation_regression
  passed

python3 scripts/test_911_routing.py
  5/5 passed
```

The same Stage 1 suite is run again after integration into `dev`.

## Existing tests not counted as passes

- `scripts/test_conversation_accuracy.py` and
  `scripts/test_followup_gate.py` import the full chatbot stack. The system
  Python 3.14 environment lacks repository dependencies; the existing virtual
  environment is Python 3.9 and cannot evaluate runtime annotations that require
  Python 3.10 or newer. No dependency installation or runtime modification was
  made for Stage 1.
- `scripts/test_crime_voice.py` completed 14 of 18 cases before the same
  interpreter/dependency mismatch reached an application import. The partial run
  is not reported as a pass.
- `scripts/test_execution_pipeline.py` completed 2 of 8 cases before the same
  mismatch. The partial run is not reported as a pass.
- `scripts/golden_fact_test.py` requires a configured database and vector store.
- `scripts/chat_regression_test.py`, `api/test_api_v2.py`, and the HTTP section of
  `scripts/run_ci_checks.sh` require a running application and may create users,
  conversations, and messages.
- ingestion, refresh, vector, production-readiness, and deployment scripts were
  excluded because they read or mutate external runtime state.
- The optional regression API mode was not run. It is loopback-only, explicitly
  gated, and disabled by default.

These exclusions are deliberate. No unexecuted live check is represented as
passing.

## Known limitations

- Synthetic fixture replay validates the harness, not all probabilistic answers
  produced by the baseline model.
- Deterministic concept checks provide a quality floor but cannot fully score
  naturalness, nuanced contradiction, or claim-level factual accuracy.
- Repetition checks are strongest when response captures contain stable IDs.
- State assertions require captured metadata.
- Fixture replay covers eight representative scenarios; uncaptured scenarios are
  listed as skipped unless `--require-all` is used.
- No production latency, database, vector-store, or DreamHost behavior was
  measured. Those concerns belong to later authorized stages.

## Safety confirmations

- No runtime application, prompt, routing, retrieval, SQL, API, frontend,
  authentication, persistence, or deployment file changed.
- `main` and `origin/main` were not modified.
- `rebuild` and `origin/rebuild` were not modified.
- DreamHost was not accessed or touched.
- No production database was accessed.
- No vector store was mutated.
- No paid or external model call was made.
- No production secret was read or exposed.

Stage 1 is complete after the documented branch integration and final verification.
Stage 2 has not begun.
