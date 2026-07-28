# ENGINEER_REVIEW_BY_CODEX

No external judge model was used.

Multi-turn scenarios: **43**.
Critical-failure scenarios: **57**.
Unique scenarios reviewed: **66**.

## `greet-005` - Greeting then civic question

The captured multi-turn behavior passed every configured deterministic check.

- Primary classification: `NO_DETERMINISTIC_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |

## `event-001` - Week correction and continuation

The week correction was understood, but continuation did not retrieve another page and the ordinal reference resolved to an answer that contained no second event.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| continuation_handling | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| completeness | 2.5 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `event-002` - Change only the event date

The assistant retained the family-event topic but mapped Saturday to July 30, 2026, which was not a Saturday; correction metadata was also unavailable to the capture.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `event-003` - Ordinal event reference

The prior list contained a second event and its time, but the follow-up lost that identity and exposed event_by_name.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `event-004` - Change only event geography

The assistant recognized Fields Corner as a location refinement but reused Ashmont results instead of performing the required fresh lookup.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `event-006` - Event accessibility

Retrieval substituted an unrelated business expo for the named resource fair, so the accessibility follow-up stayed coherent with the wrong event.

- Primary classification: `REAL_RETRIEVAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_relevance | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| uncertainty_and_restraint | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |

## `event-007` - Named event details

The named parade was present in the event product surface, but the turn bypassed exact structured event resolution and retrieved unrelated documents.

- Primary classification: `REAL_STRUCTURED_DATA_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |

## `event-008` - Correct selected event

The July 13 record and the explicit parade correction were not resolved to the known event; correction metadata was not exposed in the captured response.

- Primary classification: `REAL_STRUCTURED_DATA_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |

## `event-009` - Event source question

The library filter was ignored and the source follow-up described a generic list without resident-verifiable attribution.

- Primary classification: `REAL_STRUCTURED_DATA_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |

## `event-011` - Return to earlier event topic

After a 311 topic switch, "back to the event" did not restore the earlier selected event and was treated as a literal event title.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `event-014` - Event registration follow-up

The registration limitation was reasonable, but retrieval lost the selected workshop and attached unrelated community and policy material.

- Primary classification: `REAL_RETRIEVAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| context_retention | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| correction_handling | N/E | medium | Not exercised by this scenario. |
| continuation_handling | N/E | medium | Not exercised by this scenario. |
| topic_switch_handling | N/E | medium | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| completeness | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| naturalness | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| resident_friendly_language | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| grounding | 2.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_relevance | 1.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_type_distinction | 2.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| unnecessary_clarification | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| internal_implementation_leakage | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| repetition | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| failure_quality | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |

## `311-003` - Change 311 geography

The Codman Square geography refinement abandoned the 311 topic and returned unrelated community development material.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `311-005` - More 311 results

"Show me more" abandoned the streetlight result set and returned unrelated meeting themes.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `311-006` - Official complaints versus resident concerns

The first answer contained official counts and the second used community discussion; the phrase-only official-count check was stricter than the observable behavior.

- Primary classification: `EVALUATOR_FALSE_POSITIVE`
- Deterministic evaluation agreed: `false`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| context_retention | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| correction_handling | N/E | medium | Not exercised by this scenario. |
| continuation_handling | N/E | medium | Not exercised by this scenario. |
| topic_switch_handling | N/E | medium | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | medium | Not exercised by this scenario. |
| completeness | 2.5 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| naturalness | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| resident_friendly_language | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| grounding | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| source_relevance | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| source_type_distinction | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| uncertainty_and_restraint | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| unnecessary_clarification | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| internal_implementation_leakage | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| repetition | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| contradiction_with_earlier_turns | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| failure_quality | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |

## `311-007` - Correct 311 issue type

The issue-type correction was understood, but the Bowdoin geography was lost and replaced with all Dorchester; capture metadata could not verify the correction flag.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `311-008` - Pronoun after a 311 result

The pronoun resolved to illegal dumping, but the assistant did not actually explain the 311 reporting channel or steps.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 2.5 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `311-009` - No matching 311 evidence

Without a block location, a Dorchester-wide zero cannot support a claim about the resident's block; the answer omitted that limitation.

- Primary classification: `REAL_GROUNDING_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| grounding | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| uncertainty_and_restraint | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |

## `311-010` - Switch from 311 to events

The captured multi-turn behavior passed every configured deterministic check.

- Primary classification: `NO_DETERMINISTIC_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_relevance | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |

## `safety-001` - Safety concerns by evidence type

The source-type conversation was mostly coherent, but the initial answer never retrieved an official-data baseline; some later source assertions incorrectly required official data after the assistant explicitly said none supported the prior claims.

- Primary classification: `REAL_RETRIEVAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| context_retention | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| correction_handling | N/E | medium | Not exercised by this scenario. |
| continuation_handling | N/E | medium | Not exercised by this scenario. |
| topic_switch_handling | N/E | medium | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | medium | Not exercised by this scenario. |
| completeness | 2.33 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| naturalness | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| resident_friendly_language | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| grounding | 2.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_relevance | 1.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_type_distinction | 1.33 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| unnecessary_clarification | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| internal_implementation_leakage | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| repetition | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| failure_quality | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |

## `safety-002` - Current safety overview

A current safety judgment was synthesized from a small community corpus without an official-data baseline or a sufficiently explicit data-freshness limitation.

- Primary classification: `REAL_GROUNDING_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| grounding | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| uncertainty_and_restraint | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |

## `safety-003` - Change safety geography

The Codman Square refinement became an organization overview, and the subsequent event request returned events outside the requested neighborhood.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 1.33 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `safety-004` - Unconfirmed shooting question

The answer relied on community sources to discuss a possible recent shooting and omitted clear emergency guidance and official-data lag.

- Primary classification: `REAL_GROUNDING_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| grounding | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_type_distinction | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| uncertainty_and_restraint | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |

## `safety-005` - Crime count then community context

The robbery count path failed and leaked crime_monthly_count; the follow-up then attached unrelated neighborhood concerns to robberies.

- Primary classification: `REAL_STRUCTURED_DATA_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| completeness | 2.5 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| resident_friendly_language | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| internal_implementation_leakage | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |

## `safety-007` - Official safety trend

A structured official trend question was answered from unrelated community documents rather than the available civic-data path.

- Primary classification: `REAL_RETRIEVAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| context_retention | N/E | medium | Not exercised by this scenario. |
| correction_handling | N/E | medium | Not exercised by this scenario. |
| continuation_handling | N/E | medium | Not exercised by this scenario. |
| topic_switch_handling | N/E | medium | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| completeness | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| naturalness | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| resident_friendly_language | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| grounding | 2.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_relevance | 1.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_type_distinction | 0.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| unnecessary_clarification | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| internal_implementation_leakage | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| repetition | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| failure_quality | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |

## `safety-008` - Crime record source limitations

The source explanation described community meetings but did not accurately explain the official crime-data source or its publication lag.

- Primary classification: `REAL_GROUNDING_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| grounding | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| uncertainty_and_restraint | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |

## `safety-009` - Safety advice versus data

Unrelated Codman Square material was used for Uphams Corner, followed by personal-safety advice that was not supported by incident data.

- Primary classification: `REAL_GROUNDING_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| completeness | 2.5 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| grounding | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_type_distinction | 0.5 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| uncertainty_and_restraint | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |

## `safety-010` - Correct the safety time window

The time correction ran, but the answer changed from assaults to a malformed offense breakdown with missing counts; correction metadata was unobservable.

- Primary classification: `REAL_STRUCTURED_DATA_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |

## `safety-011` - Community claim challenged

The assistant explicitly acknowledged and adopted the resident's crossings correction; the critical failure came from unavailable correction metadata and a narrow acknowledgement phrase list.

- Primary classification: `TEST_HARNESS_DEFECT`
- Deterministic evaluation agreed: `false`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| completeness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |

## `policy-001` - Budget news and approval status

The local corpus contained no FY2027 budget, current journalism, or approval record, and retrieval filled the gap with unrelated Codman Square material.

- Primary classification: `DATA_FRESHNESS_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| grounding | 2.67 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| source_type_distinction | 1.33 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |

## `policy-002` - Development project overview

The Columbia Road query retrieved Four Corners projects and meeting material that did not answer the requested location.

- Primary classification: `REAL_RETRIEVAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_relevance | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |

## `policy-003` - Proposal versus approval

No Dorchester Avenue rezoning record was present, so the assistant could only provide cautious but unrelated background.

- Primary classification: `DATA_COVERAGE_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |

## `policy-004` - Zoning term in plain language

The captured multi-turn behavior passed every configured deterministic check.

- Primary classification: `NO_DETERMINISTIC_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| grounding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| repetition | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |

## `policy-005` - Budget amount and purpose

A budget proposal question was misinterpreted as a 311 issue count and leaked 311_issue_count.

- Primary classification: `REAL_STRUCTURED_DATA_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| resident_friendly_language | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| internal_implementation_leakage | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |

## `policy-006` - Change development geography

The local corpus had no Savin Hill or Lower Mills development inventory; unrelated Codman Square evidence was returned.

- Primary classification: `DATA_COVERAGE_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |

## `policy-007` - Resident feedback on development

The official filing was absent, so the follow-up resident discussion could not be tied to a specific project.

- Primary classification: `DATA_COVERAGE_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |

## `policy-008` - No official decision yet

"The proposed building" has no identifying context in this standalone scenario; the assistant should clarify, but the scenario instead assumes a resolvable project.

- Primary classification: `SCENARIO_SPECIFICATION_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| resident_friendly_language | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| grounding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| source_type_distinction | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| uncertainty_and_restraint | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| internal_implementation_leakage | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| failure_quality | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |

## `policy-009` - Policy effect on residents

"The new parking proposal" is not identified, so neither the policy nor its resident impact is deterministically measurable.

- Primary classification: `SCENARIO_SPECIFICATION_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| failure_quality | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |

## `policy-010` - Correct policy identity

The correction to Dorchester Avenue was understood, but no specific amendment was available; the capture also omitted correction metadata.

- Primary classification: `DATA_COVERAGE_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |

## `policy-011` - Policy news versus primary record

The scenario requires both current journalism and a primary city record, neither of which exists in the demo source inventory.

- Primary classification: `ENVIRONMENT_MISMATCH`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |

## `community-001` - Meeting concerns summary

A meeting-summary request was routed to a 311 aggregation and presented illegal dumping as the meeting's main concern.

- Primary classification: `REAL_STRUCTURED_DATA_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |

## `community-002` - Meeting detail follow-up

The follow-up accurately expanded the prior traffic concern, but the history response did not repeat source objects and the evaluator treated that as two critical content failures.

- Primary classification: `TEST_HARNESS_DEFECT`
- Deterministic evaluation agreed: `false`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| repetition | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition TEST_HARNESS_DEFECT. |

## `community-004` - News about an earlier topic

The demo corpus contained no current Morrissey Boulevard plan or relevant local journalism; unrelated older Codman Square news was returned.

- Primary classification: `DATA_FRESHNESS_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |

## `community-005` - Official versus meeting testimony

Neither "the city notice" nor "the meeting" is identified, so the expected cross-source comparison has no stable referent.

- Primary classification: `SCENARIO_SPECIFICATION_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| failure_quality | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |

## `community-006` - More news without repetition

"Any more?" repeated and expanded the same meeting themes rather than continuing to additional news items.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `community-007` - No community discussion then news

The demo corpus had no recent bus-lane meeting material or related local news, and only stale unrelated news was available.

- Primary classification: `DATA_FRESHNESS_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| source_type_distinction | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| uncertainty_and_restraint | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_FRESHNESS_FAILURE. |

## `community-009` - Correct meeting identity

A civic-meeting request was interpreted as health-center events, and the explicit Fields Corner correction did not recover.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `community-010` - Community concern to official action

The resident-testimony turn was misrouted to structured data, and the official-action follow-up returned the meeting concern again without city action.

- Primary classification: `REAL_RETRIEVAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_relevance | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| source_type_distinction | 0.5 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_RETRIEVAL_DEFECT. |

## `community-011` - Community summary by neighborhood

The captured multi-turn behavior passed every configured deterministic check.

- Primary classification: `NO_DETERMINISTIC_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |

## `entity-001` - Eversource across source types

The demo corpus has no Eversource organization or discussion material, so all three turns stayed coherent but substantively empty.

- Primary classification: `DATA_COVERAGE_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_type_distinction | 1.67 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |

## `entity-003` - Neighborhood services contact

A community development corporation was presented instead of the current city neighborhood liaison.

- Primary classification: `REAL_GROUNDING_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| grounding | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| uncertainty_and_restraint | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |

## `entity-004` - MBTA role and local impact

The MBTA role was accurately explained and the follow-up remained on transit; failures were noncritical preferred-category checks.

- Primary classification: `NONDETERMINISTIC_ACCEPTABLE_VARIATION`
- Deterministic evaluation agreed: `false`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| context_retention | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| correction_handling | N/E | medium | Not exercised by this scenario. |
| continuation_handling | N/E | medium | Not exercised by this scenario. |
| topic_switch_handling | N/E | medium | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| completeness | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| naturalness | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| resident_friendly_language | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| grounding | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| source_relevance | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| source_type_distinction | 1.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| uncertainty_and_restraint | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| unnecessary_clarification | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| internal_implementation_leakage | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| repetition | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| contradiction_with_earlier_turns | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| failure_quality | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |

## `entity-005` - Local organization identity

The answer correctly describes CSNDC from its public overview, but the source adapter labeled it official_document instead of organization.

- Primary classification: `EVALUATOR_FALSE_POSITIVE`
- Deterministic evaluation agreed: `false`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |

## `entity-007` - Correct organization identity

The Kroc Center correction was understood, but the answer was unsupported by the retrieved sources and correction metadata was unavailable.

- Primary classification: `DATA_COVERAGE_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| context_retention | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| correction_handling | 0.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| continuation_handling | N/E | medium | Not exercised by this scenario. |
| topic_switch_handling | N/E | medium | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | medium | Not exercised by this scenario. |
| completeness | 2.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| naturalness | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| resident_friendly_language | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| grounding | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_relevance | 2.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| source_type_distinction | 0.5 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| uncertainty_and_restraint | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| unnecessary_clarification | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| internal_implementation_leakage | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| repetition | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |
| failure_quality | 4.0 | medium | Score reflects the captured turns, deterministic checks, and the scenario-level disposition DATA_COVERAGE_FAILURE. |

## `entity-008` - Utility outage limitation

Street-level live utility outage lookup is not supported; the response should have said so without leaking 311_issue_recent.

- Primary classification: `UNSUPPORTED_CAPABILITY`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| grounding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| source_type_distinction | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| uncertainty_and_restraint | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| failure_quality | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |

## `cross-001` - Weekly neighborhood brief

A broad neighborhood brief collapsed to events only and omitted other civic domains.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `cross-002` - Issue, official response, and resident reaction

The trash-complaint question was routed to event listings and openly acknowledged that mismatch.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `cross-003` - Event near a reported issue

The captured multi-turn behavior passed every configured deterministic check.

- Primary classification: `NO_DETERMINISTIC_FAILURE`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NO_DETERMINISTIC_FAILURE. |

## `cross-004` - Development and transit impacts

The requested Columbia Road project and transit-impact evidence are absent from the demo source inventory.

- Primary classification: `ENVIRONMENT_MISMATCH`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| source_relevance | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition ENVIRONMENT_MISMATCH. |

## `cross-005` - Safety and events separated

The combined safety-and-events request returned only events and did not separate the two evidence scopes.

- Primary classification: `REAL_CONVERSATIONAL_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_CONVERSATIONAL_DEFECT. |

## `cross-006` - Return to first of two topics

The raw-SQL regex matched ordinary prose beginning with "Real Estate Update" and later containing "from"; separately, the return-to-cleanup answer remains a known qualitative defect.

- Primary classification: `EVALUATOR_FALSE_POSITIVE`
- Deterministic evaluation agreed: `false`
- Protect current behavior: `true`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| resident_friendly_language | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| internal_implementation_leakage | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition EVALUATOR_FALSE_POSITIVE. |

## `uncertain-001` - Unknown event location

The parade record existed in the event surface, but the query used unrelated RAG evidence and failed to return the known partial event fields.

- Primary classification: `REAL_STRUCTURED_DATA_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| grounding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| uncertainty_and_restraint | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_STRUCTURED_DATA_DEFECT. |

## `uncertain-002` - No article then official sources

The official-source follow-up mislabeled community organizations as official and returned unrelated material.

- Primary classification: `REAL_GROUNDING_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| grounding | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_type_distinction | 0.5 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| uncertainty_and_restraint | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |

## `uncertain-003` - No real-time transit claim

Real-time MBTA status is unavailable, and the failure response leaked the unrelated events_list identifier.

- Primary classification: `UNSUPPORTED_CAPABILITY`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| grounding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| source_type_distinction | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| uncertainty_and_restraint | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |
| failure_quality | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition UNSUPPORTED_CAPABILITY. |

## `uncertain-004` - Ambiguous but answerable local digest

The answer supplied a reasonable neighborhood digest; the deterministic check required specific current-scope words and source categories despite multiple valid answer structures.

- Primary classification: `NONDETERMINISTIC_ACCEPTABLE_VARIATION`
- Deterministic evaluation agreed: `false`
- Protect current behavior: `true`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | N/E | high | Not exercised by this scenario. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| grounding | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| source_type_distinction | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| uncertainty_and_restraint | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition NONDETERMINISTIC_ACCEPTABLE_VARIATION. |

## `uncertain-006` - Disputed factual claim

The meeting is never identified, making cancellation status unmeasurable; the assistant nevertheless overclaimed zero cancellations and ignored the resident's conflicting report.

- Primary classification: `SCENARIO_SPECIFICATION_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `false`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| context_retention | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| correction_handling | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| completeness | 2.5 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| grounding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| source_type_distinction | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| uncertainty_and_restraint | 0.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |
| failure_quality | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition SCENARIO_SPECIFICATION_DEFECT. |

## `uncertain-007` - No evidence does not mean never

No block location was provided, and a zero result cannot establish that a complaint has never existed.

- Primary classification: `REAL_GROUNDING_DEFECT`
- Deterministic evaluation agreed: `true`
- Protect current behavior: `false`
- Known defect: `true`

| Dimension | Score | Confidence | Rationale |
|---|---|---|---|
| intent_understanding | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| context_retention | N/E | high | Not exercised by this scenario. |
| correction_handling | N/E | high | Not exercised by this scenario. |
| continuation_handling | N/E | high | Not exercised by this scenario. |
| topic_switch_handling | N/E | high | Not exercised by this scenario. |
| reference_and_pronoun_resolution | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| completeness | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| naturalness | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| resident_friendly_language | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| grounding | 1.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_relevance | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| source_type_distinction | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| uncertainty_and_restraint | 2.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| unnecessary_clarification | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| internal_implementation_leakage | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| repetition | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| contradiction_with_earlier_turns | 3.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
| failure_quality | 4.0 | high | Score reflects the captured turns, deterministic checks, and the scenario-level disposition REAL_GROUNDING_DEFECT. |
