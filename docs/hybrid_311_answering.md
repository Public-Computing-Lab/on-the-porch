# Hybrid 311 Answering

This slice provides a bounded, structured answering path for supported
Dorchester 311 questions. It uses the existing `service_requests_311` table as
the only source of civic facts. It does not generate SQL, use semantic search,
or write to a vector store during resident chat.

## Supported questions

- potholes and road-surface repair;
- drainage, catchbasins, flooding, and standing water;
- road and pavement condition;
- trash, illegal dumping, and garbage;
- streetlights;
- supported-category overviews;
- count, status, comparison, and street-coverage follow-ups.

The path is rule-first, with optional model assistance controlled by
`CIVIC_311_HYBRID_MODE`:

- `disabled`: deterministic planning and deterministic answers only;
- `auto` (default): planner and synthesis are enabled when the configured provider is
  available; either may be explicitly disabled with `CIVIC_311_PLANNER_ENABLED` or
  `CIVIC_311_SYNTHESIS_ENABLED`;
- `required`: production startup requires provider credentials.

Exact counts, clear top-street follow-ups, and clear status follow-ups use zero model calls.
Rule-resolved recent-report, street-status, comparison, and category-overview questions may
use one synthesis call when the structured evidence benefits from natural composition.
The planner runs only for ambiguous compound 311 requests that deterministic rules cannot
safely decompose. Model output is limited to a strict, allowlisted plan; it cannot supply
SQL, arbitrary tool names, source identifiers, or resident-facing facts.

## Guardrails

- A single 15-second deadline covers planning, freshness checks, database tools,
  optional synthesis, validation, and leaves a persistence reserve.
- All database parameters are bound values and every query is from a fixed
  tool registry.
- The source freshness and covered date range are included in evidence.
- Requests outside available coverage use the latest comparable usable period
  and say so rather than imply live data.
- A source that lacks street fields produces an explicit street-coverage
  limitation, never a zero count.
- Normal 311 status answers do not imply that a closed request proves a physical
  repair, and they do not use emergency wording.
- A request permits at most one planner call, one primary synthesis call, and one
  repair call. It performs zero embedding or vector-retrieval calls.
- Planner, synthesis, repair, validation, or provider failure falls back to a
  deterministic clarification or grounded answer within the same deadline.

## Conversation behavior

The structured result stores only a bounded, trusted 311 fact context in
`thread_state_json`: issue family, executed period, geography, source coverage,
and output names. Generic references such as "Are those reports still open?"
can use that context. A substantive new topic clears it; small talk does not.

## Telemetry

Logs contain only operational fields: planner source, planner/tool/synthesis/
validation/persistence/total timings, tool count, evidence status, answer mode,
fallback state, and timeout stage. Resident text, SQL, raw rows, and source keys
are excluded. Bounded planner diagnostics are retained in private user-message
metadata and are not returned in the resident-facing response.

## Local verification

The focused tests are:

```bash
.venv/bin/python -B scripts/test_hybrid_311_answering.py
.venv/bin/python -B scripts/test_hybrid_311_answering_golden.py
.venv/bin/python -B scripts/test_hybrid_311_model_paths.py
.venv/bin/python -B scripts/test_hybrid_311_answering_live.py
.venv/bin/python -B scripts/test_hybrid_311_model_live.py --expected-mode disabled
```

The focused and golden suites are deterministic. `test_hybrid_311_model_paths.py` uses
mocked providers to validate strict planner schema, synthesis, timeout, malformed-output,
unavailable-provider, claim-validation, one-repair, and model-call-limit behavior. These
mocked cases are not real-provider validation.

The live scripts refuse to run unless configured for the local Docker demo database. The
deterministic live path exercises JSON/SSE parity, trusted follow-ups, topic switching, an
explicit street-coverage limitation, emergency and event non-regressions, idempotency, and
zero LLM/embedding calls. A separate isolated run with `--expected-mode hybrid` exercises
the configured hosted provider for real planner and synthesis calls, still with zero
embedding calls and within the 15-second kernel deadline.

Current limitations remain: local demo data is stale and ends on June 27, 2026; matching
demo rows may lack usable street fields; the legacy `google.generativeai` SDK emits a
deprecation warning; local tests do not validate the production database, DreamHost proxy
behavior, or production provider latency.
