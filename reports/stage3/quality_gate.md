# Stage 3 Quality Gate

- Authoritative calibrated gate: **PASS**
- Scenarios: **84**
- Multi-turn scenarios: **43**
- Tier 1 hard gates preserved: **9/9**
- New protected critical failures: **0**
- Known Tier 1 defects worsened: **0**

The full loopback live comparison completed 84 scenarios and 135 turns with no
blocked request. Raw deterministic results improved from 13 to 14 passing
scenarios and from 100 to 99 critical checks.

After two limited reruns, `event-006` and `uncertain-003` retained advisory live
differences. Both are Tier 2 fixture-dependent cases in
`quality_gate.yaml`. Engineering review found no Stage 3 causal change because
prompts, routing, retrieval, history, and synthesis were untouched. Details are
in `docs/STAGE_3_QUALITY_GATE.md`.
