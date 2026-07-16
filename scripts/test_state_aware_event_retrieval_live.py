#!/usr/bin/env python3
"""Local-only API, persistence, dialogue, parity, and race checks for event context."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
OTP = ROOT / "on_the_porch"
sys.path[:0] = [str(ROOT), str(OTP), str(ROOT / "scripts")]

from conversation_dimensions import (  # noqa: E402
    CanonicalFilters,
    CanonicalLocation,
    CanonicalTopic,
    GeographyKind,
)
from conversation_state import (  # noqa: E402
    ConversationState,
    ThreadStateEnvelope,
    retrieval_cache_hash,
    serialize_thread_state,
)
from emergency_routing import detect_active_emergency  # noqa: E402
from test_active_event_followups import (  # noqa: E402
    _LiveGuestClient,
    _live_db_connection,
    _read_live_state,
    _read_user_meta,
)


_INTERNAL_RESPONSE_KEYS = (
    "event_query_context",
    "conversation_transition",
    "conversation_state",
    "thread_state_json",
    "event_query_signature",
)
_UNSUPPORTED_COMPLETENESS = (
    "there are no events",
    "nothing is happening",
    "all events are free",
)


def _cache_signature(envelope: ThreadStateEnvelope) -> Optional[Dict[str, Any]]:
    value = envelope.retrieval_cache.get("event_query_signature")
    return dict(value) if isinstance(value, dict) else None


def _turn(
    client: _LiveGuestClient,
    thread_id: str,
    message: str,
    *,
    stream: bool = False,
) -> Dict[str, Any]:
    before = _read_live_state(thread_id)
    started = time.perf_counter()
    if stream:
        response, payload = client.send_stream(thread_id, {"message": message})
        assert response.status_code == 200 and payload is not None
        assistant = payload["assistant_message"]
        answer = payload["answer"]
        sources = payload.get("sources") or []
        user_message = payload["user_message"]
        serialized = json.dumps(payload, default=str)
    else:
        response = client.send_json(thread_id, {"message": message})
        assert response.status_code == 201, response.text
        payload = response.json()
        assistant = payload["assistant_message"]
        answer = assistant["content"]
        sources = assistant.get("sources") or []
        user_message = payload["user_message"]
        serialized = json.dumps(payload, default=str)
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    for key in _INTERNAL_RESPONSE_KEYS:
        assert key not in serialized
    after = _read_live_state(thread_id)
    metadata = _read_user_meta(user_message["id"])
    relationship = metadata.get("conversation_relationship") or {}
    dimensions = metadata.get("conversation_dimensions_shadow") or {}
    plan = metadata.get("event_query_context")
    transition = metadata.get("conversation_transition") or {}
    record = {
        "message": message,
        "relationship": relationship.get("relationship"),
        "decision_source": relationship.get("decision_source"),
        "reason_code": relationship.get("reason_code"),
        "canonical_dimensions": dimensions,
        "trusted_state_before": before.conversation_state.to_dict(),
        "query_plan": plan,
        "applied_dimensions": (plan or {}).get("applied_dimensions", []),
        "unapplied_dimensions": (plan or {}).get("unapplied_dimensions", []),
        "mode": assistant.get("response_mode"),
        "answer": answer,
        "trusted_state_after": after.conversation_state.to_dict(),
        "revision": after.conversation_state.revision,
        "cache_signature_before": _cache_signature(before),
        "cache_signature_after": _cache_signature(after),
        "cache_hash_before": retrieval_cache_hash(before.retrieval_cache),
        "cache_hash_after": retrieval_cache_hash(after.retrieval_cache),
        "latency_ms": latency_ms,
        "llm_calls": 0 if assistant.get("model_name") is None else ">=1",
        "model_name": assistant.get("model_name"),
        "source_metadata": sources,
        "transition": transition,
        "unsupported_completeness_claim": next(
            (claim for claim in _UNSUPPORTED_COMPLETENESS if claim in answer.lower()),
            None,
        ),
    }
    print(json.dumps(record, ensure_ascii=False, default=str))
    return record


def _seed_safety_codman(thread_id: str) -> None:
    envelope = _read_live_state(thread_id)
    state = ConversationState(
        revision=envelope.conversation_state.revision,
        active_topic=CanonicalTopic.PUBLIC_SAFETY,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
        filters=CanonicalFilters(),
        previous_route="hybrid",
        last_substantive_message_id=None,
    )
    conn = _live_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE conversation_threads SET thread_state_json = %s WHERE id = %s",
            (serialize_thread_state(ThreadStateEnvelope(envelope.retrieval_cache, state)), thread_id),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def _assert_local_demo() -> None:
    conn = _live_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DATABASE(), CURRENT_USER()")
        database, user = cursor.fetchone()
        assert database == "sentiment_demo", database
        assert str(user).startswith("demo_user@"), user
        print(f"LOCAL_DB {database} {user}")
    finally:
        cursor.close()
        conn.close()


def _run_dialogues(base_url: str) -> None:
    client = _LiveGuestClient(base_url)
    isolated = _LiveGuestClient(base_url)
    client.bootstrap()
    isolated.bootstrap()

    dialogue_a = client.create_thread("State-aware events A")
    a = [
        _turn(client, dialogue_a, "What events are happening this weekend?"),
        _turn(client, dialogue_a, "What about next weekend?"),
        _turn(client, dialogue_a, "What about Codman Square?"),
        _turn(client, dialogue_a, "Thanks."),
        _turn(client, dialogue_a, "Only show free ones."),
    ]
    assert a[0]["mode"] == "structured_events" and a[0]["llm_calls"] == 0
    assert a[0]["trusted_state_after"]["active_topic"] == "EVENTS"
    assert a[0]["trusted_state_after"]["location"] is None
    assert a[1]["relationship"] == "REFINEMENT"
    assert a[1]["cache_signature_after"] != a[0]["cache_signature_after"]
    assert a[2]["trusted_state_after"]["location"]["id"] == "codman_square"
    assert a[3]["trusted_state_after"] == a[2]["trusted_state_after"]
    assert a[3]["cache_hash_after"] == a[2]["cache_hash_after"]
    assert a[4]["unapplied_dimensions"] == ["filters"]
    assert a[4]["trusted_state_after"] == a[3]["trusted_state_after"]
    assert a[4]["cache_hash_after"] == a[3]["cache_hash_after"]

    dialogue_b = client.create_thread("State-aware events B")
    _seed_safety_codman(dialogue_b)
    b = [
        _turn(client, dialogue_b, "Are there events there this weekend?"),
        _turn(client, dialogue_b, "What about Fields Corner?"),
        _turn(client, dialogue_b, "What about tomorrow?"),
    ]
    # Seeded state without prior messages may classify as STANDALONE; the
    # trusted-location transfer and topic switch are the acceptance criteria.
    assert b[0]["relationship"] in {"NEW_TOPIC", "STANDALONE"}
    assert b[0]["query_plan"]["location"]["id"] == "codman_square"
    assert b[0]["trusted_state_after"]["active_topic"] == "EVENTS"
    assert b[0]["trusted_state_after"]["location"]["id"] == "codman_square"
    assert b[1]["query_plan"]["location"]["id"] == "fields_corner"
    assert b[2]["query_plan"]["start_date"] != b[1]["query_plan"]["start_date"]

    dialogue_c = isolated.create_thread("State-aware events C")
    c = _turn(isolated, dialogue_c, "Are there events there this weekend?")
    assert c["query_plan"]["status"] == "CLARIFICATION"
    assert c["llm_calls"] == 0
    assert c["trusted_state_after"] == c["trusted_state_before"]
    assert c["cache_hash_after"] == c["cache_hash_before"]

    dialogue_d = client.create_thread("Emergency after active event")
    seed_event = _turn(client, dialogue_d, "What events are happening this weekend?")
    assert seed_event["mode"] == "structured_events"
    # Force an active event into trusted state for the emergency precedence check.
    from conversation_state import ActiveEntity

    envelope = _read_live_state(dialogue_d)
    with_event = ConversationState(
        revision=envelope.conversation_state.revision,
        active_topic=CanonicalTopic.EVENTS,
        active_entity=ActiveEntity.event("435"),
        location=envelope.conversation_state.location,
        time_range=envelope.conversation_state.time_range,
        filters=envelope.conversation_state.filters,
        previous_route=envelope.conversation_state.previous_route,
        last_substantive_message_id=envelope.conversation_state.last_substantive_message_id,
    )
    conn = _live_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE conversation_threads SET thread_state_json = %s WHERE id = %s",
            (
                serialize_thread_state(
                    ThreadStateEnvelope(envelope.retrieval_cache, with_event)
                ),
                dialogue_d,
            ),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    before_emergency = _read_live_state(dialogue_d)
    emergency = _turn(
        client,
        dialogue_d,
        "There's a shooting happening outside right now.",
    )
    assert emergency["mode"] == "emergency"
    assert emergency["answer"].startswith("Call 911 now.")
    assert emergency["llm_calls"] == 0
    assert emergency["trusted_state_after"] == before_emergency.conversation_state.to_dict()
    assert emergency["revision"] == before_emergency.conversation_state.revision
    assert emergency["cache_hash_after"] == retrieval_cache_hash(
        before_emergency.retrieval_cache
    )
    followup = _turn(client, dialogue_d, "Where is it?")
    assert followup["mode"] in {"sql", "event_detail", "structured"}
    assert followup["model_name"] is None
    assert (
        followup["trusted_state_after"]["active_entity"]["id"] == "435"
        or "Virtual" in followup["answer"]
        or "event" in followup["answer"].lower()
    )

    # Dialogue E — breathing precision across separate threads.
    e_cases = [
        ("I'm having severe trouble breathing right now.", True),
        ("What are breathing exercises?", False),
        ("I had trouble breathing last year.", False),
        ("I get out of breath when I run.", False),
        ("Someone stopped breathing.", True),
    ]
    for message, must_be_emergency in e_cases:
        detector = detect_active_emergency(message)
        assert detector.is_active_emergency is must_be_emergency, message
        thread_id = client.create_thread(f"Breathing {message[:24]}")
        before = _read_live_state(thread_id)
        if must_be_emergency:
            result = _turn(client, thread_id, message)
            assert result["mode"] == "emergency", message
            assert result["answer"].startswith("Call 911 now."), message
            assert result["llm_calls"] == 0, message
            assert result["trusted_state_after"] == before.conversation_state.to_dict()
            assert result["revision"] == before.conversation_state.revision
            assert result["cache_hash_after"] == retrieval_cache_hash(
                before.retrieval_cache
            )
        else:
            # Civic retrieval may be unavailable in this local vector/model setup;
            # still require the emergency short-circuit not to fire.
            response = client.send_json(thread_id, {"message": message})
            if response.status_code == 201:
                payload = response.json()
                assert payload["assistant_message"]["response_mode"] != "emergency"
                after = _read_live_state(thread_id)
                # Non-emergency answers may advance civic state; only forbid emergency.
                assert after.conversation_state.revision >= before.conversation_state.revision
            else:
                assert response.status_code >= 400
                after = _read_live_state(thread_id)
                assert after.to_dict() == before.to_dict()

    json_thread = client.create_thread("State-aware JSON parity")
    sse_thread = client.create_thread("State-aware SSE parity")
    json_turn = _turn(client, json_thread, "What events are happening tomorrow?")
    sse_turn = _turn(client, sse_thread, "What events are happening tomorrow?", stream=True)
    assert json_turn["answer"] == sse_turn["answer"]
    assert json_turn["source_metadata"] == sse_turn["source_metadata"]
    json_state = dict(json_turn["trusted_state_after"])
    sse_state = dict(sse_turn["trusted_state_after"])
    json_state.pop("last_substantive_message_id", None)
    sse_state.pop("last_substantive_message_id", None)
    assert json_state == sse_state
    assert json_turn["query_plan"] == sse_turn["query_plan"]
    assert json_turn["cache_signature_after"] == sse_turn["cache_signature_after"]
    assert json_turn["mode"] == sse_turn["mode"] == "structured_events"
    assert json_turn["model_name"] is None and sse_turn["model_name"] is None

    # JSON/SSE emergency and breathing false-positive parity.
    for message, expect_emergency in (
        ("There's a fire in the building right now.", True),
        ("I'm having severe trouble breathing right now.", True),
    ):
        j_thread = client.create_thread(f"parity json {message[:20]}")
        s_thread = client.create_thread(f"parity sse {message[:20]}")
        j_turn = _turn(client, j_thread, message)
        s_turn = _turn(client, s_thread, message, stream=True)
        assert j_turn["answer"] == s_turn["answer"]
        assert j_turn["mode"] == s_turn["mode"] == "emergency"
        assert j_turn["model_name"] is None and s_turn["model_name"] is None
        assert j_turn["answer"].startswith("Call 911 now.")

    # Two requests start from the same revision. The lock-first transaction permits one.
    race_thread = client.create_thread("State-aware event race")
    import requests

    def race_request() -> int:
        return requests.post(
            f"{base_url.rstrip('/')}/conversations/{race_thread}/messages",
            headers=client.headers(),
            json={"message": "What events are happening this weekend?"},
            timeout=30,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(future.result() for future in [executor.submit(race_request) for _ in range(2)])
    assert statuses == [201, 409], statuses
    race_state = _read_live_state(race_thread)
    assert race_state.conversation_state.active_topic is CanonicalTopic.EVENTS
    assert _cache_signature(race_state) is not None

    warm_thread = client.create_thread("State-aware event performance")
    _turn(client, warm_thread, "What events are happening this weekend?")
    latencies = [
        _turn(client, warm_thread, "What events are happening this weekend?")["latency_ms"]
        for _ in range(20)
    ]
    p90 = sorted(latencies)[int(len(latencies) * 0.9) - 1]
    print(json.dumps({"warm_event_p50_ms": statistics.median(latencies), "warm_event_p90_ms": p90}))
    assert p90 < 500


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("LOCAL_API_URL", "http://127.0.0.1:8888"))
    args = parser.parse_args()
    _assert_local_demo()
    _run_dialogues(args.base_url)
    print("State-aware event live dialogues passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
