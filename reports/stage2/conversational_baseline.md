# Stage 2 Conversational Baseline

- Environment: **local demo**
- Scenarios attempted: **84**
- Turns attempted: **135**
- Deterministic scenarios passed: **13**
- Deterministic scenarios failed: **71**
- Blocked scenarios: **0**
- HTTP latency: p50 **1780.8 ms**, p90 **2880.4 ms**, p95 **3438.8 ms**, p99 **4344.9 ms**

## Latency by Mode

| Mode | Count | p50 ms | p90 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|
| advisory | 1 | 42.5 | 42.5 | 42.5 | 42.5 |
| chitchat | 4 | 24.2 | 26.0 | 26.1 | 26.2 |
| history | 14 | 1269.3 | 1771.9 | 2191.5 | 2767.9 |
| rag | 63 | 1838.4 | 2614.9 | 2953.7 | 6506.3 |
| sql | 53 | 2002.5 | 3268.2 | 3596.2 | 4253.0 |

## Blocked Scenarios

None.

Qualitative review is recorded separately as `ENGINEER_REVIEW_BY_CODEX`; no external judge model was used.

## Interpretation

This is a measurement of current behavior, not a requirement that the existing
chatbot pass every Stage 1 expectation. The largest measured weaknesses were
source relevance/type, missing concepts, uncertainty disclosure, and correction
metadata unavailable through the response capture. See
`engineer_review_by_codex.md` for the structured review and
`latency_baseline.md` for stage-level timings.
