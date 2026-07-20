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

The development corpus contains 100 cases in 25 paraphrase families. The sealed holdout contains
60 cases in 20 disjoint families. Holdout labels require an explicit runner flag and are never
loaded by production code. A provider holdout run is permitted only after one configuration
passes smoke and development gates and is frozen.

## Why the previous semantic plan was still too large

The first executable-plan experiment asked the model to reproduce a roughly 15 KB nullable
capability schema with exact implementation arguments. It failed primarily on schema and argument
precision. The first semantic-plan redesign removed executable capabilities and reduced the smoke
prompt to 3,524-3,727 bytes, but it still asked the model to propose evidence needs, relationship,
state transitions, filters, and other server-owned decisions. That plain-JSON smoke reached only
90.48% structural compliance and included an unsupported direct civic fact.

The current contract removes those remaining responsibilities. Function-mode prompts are
1,671-1,874 bytes on the 21-case smoke corpus; plain-JSON prompts are 2,999-3,202 bytes. All smoke
cases produce unique prompt hashes and the outbound privacy audit reports zero private-marker
failures.

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

The exact evaluated model IDs are:

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
trials, not retries. Smoke gates are:

- structural compliance among completed calls at least 99%;
- action accuracy at least 95%;
- domain and operation accuracy at least 95%;
- compiled tool-set accuracy at least 92%;
- unsupported direct civic facts exactly zero;
- fabricated trusted references exactly zero;
- unsafe semantic action rate no more than 0.5%;
- exactly one planner call, no civic tool execution, and no state mutation.

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
  --deadline-seconds 8 \
  --output /private/tmp/orchestrator-function-smoke.json
```

Repeated plain-JSON smoke uses the same command with `--output-mode plain`. Development and
holdout commands are intentionally omitted from the normal sequence until their preceding gates
pass. Raw response capture, when required for local diagnosis, must target `/private/tmp`.

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

## Function calling versus plain JSON

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
No encoding is selected or frozen.

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

No candidate and no encoding passed smoke. Therefore:

- the 100-case development evaluation was not run;
- no model, prompt, action encoding, parameters, timeout, or confidence threshold was frozen;
- the sealed 60-case holdout was not run or used for tuning;
- no model is selected for resident traffic;
- Phase 1 is not complete and Phase 2 cutover remains blocked.

The limiting factors are contract/model structural adherence and semantic safety, not provider
availability for `gemini-3.1-flash-lite`. `gemini-3.5-flash` also has a provider/operational health
limitation under the tested configuration. Safety gates were not reduced and no sentence-specific
rule was added.

## Runtime status and remaining work

`MODEL_LED_ORCHESTRATOR_MODE` remains off. The evaluator performs no runtime wiring, civic tool
execution, database access, state mutation, or resident-facing behavior change. Normal JSON/SSE
routes and the existing provider path are unchanged.

Before another selection run, the team must resolve the general structural and safety failure
classes without tuning to individual benchmark phrases, then repeat health and smoke from the
start. Phase 2 still requires a frozen model/encoding to pass development and one sealed holdout,
plus shadow parity, deadline, provider-fallback, transport, persistence, privacy, and concurrency
gates.
