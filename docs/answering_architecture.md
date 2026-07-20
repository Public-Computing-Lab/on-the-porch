# On the Porch Answering Architecture

This document is the authority for the current and target resident-answering architecture.
It describes intended boundaries rather than documenting every historical implementation.

## 1. Product mission

On the Porch is a trusted civic assistant for Dorchester residents. It translates natural
resident language into bounded, authoritative civic evidence and answers plainly with
source, geography, time, freshness, and uncertainty preserved. Models help interpret and
communicate; deterministic capabilities establish facts.

## 2. Current authoritative request pipeline

The modern endpoints are `POST /conversations/<thread_id>/messages` and its `/stream`
variant in `api/api_v2.py`. Both enforce identity, ownership, rate limits, CSRF, message
validation, and turn idempotency before generation. Emergency interception precedes
history-dependent processing. Normal requests load MySQL thread state and recent history,
resolve trusted UI context, classify relationship and dimensions, attempt deterministic
event handling, run the rule-first semantic dialogue controller, then call the selected
domain capability or existing agent pipeline. The final answer, sources, state,
cache metadata, and idempotency completion are committed in one MySQL transaction. JSON
and SSE serialize that persisted result; completed retries replay it.

## 3. Current responsibility map

| Responsibility | Current implementation |
| --- | --- |
| Transport, auth, ownership, persistence | `api/api_v2.py`, `api/security.py` |
| Turn idempotency | `api/turn_idempotency.py`, migration 011 |
| Rate limiting and shared cache | `api/rate_limit.py`, `api/mysql_cache.py` |
| Request deadline and metrics | `on_the_porch/request_budget.py`, `api/request_metrics.py` |
| Relationship, dimensions, state | `conversation_relationship.py`, `conversation_dimensions.py`, `conversation_state.py`, `conversation_transitions.py` |
| Semantic turn strategy | `semantic_dialogue_controller.py`; domain hints in `civic_311.py`, `event_query_context.py`, and `api/api_v2.py` |
| Emergency policy | `emergency_routing.py` |
| Event capability | `event_query_context.py`, `event_relevance.py`, `event_context.py` |
| Structured civic facts | `sql_chat/prepared_sql.py`; compatibility code in `sql_chat/app4.py` |
| Plan and execution | `execution_plan.py`, `plan_builder.py`, `plan_executor.py`, `unified_chatbot.py` |
| RAG and vector retrieval | `rag stuff/retrieval.py`, `rag stuff/vectordb_io.py` |
| Answering and validation | `answer_synthesizer.py`, `answer_validator.py`, `answer_voice.py`, `resident_response.py`, plus compatibility logic in `unified_chatbot.py` |
| Freshness and coverage | `source_freshness.py`, `civic_source_status` |
| Runtime safety | `api/launch_runtime.py`, `api/gunicorn.conf.py` |

## 4. Problems with the original architecture

The original model-led router could produce rich multi-source answers, but route selection,
generated SQL, repeated model calls, retries, and loosely bounded synthesis made accuracy,
latency, and failure behavior unpredictable. Conversation references, freshness, coverage,
and persistence were not consistently authoritative. A model could interpret well while
still selecting the wrong mechanism or overstating weak evidence.

## 5. Problems with the zero-LLM overcorrection

Rule-first routing, prepared queries, and deterministic composers restored speed and factual
control. Applied too broadly, however, keyword-to-one-query-to-template paths miss the
resident's real information need. Zero model calls is an optimization, not a product goal.
Complex but bounded evidence may deserve one synthesis call.

## 6. Final model-led answering pipeline

1. Validate request, identity, ownership, idempotency, and one global 15-second deadline.
2. Apply deterministic emergency, authentication, rate-limit, and fixed policy interception.
3. Load only bounded, server-owned MySQL conversation state and trusted reference tokens.
4. Normalize the current resident message without discarding its original form.
5. Reserve one `ORCHESTRATOR` call. The model chooses exactly `DIRECT_RESPONSE`,
   `ASK_CLARIFICATION`, or `EXECUTE_TOOLS` using the strict provider-neutral schema.
6. Reject unknown tools, incompatible domains, SQL, URLs, untrusted references, unrestricted
   filters, provider objects, recursive actions, factual tool-plan prose, and oversized state.
7. Execute one to four independent deterministic capabilities concurrently when tools were
   selected. The model never selects tables, vector collections, or arbitrary functions.
8. Convert tool results to one domain-neutral evidence bundle with stable evidence and source
   IDs, explicit scope, freshness, coverage, limitations, missing evidence, and partial failures.
9. If tools ran, reserve at most one `SYNTHESIS` call. Validate its factual claims, citations,
   source types, scope, safety language, and internal-term hygiene.
10. On any provider, synthesis, or validation failure, format completed deterministic evidence.
    There is no planner retry, synthesis retry, repair call, recursive loop, or third model call.
11. Atomically persist and return one authoritative result over JSON or SSE. Completed replay
    reserves zero model calls.

## 7. Model-led semantics and deterministic invariants

The orchestrator owns semantic interpretation for ordinary free-form resident messages:
goal, relationship, domains, trusted references, geography, semantic time, tool selection,
answer requirements, and a bounded state proposal. It receives no unrestricted history and
cannot establish civic facts. It uses semantic periods such as `CURRENT_WEEK`,
`NEXT_CALENDAR_WEEK`, `LAST_N_DAYS`, or `RETAIN_TRUSTED_PERIOD`; deterministic code calculates
dates and validates retained references.

Code remains authoritative for identity, thread ownership, emergency and fixed policy,
idempotency, deadlines, call accounting, canonical IDs, date arithmetic, tool registration,
domain compatibility, SQL prohibition, data access, source metadata, evidence sufficiency,
citations, answer validation, persistence, and replay. Exact trusted UI references remain
server-owned tokens. The model may rephrase a trusted summary but may not create a new civic
claim through `DIRECT_RESPONSE`.

Phase 1 introduces these contracts and offline evaluation only. Existing rule-first routing
remains authoritative until later phases meet documented parity gates. Evaluation examples
are not production phrase rules and expected labels are never loaded into production prompts.

### Semantic dialogue controller

The semantic dialogue controller runs after emergency interception and trusted thread-state
loading, but before a capability, SQL, event lookup, or legacy retrieval route is selected.
It returns one validated strategy from fixed enums: turn kind, domain, relationship,
response strategy, registered capability, clarification reason, and trusted reference. It
cannot return SQL, arguments, URLs, arbitrary tools, resident prose, or civic facts.

Reviewed rules have precedence for clear aggregate 311 requests, compatible pending
clarifications, exact event listing and correction forms, known trusted rephrases, zoning
location clarification, greetings/capability questions, and reviewed adversarial or
unbounded requests. A hosted model is called only when no reviewed domain hint applies and
the current message has semantic ambiguity such as correction, negation, rephrasing, or a
short context-dependent reference. The model receives only the current message and a
schema-validated bounded context: prior canonical turn/domain/capability, pending
clarification type, canonical parameters, and a server-generated previous-result summary.
Raw conversation history and prior assistant prose are not sent as authoritative state.

`SEMANTIC_DIALOGUE_CONTROLLER_LLM_ENABLED` controls that optional call. The controller uses
the existing configured Gemini provider, makes at most one call, retries zero times, and
caps the call at 2.5 seconds or the smaller remaining global deadline. Controller decisions
perform no embeddings or vector search. Malformed JSON, unknown enums, incompatible
domain/capability pairs, unavailable trusted references, low confidence, timeout, provider
failure, or provider absence fail closed to a deterministic clarification. Provider-disabled
mode still supports every rule-resolved 311 and event operation, known rephrase, zoning
clarification, and direct conversational response.

Model decisions never contain resident-facing prose. Direct responses are built by reviewed
server code and may contain only fixed clarification/conversational wording or a rephrasing
of a validated server-owned `EVENT_LIST` or `CIVIC_311_COUNT` summary. They cannot introduce
new counts, event names, locations, safety conditions, or zoning requirements. Dialogue
state is schema validated, JSON bounded to 4 KB, isolated by owned thread, and atomically
persisted with the turn.

Isolated local-demo validation exercised the configured hosted Gemini provider for an
ambiguous trusted event rephrase, a context-dependent contradictory event request, and a
trusted 311 result rephrase. Rule-only live cases covered negated event evidence, aggregate
and contextual 311, small talk, capability and zoning questions, prompt-injection and
unknown-tool requests, cross-thread/user isolation, JSON/SSE parity, and completed replay.
Separate provider-disabled and controlled-fault tests covered absence, timeout, malformed
output, unknown/mismatched capability, low confidence, and deadline pressure. These tests do
not certify production credentials, production data, or DreamHost latency.

## 8. Allowlisted tool registry

The registry describes each capability by stable name, typed input schema, output schema,
deadline class, source family, and permitted answer modes. Initial examples are event list
and detail operations; 311 count, status, top-street, examples, comparison, category, and
freshness operations; crime count, category, comparison, and freshness operations; and
bounded meeting, news, community, and transcript retrieval. Every implementation uses a
prepared query or bounded retrieval. There is no model-generated SQL or public vector write.

## 9. Civic concept ontology

Resident phrases map to a small reviewed set of canonical civic concepts, not directly to
database labels. The first 311 slice covers potholes/road surface, drainage/flooding, street
conditions, trash, and streetlights. Mappings carry aliases, supported source categories,
confidence, and clarification behavior. They must preserve semantic distinctions such as a
closed request versus verified physical repair. Uncertain concepts trigger a concise
clarification instead of a forced category.

## 10. Evidence-bundle schema

An evidence bundle contains the interpreted request; executed tool calls and statuses;
structured facts; source IDs and types; requested and actual geography/time coverage;
freshness timestamps; limitations; conflicts; unavailable optional evidence; and a stable
cache signature. Domain capabilities return structured facts, not resident prose. The bundle
is the sole factual input to composition and validation.

## 11. Evidence-sufficiency states

Plans and bundles distinguish `READY_TO_EXECUTE`, `NEEDS_CLARIFICATION`, `COMPLETE`,
`PARTIAL`, `STALE`, `OUTSIDE_COVERAGE`, `NO_MATCHING_RECORDS`,
`INSUFFICIENT_EVIDENCE`, `CONFLICTING_EVIDENCE`, `DEPENDENCY_UNAVAILABLE`, and
`UNSUPPORTED`. No matching records is not used when a source is stale, unavailable, or does
not cover the requested period.

## 12. Answer modes

- `POLICY_FIXED`: emergency, authentication, rate-limit, and fixed safety policy.
- `REPLAY`: a completed idempotent turn loaded from MySQL.
- `CLARIFICATION`: essential trusted context is missing.
- `DETERMINISTIC_DIRECT`: one exact authoritative fact.
- `DETERMINISTIC_ENRICHED`: multiple structured facts compose cleanly without a model.
- `EVIDENCE_BOUNDED_SYNTHESIS`: comparative, multi-part, or explanatory civic evidence.
- `RAG_SYNTHESIS`: meetings, news, transcripts, and other unstructured evidence.

## 13. Synthesis constraints

Synthesis receives only the resident question, compact trusted state, evidence bundle, and
answer policy. It may not add facts, silently broaden scope, treat community reports as
official records, convert closed status into proof of repair, or make categorical safety
judgments. It must cite factual claims, expose important freshness/coverage limits, avoid
internal identifiers, and fit the remaining request budget. There is no repair attempt. A
failed or invalid synthesis returns deterministic evidence formatting within the same deadline.

## 14. Deterministic answer validation

Validation checks answer mode, requested topic/entity/scope, claim-to-evidence support,
source type, citation existence, numeric consistency, evidence-state wording, internal-term
leaks, public-safety policy, and contradiction with trusted UI context. Failure selects the
best deterministic composer; it never starts an unbounded model loop.

## 15. Global 15-second deadline

One monotonic deadline covers validation, state, interpretation, tools, retrieval,
synthesis, validation, persistence, and SSE finalization. Initial internal ceilings are about
0.5 seconds for validation/state, up to 3 seconds for the orchestrator, up to 5 seconds for
concurrent tools, up to 5 seconds for synthesis and validation, and at least 1 second reserved
for persistence. These are shares of one deadline, not independent allowances. Emergency,
replay, and clarification should normally finish below 0.5 seconds; simple structured facts
below 3 seconds; enriched answers below 10 seconds; every controlled case below 15 seconds.

The 311 kernel enforces its own 15-second hard ceiling even when the wider API request budget
is configured above 15 seconds. Provider calls receive only the remaining kernel budget and
leave a persistence reserve.

## 16. Partial evidence and timeout behavior

Independent tools run concurrently and return completed evidence when an optional tool
times out. The sufficiency evaluator marks partial, stale, conflicting, or unavailable facts.
Composition states what is confirmed and what could not be checked. Planner, retrieval, or
synthesis timeout uses a grounded deterministic fallback before the deadline. A timed-out
task cannot mutate the returned response or conversation state afterward.

### Current 311 model policy

`CIVIC_311_HYBRID_MODE` controls only the structured 311 model assistance:

- `disabled` always uses the deterministic planner and deterministic composers;
- `auto` enables the configured planner and synthesis provider when credentials are
  available, while allowing either feature to be explicitly disabled;
- `required` requires provider credentials at production launch and enables available model
  assistance at runtime.

Exact counts remain zero-model. Clear status and top-street follow-ups also remain zero-model.
Rule-resolved recent-report, street-status, comparison, and category-overview plans may use
one evidence-bounded synthesis call. Ambiguous compound requests may use one planner call;
after clarification, the completed plan may use one synthesis call. Provider absence,
timeout, malformed planner output, failed claim validation, or insufficient remaining budget
returns a grounded deterministic clarification or answer.

The focused and golden suites validate deterministic behavior. Model-path tests use mocked
providers to exercise timeout, malformed output, unavailable-provider, validation, repair,
and call-limit branches. Isolated local demo tests additionally exercise the configured
hosted Gemini provider for real planning and synthesis; those results do not certify
production credentials, production data, or DreamHost latency.

## 17. Atomic persistence and replay

MySQL remains authoritative for users, sessions, threads, messages, semantic state, cache
metadata, freshness, rate-limit coordination, and turn idempotency. Persistence locks the
owned thread, verifies the expected state revision, inserts both messages and sources,
applies state/cache changes, and completes the turn in one transaction. Retries with the same
client turn ID replay the persisted assistant answer. JSON and SSE do not independently
regenerate completed turns.

## 18. Dorchester default geography

Generic civic questions default to all Dorchester. Explicit Dorchester subareas narrow the
scope. Trusted references may reuse a prior subarea only when the relationship and topic
support it. A new unrelated topic clears accidental location narrowing. Outside-Dorchester
scope requires an explicit request or a reviewed nearby-service policy. Virtual is a
modality, not proof of physical Dorchester location or community relevance.

## 19. Source freshness and coverage

Every civic tool reports its authoritative source, refresh/fetched time, source coverage,
requested coverage, and limitations using `civic_source_status` and domain metadata. Answer
wording distinguishes official data, journalism, and community sources. Stale or partial
coverage is visible to sufficiency and composition; it is never collapsed into zero records.

## 20. Minimal target module arrangement

Keep the Flask endpoints thin. `model_led_orchestrator.py` retains shared normalization and the
provider-neutral foundation. `semantic_planner.py` owns the compact non-executable first-call
contract and prompt. `semantic_plan_compiler.py` maps validated semantic tasks to the generic
typed registry in `civic_tool_catalog.py`. `model_call_ledger.py` owns the
one-orchestrator/one-synthesis invariant.
`civic_evidence.py` owns the domain-neutral tool-result envelope. Cohesive domain capabilities
for events, 311, crime, and document retrieval continue to execute deterministic operations.
Reuse existing state, idempotency, persistence, freshness, request-budget, and provider
infrastructure. Do not introduce per-question tools, generated SQL, wrapper chains, or a
second resident chat stack.

## 21. Existing modules to keep

Keep `security.py`, `turn_idempotency.py`, `mysql_cache.py`, `rate_limit.py`,
`request_budget.py`, `request_metrics.py`, relationship/dimension/state/transition modules,
`emergency_routing.py`, `source_freshness.py`, launch/runtime hardening, materialized views,
and the event capability. Keep prepared SQL as the deterministic structured-data base. Keep
deterministic answer composers as direct answers and deadline/model fallbacks.

## 22. Existing modules to consolidate later

After exercised replacements exist, consolidate orchestration spread across `api_v2.py`,
`unified_chatbot.py`, `plan_builder.py`, and `plan_executor.py`; structured behavior split
between prepared SQL and `sql_chat/app4.py`; overlapping event-list detectors; answer
correction and voice logic across synthesizer, validator, voice, resident response, and the
unified chatbot; and duplicated live-test server/session/database helpers. Isolate legacy
`/chat`, Passenger startup, obsolete Redis scripts/references, and dynamic SQL before removal.
Removal requires caller, import, runtime, and compatibility proof.

## 23. Explicit non-goals

This design does not replace MySQL, make the vector index authoritative, restore Redis,
restore dynamic SQL, allow public vector writes, build an autonomous agent loop, migrate all
domains at once, remove compatibility code speculatively, host a local generation model by
default, or certify worker counts without RSS and concurrency measurements.

## 24. Phased implementation roadmap

1. **Foundation and model adequacy:** freeze the strict decision, call-ledger, tool-catalog,
   evidence, normalization, evaluation, and off-by-default shadow contracts. Compare candidate
   models on development families and evaluate the frozen selection once on sealed holdout.
2. **Shadow integration and transport unification:** invoke the orchestrator diagnostically
   behind a short independent allowance while legacy decisions remain authoritative. Share one
   JSON/SSE orchestration function and prove no response, state, retrieval, or persistence drift.
3. **Domain cutover:** execute generic tools behind validated decisions, beginning with events
   and aggregate 311, then crime and documents. Require per-domain semantic, factual, citation,
   latency, provider-failure, replay, and concurrency parity before enabling resident traffic.
4. **Legacy retirement and tuning:** remove replaced semantic rules, planner/repair paths, and
   duplicated orchestration only after caller/import/runtime proof. Tune model choice, pools,
   caches, and worker topology from measured production-like behavior.

The July 19, 2026 executable-plan development gate did not identify an adequate candidate. The
follow-up design replaced executable tool requests with compact semantic tasks and a deterministic
compiler, reduced the prompt by about 76%, and moved offline evaluation to the maintained Gemini
SDK. The final 21-case Flash Lite plain-JSON smoke still reached only 95.24% provider success and
90.48% schema compliance, with one unsupported direct civic fact. Pro failed the required native
enum health check. No candidate became eligible for full semantic development evaluation, no
model was selected, and the sealed holdout was not run. Phase 2 cutover remains blocked until an
operational candidate clears smoke and development gates. Any future shadow diagnostic must be
post-response/offline or independently capped below one second so it cannot delay the
authoritative resident answer.

## 25. Semantic debt and deprecation map

| Current path | Future disposition | Replacement phase | Parity gate before removal |
| --- | --- | --- | --- |
| `semantic_dialogue_controller` semantic rules | Replace with orchestrator decision; keep emergency/fixed policy invariants | 2-4 | Shadow strategy/domain/tool agreement, safety gates, provider-failure fallback |
| `conversation_relationship` phrase rules | Replace semantic classification; keep ownership and bounded state loading | 2-4 | Relationship/context-isolation >= 99%, correction/refinement dialogue tests |
| `conversation_dimensions` phrase extraction | Replace with geography/time/filter/state proposal | 2-4 | Canonical geography/time >= 95%, no untrusted-state mutation |
| Event semantic regexes | Replace interpretation; expose event list/detail as `query_events` | 3 | Selected-entity, negation, date, listing/detail, JSON/SSE parity |
| 311 semantic rules and planner | Replace interpretation; expose prepared aggregates/search as generic tools | 3 | Existing 311 golden/model-path tests, factual/citation parity, no closed=resolved claim |
| Crime routing | Replace interpretation; expose `aggregate_crime` | 3 | Public-safety caution, freshness, coverage, count/comparison parity |
| `intent_router` and `plan_builder` | Remove after all public domains use validated orchestrator decisions | 4 | No remaining callers plus cross-domain and unknown-tool tests |
| `unified_chatbot` follow-up logic | Replace with bounded trusted state and relationship output | 2-4 | Multi-turn replay, correction, new-topic, entity/location isolation |
| Generic RAG routing | Replace selection; retain bounded document retrieval implementation | 3 | Corpus/source-type precision, citation support, no public vector writes |
| Post-answer classifier/regeneration | Replace with deterministic answer validator and fallback | 3-4 | Claim/source/citation/internal-term validation with zero repair calls |
| JSON/SSE orchestration duplication | Consolidate around one authoritative turn result | 2 | Byte-equivalent persisted answer/sources/state and idempotent replay |
| Planner retry, synthesis repair, regeneration calls | Remove; ledger permits only orchestrator and synthesis | 3-4 | Deterministic fallback quality and all deadline/provider-failure tests |
