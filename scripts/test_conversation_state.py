#!/usr/bin/env python3
"""Behavior tests for the namespaced thread-state envelope."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from conversation_state import (  # noqa: E402
    ActiveEntity,
    ConversationState,
    ConversationStateError,
    ProposedTransition,
    ThreadStateEnvelope,
    apply_transition,
    load_thread_state,
    merge_for_commit,
    response_allows_state_transition,
    serialize_thread_state,
    with_conversation_state,
    with_retrieval_cache,
)


LEGACY_CACHE = {
    "mode": "sql",
    "timestamp": "2026-07-14T12:00:00",
    "question": "What events are happening?",
    "sql_result": {"rows": [{"id": 435}]},
    "sql_query": "SELECT id FROM weekly_events",
    "rag_chunks": None,
    "rag_metadata": None,
    "answer": "Here are the events.",
}


def _activated_state(version: int = 1, event_id: str = "435") -> ConversationState:
    return ConversationState(
        state_version=version,
        active_topic="events",
        active_entity=ActiveEntity.event(event_id),
        previous_route="sql",
        last_substantive_message_id="message-1",
    )


def test_legacy_cache_loads_and_is_preserved_when_state_is_added() -> None:
    envelope = load_thread_state(json.dumps(LEGACY_CACHE))
    assert envelope.retrieval_cache == LEGACY_CACHE
    assert envelope.conversation_state == ConversationState()
    state = apply_transition(
        envelope.conversation_state,
        ProposedTransition.activate_event(envelope.conversation_state, "435"),
        last_substantive_message_id="message-1",
    )
    encoded = serialize_thread_state(with_conversation_state(envelope, state))
    reloaded = load_thread_state(encoded)
    assert reloaded.retrieval_cache == LEGACY_CACHE
    assert reloaded.conversation_state.active_entity == ActiveEntity.event("435")


def test_retrieval_update_preserves_conversation_state() -> None:
    envelope = ThreadStateEnvelope(dict(LEGACY_CACHE), _activated_state())
    updated_cache = {**LEGACY_CACHE, "question": "Updated question"}
    updated = with_retrieval_cache(envelope, updated_cache)
    assert updated.retrieval_cache == updated_cache
    assert updated.conversation_state == envelope.conversation_state


def test_state_update_preserves_retrieval_cache() -> None:
    envelope = ThreadStateEnvelope(dict(LEGACY_CACHE))
    updated = with_conversation_state(envelope, _activated_state())
    assert updated.retrieval_cache == LEGACY_CACHE
    assert updated.conversation_state.active_entity == ActiveEntity.event("435")


def test_strict_state_schema_and_bounded_entity() -> None:
    valid = ThreadStateEnvelope(dict(LEGACY_CACHE), _activated_state()).to_dict()
    invalid_cases = [
        {**valid, "extra": True},
        {**valid, "version": 2},
        {**valid, "conversation_state": {**valid["conversation_state"], "title": "private"}},
        {
            **valid,
            "conversation_state": {
                **valid["conversation_state"],
                "active_entity": {
                    **valid["conversation_state"]["active_entity"],
                    "title": "Virtual Baby Cafe",
                },
            },
        },
        {
            **valid,
            "conversation_state": {
                **valid["conversation_state"],
                "active_entity": {"type": "event", "id": "435", "source_ref": "event:999"},
            },
        },
        {
            **valid,
            "conversation_state": {
                **valid["conversation_state"],
                "active_entity": {"type": "event", "id": True, "source_ref": "event:True"},
            },
        },
        {
            **valid,
            "conversation_state": {
                **valid["conversation_state"],
                "active_entity": {
                    "type": "event",
                    "id": "2147483648",
                    "source_ref": "event:2147483648",
                },
            },
        },
    ]
    for raw in invalid_cases:
        try:
            load_thread_state(raw)
        except ConversationStateError:
            continue
        raise AssertionError(f"invalid state accepted: {raw}")


def test_timeout_or_persistence_failure_does_not_apply_proposal() -> None:
    envelope = ThreadStateEnvelope(dict(LEGACY_CACHE))
    proposal = ProposedTransition.activate_event(envelope.conversation_state, "435")
    # A proposal is immutable data only; without merge/commit the loaded state is unchanged.
    assert proposal.expected_state_version == 0
    assert envelope.conversation_state == ConversationState()


def test_degraded_responses_cannot_advance_state() -> None:
    assert response_allows_state_transition({
        "mode": "budget_timeout",
        "result": {"error": "request_budget_exhausted"},
    }) is False
    assert response_allows_state_transition({
        "mode": "hybrid",
        "execution_degraded": True,
        "result": {"answer": "A safe partial answer."},
    }) is False
    assert response_allows_state_transition({
        "mode": "hybrid",
        "result": {
            "parts": [
                {"path": "sql", "answer": "A supported fact."},
                {"path": "rag", "error": "request_budget_exhausted"},
            ],
        },
    }) is False
    assert response_allows_state_transition({
        "mode": "hybrid",
        "result": {
            "sql": {"answer": "A supported fact."},
            "rag": {"timed_out": True},
        },
    }) is False
    assert response_allows_state_transition({
        "mode": "sql",
        "result": {"answer": "A complete answer.", "db_backed": True},
    }) is True


def test_version_conflict_preserves_newer_state_and_cache() -> None:
    expected = ThreadStateEnvelope(dict(LEGACY_CACHE), _activated_state(1, "435"))
    newer_cache = {**LEGACY_CACHE, "question": "Concurrent request"}
    current = ThreadStateEnvelope(newer_cache, _activated_state(2, "900"))
    proposal = ProposedTransition.clear_event(expected.conversation_state, previous_route="rag")
    merged = merge_for_commit(
        current,
        expected,
        next_retrieval_cache={"mode": "rag", "question": "stale writer"},
        transition=proposal,
        last_substantive_message_id="message-2",
    )
    assert merged.version_conflict is True
    assert merged.envelope == current
    assert merged.envelope.conversation_state.active_entity == ActiveEntity.event("900")
    assert merged.envelope.retrieval_cache == newer_cache


def test_cache_conflict_preserves_newer_cache_but_allows_state_transition() -> None:
    expected = ThreadStateEnvelope(dict(LEGACY_CACHE))
    current = ThreadStateEnvelope({**LEGACY_CACHE, "question": "newer cache"})
    proposal = ProposedTransition.activate_event(expected.conversation_state, "435")
    merged = merge_for_commit(
        current,
        expected,
        next_retrieval_cache={"mode": "sql", "question": "older request"},
        transition=proposal,
        last_substantive_message_id="message-3",
    )
    assert merged.version_conflict is False
    assert merged.cache_update_applied is False
    assert merged.envelope.retrieval_cache["question"] == "newer cache"
    assert merged.envelope.conversation_state.active_entity == ActiveEntity.event("435")


def main() -> int:
    tests = [
        test_legacy_cache_loads_and_is_preserved_when_state_is_added,
        test_retrieval_update_preserves_conversation_state,
        test_state_update_preserves_retrieval_cache,
        test_strict_state_schema_and_bounded_entity,
        test_timeout_or_persistence_failure_does_not_apply_proposal,
        test_degraded_responses_cannot_advance_state,
        test_version_conflict_preserves_newer_state_and_cache,
        test_cache_conflict_preserves_newer_cache_but_allows_state_transition,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(f"\nConversation state tests passed: {len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
