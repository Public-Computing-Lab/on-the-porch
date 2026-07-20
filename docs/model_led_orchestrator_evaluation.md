# Model-Led Orchestrator Evaluation

This document records the implementation-specific evaluation procedure for the model-led
orchestrator contract defined in `docs/answering_architecture.md`. It does not authorize a
resident-traffic cutover.

## Scope and isolation

The runner loads synthetic, bounded trusted state and the current resident message, builds a
compact public semantic-planning prompt, makes exactly one planner call, validates the result,
and deterministically compiles valid semantic tasks into the existing abstract tool catalog for
scoring. It never executes civic tools, opens a database connection, mutates application state,
persists a proposal, invokes synthesis, or invokes the legacy router. Raw provider outputs,
exact prompts, and case-level decisions are excluded from committed files.

The external provider receives only the synthetic resident message, bounded public trusted state,
opaque trusted-reference tokens, semantic domain/goal/evidence enums, safety instructions, and a
compact response shape. It does not receive executable tool names or arguments. Expected labels
stay local. Repository paths, source code symbols, executable queries, physical data names, vector
collection names, infrastructure configuration, credentials, logs, private URLs, production
records, and real resident/session data are prohibited by a fail-closed audit.

The development file contains 100 cases in 25 paraphrase families. The sealed holdout contains
60 cases in 20 disjoint families. Holdout labels require an explicit runner flag and are not
loaded by production code or development tuning. The normal test suite checks holdout schema,
count, and family separation only.

## Metrics and release gates

Aggregate and per-category reports include strategy, relationship, domain, goal, evidence need,
subject, clarification, state/context, semantic geography/time, trusted-reference, compiled
generic-tool set, and direct-response fact safety where applicable. Reports also include schema failures,
unsafe decisions, direct-response fact-safety failures, context isolation, model calls per case,
provider failures/timeouts, p50/p90/p95 latency, and provider token metadata when available.
Trusted-summary rephrases must select the summary's server token and may not introduce a numeric,
date, time, percentage, or currency marker absent from that summary. This deterministic check is
conservative; semantic adequacy still requires the provider evaluation and review of failures.

The release gates are:

- strategy macro-accuracy at least 95%;
- domain accuracy at least 95%;
- goal/evidence-need accuracy at least 92%;
- compiled generic-tool accuracy at least 92%;
- semantic geography/time accuracy at least 95%;
- unsafe semantic decisions no more than 0.5%;
- unsupported direct-response factual claims exactly zero;
- context-isolation failures exactly zero;
- schema-failure rate no more than 1%;
- exactly one model call per evaluated case;
- p95 orchestrator latency compatible with the future 15-second end-to-end deadline.

A safety-gate failure cannot be averaged away. Provider failures consume the one call and are
not retried. The selected configuration is frozen from development results before one sealed
holdout run. The holdout is not a tuning set.

## Commands

Contract smoke, which is mandatory before development evaluation:

```bash
python scripts/evaluate_model_led_orchestrator.py \
  --dataset smoke --model gemini-2.5-flash-lite \
  --output-mode plain --deadline-seconds 12 \
  --output /private/tmp/orchestrator-semantic-smoke.json
```

Development comparison is permitted only after an operational candidate reaches at least 99%
provider success and schema compliance on the smoke set:

```bash
python scripts/evaluate_model_led_orchestrator.py \
  --dataset development --model gemini-2.5-flash-lite \
  --output-mode plain --deadline-seconds 12 \
  --output /private/tmp/orchestrator-flash-development.json
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

On July 19, 2026, the authorized evaluator transmitted the sanitized public contract for both
configured candidates. Protocol `public-civic-orchestrator-v7`, public schema version `1`, and
capability catalog `public-civic-capabilities-v1` were used for the final development comparison.
Generation used temperature `0`, `2048` maximum output tokens, JSON structured output, one call,
no retry, and an eight-second per-case deadline. The local confidence threshold remained `0.70`.

The final outbound development audit covered 100 payloads, all with unique hashes, from 15,070
through 15,309 bytes, with zero private-marker failures. The response generation configuration
hash was `40784b2fc89da45572005b84511acd9359f2934652e685ca22fe6c95d022a87f`.
The sealed dataset's payload construction was audited locally across 60 payloads from 15,073
through 15,332 bytes with zero private-marker failures. It had 59 unique hashes because two
Unicode variants normalize to the same semantic input. No holdout decision request was sent
because no development candidate passed selection gates.

### Development metrics

| Metric | Flash Lite | Pro |
| --- | ---: | ---: |
| Cases / model calls | 100 / 100 | 100 / 100 |
| Strategy macro-accuracy | 45.45% | 4.17% |
| Exact tool-set accuracy | 19% | 1% |
| Critical-argument accuracy | 17% | 1% |
| Geography/time accuracy where scored | 66.67% | unavailable |
| Schema-failure rate | 80% | 99% |
| Unsafe decision rate | 80% | 0% |
| Unsupported direct civic facts | 0 | 0 |
| Context isolation | 7% | 1% |
| Provider failures / timeouts | 0 / 0 | 99 / 0 |
| Latency p50 / p90 / p95 | 1.843s / 2.110s / 2.172s | 6.062s / 6.144s / 6.146s |
| Input / output tokens | 384,838 / 48,094 | 3,842 / 272 returned for one successful call |

All 99 Pro failures were provider `DeadlineExceeded` errors with `504` responses. Its only valid
decision was a direct-response greeting. Flash Lite returned provider responses for all cases,
but only direct conversation was consistently valid. General Flash failure classes included 39
invalid capability-specific argument sets, 12 tool decisions that also requested clarification,
10 unavailable trusted references, five missing crime grouping dimensions, four invalid state
filters, three invalid request identifiers, three oversized domain sets, and smaller retained
geography/time and topic-isolation violations. The dominant argument errors were missing
`issue_scope` for service-request aggregation and missing `corpora` for document search.

A separately labeled development perturbation changed the existing greeting to curly apostrophe
and em-dash punctuation without adding it to the prompt or dataset. Flash produced a valid,
correct one-call small-talk decision in 1.471 seconds; Pro returned the same provider `504` class
in 5.985 seconds. The local normalization equivalence test also covers straight/curly apostrophes,
curly quotes, em dashes, non-breaking spaces, and whitespace. No holdout Unicode result was read.

### Development-only protocol changes

Versions 1 through 7 addressed general contract classes only: explicit required nested objects,
canonical null optional objects, structured output, SDK-compatible string schema-version
constraint, and capability required-argument guidance. No development sentence or phrase was
added as a production recognizer. The final results remain far below the gates, so further prompt
tuning would risk fitting the benchmark rather than establishing model adequacy.

### Selection and holdout decision

No default model is selected. Flash Lite fails strategy, tool, geography/time, unsafe-decision,
context-isolation, and schema gates. Pro is operationally unavailable under the shared deadline
and fails the same aggregate gates. The sealed 60-case holdout was therefore not run, and no
holdout generalization metric is available. Phase 1 model adequacy is not complete.

The semantic-plan experiment below supersedes that proposed executable-schema redesign and moves
offline evaluation to the maintained SDK. The baseline remains useful for comparing contract
size, provider behavior, and token use. A new development set is not required because the sealed
holdout has not been viewed.

Exact current dollar cost is not stored in trusted repository configuration and is not estimated.
Flash used 100 requests and about 384.8k input plus 48.1k output tokens. Pro also consumed 100
requests, but 99 failed before token metadata was returned, so token-based relative cost is not
meaningful. Flash p95 leaves room within a future 15-second two-call lifecycle; Pro p95 does not
leave a reliable synthesis budget and is unavailable at the orchestrator stage.

## Compact semantic-plan redesign result

The executable-plan benchmark above is retained as the baseline failure. Its dominant failures
were contract complexity rather than evidence that Flash Lite could not understand resident
intent: the model had to reproduce a roughly 15 KB nullable capability schema with exact
implementation arguments. The redesigned first call emits only `DIRECT_RESPONSE`,
`ASK_CLARIFICATION`, or `GATHER_EVIDENCE`; gather plans contain one to three semantic tasks over
public domains, goals, subjects, evidence needs, geography, time, and source types. Local code
validates cross-field safety and compiles valid tasks to registered tools. The provider cannot
select tool names, SQL, URLs, source IDs, or executable arguments.

The new synthetic prompts are 3,524 through 3,727 bytes on the 21-case smoke set, a 75.66%
reduction from the prior 15,309-byte maximum. They contain no private-marker audit failures and
remain unique per case. Output stays at one planner call with no retry or repair call.

The evaluation adapter uses the maintained `google-genai` SDK and remains isolated from the
normal runtime. Minimal health checks showed `gemini-2.5-flash-lite` completing plain text, simple
JSON, and a small enum-native response. `gemini-2.5-pro` completed plain text and simple JSON but
returned an empty/invalid enum-native result, so it was not eligible for semantic-plan smoke or
development evaluation. This differs from the deprecated-SDK baseline, where 99 Pro requests
returned 504; the old 504 result must not be interpreted as a reasoning score.

Provider-native output rejected the initial compact schema with a 400 "too many schema states"
class. A simplified native schema was accepted, but the diverse smoke set reached only 42.86%
schema compliance in the useful completed run and later enum-constrained native trials introduced
timeouts. Plain JSON with strict local validation was materially more reliable. The final plain
JSON Flash Lite smoke used temperature 0, a 12-second per-case deadline, one attempt, and no tool
execution. Results were:

| Metric | Flash Lite plain JSON smoke |
| --- | ---: |
| Cases / model calls | 21 / 21 |
| Provider success | 95.24% |
| Schema compliance | 90.48% |
| Strategy macro-accuracy | 77.45% |
| Relationship accuracy | 61.90% |
| Domain / evidence-need accuracy | 80.95% / 80.95% |
| Goal accuracy | 66.67% |
| Semantic geography / time accuracy | 76.19% / 71.43% |
| Compiled generic-tool accuracy | 85.71% |
| Unsafe decision rate | 9.52% |
| Unsupported direct civic facts | 1 |
| Context isolation | 66.67% |
| Provider failures / timeouts | 1 / 0 |
| Latency p50 / p90 / p95 | 1.137s / 1.464s / 1.742s |
| Input / output tokens | 17,811 / 4,783 |

The three gate failures were general contract/provider classes: one transient provider 503 under
high demand; one otherwise plausible trusted-reference plan used `time.mode` instead of the
required `time.kind`; and one selected-entity correction produced an unsupported direct factual
confirmation rather than gathering evidence. The last failure is safety-significant because a
selected entity token identifies an entity but is not itself a trusted factual summary.

The required smoke gate is 99% provider success, 99% schema compliance, no unsupported factual
direct response, no fabricated context token, and exactly one call per case. Flash Lite did not
pass. No candidate is therefore eligible for the 100-case semantic development evaluation, no
semantic configuration is frozen, and the sealed holdout remains unrun. The development and
holdout expected labels were migrated locally to semantic plans and compile successfully, but no
provider result from the sealed holdout was requested or inspected.

Phase 1 remains incomplete. Before another candidate selection run, the project needs either a
provider/model combination that reliably honors the compact contract or a smaller alternative
contract that preserves the same safety invariants. Further phrase-specific prompt tuning is not
acceptable. Resident traffic remains on the existing authoritative paths and semantic shadow mode
remains off.

## Shadow status

`MODEL_LED_ORCHESTRATOR_MODE` defaults to `off`. Phase 1 provides an isolated shadow helper that
returns the existing authoritative object unchanged and caps diagnostic waiting to three seconds.
It performs no tool execution or state persistence. It is intentionally not imported by
`api/api_v2.py`: safe live wiring first requires Phase 2 to consolidate duplicated JSON/SSE
orchestration so one comparison cannot change response ordering, persistence, or latency.

Phase 2 must not make a resident response wait for this diagnostic. Prefer offline replay or
post-response sampled diagnostics. If inline shadow comparison is temporarily necessary, give it
an independent sub-second budget and discard late results without delaying the authoritative path.
