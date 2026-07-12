# Local Production-Simulation Verification

This runbook verifies the `feature/stabilize-routing-latency` branch locally
before DreamHost access is available. It is not a deployment guide.

## 1. Production-like API Runtime

Use one of these local Gunicorn profiles.

Gemini embeddings or Qdrant/Chroma only:

```bash
PRODUCTION_MODE=true \
CACHE_BACKEND=mysql \
SEMANTIC_CACHE_ENABLED=true \
GUNICORN_WORKERS=2 \
GUNICORN_THREADS=4 \
GUNICORN_TIMEOUT=75 \
REQUEST_BUDGET_SECONDS=30 \
GEMINI_REQUEST_TIMEOUT=20 \
GEMINI_STREAM_TIMEOUT=30 \
GEMINI_MAX_ATTEMPTS=1 \
CHROMA_MAX_ATTEMPTS=1 \
MYSQL_POOL_SIZE=6 \
DYNAMIC_SQL_ENABLED=false \
CHAT_VECTOR_WRITES_ENABLED=false \
ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED=false \
./start_api.sh
```

Local embeddings loaded in the API process:

```bash
PRODUCTION_MODE=true \
CACHE_BACKEND=mysql \
SEMANTIC_CACHE_ENABLED=true \
EMBED_PROVIDER=local \
GUNICORN_WORKERS=1 \
GUNICORN_THREADS=4 \
GUNICORN_TIMEOUT=75 \
REQUEST_BUDGET_SECONDS=30 \
GEMINI_REQUEST_TIMEOUT=20 \
GEMINI_STREAM_TIMEOUT=30 \
GEMINI_MAX_ATTEMPTS=1 \
CHROMA_MAX_ATTEMPTS=1 \
MYSQL_POOL_SIZE=4 \
DYNAMIC_SQL_ENABLED=false \
CHAT_VECTOR_WRITES_ENABLED=false \
ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED=false \
./start_api.sh
```

Use `GUNICORN_WORKERS=1` when `EMBED_PROVIDER=local` because the local
embedding model is memory-heavy and is duplicated per worker. Use `2` workers
for Gemini embeddings if the local machine has enough memory and you want to
simulate the upper end of the DreamHost 8 GB profile.

The frontend timeout should remain:

```javascript
window.APP_CONFIG.chatTimeoutMs = 55000;
```

### macOS-only load-test note

When running local load tests on macOS with Homebrew Python, disable Gunicorn
worker recycling:

```bash
GUNICORN_MAX_REQUESTS=0 \
GUNICORN_MAX_REQUESTS_JITTER=0 \
./start_api.sh
```

Homebrew Python plus Gunicorn worker reforking can trigger macOS Objective-C
fork-safety crashes during local load tests. This note is for macOS-only
verification. Keep the Linux production and DreamHost recommendations
unchanged.

## 2. Environment Verification Checklist

Run:

```bash
python scripts/local_prod_sim.py env
```

Values exposed by `/health` come from the running API. Values not exposed by
`/health` are read from the same local environment/config visible to this
script, so run the command with the same env profile or put the profile in
`.env`.

Check that it reports:

- effective Gemini model names from `/health`
- `DYNAMIC_SQL_ENABLED=false`
- `CHAT_VECTOR_WRITES_ENABLED=false`
- Chroma or Qdrant active, matching `VECTOR_DB_PROVIDER`
- frontend timeout `55000`
- request budget `30`
- Gemini request timeout `20` for this test profile
- Gemini stream timeout `30`
- Gemini max attempts `1`
- Chroma max attempts `1`
- MySQL pool size `4-6`
- Gunicorn timeout `75`

## 3. Smoke Test

Assumes the API is already running with the production-like command above.

```bash
python scripts/local_prod_smoke.py
```

This sends:

- `GET /health`
- one prepared SQL query: `How many 311 requests were filed last week?`
- one RAG query: `Tell me about meetings in Dorchester`
- one hybrid public-safety query: `Is Dorchester safe right now?`

It fails on HTTP/server errors, missing modes, timeouts, or any request over
40 seconds.

## 4. Two-User Soak Test

Default duration is 10 minutes.

```bash
python scripts/local_two_user_soak.py --reuse-session
```

User A cycles through RAG/hybrid resident-voice and safety questions. User B
cycles through prepared SQL, events, 311, and crime-count questions.
Use `--reuse-session` for capacity testing so the harness behaves like a small
number of concurrent users instead of creating a fresh guest session per
request. This keeps rate-limit behavior observable without letting session
churn dominate the test.

The script records latency, error count, timeout count, route distribution, and
API RSS. It fails if any request exceeds 40 seconds, if network/server errors
occur, or if API RSS grows continuously by more than 300 MB.

Short local trial:

```bash
python scripts/local_two_user_soak.py --duration 120 --reuse-session
```

## 5. Five-User Burst Test

Default duration is 180 seconds.

```bash
python scripts/local_five_user_burst.py --reuse-session
```

This simulates five concurrent guest users with mixed SQL/RAG/hybrid queries.
The expected result is graceful degradation or bounded answers, not crashes,
network failures, or requests over 40 seconds.

Short local trial:

```bash
python scripts/local_five_user_burst.py --duration 60 --reuse-session
```

## 6. Forced Slow Gemini Test

No mock is added in this branch. To test budget behavior without architecture
changes, use a failing or slow upstream configuration and verify the public chat
path still returns within the 40 second test limit.

Fast-failure model check:

```bash
GEMINI_MODEL=models/not-a-real-model \
GEMINI_FAST_MODEL=models/not-a-real-model \
GEMINI_SUMMARY_MODEL=models/not-a-real-model \
GEMINI_MAX_ATTEMPTS=1 \
GEMINI_REQUEST_TIMEOUT=5 \
GEMINI_STREAM_TIMEOUT=10 \
REQUEST_BUDGET_SECONDS=30 \
./start_api.sh
```

Then run:

```bash
python scripts/local_prod_smoke.py
```

For a slow-network simulation, point Gemini traffic at an unreachable local
proxy and keep the request timeout small:

```bash
HTTPS_PROXY=http://127.0.0.1:9 \
GEMINI_MAX_ATTEMPTS=1 \
GEMINI_REQUEST_TIMEOUT=5 \
GEMINI_STREAM_TIMEOUT=10 \
REQUEST_BUDGET_SECONDS=30 \
./start_api.sh
```

The expected behavior is a bounded error/degraded answer under 40 seconds, not
a 3-minute hang.

## 7. Chroma Contention Test

First verify resident chat does not write to the vector store:

```bash
CHAT_VECTOR_WRITES_ENABLED=false python scripts/local_chroma_contention.py --duration 60 --reuse-session
```

If no ingestion is running, vector DB file mtimes should not change. If you want
to test read behavior while ingestion is active, start ingestion in another
terminal, then run:

```bash
python scripts/local_chroma_contention.py --duration 120 --reuse-session --allow-vector-mtime-change
```

Useful ingestion commands for local-only contention checks:

```bash
python "on_the_porch/rag stuff/ingest_community_notes.py"
python "on_the_porch/rag stuff/ingest_rss.py"
```

The pass condition is no chat crash, no network/server errors, graceful timeout
or fallback behavior, and no resident-chat-triggered vector writes when
`CHAT_VECTOR_WRITES_ENABLED=false`.

## 8. Report Format

All scripts print a human summary and a JSON summary with:

- total requests
- success count
- error count
- p50, p90, p99 latency
- max latency
- memory RSS min/max/growth
- route distribution
- timeout count
- failed query examples

## 9. Stabilization Branch Verification Summary

Completed on `feature/stabilize-routing-latency` using the local demo MySQL
database only. The database was the repository's Docker demo database
(`sentiment_demo`) and is separate from any production database.

Runtime profile used for the final local pass:

```bash
APP_ENV=production
PRODUCTION_MODE=true
CACHE_BACKEND=mysql
SEMANTIC_CACHE_ENABLED=true
GUNICORN_WORKERS=1
GUNICORN_THREADS=4
GUNICORN_TIMEOUT=75
GUNICORN_MAX_REQUESTS=0
GUNICORN_MAX_REQUESTS_JITTER=0
REQUEST_BUDGET_SECONDS=30
GEMINI_REQUEST_TIMEOUT=20
GEMINI_STREAM_TIMEOUT=30
GEMINI_MAX_ATTEMPTS=1
CHROMA_MAX_ATTEMPTS=1
MYSQL_POOL_SIZE=4
DYNAMIC_SQL_ENABLED=false
CHAT_VECTOR_WRITES_ENABLED=false
ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED=false
```

Cold-start behavior: the first smoke run loaded the local embedding model in the
API process and showed a large one-time RSS increase. The warm rerun did not
continue growing.

Warm steady-state results:

- Smoke: 3 total, 3 success, 0 application errors, 0 rate limits, 0 timeouts,
  0 transport errors. Successful latency p50/p90/p99/max was
  2457/3421/3638/3663 ms. Warm RSS was 1004.9 to 1005.2 MB, +0.2 MB.
- Two-user soak: 504 total, 504 success, 0 application errors, 0 rate limits,
  0 timeouts, 0 transport errors. Successful latency p50/p90/p99/max was
  1212/2572/3613/8771 ms. Warm RSS was 889.7 to 1008.3 MB with no continuous
  post-warm-up growth.
- Five-user burst: 486 total, 486 success, 0 application errors, 0 rate limits,
  0 timeouts, 0 transport errors. Successful latency p50/p90/p99/max was
  1530/2852/3772/6087 ms. Warm RSS was 911.6 to 915.3 MB, +3.3 MB.
- Chroma contention read check: 23 total, 23 success, 0 application errors,
  0 rate limits, 0 timeouts, 0 transport errors. Successful latency
  p50/p90/p99/max was 1986/3342/3831/3901 ms. Warm RSS was 708.4 to 709.8 MB,
  +1.4 MB. Vector DB mtime did not change, confirming resident chat did not
  write to the vector store during this check.

Production database contents, production credentials, production indexes,
DreamHost runtime behavior, and DreamHost network performance were not tested
in this local verification pass.
