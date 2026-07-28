# Stage 2 Completion Report

## Scope

Stage 2 adds non-semantic turn observability, deterministic offline aggregation,
a safe local conversational-baseline runner, and a read-only production audit
record. It does not change conversational behavior and does not begin Stage 3.

## Implementation

The isolated trace module is `on_the_porch/turn_observability.py`. Offline
analysis is implemented in `on_the_porch/turn_observability_analysis.py` and
`scripts/analyze_turn_traces.py`. The live baseline runner is
`scripts/run_stage2_baseline.py`.

Runtime instrumentation is limited to:

- `api/api_v2.py`;
- `on_the_porch/unified_chatbot.py`;
- `on_the_porch/plan_executor.py`;
- `on_the_porch/answer_synthesizer.py`;
- `on_the_porch/sql_chat/app4.py`;
- `on_the_porch/rag stuff/retrieval.py`.

## Verification

Completed offline:

- 18 focused observability tests and 3 baseline-adapter tests;
- Stage 1 validation of 84 scenarios and 43 multi-turn scenarios;
- 13 Stage 1 harness tests;
- Stage 1 fixture replay with 8/8 supplied fixtures passing and 76 explicitly
  skipped;
- 5/5 911 routing checks;
- 8/8 execution-plan checks;
- 23/23 follow-up-gate checks;
- 13/13 conversation-accuracy checks;
- 18/18 crime-voice checks;
- Python compilation of changed modules;
- whitespace validation.

All commands used the isolated Python 3.14 environment at
`/private/tmp/on-the-porch-stage2-venv`; bytecode writes to the worktree were
disabled.

| Check | Result | Approx. duration |
|---|---|---:|
| Stage 1 corpus validation | 84 scenarios, 43 multi-turn | <0.1s |
| Stage 1 + observability unit tests | 31/31 | 0.02s |
| Baseline source-adapter + observability tests | 21/21 | 0.01s |
| Stage 1 fixture replay | 8/8 supplied; 76 skipped | <0.1s |
| 911 routing | 5/5 | <0.1s |
| Execution plan | 8/8 | 1.4s |
| Follow-up gate | 23/23 | 2.4s |
| Conversation accuracy | 13/13 | 2.3s |
| Crime voice | 18/18 | 1.3s |
| Golden fact/readiness | 3/4; demo vector threshold failed | 1.3s |
| Representative live baseline | 25 scenarios, 44 turns | about 77s |
| Complete live baseline | 84 scenarios, 135 turns | about 250s |
| One-turn SSE smoke | 1/1 | 2.1s |

Completed against the confirmed local demo runtime:

- representative gate: 25 scenarios and 44 turns;
- complete baseline: 84 scenarios and 135 turns;
- 135/135 successful HTTP turns;
- no blocked scenarios, transport failures, or timeouts;
- 13 deterministic scenario passes and 71 measured failures;
- `ENGINEER_REVIEW_BY_CODEX` for the required event, safety, budget, and
  Eversource sequences plus a 311-to-events topic switch;
- p50/p90/p95/p99 trace latency of 1,776.6/2,875.6/3,434.7/4,340.9 ms;
- 211 model calls, 1.563 calls per turn;
- 13 dynamic-SQL retries and 122 turns without retries;
- 59 successful SQL statements, 88 embedding calls, 321 retrieved chunks, and
  206 returned sources.

A separate one-turn SSE smoke passed with SQL mode, one accounted model call,
2,095.5 ms trace duration, a completed `streaming_duration` stage, and no
disconnect or fallback. It is excluded from the 135-turn baseline distributions
so transport verification does not change the corpus metrics.

The primary measured latency cost is model synthesis. Local model warm-up caused
the 10,975.2 ms maximum. SQL execution and vector retrieval are comparatively
small. Detailed distributions are in `reports/stage2/latency_baseline.*`.

The existing read-only golden fact test passed all three prepared-SQL cases but
failed its vector-readiness check because the local demo snapshot contains 2
transcript chunks, below that test's configured minimum of 50. The result is
reported as 3/4, not as a pass. The test was not weakened and the vector snapshot
was not mutated.

The full API/auth smoke was not rerun because the complete 84-scenario baseline
had already exercised guest bootstrap, conversation creation, message
persistence, and model-backed chat. The separate chat regression script was run
only for the one SSE case described above.
Ingestion, refresh, vector-write, production-readiness, and deployment scripts
were excluded because Stage 2 prohibits those mutations.

## Production Audit

DreamHost access is `BLOCKED_BY_ACCESS`. Repository defaults are documented
separately from active production facts. No production state was accessed or
changed.

The active production worker/thread configuration, proxy behavior, data-service
configuration, grants, indexes, and schedules remain unconfirmed. Static
repository defaults are not represented as active production facts.

## Stage Boundary

Stage 3 has not started. This stage adds no client turn ID, idempotency,
reservation/replay, global request deadline, semaphore, overload control,
dynamic SQL sandbox, ingestion separation, worker tuning, response-schema
change, prompt change, or frontend behavior change.

Final branch integration and post-merge verification are recorded in the merge
commit and command log for this stage.
