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
event handling, then call the existing agent pipeline. The final answer, sources, state,
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

## 6. Target hybrid answering pipeline

1. Validate request, identity, ownership, idempotency, and one global deadline.
2. Apply deterministic emergency and fixed policy interception.
3. Load trusted MySQL conversation state and relevant recent turns.
4. Interpret relationship, topic, geography, time, filters, and entities with rules first.
5. Resolve trusted references; ask for clarification when required context is absent.
6. Use one bounded planner call only when rules cannot construct a safe plan.
7. Validate the plan against an allowlisted registry and typed input schemas.
8. Execute independent deterministic capabilities concurrently within the remaining budget.
9. Build one structured evidence bundle and assess freshness, coverage, and sufficiency.
10. Select an answer mode; synthesize at most once when the evidence benefits from it.
11. Validate claims and citations, or use a deterministic fallback.
12. Atomically persist and return the same final answer over JSON or SSE.

## 7. Rule-first versus planner-assisted interpretation

Rules handle emergencies, exact UI entities, clear follow-ups, explicit dates and locations,
supported canonical concepts, and common factual requests. The planner is allowed only for
ambiguous decomposition or multi-capability selection. It receives bounded state, may select
only registered tools, and cannot generate SQL, names, loops, state mutations, or retries.
Structured civic requests permit at most one planner call, one primary synthesis call, and
one repair call. The current 311 slice uses no planner for exact counts, clear issue/status/
top-street follow-ups, comparisons, category overviews, or other rule-resolved requests.
It uses the planner only for ambiguous compound 311 requests that rules cannot safely
decompose.

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
internal identifiers, and fit the remaining request budget. There is at most one bounded
repair attempt.

## 14. Deterministic answer validation

Validation checks answer mode, requested topic/entity/scope, claim-to-evidence support,
source type, citation existence, numeric consistency, evidence-state wording, internal-term
leaks, public-safety policy, and contradiction with trusted UI context. Failure selects the
best deterministic composer; it never starts an unbounded model loop.

## 15. Global 15-second deadline

One monotonic deadline covers validation, state, interpretation, tools, retrieval,
synthesis, validation, persistence, and SSE finalization. Initial internal ceilings are about
0.5 seconds for validation/state, 2.5 seconds for optional planning, 3 seconds for tools,
6 seconds for synthesis, 2 seconds for validation or one repair, and the remainder for
persistence. These are shares of one deadline, not independent allowances. Emergency,
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

Keep the Flask endpoints thin. Introduce one request orchestrator only when the first 311
slice is implemented. Give it one planner interface, one tool registry, one shared evidence
and sufficiency representation, and one composer/validator interface. Retain cohesive domain
capabilities for events, 311, crime, and RAG. Reuse existing state, idempotency, persistence,
freshness, request-budget, and provider infrastructure. Avoid per-tool modules, wrapper
chains, and parallel legacy implementations.

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

0. **Current event review:** consolidate event time semantics, isolate relevance exceptions,
   keep transport thin, expose structured event evidence, measure runtime, and retain the
   draft review gate.
1. **Shared kernel for one caller:** implement the global 15-second deadline, typed
   allowlisted plan, evidence/sufficiency types, answer-mode selection, model-call limits,
   deterministic fallback, and telemetry required by the first 311 slice.
2. **311 vertical slice:** add reviewed concepts for potholes, drainage/flooding, street
   conditions, trash, and streetlights; execute count/status/top-street/recent-example/
   comparison/freshness prepared tools in parallel; resolve or clarify street references;
   synthesize at most once; validate claims; never equate closed with repaired.
3. **Crime and public safety:** reuse the kernel for counts, categories, comparisons,
   freshness, coverage, cautious synthesis, and no categorical safety judgment.
4. **Events:** retain deterministic listing/detail behavior and use synthesis only for
   comparisons, recommendations grounded in explicit attributes, or thematic summaries.
5. **Meetings, news, and community evidence:** use bounded lexical/vector retrieval,
   source-type separation, citation validation, and the same global deadline.
6. **Cleanup and tuning:** after migrated paths have callers and regression coverage, retire
   proven-dead code, consolidate test helpers, simplify imports, and tune workers, pools,
   caches, and provider initialization using measured RSS.
