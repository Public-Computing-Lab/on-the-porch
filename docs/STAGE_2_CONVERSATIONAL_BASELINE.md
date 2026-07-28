# Stage 2 Conversational Baseline

## Safe Environment

The authorized live baseline uses only:

- local MySQL at `localhost:3306`;
- database `sentiment_demo`;
- the demo database user (credential omitted);
- the repository-local Chroma snapshot;
- local BGE query embeddings;
- a loopback API on unused port `8890` (`8888` was already occupied by a
  protected local runtime);
- development Gemini credentials already configured locally.

`PRODUCTION_MODE` is false. The run may create guest users, conversations,
messages, and interaction logs only in the demo database. It does not access
DreamHost, production MySQL, production users, or production vector storage.

The runner refuses non-loopback APIs, non-local MySQL hosts, databases other than
`sentiment_demo`, and production mode.

The final run used one Gunicorn `gthread` worker with four threads. The local
model and semantic cache were allowed to warm naturally. No cache was cleared.
The first local embedding/model load is therefore represented in the latency
tail.

## Sequence

The representative gate contains 25 scenarios covering:

- the required event correction and continuation sequence;
- the required public-safety source sequence;
- the required FY2027 budget sequence;
- the required Eversource sequence;
- greetings and history-only responses;
- prepared SQL facts;
- RAG and hybrid answers;
- 311 count and category questions;
- geography and topic changes;
- no-evidence behavior;
- source-type switching;
- references, corrections, and genuine clarification.

If that subset runs safely, the same runner attempts all 84 Stage 1 scenarios.
Scenario expectations are not modified from Stage 1.

## Capture

Each turn record contains the synthetic scenario and turn IDs, synthetic user
message, history supplied by previous turns, assistant answer, source objects,
HTTP status, JSON transport, available route/mode, total HTTP latency, error,
timeout, and a sanitized environment fingerprint. Guest tokens and cookies are
never written.

Reports are written under `reports/stage2/`:

- `conversational_baseline.json`;
- `conversational_baseline.md`;
- `scenario_results/`;
- `latency_baseline.json`;
- `latency_baseline.md`;
- `blocked_scenarios.md`.

The live API exposes broad source types (`sql`, `rag`, and `event`). The Stage 2
runner maps those existing source objects to the Stage 1 evaluation taxonomy
using only table and document metadata:

- event tables and `calendar_event` documents -> `event_listing`;
- other SQL tables -> `official_data`;
- policy documents -> `official_document`;
- transcript documents -> `meeting_transcript` and `community_material`.

This is an evaluation adapter only. It does not change resident responses or
runtime source objects.

## Evaluation

The Stage 1 deterministic evaluator remains the quality floor. Key multi-turn
scenarios also receive a structured `ENGINEER_REVIEW_BY_CODEX` using the existing
0-4 dimensions. No external judge model is used and no exact-output prose gate
is introduced.

## Results

The representative gate executed 25 scenarios and 44 turns. After a temporary
test-environment dependency mismatch was corrected, the final clean run
attempted all 84 scenarios and completed all 135 turns:

- HTTP successes: 135/135;
- blocked scenarios: 0;
- transport failures: 0;
- timeouts: 0;
- deterministic scenarios passed: 13/84;
- deterministic scenarios failed: 71/84;
- critical deterministic failures: 100.

The deterministic result is a baseline measurement, not a Stage 2 regression.
The largest failure families were source-category expectations (69 failed
checks), missing required concepts (36), uncertainty disclosure (15), missing
supporting sources (14), and correction metadata unavailable in the response
capture (10). Source-category mapping is necessarily heuristic, and the current
resident response does not expose all persisted conversation metadata, so these
counts must be interpreted with the per-scenario evidence.

The structured engineering review is in
`reports/stage2/engineer_review_by_codex.md`. Its main findings are:

- structured 311 facts and clean topic switches are the strongest paths;
- source relevance is weak for broad policy and organization questions;
- event result-set continuation and ordinal references are unreliable;
- the assistant is generally natural, restrained when evidence is absent, and
  avoids unnecessary clarification;
- some current paths still expose internal intent names in degraded answers.

## Latency and Work

The final trace set contains 135 valid records and no malformed lines:

- p50: 1,776.6 ms;
- p90: 2,875.6 ms;
- p95: 3,434.7 ms;
- p99: 4,340.9 ms;
- maximum: 10,975.2 ms.

The maximum was the first local model/embedding load. Subsequent embedding p50,
p90, and p95 were 22.9 ms, 34.2 ms, and 35.3 ms. Answer synthesis dominated
normal latency (p50 1,313.4 ms), followed by model-led routing, dynamic SQL table
selection/generation/repair, and history-answer generation. SQL execution was
4.4 ms p50 and vector lookup was 0.5 ms p50.

Execution-mode counts were 63 RAG, 53 SQL, 14 history-only, 4 chitchat, and 1
advisory. The run made 211 model calls, averaging 1.563 calls per turn. SQL
averaged 2.038 calls per turn, RAG 1.413, and history-only 1.0. Thirteen turns
made one dynamic-SQL retry; 122 turns made no retry. JSON transport was used for
the corpus, so observed SSE disconnects and fallbacks were both zero rather than
evidence about production SSE behavior. A separate one-turn local SSE smoke
completed in 2,095.5 ms with one model call and no disconnect or fallback; it is
excluded from the corpus metrics.

## Limitations

- The demo database and local vector snapshot are stale relative to production.
- The test used a single local worker and does not measure parallel-user load.
- Results include development Gemini network latency and local model warm-up.
- Source-category evaluation depends on the metadata adapter described above.
- Correction state is not fully visible in the resident response schema.
- DreamHost, its proxy, and the production database were not exercised.
