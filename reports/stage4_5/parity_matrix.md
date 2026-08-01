# Stage 4.5 Parity Matrix

| Case | Production | Local main | Dev before | Dev after |
|---|---|---|---|---|
| Fresh exact | Network error at about 25.4s | RAG, 2 weak community chunks | RAG, 2 weak community chunks | RAG, no qualifying council evidence, honest limitation |
| After 311 | Document answer, 5 passages | RAG, no 311 contamination | RAG, no 311 contamination | RAG, no 311 contamination |
| Explicit switch | Not repeated | RAG | RAG | RAG with official council evidence domain |
| No-evidence fixture | Not tested | Generic absence | Generic absence | Council-domain limitation; no 311 wording |
| Relevant fixture | Not tested | Unrelated chunks not filtered | Unrelated chunks not filtered | Official dated council chunk retained; unrelated housing removed |

Local main and dev-before behaved materially the same with identical data. They did not
reproduce production's five-passage result because the local corpus has no identifiable
City Council minute record or vector chunk. Dev-after restores semantic correctness without
claiming full data parity.
