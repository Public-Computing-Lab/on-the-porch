# Stage 4 Completion Report

Stage 4 adds a bounded execution shell around the existing On the Porch conversational
core. New turns receive one 25-second monotonic budget with a 2-second finalization reserve.
External calls use remaining-time caps; transient retries are centralized and limited to two
total attempts; LLM, embedding, heavy-RAG, dynamic-SQL, and enrichment work use independent
deadline-aware gates; overload and timeout outcomes remain terminal and replayable.

The implementation preserves Stage 3 client-turn identity, duplicate suppression, JSON/SSE
sharing, atomic persistence, ownership, state versioning, and AST SQL safety. Successful
hybrid evidence survives a secondary branch timeout; all-path failures use resident-safe
messages. No schema migration was needed.

Verification summary:

- complete Python suite: 223 passed;
- Stage 1/2.5 harness and calibration: 35 passed;
- calibrated baseline: 27/84, unchanged;
- Tier 1 hard gates: 9/9;
- new critical failures: 0;
- known defects worsened: 0;
- load concurrency: 1, 5, 10, 20, 40;
- worker/thread process models: 2x4, 3x4, 2x8;
- deterministic max latency: 73.0 ms under an 80 ms fixture budget;
- bounded max LLM/embedding/RAG activity: 2/2/3;
- unfinished calls after load: 0;
- permits released: yes.

The final fresh-process local demo canary completed a prepared-SQL JSON turn in 909 ms,
replayed it in 22 ms without expensive work, and completed a transcript-RAG SSE turn in
6.66 seconds with first output at 6.63 seconds. Both turns remained inside the 25.5-second budget and
persisted without duplicate message pairs.

The normal active local vector snapshot contains only two transcript chunks, matching the
documented pre-Stage-4 fixture deficiency. The same golden suite passes 4/4 against the
repository-provided demo archive in a temporary directory (175 transcript chunks) plus the
local `sentiment_demo` database. No threshold or production/local vector data was changed.

The final merge gate additionally runs frontend lifecycle tests, routing/dialogue scripts,
Stage 3 local demo-database lifecycle and persistence verification, a local JSON/SSE canary,
report reproducibility, JSON/YAML validation, compilation, and whitespace checks.

Stage 4 did not alter prompts, routing meaning, retrieval ranking, conversation history,
answer voice, production configuration, DreamHost, production data, `main`, or `rebuild`.
Production deployment still requires the runtime audit described in
`STAGE_4_PRODUCTION_CONFIGURATION.md`. Stage 5 was not started.
