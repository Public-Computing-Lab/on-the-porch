# Stage 4 Deterministic Load Results

Seed: `20260731`. Providers: deterministic fake. Production access: no.

| Concurrency | Completed | Timed out | Overload | RPS | p50 | p95 | p99 | Max |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 112 | 3 | 0 | 91.859 | 6.359 | 19.431 | 70.417 | 71.837 |
| 5 | 112 | 3 | 0 | 389.471 | 6.35 | 20.58 | 70.372 | 72.058 |
| 10 | 108 | 3 | 4 | 579.487 | 8.066 | 26.382 | 71.07 | 71.302 |
| 20 | 97 | 3 | 15 | 729.554 | 8.156 | 36.061 | 72.512 | 72.978 |
| 40 | 96 | 3 | 16 | 732.764 | 8.13 | 34.629 | 70.885 | 70.957 |

The worker/thread matrix is a local process-model characterization, not a DreamHost recommendation. Limits remain process-local and must be multiplied by the deployed worker count during production review.
