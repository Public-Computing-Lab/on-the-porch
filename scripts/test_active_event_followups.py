#!/usr/bin/env python3
"""Behavior tests for authoritative, deterministic event-property follow-ups."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import date, time as clock_time
from pathlib import Path
from typing import Any, Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))
sys.path.insert(0, str(ROOT / "api"))

from conversation_relationship import (  # noqa: E402
    ConversationRelationship,
    EventProperty,
    ReasonCode,
    classify_shadow_relationship,
    detect_event_property,
)
from conversation_state import (  # noqa: E402
    ActiveEntity,
    ConversationState,
    ThreadStateEnvelope,
    load_thread_state,
    retrieval_cache_hash,
    serialize_thread_state,
)
from event_context import (  # noqa: E402
    EventResolution,
    MISSING_EVENT_REFERENCE_MESSAGE,
    STALE_ACTIVE_EVENT_MESSAGE,
    build_active_event_response,
    resolve_event_by_id,
)


EVENT_435_URL = "https://www.codman.org/event/virtual-baby-cafe-4/2026-07-15/"
EVENT_435 = {
    "id": 435,
    "source_pdf": "codman",
    "event_name": "Virtual Baby Cafe",
    "event_date": "2026-07-15",
    "start_date": date(2026, 7, 15),
    "end_date": date(2026, 7, 15),
    "start_time": clock_time(14, 0),
    "end_time": clock_time(16, 0),
    "raw_text": (
        "Virtual Baby Cafe\n"
        "Codman Square Health Center Baby Café Virtual Register here "
        "(857)-301-8259 2:00pm – 4:00pm\n"
        "Location: Virtual\n"
        f"URL: {EVENT_435_URL}"
    ),
}
IN_PERSON_EVENT = {
    "id": 900,
    "source_pdf": "community calendar",
    "event_name": "Neighborhood Cleanup",
    "start_date": date(2026, 7, 18),
    "start_time": clock_time(10, 0),
    "end_time": clock_time(12, 0),
    "raw_text": "Neighborhood Cleanup\nLocation: Codman Square Park\nCost: Free",
}


def _response(question: str, event: Dict[str, Any] = EVENT_435) -> str:
    property_name = detect_event_property(question)
    assert property_name is not None
    return build_active_event_response(
        question,
        EventResolution(status="exact_id", context=None, event=event),
        property_name=property_name.value,
    ).answer


def test_property_detector_is_bounded_to_supported_questions() -> None:
    expected = {
        "Where is it?": EventProperty.LOCATION,
        "Is it virtual?": EventProperty.LOCATION,
        "Is it online?": EventProperty.LOCATION,
        "Where does it take place?": EventProperty.LOCATION,
        "When is it?": EventProperty.DATE_TIME,
        "What time does it start?": EventProperty.DATE_TIME,
        "When does it end?": EventProperty.DATE_TIME,
        "Is it free?": EventProperty.COST,
        "How much does it cost?": EventProperty.COST,
        "How do I register?": EventProperty.REGISTRATION_CONTACT,
        "Who do I contact?": EventProperty.REGISTRATION_CONTACT,
        "Do I need to sign up?": EventProperty.REGISTRATION_CONTACT,
        "Who is organizing it?": EventProperty.ORGANIZER,
        "Who is hosting it?": EventProperty.ORGANIZER,
        "Tell me more about it.": EventProperty.DETAILS,
    }
    for question, property_name in expected.items():
        assert detect_event_property(question) is property_name, question
    assert detect_event_property("What events are happening this weekend?") is None


def test_active_event_relationship_and_missing_reference_are_deterministic() -> None:
    calls = 0

    def classifier(_prompt: str, _timeout: float) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("event-property rule called the classifier LLM")

    active = classify_shadow_relationship(
        "Where is it?",
        [],
        enabled=True,
        authoritative_active_event=True,
        llm_fallback_enabled=True,
        llm_classifier=classifier,
    )
    missing = classify_shadow_relationship(
        "Where is it?",
        [],
        enabled=True,
        authoritative_active_event=False,
        llm_fallback_enabled=True,
        llm_classifier=classifier,
    )
    assert active is not None and active.relationship is ConversationRelationship.FOLLOW_UP
    assert active.reason_code is ReasonCode.ACTIVE_EVENT_PROPERTY_QUESTION
    assert missing is not None and missing.reason_code is ReasonCode.MISSING_REFERENCE
    assert missing.requires_clarification is True
    assert calls == 0


def test_virtual_location_uses_required_authoritative_wording() -> None:
    answer = _response("Where is it?")
    assert answer.startswith(
        "Virtual Baby Cafe is a virtual event, so there is no physical location listed."
    )
    assert "(857) 301-8259" in answer
    assert "weekly_events" not in answer


def test_cost_does_not_infer_free_when_missing() -> None:
    answer = _response("Is it free?")
    assert answer == "The event listing does not specify a cost."
    assert "free" not in answer.lower()
    assert _response("Is it free?", IN_PERSON_EVENT) == (
        "The listing says Neighborhood Cleanup is free."
    )


def test_registration_organizer_time_and_details_use_authoritative_record() -> None:
    registration = _response("How do I register?")
    assert registration == (
        "Anyone interested in joining or confirming registration details can contact "
        "the organizer at (857) 301-8259."
    )
    assert _response("Who is organizing it?") == (
        "Virtual Baby Cafe is organized by Codman Square Health Center."
    )
    timing = _response("When is it?")
    assert timing == (
        "Virtual Baby Cafe is scheduled for Wednesday, July 15, from 2:00 PM to 4:00 PM."
    )
    details = _response("Tell me more about it.")
    assert details.startswith("**Virtual Baby Cafe** is scheduled for **Wednesday, July 15**")
    assert details.count(EVENT_435_URL) == 1


def test_in_person_and_missing_property_answers_are_explicit() -> None:
    assert _response("Where is it?", IN_PERSON_EVENT) == (
        "Neighborhood Cleanup takes place at Codman Square Park."
    )
    sparse = {"id": 901, "event_name": "Community Gathering", "raw_text": ""}
    assert _response("Where is it?", sparse) == (
        "The event listing does not provide a separate location."
    )
    assert _response("Who is organizing it?", sparse) == (
        "The event listing does not identify a separate organizer."
    )
    assert _response("How do I register?", sparse) == (
        "The event listing does not provide registration or contact information."
    )


def test_stale_and_missing_reference_answers_are_resident_safe() -> None:
    stale = build_active_event_response(
        "Where is it?",
        EventResolution(status="stale", context=None),
        property_name="location",
    )
    assert stale.answer == STALE_ACTIVE_EVENT_MESSAGE
    assert MISSING_EVENT_REFERENCE_MESSAGE.startswith("Which event are you asking about?")
    serialized = json.dumps({"stale": stale.answer, "missing": MISSING_EVENT_REFERENCE_MESSAGE})
    for internal in (
        "conversation_state", "thread_state_json", "event_by_name", "prepared_sql",
        "Chroma", "Qdrant", "retrieval failure",
    ):
        assert internal not in serialized


class _BoundCursor:
    def __init__(self, row: Optional[Dict[str, Any]]) -> None:
        self.row = row
        self.sql = ""
        self.params = None

    def execute(self, sql: str, params=None) -> None:
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.row

    def close(self) -> None:
        return None


class _BoundConnection:
    def __init__(self, row: Optional[Dict[str, Any]]) -> None:
        self.cursor_instance = _BoundCursor(row)

    def cursor(self, dictionary: bool = False):
        assert dictionary is True
        return self.cursor_instance


def test_event_refetch_is_fixed_and_bound() -> None:
    conn = _BoundConnection(EVENT_435)
    resolution = resolve_event_by_id(conn, "435")
    assert resolution.event == EVENT_435
    assert "WHERE `id` = %s LIMIT 1" in conn.cursor_instance.sql
    assert conn.cursor_instance.params == (435,)


class _LiveGuestClient:
    def __init__(self, base_url: str) -> None:
        import requests

        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session_token = ""
        self.csrf_token = ""

    def bootstrap(self) -> None:
        response = self.session.post(f"{self.base_url}/auth/guest", timeout=15)
        response.raise_for_status()
        payload = response.json()
        self.session_token = payload["session_token"]
        self.csrf_token = payload["csrf_token"]

    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.session_token}",
            "X-CSRF-Token": self.csrf_token,
            "Content-Type": "application/json",
        }

    def create_thread(self, title: str) -> str:
        response = self.session.post(
            f"{self.base_url}/conversations",
            headers=self.headers(),
            json={"title": title},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["thread"]["id"]

    def send_json(self, thread_id: str, payload: Dict[str, Any]):
        payload = dict(payload)
        payload.setdefault("client_turn_id", str(uuid.uuid4()))
        return self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json=payload,
            timeout=45,
        )

    def send_stream(self, thread_id: str, payload: Dict[str, Any]):
        payload = dict(payload)
        payload.setdefault("client_turn_id", str(uuid.uuid4()))
        response = self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages/stream",
            headers=self.headers(),
            json=payload,
            stream=True,
            timeout=45,
        )
        if response.status_code != 200:
            return response, None
        final = None
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            event = json.loads(line[5:].strip())
            if event.get("type") == "error":
                raise AssertionError(event.get("error") or "stream failed")
            if event.get("type") == "final":
                final = event
        return response, final


def _live_db_connection():
    import mysql.connector
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    host = os.getenv("MYSQL_HOST", "")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    database = os.getenv("MYSQL_DB", "")
    user = os.getenv("MYSQL_USER", "")
    assert host in {"localhost", "127.0.0.1"}, host
    assert database == "sentiment_demo", database
    assert user == "demo_user", user
    return mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database,
    )


def _read_live_state(thread_id: str) -> ThreadStateEnvelope:
    conn = _live_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT thread_state_json FROM conversation_threads WHERE id = %s LIMIT 1",
            (thread_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        return load_thread_state(row.get("thread_state_json"))
    finally:
        cursor.close()
        conn.close()


def _read_user_meta(message_id: str) -> Dict[str, Any]:
    conn = _live_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT message_meta_json FROM conversation_messages WHERE id = %s LIMIT 1",
            (message_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        return json.loads(row.get("message_meta_json") or "{}")
    finally:
        cursor.close()
        conn.close()


def _internal_terms(value: str) -> list[str]:
    lowered = (value or "").lower()
    terms = (
        "event_by_name", "prepared_sql", "conversation_state", "thread_state_json",
        "chroma", "qdrant", "sql execution", "retrieval failure", "tool call",
    )
    found = [term for term in terms if term in lowered]
    if "RAG" in value:
        found.append("RAG")
    return found


def _event_context(event: Dict[str, Any]) -> Dict[str, str]:
    return {
        "entity_type": "event",
        "entity_id": str(event["id"]),
        "entity_title": str(event["event_name"]),
        "entity_date": str(event["start_date"]),
        "source": "events_sidebar",
    }


def _send_live_turn(
    client: _LiveGuestClient,
    thread_id: str,
    message: str,
    *,
    ui_context: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    before = _read_live_state(thread_id)
    started = time.perf_counter()
    response = client.send_json(
        thread_id,
        {"message": message, **({"ui_context": ui_context} if ui_context else {})},
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    assert response.status_code == 201, response.text
    payload = response.json()
    serialized = json.dumps(payload, default=str)
    assert "conversation_state" not in serialized
    assert "thread_state_json" not in serialized
    after = _read_live_state(thread_id)
    meta = _read_user_meta(payload["user_message"]["id"])
    relationship = meta.get("conversation_relationship") or {}
    answer = payload["assistant_message"]["content"]
    record = {
        "message": message,
        "relationship": relationship.get("relationship"),
        "decision_source": relationship.get("decision_source"),
        "reason_code": relationship.get("reason_code"),
        "effective_question": message,
        "mode": payload["assistant_message"].get("response_mode"),
        "answer": answer,
        "answer_behavior_changed": bool(meta.get("active_event_followup_applied")),
        "active_state_before": before.conversation_state.to_dict(),
        "active_state_after": after.conversation_state.to_dict(),
        "state_version": after.conversation_state.state_version,
        "retrieval_cache_hash_before": retrieval_cache_hash(before.retrieval_cache),
        "retrieval_cache_hash_after": retrieval_cache_hash(after.retrieval_cache),
        "latency_ms": latency_ms,
        "llm_calls": 0 if payload["assistant_message"].get("model_name") is None else ">=1",
        "sources": payload["assistant_message"].get("sources") or [],
        "resolved_entity": payload.get("resolved_entity"),
        "internal_terms": _internal_terms(answer),
        "persisted_user_metadata": meta,
    }
    print(json.dumps(record, ensure_ascii=False, default=str))
    assert record["internal_terms"] == []
    return record


def _load_live_events() -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    conn = _live_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, event_name, start_date FROM weekly_events WHERE id = %s LIMIT 1",
            (435,),
        )
        selected = cursor.fetchone()
        cursor.execute(
            "SELECT id, event_name, start_date FROM weekly_events "
            "WHERE id <> %s AND start_date IS NOT NULL ORDER BY id ASC LIMIT 1",
            (435,),
        )
        other = cursor.fetchone()
        assert selected is not None, "local demo DB must contain weekly_events.id=435"
        return dict(selected), dict(other) if other else None
    finally:
        cursor.close()
        conn.close()


def _set_stale_active_event(thread_id: str) -> None:
    envelope = _read_live_state(thread_id)
    stale_state = ConversationState(
        state_version=envelope.conversation_state.state_version + 1,
        active_topic="events",
        active_entity=ActiveEntity.event("2147483647"),
        previous_route="sql",
        last_substantive_message_id="test-stale-event",
    )
    conn = _live_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE conversation_threads SET thread_state_json = %s WHERE id = %s",
            (
                serialize_thread_state(
                    ThreadStateEnvelope(envelope.retrieval_cache, stale_state)
                ),
                thread_id,
            ),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def run_live_api_tests(base_url: str) -> None:
    event_435, other_event = _load_live_events()
    client = _LiveGuestClient(base_url)
    isolated_client = _LiveGuestClient(base_url)
    client.bootstrap()
    isolated_client.bootstrap()

    # Warm the server-side local embedding/retrieval path on an isolated thread so
    # the required topic-switch turn measures a completed request, not model load.
    warmup_thread = client.create_thread("Active event retrieval warmup")
    warmup_response = client.send_json(
        warmup_thread,
        {"message": "What are residents saying about potholes?"},
    )
    assert warmup_response.status_code == 201

    thread_id = client.create_thread("Active event dialogue")
    records = [
        _send_live_turn(
            client,
            thread_id,
            "Tell me more about this event",
            ui_context=_event_context(event_435),
        ),
        _send_live_turn(client, thread_id, "Where is it?"),
        _send_live_turn(client, thread_id, "Thanks"),
        _send_live_turn(client, thread_id, "Is it free?"),
        _send_live_turn(client, thread_id, "Who is organizing it?"),
        _send_live_turn(client, thread_id, "What are residents saying about potholes?"),
        _send_live_turn(client, thread_id, "Where is it?"),
    ]
    assert records[0]["active_state_after"]["active_entity"]["id"] == "435"
    assert records[1]["relationship"] == "FOLLOW_UP"
    assert records[1]["reason_code"] == "active_event_property_question"
    assert records[1]["answer"].startswith(
        "Virtual Baby Cafe is a virtual event, so there is no physical location listed."
    )
    assert records[1]["llm_calls"] == 0 and records[1]["latency_ms"] < 500
    assert records[1]["state_version"] == records[0]["state_version"]
    assert records[2]["relationship"] == "SMALL_TALK"
    assert records[2]["active_state_after"]["active_entity"]["id"] == "435"
    assert records[2]["state_version"] == records[1]["state_version"]
    assert records[3]["answer"] == "The event listing does not specify a cost."
    assert records[3]["state_version"] == records[2]["state_version"]
    assert "Codman Square Health Center" in records[4]["answer"]
    assert records[4]["state_version"] == records[3]["state_version"]
    assert records[5]["active_state_after"]["active_entity"] is None
    assert records[5]["state_version"] == records[4]["state_version"] + 1
    assert records[6]["answer"] == MISSING_EVENT_REFERENCE_MESSAGE
    assert records[6]["llm_calls"] == 0

    # Typed deterministic event details activate the same authoritative state.
    typed_thread = client.create_thread("Typed event activation")
    typed = _send_live_turn(
        client,
        typed_thread,
        "Tell me more about the 'Virtual Baby Cafe' event happening on Jul 15.",
    )
    assert typed["active_state_after"]["active_entity"]["id"] == "435"

    # A second authoritative selection replaces the active event.
    if other_event:
        replaced = _send_live_turn(
            client,
            typed_thread,
            "Tell me more about this event",
            ui_context=_event_context(other_event),
        )
        assert replaced["active_state_after"]["active_entity"]["id"] == str(other_event["id"])
        replaced_followup = _send_live_turn(client, typed_thread, "Where is it?")
        assert replaced_followup["resolved_entity"]["id"] == str(other_event["id"])

    # No event state crosses threads or users.
    isolated_thread = isolated_client.create_thread("Missing event reference")
    isolated = _send_live_turn(isolated_client, isolated_thread, "Where is it?")
    assert isolated["answer"] == MISSING_EVENT_REFERENCE_MESSAGE
    assert isolated["active_state_after"]["active_entity"] is None

    stale_selection_thread = client.create_thread("Stale UI selection")
    stale_selection = _send_live_turn(
        client,
        stale_selection_thread,
        "Tell me more about this event",
        ui_context={
            "entity_type": "event",
            "entity_id": "2147483647",
            "entity_title": "Definitely Missing Event",
            "entity_date": "2026-07-15",
            "source": "events_sidebar",
        },
    )
    assert stale_selection["active_state_after"]["active_entity"] is None

    # Stale state returns a safe answer, then clears after successful persistence.
    stale_thread = client.create_thread("Stale active event")
    _set_stale_active_event(stale_thread)
    stale = _send_live_turn(client, stale_thread, "Where is it?")
    assert stale["answer"] == STALE_ACTIVE_EVENT_MESSAGE
    assert stale["active_state_after"]["active_entity"] is None

    # JSON/SSE deterministic parity, including activation.
    json_thread = client.create_thread("JSON parity")
    stream_thread = client.create_thread("SSE parity")
    json_selected = _send_live_turn(
        client,
        json_thread,
        "Tell me more about this event",
        ui_context=_event_context(event_435),
    )
    _send_live_turn(
        client,
        stream_thread,
        "Tell me more about this event",
        ui_context=_event_context(event_435),
    )
    started = time.perf_counter()
    stream_response, stream_final = client.send_stream(stream_thread, {"message": "Where is it?"})
    stream_latency = round((time.perf_counter() - started) * 1000, 2)
    assert stream_response.status_code == 200 and stream_final is not None
    json_followup = _send_live_turn(client, json_thread, "Where is it?")
    assert stream_final["answer"] == json_followup["answer"]
    assert stream_final["sources"] == json_followup["sources"]
    assert stream_final.get("resolved_entity") == json_followup["resolved_entity"]
    assert stream_final["assistant_message"]["model_name"] is None
    assert stream_latency < 500
    stream_state = _read_live_state(stream_thread)
    assert stream_state.conversation_state.active_entity == ActiveEntity.event("435")
    print("OK  live JSON/SSE multi-turn state, persistence, isolation, and parity")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="Also run multi-turn tests against the local API.")
    args = parser.parse_args()
    tests = [
        test_property_detector_is_bounded_to_supported_questions,
        test_active_event_relationship_and_missing_reference_are_deterministic,
        test_virtual_location_uses_required_authoritative_wording,
        test_cost_does_not_infer_free_when_missing,
        test_registration_organizer_time_and_details_use_authoritative_record,
        test_in_person_and_missing_property_answers_are_explicit,
        test_stale_and_missing_reference_answers_are_resident_safe,
        test_event_refetch_is_fixed_and_bound,
    ]
    started = time.perf_counter()
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    print(f"\nActive event follow-up tests passed: {len(tests)}/{len(tests)} ({elapsed_ms}ms)")
    if args.base_url:
        run_live_api_tests(args.base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
