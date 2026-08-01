# Stage 4.5 Design

Stage 4.5 adds a narrow evidence-domain contract for explicit official meeting-record
questions. It does not replace the model-led router generally.

The target flow is:

1. A rule recognizes explicit City Council minutes, council transcripts, or hearing
   transcripts with high confidence.
2. The execution plan carries `official_council_minutes` plus current-query concepts such
   as Dorchester and transportation.
3. Retrieval still queries the existing vector sources.
4. The executor admits only official council meeting records that satisfy the requested
   concepts.
5. If none qualify, synthesis returns a deterministic, current-domain limitation.
6. Failed unrelated branches are omitted when another branch has successful evidence.

The filter requires official provenance, a council marker, a meeting-record marker, and all
requested concepts. Transportation accepts transportation, transit, mobility, MBTA, street,
road, pedestrian, sidewalk, bicycle, bus, or rail language. This prevents an unrelated
housing item from being relabeled as a transportation project.

The current-turn failure mapper never renders raw plan intents or topics. Vector startup now
honors configured paths, allowing fair isolated local comparisons.
