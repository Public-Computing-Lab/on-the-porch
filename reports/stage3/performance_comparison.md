# Stage 3 Performance Comparison

| Measure | Result |
|---|---:|
| SQL validation p50 / p95 | 0.95 / 1.26 ms |
| Turn reservation p50 / p95 | 5.71 / 7.87 ms |
| Final persistence transaction | 8.15 ms |
| Completed JSON replay p50 / p95 | 9.71 / 10.57 ms |
| Completed SSE replay | 8.35 ms |
| Status lookup p50 / p95 | 7.20 / 7.63 ms |

Full 135-turn live comparison:

| Version | p50 | p95 | max |
|---|---:|---:|---:|
| Stage 2 | 1780.8 ms | 3438.8 ms | 10978.4 ms |
| Stage 3 | 1678.1 ms | 2604.3 ms | 8025.0 ms |

Provider and local-machine variance prevent treating the apparent improvement as
an optimization claim. The relevant release conclusion is that Stage 3 adds no
material latency regression and that completed replay is substantially faster
than regeneration.
