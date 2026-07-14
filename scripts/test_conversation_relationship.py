#!/usr/bin/env python3
"""Behavioral tests for diagnostic-only conversation relationship classification."""

from __future__ import annotations

import copy
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from conversation_relationship import (  # noqa: E402
    MAX_LLM_OUTPUT_BYTES,
    ConversationRelationship,
    DecisionSource,
    ReasonCode,
    build_relationship_context,
    classify_relationship,
    classify_shadow_relationship,
)


EVENT_ENTITY = {
    "type": "event",
    "id": "435",
    "title": "Virtual Baby Cafe",
    "date": "2026-07-16",
}


def _row(
    role: str,
    content: str,
    *,
    mode: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "response_mode": mode,
        "message_meta_json": json.dumps(meta or {}),
    }


def _turn(
    user: str,
    assistant: str,
    *,
    mode: str = "sql",
    entity: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return [
        _row("user", user, meta={"ui_context": {"untrusted": "not classifier input"}}),
        _row(
            "assistant",
            assistant,
            mode=mode,
            meta={"resolved_entity": entity} if entity else None,
        ),
    ]


def _classify(message: str, history: List[Dict[str, Any]]):
    decision = classify_shadow_relationship(message, history, enabled=True)
    assert decision is not None
    return decision


def _assert_relationship(
    message: str,
    history: List[Dict[str, Any]],
    expected: ConversationRelationship,
    *,
    clarification: Optional[bool] = None,
):
    decision = _classify(message, history)
    assert decision.relationship is expected, decision.to_dict()
    if clarification is not None:
        assert decision.requires_clarification is clarification, decision.to_dict()
    assert decision.decision_source is DecisionSource.RULE
    assert decision.shadow is True
    return decision


def test_required_deterministic_cases() -> None:
    selected_event = _turn(
        "Tell me about Virtual Baby Cafe",
        "Virtual Baby Cafe is on Thursday, July 16.",
        entity=EVENT_ENTITY,
    )
    _assert_relationship("Where is it?", selected_event, ConversationRelationship.FOLLOW_UP)
    _assert_relationship("Is it free?", selected_event, ConversationRelationship.FOLLOW_UP)
    _assert_relationship(
        "Only show free ones.",
        _turn("What events are happening Saturday?", "Here are three events."),
        ConversationRelationship.REFINEMENT,
    )
    _assert_relationship(
        "No, I mean the parade.",
        selected_event,
        ConversationRelationship.CORRECTION,
    )

    safety = _turn(
        "Is Dorchester safe right now?",
        "Here is the latest available safety information.",
        mode="hybrid",
    )
    _assert_relationship(
        "What about Codman Square?",
        safety,
        ConversationRelationship.REFINEMENT,
    )
    safety_codman = safety + _turn(
        "What about Codman Square?",
        "Here is the Codman Square context.",
        mode="hybrid",
    )
    _assert_relationship(
        "Are there events there this weekend?",
        safety_codman,
        ConversationRelationship.NEW_TOPIC,
    )
    _assert_relationship(
        "What are residents saying about potholes?",
        _turn("What events are happening?", "Here are this week's events."),
        ConversationRelationship.NEW_TOPIC,
    )

    why = _assert_relationship(
        "Why?",
        _turn("Are crime reports increasing?", "Reports increased in the available data."),
        ConversationRelationship.FOLLOW_UP,
        clarification=True,
    )
    assert why.reason_code is ReasonCode.UNSUPPORTED_CAUSALITY_FOLLOW_UP
    _assert_relationship("Thanks", safety, ConversationRelationship.SMALL_TALK)
    _assert_relationship(
        "What events are happening Saturday?",
        [],
        ConversationRelationship.STANDALONE,
    )

    old_crime = _turn("What is the crime trend?", "Here is the monthly trend.", mode="hybrid")
    _assert_relationship(
        "What events are happening Saturday?",
        old_crime,
        ConversationRelationship.NEW_TOPIC,
    )
    missing = _assert_relationship(
        "Where is it?",
        [],
        ConversationRelationship.FOLLOW_UP,
        clarification=True,
    )
    assert missing.reason_code is ReasonCode.MISSING_REFERENCE


def test_rule_resolved_cases_make_no_llm_calls() -> None:
    calls = 0

    def fail_if_called(_prompt: str, _timeout: float) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("rule-resolved classification called the LLM")

    decision = classify_shadow_relationship(
        "Where is it?",
        _turn("Tell me about the event", "It is listed.", entity=EVENT_ENTITY),
        enabled=True,
        llm_fallback_enabled=True,
        llm_classifier=fail_if_called,
    )
    assert decision is not None
    assert decision.relationship is ConversationRelationship.FOLLOW_UP
    assert calls == 0


def test_llm_failures_use_strict_conservative_fallback() -> None:
    ambiguous_history = _turn("Tell me something local", "Here is a local update.", mode="rag")

    cases = [
        ("malformed", lambda _prompt, _timeout: "not json", ReasonCode.CLASSIFIER_OUTPUT_INVALID),
        (
            "unknown enum",
            lambda _prompt, _timeout: json.dumps({
                "relationship": "SAME_TOPIC",
                "confidence": 0.5,
                "reason_code": "ambiguous_context",
                "retained": [],
                "cleared": [],
                "requires_clarification": True,
                "decision_source": "llm",
                "shadow": True,
            }),
            ReasonCode.CLASSIFIER_OUTPUT_INVALID,
        ),
        (
            "oversized",
            lambda _prompt, _timeout: "x" * (MAX_LLM_OUTPUT_BYTES + 1),
            ReasonCode.CLASSIFIER_OUTPUT_INVALID,
        ),
        (
            "timeout",
            lambda _prompt, _timeout: (_ for _ in ()).throw(TimeoutError()),
            ReasonCode.CLASSIFIER_TIMEOUT,
        ),
    ]
    for label, classifier, reason in cases:
        decision = classify_shadow_relationship(
            "Could that be relevant?",
            ambiguous_history,
            enabled=True,
            llm_fallback_enabled=True,
            llm_classifier=classifier,
        )
        assert decision is not None, label
        assert decision.relationship is ConversationRelationship.STANDALONE, label
        assert decision.decision_source is DecisionSource.FALLBACK, label
        assert decision.reason_code is reason, label
        assert decision.requires_clarification is True, label
        assert len(decision.cleared) == 7, label


def test_llm_contract_rejects_extra_keys_and_accepts_exact_schema() -> None:
    context = build_relationship_context(
        "Could that be relevant?",
        _turn("Tell me something local", "Here is a local update.", mode="rag"),
    )
    valid = {
        "relationship": "FOLLOW_UP",
        "confidence": 0.55,
        "reason_code": "ambiguous_context",
        "retained": ["active_topic"],
        "cleared": [],
        "requires_clarification": True,
        "decision_source": "llm",
        "shadow": True,
    }
    decision = classify_relationship(
        context,
        llm_fallback_enabled=True,
        llm_classifier=lambda _prompt, _timeout: json.dumps(valid),
    )
    assert decision.relationship is ConversationRelationship.FOLLOW_UP
    assert decision.decision_source is DecisionSource.LLM

    decision = classify_relationship(
        context,
        llm_fallback_enabled=True,
        llm_classifier=lambda _prompt, _timeout: json.dumps({**valid, "explanation": "no"}),
    )
    assert decision.decision_source is DecisionSource.FALLBACK
    assert decision.reason_code is ReasonCode.CLASSIFIER_OUTPUT_INVALID


def test_compact_context_is_bounded_and_uses_authoritative_assistant_metadata_only() -> None:
    history: List[Dict[str, Any]] = []
    for index in range(5):
        entity = EVENT_ENTITY if index == 4 else None
        history += _turn(
            f"What happened in local topic {index}? " + "u" * 800,
            "a" * 800,
            mode="rag",
            entity=entity,
        )
    context = build_relationship_context("m" * 2000, history)
    serialized = json.dumps(context.to_dict())
    assert len(context.current_message) == 1200
    assert len(context.previous_turns) == 2
    assert all(len(turn.user) <= 500 and len(turn.assistant) <= 500 for turn in context.previous_turns)
    assert context.resolved_entity is not None
    assert context.resolved_entity.entity_id == "435"
    assert "untrusted" not in serialized
    assert "ui_context" not in serialized
    assert "raw_chunks" not in serialized
    assert "sql_rows" not in serialized


def test_llm_prompt_does_not_include_prior_assistant_prose() -> None:
    history = _turn(
        "Tell me something local",
        "Private Resident Name said something in a source transcript.",
        mode="rag",
    )
    captured = {}

    def classifier(prompt: str, timeout: float) -> str:
        captured["prompt"] = prompt
        captured["timeout"] = timeout
        return json.dumps({
            "relationship": "STANDALONE",
            "confidence": 0.5,
            "reason_code": "ambiguous_context",
            "retained": [],
            "cleared": [],
            "requires_clarification": True,
            "decision_source": "llm",
            "shadow": True,
        })

    decision = classify_shadow_relationship(
        "Could that be relevant?",
        history,
        enabled=True,
        llm_fallback_enabled=True,
        llm_classifier=classifier,
    )
    assert decision is not None
    assert decision.decision_source is DecisionSource.LLM
    assert "Private Resident Name" not in captured["prompt"]
    assert "source transcript" not in captured["prompt"]
    assert '"assistant_result_exists":true' in captured["prompt"]
    assert 0.0 < captured["timeout"] <= 2.0


def test_thread_entity_isolation() -> None:
    thread_a = _turn("Tell me about the event", "It is listed.", entity=EVENT_ENTITY)
    thread_b = _turn("What are the latest 311 issues?", "Here are the 311 totals.")
    a = _classify("Where is it?", thread_a)
    b = _classify("Where is it?", thread_b)
    assert a.reason_code is ReasonCode.ACTIVE_EVENT_PROPERTY_QUESTION
    assert b.reason_code is ReasonCode.MISSING_REFERENCE
    assert b.requires_clarification is True


def test_shadow_mode_does_not_mutate_behavioral_inputs_or_response_schema() -> None:
    history = _turn("What events are happening?", "Here are the events.")
    message = "Only show free ones."
    retrieval_cache = {"question": "What events are happening?", "rows": [1, 2]}
    thread_state = {"route": "sql", "topic": "events"}
    response = {
        "answer": "Here are the free events.",
        "mode": "sql",
        "sources": [{"label": "Events"}],
        "resolved_entity": None,
    }
    originals = copy.deepcopy((history, retrieval_cache, thread_state, response))

    enabled = classify_shadow_relationship(message, history, enabled=True)
    disabled = classify_shadow_relationship(message, history, enabled=False)

    assert enabled is not None
    assert disabled is None
    assert (history, retrieval_cache, thread_state, response) == originals
    assert set(response) == {"answer", "mode", "sources", "resolved_entity"}
    assert "conversation_relationship" not in response


def test_rule_classifier_latency_is_negligible() -> None:
    history = _turn("Tell me about the event", "It is listed.", entity=EVENT_ENTITY)
    durations = []
    for _ in range(500):
        started = time.perf_counter()
        decision = classify_shadow_relationship("Where is it?", history, enabled=True)
        durations.append((time.perf_counter() - started) * 1000)
        assert decision is not None
    p50 = statistics.median(durations)
    p99 = sorted(durations)[int(len(durations) * 0.99) - 1]
    assert p50 < 2.0, p50
    assert p99 < 10.0, p99
    print(f"INFO deterministic classifier latency p50={p50:.3f}ms p99={p99:.3f}ms")


def main() -> int:
    tests = [
        test_required_deterministic_cases,
        test_rule_resolved_cases_make_no_llm_calls,
        test_llm_failures_use_strict_conservative_fallback,
        test_llm_contract_rejects_extra_keys_and_accepts_exact_schema,
        test_compact_context_is_bounded_and_uses_authoritative_assistant_metadata_only,
        test_llm_prompt_does_not_include_prior_assistant_prose,
        test_thread_entity_isolation,
        test_shadow_mode_does_not_mutate_behavioral_inputs_or_response_schema,
        test_rule_classifier_latency_is_negligible,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(f"\nConversation relationship tests passed: {len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
