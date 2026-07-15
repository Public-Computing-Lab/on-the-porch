#!/usr/bin/env python3
"""Local-demo API verification for canonical conversation-state transitions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from conversation_state import load_thread_state, retrieval_cache_hash  # noqa: E402
from intent_router import detect_civic_topic  # noqa: E402
from unified_chatbot import create_empty_cache, resolve_effective_question  # noqa: E402


def _db_connection():
    import mysql.connector

    host = os.getenv("MYSQL_HOST", "")
    database = os.getenv("MYSQL_DB", "")
    user = os.getenv("MYSQL_USER", "")
    assert host in {"localhost", "127.0.0.1"}, host
    assert database == "sentiment_demo", database
    assert user == "demo_user", user
    return mysql.connector.connect(
        host=host,
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=user,
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database,
    )


def _thread_snapshot(thread_id: str) -> Dict[str, Any]:
    conn = _db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT thread_state_json FROM conversation_threads WHERE id = %s LIMIT 1",
            (thread_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        envelope = load_thread_state(row.get("thread_state_json"))
        return {
            "state": envelope.conversation_state.to_dict(),
            "cache": envelope.retrieval_cache,
            "cache_hash": retrieval_cache_hash(envelope.retrieval_cache),
        }
    finally:
        cursor.close()
        conn.close()


def _message_meta(message_id: str) -> Dict[str, Any]:
    conn = _db_connection()
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


def _history(thread_id: str) -> list[Dict[str, str]]:
    conn = _db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT role, content FROM conversation_messages WHERE thread_id = %s "
            "ORDER BY created_at, id",
            (thread_id,),
        )
        return [
            {"role": row["role"], "content": row["content"]}
            for row in cursor.fetchall()
            if row["role"] in {"user", "assistant"}
        ][-20:]
    finally:
        cursor.close()
        conn.close()


class GuestClient:
    def __init__(self, base_url: str) -> None:
        import requests

        self.session = requests.Session()
        self.base_url = base_url.rstrip("/")
        response = self.session.post(f"{self.base_url}/auth/guest", timeout=15)
        response.raise_for_status()
        payload = response.json()
        self.token = payload["session_token"]
        self.csrf = payload["csrf_token"]

    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-CSRF-Token": self.csrf,
            "Content-Type": "application/json",
            "Connection": "close",
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

    def send_json(self, thread_id: str, message: str) -> tuple[int, Dict[str, Any]]:
        response = self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json={"message": message},
            timeout=45,
        )
        return response.status_code, response.json()

    def send_sse(self, thread_id: str, message: str) -> tuple[int, Dict[str, Any]]:
        response = self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages/stream",
            headers=self.headers(),
            json={"message": message},
            stream=True,
            timeout=45,
        )
        final = None
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            event = json.loads(line[5:].strip())
            if event.get("type") == "error":
                raise AssertionError(event)
            if event.get("type") == "final":
                final = event
        assert final is not None
        return response.status_code, final


def _assert_hidden(payload: Mapping[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=True, default=str)
    for internal in (
        "conversation_transition",
        "conversation_dimensions_shadow",
        "conversation_relationship",
        "commit_allowed",
        "reason_codes",
        "thread_state_json",
        "schema_version",
    ):
        assert internal not in serialized, internal


def _effective_question(message: str, before: Dict[str, Any], history: list[Dict[str, str]]) -> str:
    effective, _force_fresh = resolve_effective_question(message, history, before["cache"])
    return effective


def _send_turn(
    client: GuestClient,
    thread_id: str,
    message: str,
    *,
    streaming: bool = False,
) -> Dict[str, Any]:
    before = _thread_snapshot(thread_id)
    history = _history(thread_id)
    effective_question = _effective_question(message, before, history)
    started = time.perf_counter()
    if streaming:
        status, payload = client.send_sse(thread_id, message)
        assert status == 200, payload
    else:
        status, payload = client.send_json(thread_id, message)
        assert status == 201, payload
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    _assert_hidden(payload)
    after = _thread_snapshot(thread_id)
    meta = _message_meta(payload["user_message"]["id"])
    relationship = meta.get("conversation_relationship") or {}
    dimensions = meta.get("conversation_dimensions_shadow") or {}
    transition = meta.get("conversation_transition") or {}
    assert transition
    assert len(json.dumps(transition).encode("utf-8")) < 2048
    for forbidden in ("next_state", "previous_state", "answer", "sql", "rag_chunks"):
        assert forbidden not in transition

    state_before = before["state"]
    state_after = after["state"]
    if transition.get("committed") and transition.get("action") != "NO_CHANGE":
        assert state_after["revision"] == state_before["revision"] + 1
    else:
        assert state_after["revision"] == state_before["revision"]
    if not transition.get("commit_allowed"):
        assert state_after == state_before
        assert after["cache_hash"] == before["cache_hash"]
    if transition.get("cache_action") == "INVALIDATE" and transition.get("committed"):
        assert after["cache"] == create_empty_cache()

    assistant = payload["assistant_message"]
    record = {
        "message": message,
        "transport": "sse" if streaming else "json",
        "relationship": relationship.get("relationship"),
        "decision_source": relationship.get("decision_source"),
        "reason_code": relationship.get("reason_code"),
        "canonical_dimensions": {
            key: dimensions.get(key)
            for key in ("topic", "location", "time_range", "filters", "references")
        },
        "proposed_transition": transition,
        "completion_status": transition.get("completion_status"),
        "transition_committed": transition.get("committed"),
        "state_before": state_before,
        "state_after": state_after,
        "revision_before": state_before["revision"],
        "revision_after": state_after["revision"],
        "retrieval_cache_hash_before": before["cache_hash"],
        "retrieval_cache_hash_after": after["cache_hash"],
        "route_mode": assistant.get("response_mode"),
        "detected_topic": detect_civic_topic(effective_question),
        "effective_question": effective_question,
        "answer": assistant.get("content"),
        "latency_ms": latency_ms,
        "llm_calls": 0 if assistant.get("model_name") is None else ">=1",
        "answer_behavior_change": (
            "narrow_codman_source_correction"
            if message == "What about Codman Square?"
            else "none_expected"
        ),
    }
    print(json.dumps(record, ensure_ascii=False, default=str))
    return record


def test_dialogue(base_url: str) -> list[Dict[str, Any]]:
    client = GuestClient(base_url)
    thread_id = client.create_thread(f"Transition dialogue {uuid.uuid4().hex[:8]}")
    messages = [
        "Is Dorchester safe right now?",
        "What about Codman Square?",
        "Are there events there this weekend?",
        "Only show free ones.",
        "Thanks.",
        "What are residents saying about potholes?",
    ]
    records = [_send_turn(client, thread_id, message) for message in messages]
    if records[0]["completion_status"] == "ACCEPTABLE":
        assert records[0]["state_after"]["active_topic"] == "PUBLIC_SAFETY"
    else:
        assert records[0]["transition_committed"] is False
        assert records[0]["state_before"] == records[0]["state_after"]
    assert records[1]["detected_topic"] != "news"
    assert records[1]["canonical_dimensions"]["location"]["id"] == "codman_square"
    if records[0]["transition_committed"] is False:
        assert records[1]["transition_committed"] is False
        assert records[1]["proposed_transition"]["reason_codes"] == ["missing_reference"]
    assert records[2]["transition_committed"] is False
    assert records[2]["proposed_transition"]["reason_codes"] == [
        "reference_not_applied_to_route"
    ]
    assert records[4]["revision_before"] == records[4]["revision_after"]
    assert records[5]["state_after"]["active_topic"] == "COMMUNITY_OPINION"
    print("OK  six-turn transition dialogue")
    return records


def _state_without_message_id(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    value = dict(snapshot["state"])
    value.pop("last_substantive_message_id", None)
    return value


def test_json_sse_parity(base_url: str) -> None:
    client = GuestClient(base_url)
    json_thread = client.create_thread(f"Transition JSON {uuid.uuid4().hex[:8]}")
    sse_thread = client.create_thread(f"Transition SSE {uuid.uuid4().hex[:8]}")
    message = "What events are happening this weekend?"
    json_record = _send_turn(client, json_thread, message)
    sse_record = _send_turn(client, sse_thread, message, streaming=True)
    assert _state_without_message_id(_thread_snapshot(json_thread)) == _state_without_message_id(
        _thread_snapshot(sse_thread)
    )
    assert json_record["proposed_transition"] == sse_record["proposed_transition"]
    assert json_record["route_mode"] == sse_record["route_mode"]
    print("OK  JSON/SSE transition parity")


def test_user_thread_isolation(base_url: str) -> None:
    safety_client = GuestClient(base_url)
    event_client = GuestClient(base_url)
    safety_thread = safety_client.create_thread(f"Safety isolation {uuid.uuid4().hex[:8]}")
    event_thread = event_client.create_thread(f"Event isolation {uuid.uuid4().hex[:8]}")
    _send_turn(safety_client, safety_thread, "Is Dorchester safe right now?")
    _send_turn(event_client, event_thread, "What events are happening this weekend?")
    safety = _thread_snapshot(safety_thread)
    events = _thread_snapshot(event_thread)
    assert safety["state"]["active_topic"] == "PUBLIC_SAFETY"
    assert events["state"]["active_topic"] == "EVENTS"
    assert safety["state"] != events["state"]
    print("OK  user/thread transition isolation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8888")
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "database": f"{os.getenv('MYSQL_USER')}@{os.getenv('MYSQL_HOST')}:"
                f"{os.getenv('MYSQL_PORT', '3306')}/{os.getenv('MYSQL_DB')}",
                "production_database_used": False,
            }
        )
    )
    test_dialogue(args.base_url)
    test_json_sse_parity(args.base_url)
    test_user_thread_isolation(args.base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
