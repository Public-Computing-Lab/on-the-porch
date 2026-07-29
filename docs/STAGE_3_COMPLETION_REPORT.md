# Stage 3 Completion Report

## Delivered

Stage 3A adds one AST-based, bounded, read-only execution boundary for
model-generated civic SQL. Stage 3B adds persistent logical turn identity,
cross-worker reservation, exact replay, same-thread protection, state versioning,
atomic final persistence, one JSON/SSE lifecycle, and frontend transport replay.

The conversational core remains authoritative. No prompt, router, retrieval
ranking, model choice, history format, answer voice, worker setting, retry
policy, global deadline, semaphore, vector provider, or production setting was
changed.

## Verification

- Stage 3 commits:
  - `fdec97e7` — Contain model-generated civic SQL
  - `b7c9d95e` — Add persistent idempotent conversation turns
  - `18330beb` — Reuse logical turn IDs across browser transports
  - final verification commit — Verify and document Stage 3 reliability
- repository Python tests: 144 passed
- SQL/turn focused tests: 88 passed
- conversation harness/calibration: 35 passed
- safe routing/dialogue scripts: 67 passed
- frontend lifecycle assertions: 8 passed
- corpus validation: 84 scenarios, 43 multi-turn
- calibrated frozen-response quality gate: PASS
- full live comparison: 84 scenarios, 135 turns, 0 blocked
- lifecycle demo verification: PASS
- atomic persistence demo verification: PASS
- clean-schema migration verification: PASS
- JavaScript syntax, Python compilation, JSON validation, and whitespace: PASS

The final canary ran through Gunicorn on loopback port `8893` with
`PRODUCTION_MODE=false` and `demo_user@127.0.0.1/sentiment_demo`. It verified
new JSON execution, completed JSON and SSE replay, SSE disconnect followed by
same-ID JSON fallback, fingerprint conflict, ownership, one message pair,
same-thread exclusion, different-thread independence, atomic rollback, stale
state protection, and restart replay with zero load, generation, or persistence
callbacks.

## Safety

All live and migration tests used loopback services and local demo MySQL only.
DreamHost, production MySQL, production vector data, `main`, and `rebuild` were
not modified. Production still requires a dedicated read-only dynamic SQL
account before dynamic SQL can be enabled.

## Known Limitations

- A running Python thread cannot be forcibly killed after an SSE client
  disconnects; persistent MySQL ownership prevents duplicate execution and the
  finite generation continues to completion.
- Turn lease expiry makes an abandoned same-ID turn terminal rather than
  automatically rerunning it.
- Replay cleanup is documented but not scheduled in Stage 3.
- Two Tier 2 live quality scenarios remain fixture-dependent advisory cases.
- DreamHost runtime and production database configuration remain unaudited.
- Stage 4 deadlines, retries, concurrency controls, and runtime tuning were not
  started.
