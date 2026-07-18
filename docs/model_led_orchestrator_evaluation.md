# Model-Led Orchestrator Evaluation

This document records the implementation-specific evaluation procedure for the model-led
orchestrator contract defined in `docs/answering_architecture.md`. It does not authorize a
resident-traffic cutover.

## Scope and isolation

The runner loads synthetic, bounded trusted state and the current resident message, builds the
same provider-neutral prompt used by the future orchestrator, makes exactly one orchestrator
call, validates the strict decision, and scores semantic output. It never executes civic tools,
opens a database connection, mutates application state, persists a proposal, or invokes the
legacy router. Raw provider outputs and case-level decisions are excluded from committed files.

The development file contains 100 cases in 25 paraphrase families. The sealed holdout contains
60 cases in 20 disjoint families. Holdout labels require an explicit runner flag and are not
loaded by production code or development tuning. The normal test suite checks holdout schema,
count, and family separation only.

## Metrics and release gates

Aggregate and per-category reports include strategy, relationship, domain, exact tool-set,
critical-argument, clarification, state/context, canonical geography/time, trusted-reference,
and direct-response-basis accuracy where applicable. Reports also include schema failures,
unsafe decisions, direct-response fact-safety failures, context isolation, model calls per case,
provider failures/timeouts, p50/p90/p95 latency, and provider token metadata when available.
Trusted-summary rephrases must select the summary's server token and may not introduce a numeric,
date, time, percentage, or currency marker absent from that summary. This deterministic check is
conservative; semantic adequacy still requires the provider evaluation and review of failures.

The release gates are:

- strategy macro-accuracy at least 95%;
- exact tool-set accuracy at least 92%;
- canonical geography/time accuracy at least 95%;
- unsafe or unregistered tool decisions no more than 0.5%;
- unsupported direct-response factual claims exactly zero;
- context isolation at least 99%;
- exactly one model call per evaluated case;
- p95 orchestrator latency compatible with the future 15-second end-to-end deadline.

A safety-gate failure cannot be averaged away. Provider failures consume the one call and are
not retried. The selected configuration is frozen from development results before one sealed
holdout run. The holdout is not a tuning set.

## Commands

Development comparison:

```bash
python scripts/evaluate_model_led_orchestrator.py \
  --dataset development --model gemini-2.5-flash-lite \
  --output /private/tmp/orchestrator-flash-development.json

python scripts/evaluate_model_led_orchestrator.py \
  --dataset development --model gemini-2.5-pro \
  --output /private/tmp/orchestrator-pro-development.json
```

After freezing the selected model, run the sealed holdout exactly once for that configuration:

```bash
python scripts/evaluate_model_led_orchestrator.py \
  --dataset holdout --allow-sealed-holdout --model SELECTED_MODEL \
  --output /private/tmp/orchestrator-selected-holdout.json
```

Outputs belong in `/private/tmp` or another non-repository location. API keys, raw prompts,
provider objects, and secrets must never be printed or committed.

## Phase 1 provider result status

On July 18, 2026, the local focused runner and dataset tests passed. A one-case Flash Lite
development smoke call was requested, but the execution environment rejected external
transmission because the prompt contains private repository tool-contract metadata. No request
was sent, no provider result was produced, and no workaround was attempted.

Consequently:

- Flash Lite development metrics: not measured;
- Pro development metrics: not measured;
- frozen configuration: none;
- sealed holdout: not opened through the provider runner;
- default orchestrator model recommendation: pending real-provider evaluation.

The contracts and draft integration may be reviewed independently, but model adequacy and the
release gates remain unverified until an explicitly authorized environment can transmit the
synthetic prompt and tool metadata to the configured provider.

## Shadow status

`MODEL_LED_ORCHESTRATOR_MODE` defaults to `off`. Phase 1 provides an isolated shadow helper that
returns the existing authoritative object unchanged and caps diagnostic waiting to three seconds.
It performs no tool execution or state persistence. It is intentionally not imported by
`api/api_v2.py`: safe live wiring first requires Phase 2 to consolidate duplicated JSON/SSE
orchestration so one comparison cannot change response ordering, persistence, or latency.
