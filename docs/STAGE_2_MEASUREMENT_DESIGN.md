# Stage 2 Measurement Design

## Purpose

Stage 2 measures the existing chatbot without changing conversational semantics.
It adds request-scoped traces around the current API, routing, SQL, retrieval,
model, synthesis, streaming, and persistence paths. It does not introduce
idempotency, deadlines, concurrency limits, SQL policy changes, ingestion
separation, prompt changes, or worker changes.

## Architecture

`on_the_porch/turn_observability.py` owns all trace state, redaction, timing,
model-call accounting, and sinks. The current trace is stored in a `ContextVar`.
`submit_with_trace()` copies that context separately into each executor task, so
parallel SQL and RAG branches contribute to the same thread-safe turn trace.

Instrumentation in runtime files consists only of:

- stage context managers around existing calls;
- counters after existing successful operations;
- retry counters immediately before existing retries;
- context propagation when existing executor or streaming threads are created;
- turn creation and completion at the JSON, SSE, and legacy API boundaries.

The instrumentation is fail-open. A disabled trace is `None`; recording
operations become no-ops. Sink exceptions are swallowed and never change a chat
response.

## Trace Schema

Each `turn_trace` record contains:

- schema version, trace ID, request ID, conversation ID, user category, and
  transport;
- UTC start and completion timestamps, monotonic duration, completion status,
  and HTTP status;
- selected route and execution mode;
- history count and prompt character approximation;
- SQL query and row counts plus non-reversible SQL fingerprints;
- model-call, retry, embedding, retrieved-chunk, source, and response-size
  counts;
- normalized error and timeout categories;
- SSE disconnect and fallback flags;
- stage timing records with parent and nested markers;
- model-call records with purpose, model, attempt, duration, success, normalized
  error category, and input/output character approximations.

No prompt, answer, raw SQL, retrieved passage, authorization value, cookie,
session token, API key, password, database URL, or email is part of the normal
trace schema.

## Stage Definitions

The runtime records applicable stages from this set:

- `authentication`;
- `ownership_verification`;
- `history_loading`;
- `thread_state_loading`;
- `follow_up_gate`;
- `routing`;
- `planning`;
- `plan_execution`;
- `table_selection`;
- `sql_generation`;
- `sql_execution`;
- `sql_repair`;
- `embedding`;
- `vector_retrieval`;
- `bm25_retrieval`;
- `reranking_fusion`;
- `rag_synthesis`;
- `sql_synthesis`;
- `hybrid_synthesis`;
- `answer_synthesis`;
- `answer_validation`;
- `boston_gov_enrichment`;
- `turn_execution`;
- `message_persistence`;
- `thread_state_persistence`;
- `interaction_logging`;
- `streaming_duration`.

Nested stages explicitly carry `nested: true` and a parent name. Aggregation
reports each stage independently and does not sum nested durations into a
synthetic total. Total request duration is measured separately.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TURN_OBSERVABILITY_ENABLED` | `false` | Enables sampled turn traces. |
| `TURN_OBSERVABILITY_SINK` | `stdout` | `stdout`, `jsonl`, or `none`. |
| `TURN_OBSERVABILITY_SAMPLE_RATE` | `1.0` | Deterministic trace sampling from 0 to 1. |
| `TURN_OBSERVABILITY_LOG_TEXT` | `false` | Allows approved synthetic scenario IDs for local evaluation. It does not enable prompt or answer logging. |
| `TURN_OBSERVABILITY_OUTPUT_PATH` | `logs/turn_observability.jsonl` | Ignored local JSONL path. |

Observability remains disabled unless explicitly enabled. JSONL output is under
the ignored `logs/` directory by default.

## Privacy

The trace API exposes a fixed allowlist of turn-level fields. Recursive redaction
removes authorization values, cookies, CSRF and session tokens, API keys,
passwords, secrets, database URLs, sensitive query parameters, and emails.
Generated SQL is represented only by a SHA-256-derived fingerprint after string
literals are replaced.

The local Stage 2 baseline stores the synthetic Stage 1 resident messages and
assistant answers in sanitized reports because those fixtures are approved test
content. It never stores guest tokens or CSRF values.

## Offline Analysis

Run:

```text
python3 scripts/analyze_turn_traces.py logs/turn_observability.jsonl \
  --json-output reports/stage2/latency_baseline.json \
  --markdown-output reports/stage2/latency_baseline.md
```

The analyzer skips malformed and unrelated lines deterministically and reports
turn counts, success/failure/timeout rates, p50/p90/p95/p99 latency, route and
mode distributions, model latency and calls per turn, retries per turn, stage
distributions, failure categories, slowest scenarios, and SSE
disconnect/fallback counts.

## What This Does Not Change

The instrumentation does not modify prompts, routing order, history formatting,
retrieval rank or thresholds, SQL generation or validation, model selection,
retry limits, answer formatting, persistence ordering, response schemas, or
frontend behavior. Stage 3 execution controls have not been added.
