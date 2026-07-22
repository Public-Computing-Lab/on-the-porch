#!/usr/bin/env python3
"""Deterministic active-emergency routing and integration contract tests."""

from __future__ import annotations

import ast
import inspect
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "on_the_porch"))

from conversation_dimensions import (  # noqa: E402
    CanonicalFilters,
    CanonicalLocation,
    CanonicalTimeRange,
    CanonicalTopic,
    CostFilter,
    GeographyKind,
    TimeGranularity,
    TimeLabelCode,
)
from conversation_state import (  # noqa: E402
    ActiveEntity,
    ConversationState,
    ProposedTransition,
    ThreadStateEnvelope,
    serialize_thread_state,
)
from conversation_transitions import (  # noqa: E402
    CacheAction,
    CompletionStatus,
    TransitionAction,
    TransitionReason,
)
from emergency_routing import (  # noqa: E402
    EMERGENCY_RESPONSE,
    MAX_MESSAGE_CHARS,
    MAX_SERIALIZED_BYTES,
    EmergencyCategory,
    EmergencyDecision,
    EmergencyDecisionSource,
    EmergencyReasonCode,
    detect_active_emergency,
)


TRUE_CASES = (
    (
        "There is a shooting happening outside right now.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ONGOING_SHOOTING,
    ),
    (
        "I hear gunshots right now.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.HEARD_GUNSHOTS_NOW,
    ),
    (
        "Someone has a gun and is threatening people.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ARMED_THREAT,
    ),
    (
        "A person is attacking someone outside.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ACTIVE_ATTACK,
    ),
    (
        "Someone is trying to break in and has a weapon.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ARMED_BREAK_IN,
    ),
    (
        "Someone is trying to break in.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ACTIVE_BREAK_IN,
    ),
    (
        "There is an intruder in my apartment.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ACTIVE_BREAK_IN,
    ),
    (
        "My house is on fire.",
        EmergencyCategory.FIRE,
        EmergencyReasonCode.BUILDING_FIRE,
    ),
    (
        "There is a fire in the building right now.",
        EmergencyCategory.FIRE,
        EmergencyReasonCode.BUILDING_FIRE,
    ),
    (
        "My car is on fire.",
        EmergencyCategory.FIRE,
        EmergencyReasonCode.VEHICLE_FIRE,
    ),
    (
        "The apartment is filling with smoke.",
        EmergencyCategory.FIRE,
        EmergencyReasonCode.SMOKE_IN_BUILDING,
    ),
    (
        "Someone is unconscious and not responding.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.UNRESPONSIVE_PERSON,
    ),
    (
        "He's unconscious.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.UNRESPONSIVE_PERSON,
    ),
    (
        "I can't wake him up.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.UNRESPONSIVE_PERSON,
    ),
    (
        "I can't breathe.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.BREATHING_EMERGENCY,
    ),
    (
        "I think I'm having a heart attack.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.SUSPECTED_HEART_ATTACK,
    ),
    (
        "Someone overdosed and is not responding.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.UNRESPONSIVE_OVERDOSE,
    ),
    (
        "Someone is overdosing.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.ACTIVE_OVERDOSE,
    ),
    (
        "A person is having a seizure and won't wake up.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.ACTIVE_SEIZURE,
    ),
    (
        "Someone is hurting my kid.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ACTIVE_ATTACK,
    ),
    (
        "My child is in immediate danger.",
        EmergencyCategory.OTHER_IMMEDIATE_DANGER,
        EmergencyReasonCode.CHILD_IN_DANGER,
    ),
    (
        "Someone says they are going to kill themselves right now.",
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_SELF_HARM,
    ),
    (
        "I am about to hurt myself.",
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_SELF_HARM,
    ),
    (
        "Someone is threatening to kill another person right now.",
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_HARM_TO_OTHERS,
    ),
    (
        "There's a shooting happening outside right now.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ONGOING_SHOOTING,
    ),
    (
        "Someone is shooting outside.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ONGOING_SHOOTING,
    ),
    (
        "There's a fire in the building right now.",
        EmergencyCategory.FIRE,
        EmergencyReasonCode.BUILDING_FIRE,
    ),
    (
        "I'm about to hurt myself.",
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_SELF_HARM,
    ),
    (
        "I'm going to kill myself.",
        EmergencyCategory.IMMINENT_SELF_HARM_OR_HARM,
        EmergencyReasonCode.IMMINENT_SELF_HARM,
    ),
    (
        "I'm having a heart attack.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.SUSPECTED_HEART_ATTACK,
    ),
    (
        "I'm unable to breathe.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.BREATHING_EMERGENCY,
    ),
    (
        "I'm having severe trouble breathing right now.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.BREATHING_EMERGENCY,
    ),
    (
        "There's a shooting happening outside.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ONGOING_SHOOTING,
    ),
    (
        "There's someone outside with a gun.",
        EmergencyCategory.VIOLENT_THREAT,
        EmergencyReasonCode.ARMED_THREAT,
    ),
    (
        "There's smoke filling the apartment.",
        EmergencyCategory.FIRE,
        EmergencyReasonCode.SMOKE_IN_BUILDING,
    ),
    (
        "There's a fire in the building.",
        EmergencyCategory.FIRE,
        EmergencyReasonCode.BUILDING_FIRE,
    ),
    (
        "Someone stopped breathing.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.BREATHING_EMERGENCY,
    ),
    (
        "They stopped breathing.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.BREATHING_EMERGENCY,
    ),
    (
        "Someone is turning blue and can't breathe.",
        EmergencyCategory.MEDICAL_EMERGENCY,
        EmergencyReasonCode.BREATHING_EMERGENCY,
    ),
)

FALSE_CASES = (
    "How many shootings happened last month?",
    "Was there a shooting yesterday?",
    "What are residents saying about shootings?",
    "Is Dorchester safe?",
    "Why did shootings increase?",
    "What should people do during a fire?",
    "The fire department is holding an event.",
    "There was a fire in 2024.",
    "Tell me about overdose statistics.",
    "What services help people experiencing suicidal thoughts?",
    "I heard about a shooting in the news.",
    "This music is fire.",
    "The team is killing it.",
    "I feel unsafe walking at night.",
    "Are there many break-ins in this neighborhood?",
    "Tell me about an event called Fire Safety Day.",
    "What are the public-health effects of smoke?",
    "I can't breathe when I laugh.",
    "I cannot breathe underwater in this game.",
    "Someone is shooting a movie downtown.",
    "What are breathing exercises?",
    "I get out of breath when I run.",
    "I had trouble breathing last year.",
    "What causes shortness of breath?",
    "Are breathing problems common with allergies?",
    "The meeting discussed air quality and breathing problems.",
    "Tell me about asthma statistics.",
    "I can't breathe in this song",
    "I need services for chronic breathing problems.",
)


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _import_api():
    import api_v2

    return api_v2


def _patch(module, **replacements):
    original = {name: getattr(module, name) for name in replacements}
    for name, value in replacements.items():
        setattr(module, name, value)

    class _Restore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            for name, value in original.items():
                setattr(module, name, value)

    return _Restore()


class _FakeConnection:
    def __init__(self) -> None:
        self.started = False
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def start_transaction(self) -> None:
        self.started = True

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _cache() -> Dict[str, Any]:
    return {
        "mode": "hybrid",
        "timestamp": "2026-07-15T12:00:00-04:00",
        "question": "What events are in Codman Square this weekend?",
        "sql_result": {"rows": [{"id": 435}]},
        "sql_query": "SELECT id FROM weekly_events",
        "rag_chunks": ["trusted prior chunk"],
        "rag_metadata": [{"source": "prior source"}],
        "answer": "Prior answer",
    }


def _state() -> ConversationState:
    return ConversationState(
        revision=7,
        active_topic=CanonicalTopic.EVENTS,
        active_entity=ActiveEntity.event("435"),
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
        time_range=CanonicalTimeRange(
            label_code=TimeLabelCode.THIS_WEEKEND,
            start_date="2026-07-18",
            end_date_exclusive="2026-07-20",
            timezone="America/New_York",
            granularity=TimeGranularity.RANGE,
        ),
        filters=CanonicalFilters(cost=CostFilter.FREE_ONLY),
        previous_route="sql",
        last_substantive_message_id="message-1",
    )


def test_true_positive_cases() -> None:
    for message, category, reason in TRUE_CASES:
        decision = detect_active_emergency(message)
        assert decision.is_active_emergency, message
        assert decision.category is category, message
        assert decision.reason_code is reason, message
        assert decision.decision_source is EmergencyDecisionSource.RULE
        assert decision.confidence == 1.0


def test_false_positive_cases() -> None:
    for message in FALSE_CASES:
        decision = detect_active_emergency(message)
        assert not decision.is_active_emergency, message
        assert decision.category is EmergencyCategory.NONE, message
        assert decision.reason_code is EmergencyReasonCode.NO_MATCH, message
        assert decision.confidence == 0.0


def test_exact_bounded_contract_and_serialization() -> None:
    decision = detect_active_emergency(TRUE_CASES[0][0])
    serialized = decision.to_dict()
    assert set(serialized) == {
        "is_active_emergency",
        "category",
        "reason_code",
        "decision_source",
        "confidence",
    }
    assert EmergencyDecision.from_mapping(serialized) == decision
    assert len(str(serialized).encode("utf-8")) < MAX_SERIALIZED_BYTES

    invalid_values = (
        {**serialized, "raw_message": TRUE_CASES[0][0]},
        {**serialized, "category": EmergencyCategory.NONE.value},
        {**serialized, "decision_source": "llm"},
        {**serialized, "confidence": 0.5},
    )
    for invalid in invalid_values:
        try:
            EmergencyDecision.from_mapping(invalid)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid decision accepted: {invalid}")


def test_no_llm_fallback_or_history_dependency() -> None:
    signature = inspect.signature(detect_active_emergency)
    assert tuple(signature.parameters) == ("message",)
    try:
        detect_active_emergency(  # type: ignore[call-arg]
            TRUE_CASES[0][0],
            llm_classifier=lambda *_args: "unsafe",
        )
    except TypeError:
        pass
    else:
        raise AssertionError("detector accepted an LLM fallback")

    long_irrelevant_history = [
        {"role": "user", "content": "There was a fire in 2024."}
        for _ in range(10_000)
    ]
    assert long_irrelevant_history
    current = detect_active_emergency("How many shootings happened last month?")
    assert not current.is_active_emergency


def test_invalid_and_oversized_inputs_are_not_parsed() -> None:
    for value in (None, 123, {}, "", " "):
        decision = detect_active_emergency(value)
        assert not decision.is_active_emergency
        assert decision.reason_code is EmergencyReasonCode.INVALID_INPUT
    oversized = ("x" * MAX_MESSAGE_CHARS) + " someone is shooting outside"
    decision = detect_active_emergency(oversized)
    assert not decision.is_active_emergency
    assert decision.reason_code is EmergencyReasonCode.INVALID_INPUT

    api = _import_api()
    with api.app.test_request_context("/test"):
        response, status = api._validate_message("x" * (api.Config.MAX_MESSAGE_LENGTH + 1))
        assert status == 400
        assert response.get_json()["code"] == "message_too_long"


def test_no_raw_message_in_diagnostics() -> None:
    message = "Someone has a gun at 123 Private Street and is threatening people."
    decision = detect_active_emergency(message)
    serialized = str(decision.to_dict())
    assert decision.is_active_emergency
    assert "123" not in serialized
    assert "private" not in serialized.lower()
    assert "threatening people" not in serialized.lower()


def test_shared_agent_result_and_heavy_path_bypass() -> None:
    api = _import_api()
    message = TRUE_CASES[0][0]
    original_cache = _cache()
    decision = detect_active_emergency(message)
    result = api._emergency_agent_result(decision, original_cache)
    assert result["answer"] == EMERGENCY_RESPONSE
    assert result["answer"].startswith("Call 911 now.")
    assert result["mode"] == "emergency"
    assert result["sources"] == []
    assert result["model_name"] is None
    assert result["llm_calls"] == 0
    assert result["embedding_calls"] == 0
    assert result["sql_calls"] == 0
    assert result["vector_calls"] == 0
    assert result["retrieval_cache"] == original_cache
    assert result["retrieval_cache"] is not original_cache

    def forbidden(*_args, **_kwargs):
        raise AssertionError("heavy path was called for an active emergency")

    class _ForbiddenCache:
        def get(self, *_args, **_kwargs):
            return forbidden()

        def put(self, *_args, **_kwargs):
            return forbidden()

    with _patch(
        api,
        _semantic_cache=_ForbiddenCache(),
        is_small_talk=forbidden,
        _try_311_advisory_response=forbidden,
        _try_crime_clarification_response=forbidden,
        _check_if_needs_new_data=forbidden,
        _route_question=forbidden,
        run_execution_pipeline=forbidden,
        _run_sql=forbidden,
        _run_rag=forbidden,
        _run_hybrid=forbidden,
        _get_llm_client=forbidden,
    ):
        bypassed = api._execute_agent_response(
            message,
            conversation_history=[{"role": "assistant", "content": "untrusted history"}],
            retrieval_cache=original_cache,
        )
    assert bypassed["answer"] == EMERGENCY_RESPONSE


def test_selected_event_is_lower_precedence_than_emergency() -> None:
    api = _import_api()
    decision = detect_active_emergency(TRUE_CASES[0][0])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("selected-event handling preceded emergency routing")

    with _patch(api, _selected_event_agent_result=forbidden):
        result = api._execute_agent_response_with_event_context(
            TRUE_CASES[0][0],
            conversation_history=[],
            retrieval_cache=_cache(),
            event_resolution=object(),
            emergency_decision=decision,
        )
    assert result["mode"] == "emergency"


def test_emergency_json_path_skips_history_load() -> None:
    """Modern emergency turns must not load conversation history."""
    source = (ROOT / "api" / "api_v2.py").read_text(encoding="utf-8")
    json_fn = source.split("def post_conversation_message(thread_id: str):", 1)[1]
    json_fn = json_fn.split("def post_conversation_message_stream", 1)[0]
    stream_fn = source.split(
        "def post_conversation_message_stream(thread_id: str):", 1
    )[1]
    stream_fn = stream_fn.split("def chat():", 1)[0]
    for label, body in (("json", json_fn), ("sse", stream_fn)):
        assert "if not emergency_decision.is_active_emergency:" in body, label
        assert "_fetch_recent_history" in body, label
        before, after = body.split(
            "if not emergency_decision.is_active_emergency:", 1
        )
        assert "_fetch_recent_history" not in before, label
        assert "_fetch_recent_history" in after, label
        assert "set_initial_title=(" in body or "set_initial_title=" in body
        assert "if emergency" in body.replace("_decision", "").replace(
            "_request", ""
        ) or "emergency_decision.is_active_emergency" in body
        assert "False" in body and "set_initial_title" in body


def test_state_cache_revision_and_active_event_are_preserved() -> None:
    api = _import_api()
    state = _state()
    cache = _cache()
    transition = api._emergency_conversation_transition(state)
    assert transition.action is TransitionAction.NO_CHANGE
    assert transition.cache_action is CacheAction.PRESERVE
    assert transition.completion_status is CompletionStatus.ACCEPTABLE
    assert transition.reason_codes == (TransitionReason.EMERGENCY_CONTEXT_PRESERVED,)
    assert transition.next_state == state
    proposal = transition.state_proposal(state)
    assert proposal == ProposedTransition.preserve(state)
    persisted_cache, invalidate = api._cache_for_conversation_transition(
        current_cache=cache,
        next_cache=dict(cache),
        transition=transition,
    )
    assert persisted_cache == cache
    assert invalidate is False
    assert state.revision == 7
    assert state.active_entity == ActiveEntity.event("435")
    assert state.last_substantive_message_id == "message-1"


def test_emergency_metadata_is_bounded_and_hidden() -> None:
    api = _import_api()
    decision = detect_active_emergency(TRUE_CASES[0][0])
    transition = api._emergency_conversation_transition(_state())
    metadata = api._user_message_metadata(
        event_context=None,
        relationship=None,
        dimensions=None,
        transition=transition,
        emergency_decision=decision,
    )
    assert metadata and metadata["emergency_decision"] == decision.to_dict()
    row = {
        "id": "message-1",
        "thread_id": "thread-1",
        "role": "user",
        "content": TRUE_CASES[0][0],
        "response_mode": None,
        "sources_json": None,
        "model_name": None,
        "message_meta_json": api.json_dumps(metadata),
        "created_at": datetime(2026, 7, 16),
    }
    public = api._serialize_message(row)
    assert "emergency_decision" not in public
    assert "conversation_transition" not in public


def test_persistence_skips_interaction_log_and_nested_connection() -> None:
    api = _import_api()
    state = _state()
    envelope = ThreadStateEnvelope(_cache(), state)
    core_conn = _FakeConnection()
    connection_calls = []
    inserted = iter(({"id": "user-message"}, {"id": "assistant-message"}))
    last_message_at = datetime(2026, 7, 16, 0, 0, 0)

    def get_connection():
        connection_calls.append(True)
        return core_conn

    def forbidden_log(**_kwargs):
        raise AssertionError("raw emergency content reached interaction_log")

    with _patch(
        api,
        get_db_connection=get_connection,
        _lock_thread_for_persistence=lambda *_args: {
            "title": "Existing conversation",
            "last_message_at": last_message_at,
            "thread_state_json": serialize_thread_state(envelope),
        },
        _insert_message=lambda *_args, **_kwargs: next(inserted),
        _merge_locked_thread_state=lambda *_args, **_kwargs: {
            "transition_applied": False,
            "cache_update_applied": True,
            "state": state,
        },
        _fetch_thread=lambda *_args, **_kwargs: {"id": "thread-1"},
        log_interaction=forbidden_log,
    ):
        result = api._persist_conversation_turn(
            thread_id="thread-1",
            user_id="user-1",
            session_id="session-1",
            expected_envelope=envelope,
            next_retrieval_cache=_cache(),
            transition=ProposedTransition.preserve(state),
            user_content=TRUE_CASES[0][0],
            user_meta={},
            assistant_content=EMERGENCY_RESPONSE,
            assistant_mode="emergency",
            assistant_sources=[],
            assistant_model_name=None,
            resolved_entity=None,
            set_initial_title=False,
            interaction_log_enabled=False,
            expected_last_message_at=last_message_at,
        )
    assert result["assistant_message"]["id"] == "assistant-message"
    assert connection_calls == [True]
    assert core_conn.committed and core_conn.closed and not core_conn.rolled_back


def test_emergency_timestamp_conflict_precedes_message_insert() -> None:
    api = _import_api()
    state = _state()
    envelope = ThreadStateEnvelope(_cache(), state)
    core_conn = _FakeConnection()
    insert_calls = []
    expected_time = datetime(2026, 7, 16, 0, 0, 0)
    newer_time = datetime(2026, 7, 16, 0, 0, 1)
    with _patch(
        api,
        get_db_connection=lambda: core_conn,
        _lock_thread_for_persistence=lambda *_args: {
            "title": "Existing conversation",
            "last_message_at": newer_time,
            "thread_state_json": serialize_thread_state(envelope),
        },
        _insert_message=lambda *_args, **_kwargs: insert_calls.append(True),
    ):
        try:
            api._persist_conversation_turn(
                thread_id="thread-1",
                user_id="user-1",
                session_id="session-1",
                expected_envelope=envelope,
                next_retrieval_cache=_cache(),
                transition=ProposedTransition.preserve(state),
                user_content=TRUE_CASES[0][0],
                user_meta={},
                assistant_content=EMERGENCY_RESPONSE,
                assistant_mode="emergency",
                assistant_sources=[],
                assistant_model_name=None,
                resolved_entity=None,
                set_initial_title=False,
                interaction_log_enabled=False,
                expected_last_message_at=expected_time,
            )
        except api.ConversationConflictError:
            pass
        else:
            raise AssertionError("stale emergency writer did not conflict")
    assert insert_calls == []
    assert core_conn.rolled_back and core_conn.closed and not core_conn.committed


def test_detector_performance() -> None:
    messages = [item[0] for item in TRUE_CASES] + list(FALSE_CASES)
    timings = []
    for index in range(10_000):
        started = time.perf_counter()
        detect_active_emergency(messages[index % len(messages)])
        timings.append((time.perf_counter() - started) * 1000.0)
    p50 = statistics.median(timings)
    p99 = _nearest_rank(timings, 0.99)
    assert p99 < 1.0, (p50, p99)
    print(f"PERF emergency detector p50={p50:.4f}ms p99={p99:.4f}ms")


def test_emergency_module_has_only_standard_library_dependencies() -> None:
    source = (ROOT / "on_the_porch" / "emergency_routing.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imports <= {
        "__future__",
        "dataclasses",
        "enum",
        "json",
        "re",
        "typing",
        "unicodedata",
    }


def main() -> int:
    tests = (
        test_true_positive_cases,
        test_false_positive_cases,
        test_exact_bounded_contract_and_serialization,
        test_no_llm_fallback_or_history_dependency,
        test_invalid_and_oversized_inputs_are_not_parsed,
        test_no_raw_message_in_diagnostics,
        test_shared_agent_result_and_heavy_path_bypass,
        test_selected_event_is_lower_precedence_than_emergency,
        test_emergency_json_path_skips_history_load,
        test_state_cache_revision_and_active_event_are_preserved,
        test_emergency_metadata_is_bounded_and_hidden,
        test_persistence_skips_interaction_log_and_nested_connection,
        test_emergency_timestamp_conflict_precedes_message_insert,
        test_detector_performance,
        test_emergency_module_has_only_standard_library_dependencies,
    )
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(
        "\nEmergency routing tests passed: "
        f"{len(tests)}/{len(tests)} suites, "
        f"{len(TRUE_CASES)} true positives, {len(FALSE_CASES)} false positives"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
