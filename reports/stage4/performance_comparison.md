# Stage 4 Bounded vs Intentionally Unbounded

At concurrency 40, bounded mode capped LLM/embedding/RAG activity at 2/2/3, versus 8/6/11 without limits. Bounded mode rejects excess work predictably instead of allowing provider-call growth.
