# Model-Led Orchestrator Evaluation

This document records the implementation-specific evaluation procedure and results for the
model-led orchestrator contract in `docs/answering_architecture.md`. It does not authorize a
resident-traffic cutover.

## Scope and isolation

The evaluator loads only synthetic resident messages and bounded synthetic trusted state. It
builds a public semantic-planning prompt, makes exactly one planner call, validates the returned
action, and deterministically compiles valid semantic tasks into the existing abstract tool
catalog for scoring. It does not execute civic tools, open a database, mutate state, persist a
proposal, invoke answer synthesis, or call the legacy router.

Provider input excludes executable tool names, SQL, URLs, physical table or collection names,
source IDs, repository paths, infrastructure configuration, credentials, logs, private data,
production records, and real resident or session data. A fail-closed outbound audit checks those
boundaries before every provider request. Raw provider output and case-level decisions remain in
`/private/tmp` and are not committed.

The development corpus contains 100 cases in 25 paraphrase families. The accepted replacement
holdout contains 60 synthetic cases in 20 disjoint families. Holdout labels require an explicit
runner flag and are never loaded by production code. A provider holdout run is permitted only
after one configuration passes smoke and development gates and is frozen.

## Frozen configurations

Historical Phase 1 offline freeze:
`evaluation/model_led_orchestrator/frozen_configuration.json` (prompt v4; marked historical;
not authoritative for resident traffic).

Authoritative Phase 2 runtime freeze:
`evaluation/model_led_turn_service/authoritative_runtime_freeze.json`. It records the maintained
`google-genai` SDK, `gemini-3.1-flash-lite`, function calling, the current semantic protocol and
generalized planner prompt, compiler/normalization hashes, one-attempt/no-retry policy,
confidence threshold, planner timeout, development dataset, and fresh sealed holdout identifier.
`MODEL_LED_ORCHESTRATOR_MODE=authoritative` is the resident-runtime mode documented by that
freeze after Phase 2 cleanup.

The final repeated smoke run made 63 independent provider calls with 100% availability,
structure, compiled-tool accuracy, and guarded safety; action and guarded usefulness were 95.24%
and p95 was 0.868 seconds. The final development run made 100 calls with 99% availability, 98%
guarded strategy, 97% correct tool-or-targeted-clarification outcomes, 97.7% domain and compiled-
tool accuracy, 96.55% geography accuracy, 95.4% time accuracy, and 1.044-second p95. All unsafe-
tool, unsupported-fact, context-isolation, prompt-injection, and unnecessary-clarification counts
were zero.

The replacement sealed holdout made exactly 60 provider calls. Availability and completed
structure were 100%; guarded strategy and useful tool-or-targeted-clarification outcomes were
98.33%; compiled-tool accuracy among executed plans was 97.78%; guarded safety was 100%; and p95
was 0.957 seconds. Unsupported facts, unsafe tools, fabricated references, context-isolation
failures, prompt-injection failures, and unnecessary clarifications were all zero.

## Why the previous semantic plan was still too large

The first executable-plan experiment asked the model to reproduce a roughly 15 KB nullable
capability schema with exact implementation arguments. It failed primarily on schema and argument
precision. The first semantic-plan redesign removed executable capabilities and reduced the smoke
prompt to 3,524-3,727 bytes, but it still asked the model to propose evidence needs, relationship,
state transitions, filters, and other server-owned decisions. That plain-JSON smoke reached only
90.48% structural compliance and included an unsupported direct civic fact.

The current contract removes those remaining responsibilities. The final function-mode audited
payloads are about 4.3-4.5 KB and include the compact function schema, trusted policy, bounded
synthetic state, and one resident message. All cases produce unique payload hashes and the
outbound privacy audit reports zero private-marker failures. Expected actions, tools, and holdout
labels are local scorer inputs and are absent from provider requests.

## Three high-level actions

The model selects exactly one public action:

1. `respond_directly` for social conversation, capability descriptions, acknowledgements,
   formatting requests, or rephrasing a supplied trusted summary.
2. `ask_clarification` for missing subject, geography, time, source type, reference, or user
   meaning.
3. `gather_evidence` with one to three semantic tasks.

A semantic task contains only domain, operation, subject, semantic geography, semantic time, and
materially requested source scope. Local validation is authoritative in function and plain-JSON
modes. Direct civic facts are prohibited. A trusted-summary rephrase requires the exact server
token and may not introduce numeric markers or named entities absent from that summary.

## Deterministic ownership

The compiler, not the model, derives exact evidence requirements, registered tool requests,
canonical date boundaries, source-status checks, bounded arguments, and safe state effects. The
model cannot emit tool names, SQL, URLs, table names, Python functions, source IDs, arbitrary
filters, exact date boundaries, evidence-needs lists, relationship labels, or state proposals.

The compiler maps semantic combinations to allowlisted capabilities, for example:

- service requests plus count or group to `service_requests.aggregate`;
- event find or existence checks to `events.search`;
- crime comparison to `crime.aggregate`;
- policy explanation to bounded document search;
- freshness/status needs to source status checks.

Relationship and state effects are inferred from the selected action, trusted references actually
used, compiled tasks, and existing server-owned state. The compiler performs no phrase matching
and no civic execution.

## Maintained SDK evaluation path

Offline evaluation uses the maintained `google-genai` SDK behind one provider-neutral adapter.
It supports synchronous and asynchronous calls, one attempt, no retry, strict outer deadlines,
bounded SDK transport timeouts, and sanitized provider metadata. The resident runtime remains on
its existing provider path; these models and this adapter are not imported into the normal API.
The legacy `google.generativeai` runtime dependency should be removed in a later controlled
provider migration after parity and launch gates pass.

The exact evaluated stable model IDs included:

- `gemini-2.0-flash`
- `gemini-2.0-flash-lite`
- `gemini-2.5-flash`
- `gemini-2.5-flash-lite`
- `gemini-2.5-pro`
- `gemini-3.5-flash`
- `gemini-3.1-flash-lite`

Preview and `latest` aliases are prohibited.

## Metrics and gates

Provider availability, structural compliance, semantic correctness, and safety are reported
separately. A provider error is not classified as an unsafe semantic decision. Wilson 95%
confidence intervals are reported for availability and completed-response structure.

A candidate must first pass repeated operational health checks for plain text, small JSON,
high-level function selection, and compact gather-evidence shape:

- provider availability at least 98%;
- structural compliance among completed responses at least 99% after documented generic
  canonicalization;
- p95 latency compatible with a future 15-second, two-call lifecycle.

Every smoke case then receives at least three independent calls. These are independent evaluation
trials, not retries. The promotion gates are:

- structural compliance among completed calls at least 99%;
- unsupported direct civic facts exactly zero;
- fabricated trusted references exactly zero;
- unsafe tool execution exactly zero;
- exactly one planner call, no civic tool execution, and no state mutation.
- planner p95 no more than 2.5 seconds.

Raw action, task-field, and compiler scores remain visible as diagnostics. Full guarded strategy,
semantic field, compiled-tool, unnecessary-clarification, and safety gates are applied on the
100-case development corpus before freezing.

Only smoke-passing candidates may run development. Only a development-passing frozen
configuration may run the sealed holdout once.

## Reproduction commands

Repeated health:

```bash
python scripts/evaluate_model_led_orchestrator.py \
  --health-check --health-repetitions 5 \
  --model gemini-3.1-flash-lite --deadline-seconds 10
```

Repeated function smoke:

```bash
python scripts/evaluate_model_led_orchestrator.py \
  --dataset smoke --repetitions 3 \
  --model gemini-3.1-flash-lite --output-mode function \
  --deadline-seconds 3 \
  --output /private/tmp/orchestrator-function-smoke.json
```

Repeated plain-JSON smoke uses the same command with `--output-mode plain`. The accepted holdout
is consumed and must not be rerun. Raw response capture, when required for local diagnosis, must
target `/private/tmp` and must not be committed.

## July 20, 2026 operational health

Each model received five independent calls in each of four modes, for 20 health calls per model.
The generic plain-text canonicalizer ignores terminal punctuation in `READY` only; it does not
repair semantic output.

| Model | Calls | Provider availability | Completed structure | p50 | p95 | Eligible |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `gemini-3.5-flash` | 20 | 90.00% | 72.22% | 2.107s | 9.676s | No |
| `gemini-3.1-flash-lite` | 20 | 100.00% | 100.00% | 0.604s | 0.818s | Yes |

`gemini-3.5-flash` completed all plain-text and function-selection checks, but its five small-JSON
responses were structurally empty and two compact gather calls returned provider server errors.
Function-selection p95 was 5.458s and gather p95 was 9.872s. It failed availability, structural,
and latency health gates and therefore did not run the semantic smoke corpus.

`gemini-3.1-flash-lite` completed all 20 calls with 100% structure in every mode. Function
selection p95 was 0.802s and compact gather p95 was 0.728s. It was the only smoke-eligible model.

## Historical function calling versus plain JSON iteration

The eligible model received 63 independent calls per encoding: 21 cases times three trials.

| Metric | Function calling | Plain JSON |
| --- | ---: | ---: |
| Provider availability | 100.00% | 100.00% |
| Availability 95% CI | 94.25%-100% | 94.25%-100% |
| Completed structural compliance | 85.71% | 14.29% |
| Structural 95% CI | 75.03%-92.30% | 7.70%-24.97% |
| Action accuracy | 94.44% | 66.67% |
| Domain accuracy | 100.00% | 50.00% |
| Operation accuracy | 80.00% | 50.00% |
| Subject accuracy | 60.00% | 0.00% |
| Geography accuracy | 93.33% | 50.00% |
| Time accuracy | 93.33% | 50.00% |
| Source-scope accuracy | 100.00% | 50.00% |
| Compiled tool-set accuracy | 86.67% | 50.00% |
| Unsafe semantic action rate | 4.76% | 14.29% |
| Unsupported direct civic facts | 3 | 9 |
| Fabricated trusted references | 0 | 0 |
| Prompt-injection safety (end to end) | 100.00% | 0.00% |
| One-call compliance | 100.00% | 100.00% |
| p50 / p95 | 0.656s / 0.784s | 0.821s / 1.068s |
| Input / output tokens | 63,660 / 3,801 | 40,791 / 7,830 |

Function calling is materially better on structure, action selection, compilation, latency, and
output size, but it still fails mandatory structural, action, domain, operation, direct-fact, and
safety gates. Plain JSON is not viable under this contract because malformed output dominates.
At that iteration, no encoding was selected or frozen. The later guarded contract and
role-separated adapter results above supersede this failed comparison for selection purposes.

## General failure classes

Function-mode aggregate failures were: 12 incorrect subjects, nine wrong operations, six malformed
structures, three wrong actions, three incorrect geography scores, three incorrect time scores,
and three trusted-reference safety failures. The repeated pattern across all three trials indicates
contract/model behavior rather than transient provider availability.

Plain-JSON aggregate failures were 48 malformed structures, six trusted-reference safety failures,
three inappropriate direct responses, and three incorrect subjects. Raw sanitized responses were
used only for local diagnosis and remain outside the repository.

The same 63 independent provider outputs were rescored after two general evaluation corrections:
structured civic tasks now use source scope `ANY` unless the resident materially asks for a source
type, and non-gather turns no longer inflate task-field metrics. No provider response was changed,
repaired, or retried.

## Selection decision

`gemini-3.1-flash-lite` with function calling and the role-separated adapter is selected for the
guarded planner. It is the lowest-cost, lowest-latency available candidate that cleared repeated
smoke, development, and sealed-holdout gates. Larger or older candidates either failed operational
health, timed out too often, or failed guarded usefulness. Safety gates were not reduced and no
sentence-specific production rule was added.

The original sealed holdout was consumed and failed before the adapter correction. Aggregate
diagnosis showed that trusted policy, trusted state, and untrusted resident text had shared one
provider role. That infrastructure defect was corrected independently of holdout wording. A new
60-case corpus was independently authored, hashed, frozen after development passed, audited for
privacy, and then run exactly once. The replacement passed all established gates and is now also
consumed.

## Runtime status and remaining work

The offline evaluator remains isolated: it does not wire resident traffic, mutate production
state, or certify DreamHost deployment. Phase 2 authoritative resident traffic is documented in
`docs/answering_architecture.md` and frozen in
`evaluation/model_led_turn_service/authoritative_runtime_freeze.json`. Phase 3 domain migration
(crime, documents/RAG, legacy caller retirement) remains future work.
