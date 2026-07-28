# Conversation Regression Harness

This directory freezes the conversational behavior of production baseline
`840ec54dd1265b4f2f42cc18b66b54cbf2a99b89` without freezing exact model prose.
It is a local, deterministic quality gate for corrections, follow-ups, source
handling, topic changes, uncertainty, and resident-facing language.

## Safe default

```bash
python3 tests/conversation_regression/run_regression.py
```

The default command only validates local files. It does not start the app, open a
database, mutate a vector index, use credentials, or call an external model.

## Files

- `scenario_schema.json` documents the authoring contract as JSON Schema.
- `scenarios.yaml` is JSON-compatible YAML so the standard library can load it.
- `evaluators.py` contains deterministic semantic and provenance checks.
- `reporting.py` emits machine-readable JSON and human-readable Markdown.
- `run_regression.py` provides validation, fixture, captured-report, and opt-in
  loopback API modes.
- `test_harness.py` tests the test system itself.
- `fixtures/baseline_responses.json` contains synthetic examples for offline replay.
- `reports/` ignores generated reports except for its `.gitignore`.

## Runner modes

Validate the corpus:

```bash
python3 tests/conversation_regression/run_regression.py validate
```

Replay the synthetic fixture subset:

```bash
python3 tests/conversation_regression/run_regression.py fixture \
  --output-dir /tmp/on-the-porch-regression
```

Evaluate previously captured responses:

```bash
python3 tests/conversation_regression/run_regression.py report \
  --responses /path/to/responses.json \
  --output-dir /tmp/on-the-porch-regression
```

The optional API mode accepts loopback URLs only and requires an explicit opt-in
plus caller-supplied local credentials:

```bash
python3 tests/conversation_regression/run_regression.py api \
  --allow-local-api \
  --base-url http://127.0.0.1:8888 \
  --bearer-token "$LOCAL_TOKEN" \
  --csrf-token "$LOCAL_CSRF" \
  --scenario-id event-001
```

API mode is not part of the default Stage 1 gate. Never point it at DreamHost or
another non-loopback host.

## Scenario authoring

Each scenario records:

- a unique ID, domain, conversation category, coverage tags, severity, and notes;
- initial conversational context;
- ordered user turns;
- a semantic behavior requirement for every assistant turn;
- correction, continuation, topic-change, clarification, and context flags;
- required and forbidden concepts;
- expected and forbidden source categories;
- source requirement level and acceptable evidence limitations;
- optional state assertions, uncertainty requirements, and repetition checks.

`expected_defaults` keeps the source file concise. The loader expands those
defaults before validation, so every evaluated turn has the complete contract.

Concepts use phrase groups or regular expressions. Prefer several stable aliases
that express the idea. Do not snapshot a complete answer or depend on one model's
punctuation and sentence order.

When adding a scenario:

1. Choose a new descriptive ID.
2. Add real conversational pressure, not a paraphrase of an existing case.
3. Use `context_required` only when a prior turn is necessary.
4. Mark explicit corrections and add a metadata assertion when state is testable.
5. Require sources when the answer makes civic factual claims.
6. Distinguish official data, official documents, journalism, transcripts,
   community material, organization pages, and event listings.
7. Add uncertainty requirements when absence, freshness, or real-time status
   matters.
8. Run validation and the harness unit tests.

## Deterministic scoring

Every applicable dimension receives:

- `0` - a critical requirement failed;
- `1` - all applicable non-critical checks failed;
- `2` - some applicable checks passed;
- `3` - reserved for future qualitative judgment of acceptable behavior;
- `4` - all applicable deterministic checks passed.

Dimensions without a deterministic check for that turn are reported as `null`.
The optional `QualitativeEvaluator` interface can later assign richer judgments,
but it is disabled and has no provider implementation in Stage 1.

Critical failures include fabricated or unsupported civic claims, losing an
explicit correction, treating testimony as official policy, exposing raw SQL or
internal tool language, repeating the same result after a continuation request,
answering the wrong active topic, and claiming certainty when evidence is absent.

## Response capture format

Fixture and captured-response files use:

```json
{
  "responses": {
    "scenario-id": [
      {
        "answer": "Resident-facing answer",
        "sources": [{"id": "stable-id", "category": "official_data"}],
        "result_ids": ["stable-result-id"],
        "metadata": {"conversation": {"correction": true}},
        "clarification": false
      }
    ]
  }
}
```

Missing scenarios are reported as skipped. Add `--require-all` when a complete
capture is mandatory.

## Pull-request use

Infrastructure changes that affect turn execution should run:

```bash
python3 tests/conversation_regression/run_regression.py validate
python3 -m unittest tests/conversation_regression/test_harness.py
python3 tests/conversation_regression/run_regression.py fixture \
  --output-dir /tmp/on-the-porch-regression
```

A future live baseline capture may be compared through `report` or explicitly
enabled loopback `api` mode. A pass means the tested semantic contract survived;
it does not prove every generated answer is ideal. Review critical and low-scoring
turns, not only the aggregate count.
