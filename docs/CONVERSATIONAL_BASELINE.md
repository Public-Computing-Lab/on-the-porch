# Conversational Baseline

## Baseline

- Protected branch: `main`
- Protected commit: `840ec54dd1265b4f2f42cc18b66b54cbf2a99b89`
- Baseline created: 2026-07-28
- Purpose: preserve the current resident-facing conversational quality while
  later stages improve execution and infrastructure.

This baseline is behavioral. It does not assert that every answer at this commit
is correct, nor does it freeze exact model wording.

## Why this behavior is protected

On the Porch should feel like a knowledgeable local civic guide, not a search
form. The current production baseline can use raw conversation history, interpret
natural corrections and references, combine structured civic facts with
community material, and explain results in a resident-oriented voice. Those
qualities are easy to lose when routing, state, persistence, or concurrency is
rewritten.

Stage 1 creates a quality gate before that infrastructure changes. Future work
must be able to show that it preserves or improves the conversational behavior of
the protected commit.

## Conversational core

The protected core currently spans:

- `on_the_porch/unified_chatbot.py` for history-aware turn orchestration, cached
  retrieval context, follow-up decisions, SQL/RAG/hybrid execution, and history
  answers;
- `on_the_porch/answer_voice.py` for resident-facing voice and conversational
  presentation;
- `on_the_porch/answer_synthesizer.py` for grounded SQL, RAG, and hybrid synthesis;
- `on_the_porch/intent_router.py`, `on_the_porch/plan_builder.py`, and
  `on_the_porch/plan_executor.py` for civic routing and execution;
- `on_the_porch/sql_chat/` for prepared civic facts and structured formatting;
- `on_the_porch/rag stuff/` for document retrieval and source metadata;
- `api/api_v2.py` for conversation history, response delivery, and persistence;
- `public/` for the resident-facing conversation lifecycle.

Stage 1 changes none of these files.

## Protected behavior

The initial quality gate protects:

- raw conversational history;
- natural correction handling;
- direct follow-ups and pronoun or ordinal references;
- continuation without repeating results;
- date, location, and source-type refinements;
- safe topic switching and return to earlier topics;
- hybrid SQL and RAG synthesis;
- resident-facing answer voice;
- model-led explanation rather than database-shaped output;
- broad synthesis across official records, journalism, and community material;
- willingness to answer reasonably clear questions without excessive
  clarification;
- honest partial and no-evidence answers;
- source-type distinctions, especially official records versus resident
  testimony.

## What counts as a regression

A conversational regression includes:

- misunderstanding the resident's current intent;
- losing a relevant entity, location, date, source preference, or explicit
  correction;
- carrying unrelated prior context into a new topic;
- asking a generic clarification for a reasonably clear question;
- repeating an answer or stable result after "more";
- resolving "it," "that," or "the second one" against the wrong context;
- dropping known partial facts because another field is absent;
- treating community testimony or journalism as official policy;
- using irrelevant or missing sources for a factual claim;
- claiming certainty when sources are absent, stale, or incomplete;
- exposing SQL, internal route names, tool language, or vector-store names;
- becoming less natural, less complete, or less useful to a resident.

## Why exact snapshots are insufficient

The answer model is probabilistic and multiple phrasings can be equally correct.
Full-text snapshots would reject harmless wording changes and still miss semantic
errors hidden behind familiar prose. The regression corpus instead records
required concepts, forbidden concepts, source categories, state assertions,
uncertainty requirements, and continuation behavior.

Deterministic checks provide a repeatable floor. Human or model-based qualitative
judgment can later evaluate naturalness and nuanced completeness through the
disabled-by-default evaluator interface.

## Evaluation dimensions

The harness defines 18 dimensions:

1. Intent understanding
2. Context retention
3. Correction handling
4. Continuation handling
5. Topic-switch handling
6. Reference and pronoun resolution
7. Completeness
8. Naturalness
9. Resident-friendly language
10. Grounding
11. Source relevance
12. Source-type distinction
13. Uncertainty and restraint
14. Unnecessary clarification
15. Internal implementation leakage
16. Repetition
17. Contradiction with earlier turns
18. Failure quality

The scale is 0 for a critical failure, 1 for materially incorrect, 2 for partially
correct, 3 for acceptable qualitative behavior, and 4 for strong behavior.

## Using the gate

Every pull request touching history, prompts, routing, retrieval, SQL formatting,
answer synthesis, source metadata, persistence, deadlines, retries, concurrency,
or response delivery should run the local validation, harness unit tests, and
fixture replay described in
`tests/conversation_regression/README.md`.

Changes to conversation semantics require new or expanded scenarios. Examples
include a new civic domain, a new source category, a new state transition, a new
clarification policy, or a new fallback. Infrastructure-only changes do not need
new scenarios unless they introduce a new failure mode.

An infrastructure change fails the gate when deterministic requirements regress
or critical failures appear. A clean deterministic result is necessary but not
sufficient for major prompt or model changes; those also require controlled live
capture and human review in a later authorized stage.

## Known limitations

- Stage 1 does not call the live model or production API.
- Synthetic fixtures prove the evaluator, not the quality of every live answer.
- Phrase-group checks cannot fully judge naturalness or nuanced contradictions.
- Stable-result repetition checks work only when captured responses include IDs.
- Metadata assertions work only when the capture exposes the relevant state.
- The baseline protects current strengths but does not endorse known product
  defects at the baseline commit.
- Production measurement, latency tracing, and DreamHost auditing belong to Stage
  2 and are intentionally excluded.
