# Stage 4.5 Root-Cause Analysis

## Diagnosis

The local 311 response was not caused by an incorrect Git revert. Local `main` was exactly
`840ec54d...`, and protected conversational files were byte-identical to that commit.

The defect required a combination of conditions:

- the exact wording missed deterministic meeting-transcript recognition and depended on
  model routing;
- executor failures rendered a plan topic or intent directly, so an erroneous 311 plan
  could become a Boston 311 resident-facing failure;
- the local database had no identifiable City Council minutes and no transportation civic
  documents;
- the active local vector collection had only 35 chunks and no council-minute chunks;
- startup ignored configured isolated vector paths;
- production appears to have richer sources, but its exact deployed code and corpus remain
  unknown.

The wrong 311 response did not recur in controlled main/dev runs. Both routed to RAG and
used weak community evidence. This makes model nondeterminism a plausible trigger, while
the unsafe fallback mapping makes the observed wording fully explainable.

## Classification

Confirmed: `LOCAL_DATA_COVERAGE_FAILURE`, `LOCAL_VECTOR_INDEX_FAILURE`,
`LOCAL_ENVIRONMENT_MISMATCH`, `ROUTING_DEFECT`, `FAILURE_MESSAGE_MAPPING_DEFECT`,
`PRODUCTION_RESPONSE_GROUNDING_DEFECT`, and `PRODUCTION_CODE_UNKNOWN`.

Supported with medium confidence: `MODEL_NONDETERMINISM` and
`PRODUCTION_DATA_ADVANTAGE`.

Not confirmed: stale thread state, Stage 3 regression, Stage 4 regression, or code-revert
mismatch.

## Restoration assessment

1. Local main was the exact protected SHA: yes.
2. Protected files were byte-identical: yes.
3. It reproduced the production answer: no.
4. It reproduced the production document route: broadly yes, but with different evidence.
5. It retrieved five passages: no; it retrieved two.
6. It had the same source inventory: no.
7. The gap was runtime/data/vector restoration, not Git restoration.
8. Restored: source code at the protected commit.
9. Not restored: production environment, corpus, vector index, and deployed configuration.
10. "Local was not properly reverted" is inaccurate for code and partially accurate only
    if "reverted" was intended to include the entire runtime and data state.
