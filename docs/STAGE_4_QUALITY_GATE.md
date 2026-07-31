# Stage 4 Quality Gate

Stage 4 protects the existing conversational core and changes only its execution shell.
The frozen calibrated gate remains authoritative:

- scenarios: 84;
- multi-turn scenarios: 43;
- calibrated result: 27/84, unchanged;
- Tier 1 hard gates: 9/9 preserved;
- new critical failures: 0;
- known Tier 1 defects worsened: 0.

The Stage 1/2.5 harness and calibration tests pass 35/35, and the complete Python suite
passes 223 tests. Existing routing and dialogue spot suites are run separately in the final
gate. Prompts, history semantics, route-selection semantics, retrieval ranking and
thresholds, source admission, and answer voice were not changed.

Infrastructure-only timeout fixtures verify deterministic degraded behavior without relying
on model wording. Full-success paths continue through the existing synthesis and formatting
code. Partial hybrid output is permitted only from successful canonical evidence and records
which branch was unavailable.

The committed report is `reports/stage4/quality_gate.json`. The local live-development
canary is directional because provider responses vary; any suspected conversational change
must be reviewed against the frozen gate rather than used to weaken it.

The structured golden facts pass against `sentiment_demo`. The active local vector snapshot
retains its known two-transcript limitation; the repository demo archive independently passes
the same transcript gate with 175 chunks. Stage 4 does not rewrite local vector fixtures.
