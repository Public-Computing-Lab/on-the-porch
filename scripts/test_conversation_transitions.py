#!/usr/bin/env python3
"""Behavioral tests for canonical conversation-state transitions."""

from __future__ import annotations

import copy
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from conversation_dimensions import (  # noqa: E402
    CanonicalFilters,
    CanonicalLocation,
    CanonicalTimeRange,
    CanonicalTopic,
    ConversationDimensions,
    CostFilter,
    DecisionSource as DimensionDecisionSource,
    DimensionContext,
    GeographyKind,
    ReasonCode as DimensionReasonCode,
    TimeGranularity,
    TimeLabelCode,
    extract_conversation_dimensions,
)
from conversation_relationship import (  # noqa: E402
    ContextDimension,
    ConversationRelationship,
    DecisionSource as RelationshipDecisionSource,
    ReasonCode as RelationshipReasonCode,
    RelationshipDecision,
)
from conversation_state import (  # noqa: E402
    ActiveEntity,
    ConversationState,
    ProposedTransition,
    ThreadStateEnvelope,
    apply_transition,
    merge_for_commit,
    retrieval_cache_hash,
)
from conversation_transitions import (  # noqa: E402
    CacheAction,
    CompletionStatus,
    TransitionAction,
    TransitionReason,
    classify_completion_status,
    plan_conversation_transition,
    semantic_signature,
)
from intent_router import detect_civic_topic, route_with_rules  # noqa: E402


NOW = datetime.fromisoformat("2026-07-15T12:00:00-04:00")
EMPTY_CACHE = {
    "mode": None,
    "timestamp": None,
    "question": None,
    "sql_result": None,
    "sql_query": None,
    "rag_chunks": None,
    "rag_metadata": None,
    "answer": None,
}
FULL_CACHE = {**EMPTY_CACHE, "mode": "hybrid", "question": "old", "answer": "old"}


def _relationship(
    relationship: ConversationRelationship,
    *,
    reason: RelationshipReasonCode | None = None,
    clarification: bool = False,
    source: RelationshipDecisionSource = RelationshipDecisionSource.RULE,
) -> RelationshipDecision:
    reasons = {
        ConversationRelationship.STANDALONE: RelationshipReasonCode.SELF_CONTAINED_CIVIC_QUESTION,
        ConversationRelationship.FOLLOW_UP: RelationshipReasonCode.REFERENTIAL_FOLLOW_UP,
        ConversationRelationship.REFINEMENT: RelationshipReasonCode.LOCATION_REFINEMENT,
        ConversationRelationship.CORRECTION: RelationshipReasonCode.EXPLICIT_CORRECTION,
        ConversationRelationship.NEW_TOPIC: RelationshipReasonCode.CIVIC_TOPIC_SWITCH,
        ConversationRelationship.SMALL_TALK: RelationshipReasonCode.SMALL_TALK_THANKS,
    }
    retained = (
        (ContextDimension.ACTIVE_TOPIC, ContextDimension.ACTIVE_ENTITY)
        if relationship in {ConversationRelationship.FOLLOW_UP, ConversationRelationship.REFINEMENT}
        else ()
    )
    return RelationshipDecision(
        relationship=relationship,
        confidence=0.99,
        reason_code=reason or reasons[relationship],
        retained=retained,
        cleared=(),
        requires_clarification=clarification,
        decision_source=source,
        shadow=True,
    )


def _dimensions(message: str, state: ConversationState) -> ConversationDimensions:
    result = extract_conversation_dimensions(
        message,
        enabled=True,
        context=DimensionContext(
            active_topic=state.active_topic,
            location=state.location,
            time_range=state.time_range,
            authoritative_active_event=state.active_entity is not None,
        ),
        now=NOW,
    )
    assert result is not None
    return result


def _plan(
    state: ConversationState,
    message: str,
    relationship: ConversationRelationship,
    route: str,
    *,
    completion: CompletionStatus = CompletionStatus.ACCEPTABLE,
    authoritative_entity: ActiveEntity | None = None,
    relationship_reason: RelationshipReasonCode | None = None,
    dimensions: ConversationDimensions | None = None,
    stale: bool = False,
):
    return plan_conversation_transition(
        current=state,
        relationship=_relationship(relationship, reason=relationship_reason),
        dimensions=dimensions or _dimensions(message, state),
        authoritative_entity=authoritative_entity,
        detected_topic=detect_civic_topic(message),
        completed_route=route,
        completion_status=completion,
        stale_active_entity=stale,
        now=NOW,
    )


def _apply(state: ConversationState, transition) -> ConversationState:
    return apply_transition(
        state,
        transition.state_proposal(state),
        last_substantive_message_id="message-1",
    )


def _event_state(event_id: str = "435") -> ConversationState:
    return ConversationState(
        revision=3,
        active_topic=CanonicalTopic.EVENTS,
        active_entity=ActiveEntity.event(event_id),
        previous_route="sql",
        last_substantive_message_id="message-old",
    )


def _safety_state(*, codman: bool = False) -> ConversationState:
    return ConversationState(
        revision=2,
        active_topic=CanonicalTopic.PUBLIC_SAFETY,
        location=CanonicalLocation(
            "codman_square" if codman else "dorchester",
            GeographyKind.SUB_NEIGHBORHOOD if codman else GeographyKind.NEIGHBORHOOD,
        ),
        time_range=CanonicalTimeRange(
            TimeLabelCode.RIGHT_NOW,
            "2026-07-15",
            "2026-07-16",
            "America/New_York",
            TimeGranularity.INSTANT,
        ),
        previous_route="hybrid",
        last_substantive_message_id="message-old",
    )


def _event_list_state() -> ConversationState:
    return ConversationState(
        revision=4,
        active_topic=CanonicalTopic.EVENTS,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
        time_range=CanonicalTimeRange(
            TimeLabelCode.THIS_WEEKEND,
            "2026-07-18",
            "2026-07-20",
            "America/New_York",
            TimeGranularity.RANGE,
        ),
        previous_route="sql",
        last_substantive_message_id="message-old",
    )


def test_standalone_events_and_safety_activate_canonical_state() -> None:
    empty = ConversationState()
    events = _plan(
        empty,
        "What events are happening this weekend?",
        ConversationRelationship.STANDALONE,
        "sql",
    )
    assert events.action is TransitionAction.UPDATE
    assert events.next_state.active_topic is CanonicalTopic.EVENTS
    assert events.next_state.time_range.label_code is TimeLabelCode.THIS_WEEKEND
    assert events.cache_action is CacheAction.INVALIDATE
    assert _apply(empty, events).revision == 1

    safety = _plan(
        empty,
        "Is Dorchester safe right now?",
        ConversationRelationship.STANDALONE,
        "hybrid",
    )
    assert safety.next_state.active_topic is CanonicalTopic.PUBLIC_SAFETY
    assert safety.next_state.location.id == "dorchester"
    assert safety.next_state.time_range.label_code is TimeLabelCode.RIGHT_NOW


def test_location_refinement_and_referenced_new_topic() -> None:
    safety = _safety_state()
    codman = _plan(
        safety,
        "What about Codman Square?",
        ConversationRelationship.REFINEMENT,
        "hybrid",
    )
    assert codman.action is TransitionAction.UPDATE
    assert codman.next_state.active_topic is CanonicalTopic.PUBLIC_SAFETY
    assert codman.next_state.location.id == "codman_square"
    assert codman.cache_action is CacheAction.INVALIDATE

    codman_state = _apply(safety, codman)
    events = _plan(
        codman_state,
        "Are there events there this weekend?",
        ConversationRelationship.NEW_TOPIC,
        "sql",
    )
    assert events.action is TransitionAction.REPLACE
    assert events.next_state.active_topic is CanonicalTopic.EVENTS
    assert events.next_state.location.id == "codman_square"
    assert events.next_state.time_range.label_code is TimeLabelCode.THIS_WEEKEND
    assert events.next_state.active_entity is None


def test_event_filter_refinement_and_route_gate() -> None:
    state = _event_list_state()
    accepted = _plan(
        state,
        "Only show free ones.",
        ConversationRelationship.REFINEMENT,
        "sql",
    )
    assert accepted.next_state.filters.cost is CostFilter.FREE_ONLY
    assert accepted.cache_action is CacheAction.INVALIDATE

    unsupported_by_current_answer = _plan(
        state,
        "Only show free ones.",
        ConversationRelationship.REFINEMENT,
        "history",
    )
    assert unsupported_by_current_answer.action is TransitionAction.NO_CHANGE
    assert unsupported_by_current_answer.commit_allowed is False
    assert unsupported_by_current_answer.completion_status is CompletionStatus.DEGRADED
    assert TransitionReason.ROUTE_INCOMPATIBLE in unsupported_by_current_answer.reason_codes


def test_active_event_followup_and_small_talk_preserve_without_revision() -> None:
    state = _event_state()
    followup = _plan(
        state,
        "Where is it?",
        ConversationRelationship.FOLLOW_UP,
        "sql",
        relationship_reason=RelationshipReasonCode.ACTIVE_EVENT_PROPERTY_QUESTION,
    )
    assert followup.action is TransitionAction.NO_CHANGE
    assert followup.cache_action is CacheAction.PRESERVE
    assert _apply(state, followup) == state

    small_talk = _plan(
        state,
        "Thanks",
        ConversationRelationship.SMALL_TALK,
        "chitchat",
    )
    assert small_talk.action is TransitionAction.NO_CHANGE
    assert _apply(state, small_talk).revision == state.revision
    assert _apply(state, small_talk).last_substantive_message_id == "message-old"


def test_successful_new_topic_clears_event_but_failure_preserves_it() -> None:
    state = _event_state()
    success = _plan(
        state,
        "What are residents saying about potholes?",
        ConversationRelationship.NEW_TOPIC,
        "hybrid",
    )
    assert success.next_state.active_topic is CanonicalTopic.COMMUNITY_OPINION
    assert success.next_state.active_entity is None
    assert success.cache_action is CacheAction.INVALIDATE

    for status in (CompletionStatus.PARTIAL, CompletionStatus.DEGRADED, CompletionStatus.FAILED):
        failed = _plan(
            state,
            "What are residents saying about potholes?",
            ConversationRelationship.NEW_TOPIC,
            "hybrid",
            completion=status,
        )
        assert failed.action is TransitionAction.NO_CHANGE
        assert failed.cache_action is CacheAction.PRESERVE
        assert failed.commit_allowed is False


def test_authoritative_correction_replaces_event_and_unresolved_does_not() -> None:
    state = _event_state()
    replacement = _plan(
        state,
        "No, I mean the parade.",
        ConversationRelationship.CORRECTION,
        "sql",
        authoritative_entity=ActiveEntity.event("900"),
    )
    assert replacement.action is TransitionAction.REPLACE
    assert replacement.next_state.active_entity == ActiveEntity.event("900")
    assert replacement.cache_action is CacheAction.INVALIDATE

    unresolved = _plan(
        state,
        "No, I mean the parade.",
        ConversationRelationship.CORRECTION,
        "sql",
    )
    assert unresolved.action is TransitionAction.NO_CHANGE
    assert unresolved.requires_clarification is True
    assert unresolved.commit_allowed is False


def test_reference_resolution_is_bounded_and_explicit_values_win() -> None:
    empty = ConversationState()
    missing = _plan(
        empty,
        "Are there events there this weekend?",
        ConversationRelationship.STANDALONE,
        "sql",
    )
    assert missing.action is TransitionAction.NO_CHANGE
    assert missing.requires_clarification is True

    missing_topic = _plan(
        empty,
        "What about Codman Square?",
        ConversationRelationship.REFINEMENT,
        "rag",
    )
    assert missing_topic.action is TransitionAction.NO_CHANGE
    assert missing_topic.commit_allowed is False
    assert missing_topic.requires_clarification is True

    state = _event_list_state()
    explicit = _plan(
        state,
        "Are there events near Fields Corner this weekend?",
        ConversationRelationship.REFINEMENT,
        "sql",
    )
    assert explicit.next_state.location.id == "fields_corner"

    new_time = _plan(
        state,
        "What about tomorrow?",
        ConversationRelationship.REFINEMENT,
        "sql",
    )
    assert new_time.next_state.time_range.label_code is TimeLabelCode.TOMORROW


def test_new_topic_clears_unreferenced_dimensions() -> None:
    state = _safety_state(codman=True)
    fresh = _plan(
        state,
        "What events are happening this weekend?",
        ConversationRelationship.NEW_TOPIC,
        "sql",
    )
    assert fresh.next_state.location is None
    assert fresh.next_state.time_range.label_code is TimeLabelCode.THIS_WEEKEND

    referenced = _plan(
        state,
        "Are there events there this weekend?",
        ConversationRelationship.NEW_TOPIC,
        "sql",
    )
    assert referenced.next_state.location.id == "codman_square"


def test_unknown_ambiguous_and_unsupported_values_never_persist() -> None:
    empty = ConversationState()
    unknown = _plan(
        empty,
        "Tell me something",
        ConversationRelationship.STANDALONE,
        "rag",
    )
    assert unknown.next_state.active_topic is None
    assert unknown.commit_allowed is False

    ambiguous_dimensions = _dimensions(
        "What is happening between Codman Square and Fields Corner?",
        empty,
    )
    ambiguous = _plan(
        empty,
        "What is happening between Codman Square and Fields Corner?",
        ConversationRelationship.STANDALONE,
        "rag",
        dimensions=ambiguous_dimensions,
    )
    assert ambiguous.next_state.location is None
    assert ambiguous.commit_allowed is False

    unsupported = _plan(
        empty,
        "Show family-friendly events.",
        ConversationRelationship.STANDALONE,
        "sql",
    )
    assert unsupported.next_state.filters == CanonicalFilters()


def test_untrusted_llm_decisions_cannot_authorize_state() -> None:
    empty = ConversationState()
    dimensions = _dimensions("What events are happening this weekend?", empty)
    llm_relationship = _relationship(
        ConversationRelationship.STANDALONE,
        source=RelationshipDecisionSource.LLM,
    )
    transition = plan_conversation_transition(
        current=empty,
        relationship=llm_relationship,
        dimensions=dimensions,
        authoritative_entity=None,
        detected_topic="events",
        completed_route="sql",
        completion_status=CompletionStatus.ACCEPTABLE,
        now=NOW,
    )
    assert transition.action is TransitionAction.NO_CHANGE
    assert transition.commit_allowed is False

    llm_dimensions = ConversationDimensions(
        topic=dimensions.topic,
        location=dimensions.location,
        time_range=dimensions.time_range,
        filters=dimensions.filters,
        references=dimensions.references,
        confidence=0.9,
        reason_codes=dimensions.reason_codes,
        decision_source=DimensionDecisionSource.LLM,
        requires_clarification=False,
        shadow=True,
    )
    transition = plan_conversation_transition(
        current=empty,
        relationship=_relationship(ConversationRelationship.STANDALONE),
        dimensions=llm_dimensions,
        authoritative_entity=None,
        detected_topic=None,
        completed_route="sql",
        completion_status=CompletionStatus.ACCEPTABLE,
        now=NOW,
    )
    assert transition.action is TransitionAction.NO_CHANGE


def test_equivalent_context_preserves_cache_and_factual_change_invalidates() -> None:
    state = _event_list_state()
    same = _plan(
        state,
        "What events are happening in Codman Square this weekend?",
        ConversationRelationship.STANDALONE,
        "sql",
    )
    assert same.action is TransitionAction.NO_CHANGE
    assert same.cache_action is CacheAction.PRESERVE
    assert semantic_signature(state) == semantic_signature(same.next_state)

    changed = _plan(
        state,
        "What events are happening near Fields Corner this weekend?",
        ConversationRelationship.STANDALONE,
        "sql",
    )
    assert changed.action is not TransitionAction.NO_CHANGE
    assert changed.cache_action is CacheAction.INVALIDATE


def test_state_cache_merge_is_atomic_and_conflicts_preserve_winner() -> None:
    state = _safety_state()
    expected = ThreadStateEnvelope(copy.deepcopy(FULL_CACHE), state)
    current_with_new_cache = ThreadStateEnvelope({**FULL_CACHE, "question": "newer"}, state)
    transition = _plan(
        state,
        "What about Codman Square?",
        ConversationRelationship.REFINEMENT,
        "hybrid",
    )
    merged = merge_for_commit(
        current_with_new_cache,
        expected,
        next_retrieval_cache=EMPTY_CACHE,
        transition=transition.state_proposal(state),
        last_substantive_message_id="message-winner",
        force_cache_update=True,
    )
    assert merged.cache_update_applied is True
    assert merged.envelope.retrieval_cache == EMPTY_CACHE
    assert merged.envelope.conversation_state.location.id == "codman_square"

    loser = merge_for_commit(
        merged.envelope,
        expected,
        next_retrieval_cache=FULL_CACHE,
        transition=transition.state_proposal(state),
        last_substantive_message_id="message-loser",
        force_cache_update=True,
    )
    assert loser.version_conflict is True
    assert loser.envelope == merged.envelope


def test_completion_mapping_allows_authoritative_no_results_only() -> None:
    no_results = {
        "answer": "No matching events were found.",
        "mode": "sql",
        "result": {"result": {"rows": []}},
    }
    assert classify_completion_status(no_results) is CompletionStatus.ACCEPTABLE
    assert classify_completion_status(no_results, partial=True) is CompletionStatus.PARTIAL
    assert classify_completion_status({**no_results, "execution_degraded": True}) is CompletionStatus.DEGRADED
    assert classify_completion_status({"answer": "", "mode": "sql"}) is CompletionStatus.FAILED
    assert classify_completion_status(no_results, conflicted=True) is CompletionStatus.CONFLICTED


def test_topic_only_correction_replaces_trusted_topic() -> None:
    state = _safety_state(codman=True)
    corrected = _plan(
        state,
        "No, I mean community events.",
        ConversationRelationship.CORRECTION,
        "sql",
    )
    assert corrected.commit_allowed is True
    assert corrected.action is not TransitionAction.NO_CHANGE
    assert corrected.next_state.active_topic is CanonicalTopic.EVENTS
    assert corrected.next_state.location == state.location
    assert corrected.next_state.active_entity is None
    assert corrected.next_state.filters == CanonicalFilters()
    assert corrected.cache_action is CacheAction.INVALIDATE


def test_route_compatibility_prevents_stronger_state_than_answer() -> None:
    empty = ConversationState()
    safety_via_rag = _plan(
        empty,
        "Is Dorchester safe right now?",
        ConversationRelationship.STANDALONE,
        "rag",
    )
    assert safety_via_rag.commit_allowed is False
    assert safety_via_rag.action is TransitionAction.NO_CHANGE
    assert safety_via_rag.completion_status is CompletionStatus.DEGRADED

    state = _safety_state(codman=True)
    message = "Are there events there this weekend?"
    unresolved_route_context = plan_conversation_transition(
        current=state,
        relationship=_relationship(ConversationRelationship.NEW_TOPIC),
        dimensions=_dimensions(message, state),
        authoritative_entity=None,
        detected_topic="events",
        completed_route="sql",
        completion_status=CompletionStatus.ACCEPTABLE,
        reference_context_applied=False,
        now=NOW,
    )
    assert unresolved_route_context.action is TransitionAction.NO_CHANGE
    assert unresolved_route_context.commit_allowed is False
    assert TransitionReason.REFERENCE_NOT_APPLIED_TO_ROUTE in unresolved_route_context.reason_codes


def test_diagnostics_are_bounded_and_exclude_states_or_prose() -> None:
    transition = _plan(
        _safety_state(),
        "What about Codman Square?",
        ConversationRelationship.REFINEMENT,
        "hybrid",
    )
    diagnostic = transition.to_diagnostic_dict()
    assert set(diagnostic) == {
        "action", "relationship", "cache_action", "retained", "replaced", "cleared",
        "reason_codes", "commit_allowed", "committed", "requires_clarification",
        "completion_status", "requested_dimensions", "applied_dimensions",
        "unapplied_dimensions", "retrieval_matched",
    }
    serialized = str(diagnostic)
    for forbidden in ("next_state", "answer", "rag_chunks", "sql_result", "message"):
        assert forbidden not in serialized


def test_json_sse_planning_parity_and_isolation() -> None:
    first = _safety_state()
    kwargs = {
        "current": first,
        "relationship": _relationship(ConversationRelationship.REFINEMENT),
        "dimensions": _dimensions("What about Codman Square?", first),
        "authoritative_entity": None,
        "detected_topic": None,
        "completed_route": "hybrid",
        "completion_status": CompletionStatus.ACCEPTABLE,
        "now": NOW,
    }
    assert plan_conversation_transition(**kwargs) == plan_conversation_transition(**kwargs)

    other = _event_state("900")
    planned = plan_conversation_transition(**kwargs)
    assert other.active_entity == ActiveEntity.event("900")
    assert planned.next_state.active_entity is None
    assert first.location.id == "dorchester"


def test_codman_geography_is_not_a_bare_news_source() -> None:
    geographic = route_with_rules("What about Codman Square?")
    assert geographic is None or geographic.get("intent") != "news_source"
    assert detect_civic_topic("What about Codman Square?") != "news"

    for question in (
        "What are the latest updates from Codman?",
        "Show me the Codman newsletter",
        "What is the Codman Square news?",
    ):
        source = route_with_rules(question)
        assert source["mode"] == "rag" and source["intent"] == "news_source", (question, source)


def test_transition_planning_latency() -> None:
    state = _safety_state()
    relationship = _relationship(ConversationRelationship.REFINEMENT)
    dimensions = _dimensions("What about Codman Square?", state)
    samples = []
    for _ in range(2000):
        started = time.perf_counter_ns()
        transition = plan_conversation_transition(
            current=state,
            relationship=relationship,
            dimensions=dimensions,
            authoritative_entity=None,
            detected_topic=None,
            completed_route="hybrid",
            completion_status=CompletionStatus.ACCEPTABLE,
            now=NOW,
        )
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    assert transition.next_state.location.id == "codman_square"
    p99 = sorted(samples)[int(len(samples) * 0.99) - 1]
    print(f"PERF transition planning p50={statistics.median(samples):.4f}ms p99={p99:.4f}ms")
    assert p99 < 1.0


def main() -> int:
    tests = [
        test_standalone_events_and_safety_activate_canonical_state,
        test_location_refinement_and_referenced_new_topic,
        test_event_filter_refinement_and_route_gate,
        test_active_event_followup_and_small_talk_preserve_without_revision,
        test_successful_new_topic_clears_event_but_failure_preserves_it,
        test_authoritative_correction_replaces_event_and_unresolved_does_not,
        test_reference_resolution_is_bounded_and_explicit_values_win,
        test_new_topic_clears_unreferenced_dimensions,
        test_unknown_ambiguous_and_unsupported_values_never_persist,
        test_untrusted_llm_decisions_cannot_authorize_state,
        test_equivalent_context_preserves_cache_and_factual_change_invalidates,
        test_state_cache_merge_is_atomic_and_conflicts_preserve_winner,
        test_completion_mapping_allows_authoritative_no_results_only,
        test_topic_only_correction_replaces_trusted_topic,
        test_route_compatibility_prevents_stronger_state_than_answer,
        test_diagnostics_are_bounded_and_exclude_states_or_prose,
        test_json_sse_planning_parity_and_isolation,
        test_codman_geography_is_not_a_bare_news_source,
        test_transition_planning_latency,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(f"\nConversation transition tests passed: {len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
