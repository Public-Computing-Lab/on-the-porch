# ENGINEER_REVIEW_BY_CODEX

This is a structured engineering review of five important live demo scenarios.
No external judge model was used. Scores use the Stage 1 scale: 0 critical
failure, 1 materially incorrect, 2 partially correct, 3 acceptable, and 4
strong. `N/E` means the dimension was not exercised by that scenario.

| Dimension | event-001 | safety-001 | policy-001 | entity-001 | 311-010 |
|---|---:|---:|---:|---:|---:|
| Intent understanding | 3 | 3 | 2 | 1 | 4 |
| Context retention | 2 | 3 | 3 | 3 | 4 |
| Correction handling | 3 | N/E | N/E | N/E | N/E |
| Continuation handling | 2 | 3 | 3 | 3 | N/E |
| Topic switching | N/E | 3 | 3 | N/E | 4 |
| Pronoun/reference resolution | 1 | 3 | 3 | 4 | N/E |
| Completeness | 2 | 3 | 2 | 1 | 4 |
| Naturalness | 3 | 3 | 3 | 3 | 3 |
| Resident-friendly language | 3 | 3 | 3 | 3 | 4 |
| Grounding | 2 | 3 | 1 | 2 | 4 |
| Source relevance | 1 | 3 | 1 | 1 | 4 |
| Source-type distinction | 2 | 4 | 3 | 3 | 4 |
| Uncertainty/restraint | 3 | 3 | 4 | 4 | 3 |
| Unnecessary clarification | 4 | 4 | 4 | 4 | 4 |
| Internal terminology leakage | 4 | 4 | 4 | 4 | 4 |
| Repetition | 2 | 3 | 2 | 2 | 3 |
| Contradiction | 1 | 4 | 4 | 4 | 4 |
| Failure quality | 2 | 3 | 3 | 3 | 4 |

## Scenario Notes

### `event-001` - Week correction and continuation

The first event list and next-week correction are natural and mostly useful.
The continuation relies on the prior prose instead of retrieving a fresh page,
and "the second one" is resolved to the only event from the corrected answer
even though that answer contains no second event. The final RAG sources are
mostly unrelated to the selected event. This is the clearest measured
conversation-state weakness.

### `safety-001` - Safety concerns by evidence type

The sequence retains the safety topic and correctly distinguishes meeting
discussion from official city data. Resident statements are attributed with
reasonable restraint. The initial answer is limited to the small available
community corpus and does not provide an official-data baseline, so it is useful
but incomplete.

### `policy-001` - Budget news and approval status

The assistant retains the FY2027 topic and is appropriately cautious about
missing budget evidence. However, it fills the answer with unrelated Codman
Square material, labels community activity as budget-adjacent context, and does
not retrieve relevant journalism or an official budget document. The final
approval response is careful but answers about the unrelated retrieved
discussion rather than the requested budget.

### `entity-001` - Eversource across source types

Pronouns remain attached to Eversource across turns, and the assistant does not
invent resident views. The opening answer fails the basic entity question: it
does not explain that Eversource is an energy utility. Subsequent retrieval
remains dominated by unrelated Codman Square sources, so the conversation is
coherent but not substantively useful.

### `311-010` - Switch from 311 to events

The assistant gives a grounded 311 ranking, then cleanly switches to events
without carrying the prior 311 context. Both turns use the correct source type.
The answers are more verbose than necessary, but this scenario demonstrates
strong standalone routing and topic-switch behavior.

## Overall Finding

The current chatbot is conversationally fluent and usually avoids unnecessary
clarification, but source relevance and result-set state are the principal
quality constraints. Structured 311 and event lookups are strongest. Broad
policy/entity questions and ordinal event follow-ups are weakest. These are
baseline findings only; Stage 2 does not change the behavior.
