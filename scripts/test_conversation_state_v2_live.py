#!/usr/bin/env python3
"""Local-demo live verification for conversation-state v2 migration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from conversation_state import (  # noqa: E402
    ThreadStateForm,
    detect_thread_state_form,
    load_thread_state,
    retrieval_cache_hash,
)


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


def _raw_thread_state(thread_id: str) -> str:
    conn = _db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT thread_state_json FROM conversation_threads WHERE id = %s LIMIT 1",
            (thread_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        return str(row.get("thread_state_json") or "")
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


def _seed_legacy_event_state(thread_id: str, retrieval_cache: Dict[str, Any]) -> None:
    legacy = {
        "version": 1,
        "retrieval_cache": retrieval_cache,
        "conversation_state": {
            "state_version": 5,
            "active_topic": "events",
            "active_entity": {
                "type": "event",
                "id": "435",
                "source_ref": "event:435",
            },
            "previous_route": "sql",
            "last_substantive_message_id": "legacy-seed",
        },
    }
    conn = _db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE conversation_threads SET thread_state_json = %s WHERE id = %s",
            (json.dumps(legacy, ensure_ascii=True, separators=(",", ":")), thread_id),
        )
        assert cursor.rowcount == 1
        conn.commit()
    finally:
        cursor.close()
        conn.close()


class _GuestClient:
    def __init__(self, base_url: str) -> None:
        import requests

        self.requests = requests
        self.base_url = base_url.rstrip("/")
        self.token = ""
        self.csrf = ""

    def bootstrap(self) -> None:
        response = self.requests.post(f"{self.base_url}/auth/guest", timeout=15)
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

    def create_thread(self) -> str:
        response = self.requests.post(
            f"{self.base_url}/conversations",
            headers=self.headers(),
            json={"title": "Conversation state v2 migration"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["thread"]["id"]

    def send(self, thread_id: str, message: str):
        return self.requests.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json={"message": message},
            timeout=45,
        )

    def send_stream(self, thread_id: str, message: str):
        response = self.requests.post(
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
        return response, final


def _state_snapshot(raw: str) -> Dict[str, Any]:
    envelope = load_thread_state(raw)
    state = envelope.conversation_state
    return {
        "stored_form": detect_thread_state_form(raw).value,
        "normalized_state": state.to_dict(),
        "schema_version": state.schema_version,
        "revision": state.revision,
        "active_entity": state.active_entity.to_dict() if state.active_entity else None,
        "location": state.location.to_dict() if state.location else None,
        "time_range": state.time_range.to_dict() if state.time_range else None,
        "filters": state.to_dict()["filters"],
        "retrieval_cache_hash": retrieval_cache_hash(envelope.retrieval_cache),
        "raw_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


def _send_turn(
    client: _GuestClient,
    thread_id: str,
    message: str,
    *,
    streaming: bool = False,
) -> Dict[str, Any]:
    raw_before = _raw_thread_state(thread_id)
    before = _state_snapshot(raw_before)
    started = time.perf_counter()
    if streaming:
        response, payload = client.send_stream(thread_id, message)
    else:
        response = client.send(thread_id, message)
        payload = response.json() if response.status_code == 201 else None
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    expected_status = 200 if streaming else 201
    assert response.status_code == expected_status, response.text
    assert payload is not None
    serialized_response = json.dumps(payload, ensure_ascii=True, default=str)
    for internal in ("schema_version", "thread_state_json", "conversation_state", "revision"):
        assert internal not in serialized_response

    raw_after = _raw_thread_state(thread_id)
    after = _state_snapshot(raw_after)
    meta = _message_meta(payload["user_message"]["id"])
    relationship = meta.get("conversation_relationship") or {}
    dimensions = meta.get("conversation_dimensions_shadow") or {}
    assistant = payload["assistant_message"]
    record = {
        "message": message,
        "transport": "sse" if streaming else "json",
        "stored_form_before": before["stored_form"],
        "normalized_in_memory_before": before["normalized_state"],
        "schema_version_before": before["schema_version"],
        "revision_before": before["revision"],
        "active_entity_before": before["active_entity"],
        "location_before": before["location"],
        "time_range_before": before["time_range"],
        "filters_before": before["filters"],
        "retrieval_cache_hash_before": before["retrieval_cache_hash"],
        "relationship": relationship.get("relationship"),
        "decision_source": relationship.get("decision_source"),
        "reason_code": relationship.get("reason_code"),
        "shadow_topic": dimensions.get("topic"),
        "mode": assistant.get("response_mode"),
        "answer": assistant.get("content"),
        "latency_ms": latency_ms,
        "llm_calls": 0 if assistant.get("model_name") is None else ">=1",
        "stored_form_after": after["stored_form"],
        "schema_version_after": after["schema_version"],
        "revision_after": after["revision"],
        "active_entity_after": after["active_entity"],
        "location_after": after["location"],
        "time_range_after": after["time_range"],
        "filters_after": after["filters"],
        "retrieval_cache_hash_after": after["retrieval_cache_hash"],
    }
    print(json.dumps(record, ensure_ascii=False, default=str))
    return record


def run(base_url: str) -> None:
    client = _GuestClient(base_url)
    client.bootstrap()
    thread_id = client.create_thread()

    initial = load_thread_state(_raw_thread_state(thread_id))
    _seed_legacy_event_state(thread_id, initial.retrieval_cache)
    seeded_raw = _raw_thread_state(thread_id)
    seeded_snapshot = _state_snapshot(seeded_raw)
    assert seeded_snapshot["stored_form"] == ThreadStateForm.ENVELOPE_V1_STATE_V1.value
    assert seeded_snapshot["revision"] == 5
    assert seeded_snapshot["active_entity"]["id"] == "435"

    # Loading performs in-memory migration only and does not rewrite the row.
    load_thread_state(seeded_raw)
    assert _raw_thread_state(thread_id) == seeded_raw
    print(json.dumps({"read_only_load": "unchanged", **seeded_snapshot}, default=str))

    where = _send_turn(client, thread_id, "Where is it?")
    assert where["stored_form_before"] == ThreadStateForm.ENVELOPE_V1_STATE_V1.value
    assert where["stored_form_after"] == ThreadStateForm.ENVELOPE_V1_STATE_V2.value
    assert where["schema_version_after"] == 2
    assert where["revision_after"] == 5
    assert where["active_entity_after"]["id"] == "435"
    assert where["mode"] == "sql"
    assert where["llm_calls"] == 0
    assert where["retrieval_cache_hash_before"] == where["retrieval_cache_hash_after"]

    parity_thread = client.create_thread()
    parity_initial = load_thread_state(_raw_thread_state(parity_thread))
    _seed_legacy_event_state(parity_thread, parity_initial.retrieval_cache)
    sse_where = _send_turn(client, parity_thread, "Where is it?", streaming=True)
    assert sse_where["stored_form_after"] == ThreadStateForm.ENVELOPE_V1_STATE_V2.value
    assert sse_where["revision_after"] == where["revision_after"]
    assert sse_where["active_entity_after"] == where["active_entity_after"]
    assert sse_where["mode"] == where["mode"]
    assert sse_where["answer"] == where["answer"]
    assert sse_where["llm_calls"] == 0

    thanks = _send_turn(client, thread_id, "Thanks")
    assert thanks["revision_after"] == thanks["revision_before"]
    assert thanks["active_entity_after"]["id"] == "435"

    civic = _send_turn(client, thread_id, "How many 311 requests were filed last week?")
    assert civic["stored_form_after"] == ThreadStateForm.ENVELOPE_V1_STATE_V2.value
    assert civic["schema_version_after"] == 2
    assert civic["revision_after"] == civic["revision_before"] + 1
    assert civic["active_entity_after"] is None
    assert civic["location_after"] is None
    assert civic["time_range_after"] is None
    assert civic["filters_after"] == {}

    print(f"OK  local legacy-to-v2 migration dialogue thread={thread_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8888")
    args = parser.parse_args()
    run(args.base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
