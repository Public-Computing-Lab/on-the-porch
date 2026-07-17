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

The planner uses deterministic rules by default. `CIVIC_311_PLANNER_ENABLED`
and `CIVIC_311_SYNTHESIS_ENABLED` are opt-in and default to false. Even when
enabled, model output is limited to a strict, allowlisted plan; it cannot supply
SQL, tool names, source identifiers, or resident-facing facts.

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
.venv/bin/python -B scripts/test_hybrid_311_answering_live.py
```

The live script refuses to run unless configured for the local Docker demo
database. It exercises JSON and SSE parity, trusted follow-ups, topic switching,
an explicit street-coverage limitation, an active-emergency non-regression,
idempotency, and zero LLM/embedding calls in the default structured path.
