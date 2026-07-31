# Concurrency and Overload

Stage 4 uses independent process-local FIFO gates for LLM generation, embeddings, heavy
RAG, dynamic SQL, and Boston.gov enrichment. Development limits are 2, 2, 4, 2, and 2
respectively. Queue acquisition is limited to 750 ms and also cannot exceed the working
turn deadline.

Each gate records active and waiting counts, maximum active work, queue duration,
acquisition, rejection, and release. A ticket already waiting cannot be bypassed by a new
request. The queue is bounded by time rather than an unbounded task collection. Replay does
not enter expensive gates.

When capacity is unavailable, no provider call starts. The logical turn receives a terminal,
replayable `capacity_unavailable` result. JSON returns HTTP 503 with a safe `Retry-After`;
SSE produces the same normalized terminal outcome. Resident text does not expose gates,
workers, provider limits, or stack traces.

A noncancelable timed-out provider call retains its permit until the underlying future
actually completes. Different conversations may use available capacity concurrently, while
Stage 3 turn reservation and same-thread ownership remain separate and authoritative.

These limits are per process. Approximate host capacity is the per-process limit multiplied
by worker count, so production review must reconcile Gunicorn workers, MySQL pools, provider
quotas, and host memory. No cross-worker distributed limiter is claimed.
