# Stage 4 Load Testing

The deterministic load runner is `scripts/run_stage4_load.py`. It uses only fake providers
and local process resources; it cannot select a production URL. The seeded logical mix is
40% prepared SQL, 30% RAG, 15% hybrid, 10% conversational follow-up, and 5% dynamic SQL.
Each run also includes JSON and SSE delivery, five completed replays, ten duplicate
submissions, transient failures, slow model calls, slow embeddings, slow enrichment, and
capacity pressure.

The suite exercises concurrency 1, 5, 10, 20, and 40. A local process-model matrix compares
2 workers x 4 threads, 3 x 4, and 2 x 8. This matrix is deterministic fake-provider
characterization, not a Gunicorn or DreamHost recommendation.

Run:

```bash
python scripts/run_stage4_load.py
python scripts/build_stage4_reports.py --check
pytest -q tests/test_stage4_load_harness.py
```

The committed seed `20260731` observed a maximum level latency of 73.0 ms against an 80 ms
fixture budget. At concurrency 40, bounded mode capped simultaneous LLM/embedding/RAG work
at 2/2/3; the intentionally unbounded comparison reached 9/6/11. Bounded mode intentionally
returned controlled overload for excess work. All permits were released, unfinished calls
were zero, replay p50 was approximately 0.002 ms, and RSS growth was small and nonpersistent.

Measured output includes request outcomes, throughput, p50/p90/p95/p99/max, first SSE token,
queue percentiles, active maxima, retries, model/embedding/RAG/SQL counts, duplicate
suppression, replay, SQL validation, persistence, observable MySQL wait, RSS, threads, and
unfinished calls. The runner owns no server process and leaves no background provider work.

After deterministic load passes, `scripts/run_stage4_canary.py` performs exactly two new
local demo turns: one prepared-SQL JSON request and one RAG SSE request, followed by one
completed replay. It refuses nonloopback APIs, nonlocal MySQL, production mode, and any
database other than `sentiment_demo`. It is a small live-development-provider check, not a
high-concurrency benchmark.
