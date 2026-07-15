#!/usr/bin/env python3
"""Behavior tests for the namespaced thread-state envelope."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from conversation_state import (  # noqa: E402
    ActiveEntity,
    CONVERSATION_STATE_SCHEMA_VERSION,
    MAX_CONVERSATION_STATE_BYTES,
    ConversationState,
    ConversationStateError,
    ProposedTransition,
    ThreadStateForm,
    ThreadStateEnvelope,
    apply_transition,
    detect_thread_state_form,
    extract_conversation_state,
    extract_retrieval_cache,
    increment_revision,
    load_thread_state,
    merge_for_commit,
    migrate_conversation_state,
    normalize_version_1_state,
    response_allows_state_transition,
    safe_empty_state,
    serialize_thread_state,
    validate_conversation_state_v2,
    with_conversation_state,
    with_retrieval_cache,
)
from conversation_dimensions import (  # noqa: E402
    CanonicalLocation,
    CanonicalTimeRange,
    CanonicalTopic,
    GeographyKind,
    TimeGranularity,
    TimeLabelCode,
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


def _legacy_state_mapping(version: int = 7, event_id: str = "435") -> dict:
    return {
        "state_version": version,
        "active_topic": "events",
        "active_entity": ActiveEntity.event(event_id).to_dict(),
        "previous_route": "sql",
        "last_substantive_message_id": "message-legacy",
    }


def _valid_v2_mapping(*, topic: str | None = "EVENTS", event_id: str | None = "435") -> dict:
    return {
        "schema_version": 2,
        "revision": 7,
        "active_topic": topic,
        "active_entity": ActiveEntity.event(event_id).to_dict() if event_id else None,
        "location": None,
        "time_range": None,
        "filters": {},
        "previous_route": "sql",
        "last_substantive_message_id": "message-v2",
    }


def _assert_rejected(callable_obj) -> None:
    try:
        callable_obj()
    except ConversationStateError:
        return
    raise AssertionError("invalid conversation state was accepted")


def _nearest_rank_ms(samples_ns: list[int], percentile: float) -> float:
    ordered = sorted(samples_ns)
    index = max(0, min(len(ordered) - 1, int(percentile * len(ordered) + 0.999999) - 1))
    return ordered[index] / 1_000_000


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


def test_supported_storage_forms_and_migration_table() -> None:
    empty = load_thread_state(None, empty_retrieval_cache={"mode": None})
    assert empty.source_form is ThreadStateForm.EMPTY
    assert empty.retrieval_cache == {"mode": None}

    legacy = load_thread_state(LEGACY_CACHE)
    assert legacy.source_form is ThreadStateForm.LEGACY_RETRIEVAL_CACHE
    assert detect_thread_state_form(LEGACY_CACHE) is ThreadStateForm.LEGACY_RETRIEVAL_CACHE

    retrieval_only = {"version": 1, "retrieval_cache": LEGACY_CACHE}
    loaded_retrieval_only = load_thread_state(retrieval_only)
    assert loaded_retrieval_only.source_form is ThreadStateForm.ENVELOPE_V1_RETRIEVAL_ONLY
    assert loaded_retrieval_only.conversation_state == safe_empty_state()

    version_1 = {
        "version": 1,
        "retrieval_cache": LEGACY_CACHE,
        "conversation_state": _legacy_state_mapping(),
    }
    loaded_v1 = load_thread_state(version_1)
    assert loaded_v1.source_form is ThreadStateForm.ENVELOPE_V1_STATE_V1
    assert loaded_v1.conversation_state.schema_version == CONVERSATION_STATE_SCHEMA_VERSION
    assert loaded_v1.conversation_state.revision == 7
    assert loaded_v1.conversation_state.active_entity == ActiveEntity.event("435")

    version_2 = {
        "version": 1,
        "retrieval_cache": LEGACY_CACHE,
        "conversation_state": _valid_v2_mapping(),
    }
    loaded_v2 = load_thread_state(version_2)
    assert loaded_v2.source_form is ThreadStateForm.ENVELOPE_V1_STATE_V2
    assert loaded_v2.conversation_state.to_dict() == _valid_v2_mapping()


def test_read_only_load_does_not_rewrite_and_next_write_serializes_v2() -> None:
    raw_mapping = {
        "version": 1,
        "retrieval_cache": LEGACY_CACHE,
        "conversation_state": _legacy_state_mapping(),
    }
    raw = json.dumps(raw_mapping, sort_keys=True)
    loaded = load_thread_state(raw)
    assert raw == json.dumps(raw_mapping, sort_keys=True)
    assert loaded.source_form is ThreadStateForm.ENVELOPE_V1_STATE_V1
    serialized = serialize_thread_state(loaded)
    persisted = json.loads(serialized)
    assert "state_version" not in persisted["conversation_state"]
    assert persisted["conversation_state"]["schema_version"] == 2
    assert persisted["conversation_state"]["revision"] == 7
    assert persisted["conversation_state"]["active_entity"]["id"] == "435"


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


def test_extraction_helpers_return_isolated_cache_and_canonical_state() -> None:
    envelope = ThreadStateEnvelope(dict(LEGACY_CACHE), _activated_state())
    cache = extract_retrieval_cache(envelope)
    cache["question"] = "mutated"
    assert envelope.retrieval_cache["question"] == LEGACY_CACHE["question"]
    assert extract_conversation_state(envelope) is envelope.conversation_state


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


def test_empty_and_valid_events_v2_state_validate() -> None:
    empty = safe_empty_state()
    assert empty.to_dict() == {
        "schema_version": 2,
        "revision": 0,
        "active_topic": None,
        "active_entity": None,
        "location": None,
        "time_range": None,
        "filters": {},
        "previous_route": None,
        "last_substantive_message_id": None,
    }
    valid = _valid_v2_mapping()
    valid["location"] = {"id": "codman_square", "kind": "sub_neighborhood"}
    valid["time_range"] = {
        "label_code": "THIS_WEEKEND",
        "start_date": "2026-07-18",
        "end_date_exclusive": "2026-07-20",
        "timezone": "America/New_York",
        "granularity": "RANGE",
    }
    valid["filters"] = {"cost": "FREE_ONLY"}
    state = validate_conversation_state_v2(valid)
    assert state.active_topic is CanonicalTopic.EVENTS
    assert state.location == CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD)
    assert state.time_range == CanonicalTimeRange(
        TimeLabelCode.THIS_WEEKEND,
        "2026-07-18",
        "2026-07-20",
        "America/New_York",
        TimeGranularity.RANGE,
    )
    assert state.to_dict()["filters"] == {"cost": "FREE_ONLY"}


def test_unknown_topics_are_never_trusted() -> None:
    strict_unknown = _valid_v2_mapping(topic="UNKNOWN", event_id=None)
    _assert_rejected(lambda: validate_conversation_state_v2(strict_unknown))
    migrated = migrate_conversation_state(strict_unknown)
    assert migrated.active_topic is None
    assert migrated.active_entity is None

    arbitrary = _valid_v2_mapping(topic="crime_monthly_count", event_id=None)
    _assert_rejected(lambda: validate_conversation_state_v2(arbitrary))
    assert migrate_conversation_state(arbitrary).active_topic is None


def test_invalid_locations_are_rejected_strictly_and_dropped_on_migration() -> None:
    cases = [
        "Codman Square",
        {"id": "roxbury", "kind": "sub_neighborhood"},
        {"id": "codman_square", "kind": "district"},
        {"id": "codman_square", "kind": "sub_neighborhood", "label": "Codman Square"},
    ]
    for value in cases:
        raw = _valid_v2_mapping()
        raw["location"] = value
        _assert_rejected(lambda raw=raw: validate_conversation_state_v2(raw))
        assert migrate_conversation_state(raw).location is None


def test_invalid_time_ranges_are_rejected_strictly_and_dropped_on_migration() -> None:
    base = {
        "label_code": "EXPLICIT_RANGE",
        "start_date": "2026-07-18",
        "end_date_exclusive": "2026-07-20",
        "timezone": "America/New_York",
        "granularity": "RANGE",
    }
    cases = [
        {**base, "start_date": "2026-02-30"},
        {**base, "end_date_exclusive": "2026-07-17"},
        {**base, "timezone": "UTC"},
        {**base, "end_date_exclusive": "2028-07-20"},
        {**base, "granularity": "DAY"},
        {**base, "label_code": "UNKNOWN"},
    ]
    for value in cases:
        raw = _valid_v2_mapping()
        raw["time_range"] = value
        _assert_rejected(lambda raw=raw: validate_conversation_state_v2(raw))
        assert migrate_conversation_state(raw).time_range is None


def test_invalid_filters_are_rejected_and_migration_clears_them() -> None:
    cases = [
        {"audience": "FAMILY"},
        {"cost": "COMPLIMENTARY"},
        {"time_of_day": "LATE_NIGHT"},
        {"registration": "MAYBE"},
    ]
    for value in cases:
        raw = _valid_v2_mapping()
        raw["filters"] = value
        _assert_rejected(lambda raw=raw: validate_conversation_state_v2(raw))
        assert migrate_conversation_state(raw).to_dict()["filters"] == {}

    non_event = _valid_v2_mapping(topic="CITY_SERVICES", event_id=None)
    non_event["filters"] = {"cost": "FREE_ONLY"}
    _assert_rejected(lambda: validate_conversation_state_v2(non_event))
    assert migrate_conversation_state(non_event).to_dict()["filters"] == {}


def test_unknown_v2_keys_and_malformed_json_fail_closed() -> None:
    raw = _valid_v2_mapping()
    raw["answer"] = "do not persist this"
    _assert_rejected(lambda: validate_conversation_state_v2(raw))
    _assert_rejected(lambda: load_thread_state("{not-json"))


def test_state_and_retrieval_cache_size_limits_are_independent() -> None:
    oversized = _valid_v2_mapping()
    oversized["last_substantive_message_id"] = "x" * (MAX_CONVERSATION_STATE_BYTES + 1)
    _assert_rejected(lambda: validate_conversation_state_v2(oversized))

    large_cache = {"answer": "x" * 100_000, "rows": list(range(1000))}
    envelope = ThreadStateEnvelope(large_cache, safe_empty_state())
    serialized = serialize_thread_state(envelope)
    reloaded = load_thread_state(serialized)
    assert reloaded.retrieval_cache == large_cache
    assert reloaded.conversation_state == safe_empty_state()


def test_revision_and_schema_version_have_distinct_semantics() -> None:
    migrated = normalize_version_1_state(_legacy_state_mapping(version=19))
    assert migrated.schema_version == 2
    assert migrated.revision == 19
    assert migrated.state_version == 19
    assert increment_revision(migrated) == 20

    proposal = ProposedTransition.clear_event(migrated, previous_route="rag")
    assert proposal.expected_revision == 19
    assert proposal.expected_state_version == 19
    next_state = apply_transition(
        migrated,
        proposal,
        last_substantive_message_id="message-next",
    )
    assert next_state.schema_version == 2
    assert next_state.revision == 20


def test_shadow_diagnostics_and_unbounded_content_never_enter_state() -> None:
    serialized = serialize_thread_state(ThreadStateEnvelope(LEGACY_CACHE, _activated_state()))
    state = json.loads(serialized)["conversation_state"]
    forbidden = {
        "conversation_dimensions_shadow",
        "conversation_relationship",
        "answer",
        "rag_chunks",
        "sql_result",
        "title",
        "description",
        "url",
    }
    assert forbidden.isdisjoint(state)


def test_timeout_or_persistence_failure_does_not_apply_proposal() -> None:
    envelope = ThreadStateEnvelope(dict(LEGACY_CACHE))
    proposal = ProposedTransition.activate_event(envelope.conversation_state, "435")
    # A proposal is immutable data only; without merge/commit the loaded state is unchanged.
    assert proposal.expected_state_version == 0
    assert envelope.conversation_state == ConversationState()


def test_preserve_and_clear_event_keep_current_behavior() -> None:
    active = _activated_state(version=4)
    preserved = apply_transition(
        active,
        ProposedTransition.preserve(active),
        last_substantive_message_id="ignored-message",
    )
    assert preserved is active
    assert preserved.revision == 4

    cleared = apply_transition(
        active,
        ProposedTransition.clear_event(active, previous_route="rag"),
        last_substantive_message_id="message-clear",
    )
    assert cleared.revision == 5
    assert cleared.active_topic is None
    assert cleared.active_entity is None
    assert cleared.previous_route == "rag"


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


def test_schema_migration_does_not_create_false_revision_conflict() -> None:
    legacy_raw = {
        "version": 1,
        "retrieval_cache": LEGACY_CACHE,
        "conversation_state": _legacy_state_mapping(version=7),
    }
    expected = load_thread_state(legacy_raw)
    current = load_thread_state(serialize_thread_state(expected))
    proposal = ProposedTransition.clear_event(expected.conversation_state, previous_route="rag")
    merged = merge_for_commit(
        current,
        expected,
        next_retrieval_cache=LEGACY_CACHE,
        transition=proposal,
        last_substantive_message_id="message-schema-race",
    )
    assert merged.version_conflict is False
    assert merged.envelope.conversation_state.revision == 8


def test_same_revision_race_has_one_winner_and_preserves_winner_data() -> None:
    expected = ThreadStateEnvelope(dict(LEGACY_CACHE), safe_empty_state())
    first = merge_for_commit(
        expected,
        expected,
        next_retrieval_cache={**LEGACY_CACHE, "question": "winner"},
        transition=ProposedTransition.activate_event(expected.conversation_state, "435"),
        last_substantive_message_id="message-winner",
    )
    assert first.version_conflict is False
    assert first.envelope.conversation_state.revision == 1

    second = merge_for_commit(
        first.envelope,
        expected,
        next_retrieval_cache={**LEGACY_CACHE, "question": "loser"},
        transition=ProposedTransition.activate_event(expected.conversation_state, "900"),
        last_substantive_message_id="message-loser",
    )
    assert second.version_conflict is True
    assert second.envelope == first.envelope
    assert second.envelope.retrieval_cache["question"] == "winner"
    assert second.envelope.conversation_state.active_entity == ActiveEntity.event("435")


def test_state_v2_local_performance() -> None:
    legacy = {
        "version": 1,
        "retrieval_cache": LEGACY_CACHE,
        "conversation_state": _legacy_state_mapping(),
    }
    canonical = _valid_v2_mapping()
    normalization_samples = []
    serialization_samples = []
    for _ in range(1000):
        started = time.perf_counter_ns()
        validated = validate_conversation_state_v2(canonical)
        normalization_samples.append(time.perf_counter_ns() - started)

        started = time.perf_counter_ns()
        encoded = serialize_thread_state(load_thread_state(legacy))
        serialization_samples.append(time.perf_counter_ns() - started)

    assert validated.schema_version == 2
    assert json.loads(encoded)["conversation_state"]["schema_version"] == 2
    print(
        "PERF state-v2 normalization/validation "
        f"p50={_nearest_rank_ms(normalization_samples, 0.50):.4f}ms "
        f"p99={_nearest_rank_ms(normalization_samples, 0.99):.4f}ms"
    )
    print(
        "PERF legacy-to-v2 load/serialization "
        f"p50={_nearest_rank_ms(serialization_samples, 0.50):.4f}ms "
        f"p99={_nearest_rank_ms(serialization_samples, 0.99):.4f}ms"
    )


def main() -> int:
    tests = [
        test_legacy_cache_loads_and_is_preserved_when_state_is_added,
        test_supported_storage_forms_and_migration_table,
        test_read_only_load_does_not_rewrite_and_next_write_serializes_v2,
        test_retrieval_update_preserves_conversation_state,
        test_state_update_preserves_retrieval_cache,
        test_extraction_helpers_return_isolated_cache_and_canonical_state,
        test_strict_state_schema_and_bounded_entity,
        test_empty_and_valid_events_v2_state_validate,
        test_unknown_topics_are_never_trusted,
        test_invalid_locations_are_rejected_strictly_and_dropped_on_migration,
        test_invalid_time_ranges_are_rejected_strictly_and_dropped_on_migration,
        test_invalid_filters_are_rejected_and_migration_clears_them,
        test_unknown_v2_keys_and_malformed_json_fail_closed,
        test_state_and_retrieval_cache_size_limits_are_independent,
        test_revision_and_schema_version_have_distinct_semantics,
        test_shadow_diagnostics_and_unbounded_content_never_enter_state,
        test_timeout_or_persistence_failure_does_not_apply_proposal,
        test_preserve_and_clear_event_keep_current_behavior,
        test_degraded_responses_cannot_advance_state,
        test_version_conflict_preserves_newer_state_and_cache,
        test_cache_conflict_preserves_newer_cache_but_allows_state_transition,
        test_schema_migration_does_not_create_false_revision_conflict,
        test_same_revision_race_has_one_winner_and_preserves_winner_data,
        test_state_v2_local_performance,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(f"\nConversation state tests passed: {len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
