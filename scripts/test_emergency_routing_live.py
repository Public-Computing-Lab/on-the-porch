#!/usr/bin/env python3
"""Local MySQL/Gunicorn validation for deterministic emergency routing."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests


ROOT = Path(__file__).resolve().parents[1]
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
    ThreadStateEnvelope,
    load_thread_state,
    serialize_thread_state,
)
from emergency_routing import EMERGENCY_RESPONSE  # noqa: E402
from unified_chatbot import create_empty_cache  # noqa: E402


def _db_connection():
    import mysql.connector

    host = os.getenv("MYSQL_HOST", "")
    database = os.getenv("MYSQL_DB", "")
    user = os.getenv("MYSQL_USER", "")
    assert host in {"127.0.0.1", "localhost"}, host
    assert database and user
    return mysql.connector.connect(
        host=host,
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=user,
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database,
    )


def _nearest_rank(values: List[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = math.ceil(percentile * len(ordered)) - 1
    index = max(0, min(len(ordered) - 1, rank))
    return ordered[index]


def _seed_envelope() -> ThreadStateEnvelope:
    cache = create_empty_cache()
    cache.update(
        {
            "mode": "hybrid",
            "timestamp": "2026-07-15T12:00:00-04:00",
            "question": "What events are in Codman Square this weekend?",
            "sql_result": {"rows": [{"id": 435}]},
            "sql_query": "SELECT id FROM weekly_events",
            "rag_chunks": ["prior trusted evidence"],
            "rag_metadata": [{"source": "prior source"}],
            "answer": "Prior civic answer",
        }
    )
    state = ConversationState(
        revision=7,
        active_topic=CanonicalTopic.EVENTS,
        active_entity=ActiveEntity.event("435"),
        location=CanonicalLocation(
            "codman_square",
            GeographyKind.SUB_NEIGHBORHOOD,
        ),
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
    return ThreadStateEnvelope(cache, state)


def _write_state(thread_id: str, envelope: ThreadStateEnvelope) -> None:
    conn = _db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE conversation_threads SET thread_state_json = %s "
            "WHERE id = %s",
            (serialize_thread_state(envelope), thread_id),
        )
        assert cursor.rowcount == 1
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def _read_state(thread_id: str) -> ThreadStateEnvelope:
    conn = _db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT thread_state_json FROM conversation_threads WHERE id = %s",
            (thread_id,),
        )
        row = cursor.fetchone()
        assert row
        return load_thread_state(
            row[0],
            empty_retrieval_cache=create_empty_cache(),
        )
    finally:
        cursor.close()
        conn.close()


def _interaction_count(
    *,
    message_id: str | None = None,
    text: str | None = None,
) -> int:
    conn = _db_connection()
    cursor = conn.cursor()
    try:
        if message_id:
            cursor.execute(
                "SELECT COUNT(*) FROM interaction_log WHERE message_id = %s",
                (message_id,),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM interaction_log "
                "WHERE client_query = %s OR app_response = %s",
                (text, text),
            )
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()
        conn.close()


def _message_count(thread_id: str) -> int:
    conn = _db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM conversation_messages WHERE thread_id = %s",
            (thread_id,),
        )
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()
        conn.close()


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        response = self.session.post(f"{self.base_url}/auth/guest", timeout=15)
        response.raise_for_status()
        payload = response.json()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {payload['session_token']}",
                "X-CSRF-Token": payload["csrf_token"],
            }
        )

    def create_thread(self, title: str) -> str:
        response = self.session.post(
            f"{self.base_url}/conversations",
            json={"title": title},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["thread"]["id"]

    def post_json(
        self,
        thread_id: str,
        message: str,
        *,
        ui_context: Dict[str, Any] | None = None,
    ) -> requests.Response:
        payload: Dict[str, Any] = {"message": message}
        if ui_context is not None:
            payload["ui_context"] = ui_context
        return self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            json=payload,
            timeout=15,
        )

    def post_stream(
        self,
        thread_id: str,
        message: str,
        *,
        ui_context: Dict[str, Any] | None = None,
    ) -> tuple[requests.Response, List[Dict[str, Any]]]:
        payload: Dict[str, Any] = {"message": message}
        if ui_context is not None:
            payload["ui_context"] = ui_context
        response = self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages/stream",
            json=payload,
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=15,
        )
        events = []
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            events.append(json.loads(line[5:].strip()))
        return response, events

    def post_legacy(
        self,
        message: str,
        *,
        history: List[Dict[str, str]],
        ui_context: Dict[str, Any] | None = None,
    ) -> requests.Response:
        payload: Dict[str, Any] = {
            "message": message,
            "conversation_history": history,
        }
        if ui_context is not None:
            payload["ui_context"] = ui_context
        return self.session.post(
            f"{self.base_url}/chat",
            json=payload,
            timeout=15,
        )


def _assert_persisted_emergency(payload: Dict[str, Any]) -> None:
    assistant = payload["assistant_message"]
    assert assistant["content"] == EMERGENCY_RESPONSE
    assert assistant["content"].startswith("Call 911 now.")
    assert assistant["response_mode"] == "emergency"
    assert assistant["sources"] == []
    assert assistant["model_name"] is None
    assert assistant["log_id"] is None
    assert "emergency_decision" not in payload
    assert "conversation_transition" not in payload
    assert _interaction_count(message_id=assistant["id"]) == 0


def test_json_and_selected_event_precedence(base_url: str) -> Dict[str, Any]:
    client = Client(base_url)
    thread_id = client.create_thread("Emergency JSON")
    before = _seed_envelope()
    _write_state(thread_id, before)
    marker = uuid.uuid4().hex
    message = f"There is a shooting happening outside right now. Ref {marker}"
    raw_before = _interaction_count(text=message)
    answer_before = _interaction_count(text=EMERGENCY_RESPONSE)
    started = time.perf_counter()
    response = client.post_json(
        thread_id,
        message,
        ui_context={
            "entity_type": "event",
            "entity_id": "435",
            "entity_title": "Untrusted browser title",
            "entity_date": "2026-07-15",
            "source": "events_sidebar",
            "unexpected": "must be ignored for an emergency",
        },
    )
    latency_ms = (time.perf_counter() - started) * 1000.0
    assert response.status_code == 201, response.text
    payload = response.json()
    _assert_persisted_emergency(payload)
    assert _read_state(thread_id).to_dict() == before.to_dict()
    assert _interaction_count(text=message) == raw_before
    assert _interaction_count(text=EMERGENCY_RESPONSE) == answer_before
    return {
        "client": client,
        "thread_id": thread_id,
        "payload": payload,
        "latency_ms": latency_ms,
    }


def test_sse_parity(base_url: str, json_answer: str) -> float:
    client = Client(base_url)
    thread_id = client.create_thread("Emergency SSE")
    before = _seed_envelope()
    _write_state(thread_id, before)
    message = f"My house is on fire. Ref {uuid.uuid4().hex}"
    started = time.perf_counter()
    response, events = client.post_stream(thread_id, message)
    latency_ms = (time.perf_counter() - started) * 1000.0
    assert response.status_code == 200
    assert not [event for event in events if event.get("type") == "correction"]
    assert not [event for event in events if event.get("type") == "error"]
    finals = [event for event in events if event.get("type") == "final"]
    assert len(finals) == 1, events
    final = finals[0]
    assert final["answer"] == json_answer == EMERGENCY_RESPONSE
    assert final["mode"] == "emergency"
    assert final["sources"] == []
    _assert_persisted_emergency(final)
    assert _read_state(thread_id).to_dict() == before.to_dict()
    return latency_ms


def test_legacy_ignores_history_and_skips_log(base_url: str) -> float:
    client = Client(base_url)
    message = f"I can't breathe. Ref {uuid.uuid4().hex}"
    raw_before = _interaction_count(text=message)
    answer_before = _interaction_count(text=EMERGENCY_RESPONSE)
    started = time.perf_counter()
    response = client.post_legacy(
        message,
        history=[
            {"role": "user", "content": "Tell me about a fire in 2024."},
            {"role": "assistant", "content": "Historical answer."},
        ]
        * 100,
        ui_context={"bad": "shape ignored because emergency routing wins"},
    )
    latency_ms = (time.perf_counter() - started) * 1000.0
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["response"] == EMERGENCY_RESPONSE
    assert payload["mode"] == "emergency"
    assert payload["sources"] == []
    assert payload["log_id"] is None
    assert _interaction_count(text=message) == raw_before
    assert _interaction_count(text=EMERGENCY_RESPONSE) == answer_before
    return latency_ms


def test_ownership_rejection(base_url: str) -> None:
    owner = Client(base_url)
    other = Client(base_url)
    thread_id = owner.create_thread("Owned")
    response = other.post_json(thread_id, "Someone is shooting outside.")
    assert response.status_code == 404
    assert _message_count(thread_id) == 0


def test_same_thread_conflict(base_url: str) -> Dict[str, int]:
    client = Client(base_url)
    thread_id = client.create_thread("Emergency race")
    before = _seed_envelope()
    _write_state(thread_id, before)
    baseline_messages = _message_count(thread_id)
    messages = [
        f"Someone is shooting outside. Race {index} {uuid.uuid4().hex}"
        for index in range(12)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        responses = list(
            executor.map(
                lambda text: client.post_json(thread_id, text),
                messages,
            )
        )
    statuses = [response.status_code for response in responses]
    assert set(statuses) <= {201, 409}, [
        (response.status_code, response.text) for response in responses
    ]
    successes = statuses.count(201)
    conflicts = statuses.count(409)
    assert successes >= 1
    assert conflicts >= 1
    assert _message_count(thread_id) == baseline_messages + (2 * successes)
    assert _read_state(thread_id).to_dict() == before.to_dict()
    return {"successes": successes, "conflicts": conflicts}


def test_multi_user_isolation(base_url: str) -> None:
    clients = [Client(base_url) for _ in range(8)]
    thread_ids = [
        client.create_thread(f"Isolated emergency {index}")
        for index, client in enumerate(clients)
    ]
    for thread_id in thread_ids:
        _write_state(thread_id, _seed_envelope())
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(
            executor.map(
                lambda item: item[0].post_json(
                    item[1],
                    "Someone has a gun and is threatening people. "
                    f"{uuid.uuid4().hex}",
                ),
                zip(clients, thread_ids),
            )
        )
    statuses = [response.status_code for response in responses]
    assert statuses == [201] * len(responses)
    for response, thread_id in zip(responses, thread_ids):
        payload = response.json()
        _assert_persisted_emergency(payload)
        assert payload["thread"]["id"] == thread_id
        assert _message_count(thread_id) == 2


def test_warm_endpoint_latency(base_url: str) -> Dict[str, float]:
    client = Client(base_url)
    thread_id = client.create_thread("Emergency latency")
    _write_state(thread_id, _seed_envelope())
    latencies = []
    for index in range(25):
        started = time.perf_counter()
        response = client.post_json(
            thread_id,
            f"Someone is unconscious and not responding. Sample {index}",
        )
        latencies.append((time.perf_counter() - started) * 1000.0)
        assert response.status_code == 201, response.text
        _assert_persisted_emergency(response.json())
    p50 = statistics.median(latencies)
    p90 = _nearest_rank(latencies, 0.90)
    assert p90 < 100.0, (p50, p90, latencies)
    return {"p50_ms": p50, "p90_ms": p90, "max_ms": max(latencies)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    json_result = test_json_and_selected_event_precedence(args.base_url)
    print(
        "OK  JSON persistence, selected-event precedence, "
        "state/cache, privacy"
    )
    sse_ms = test_sse_parity(
        args.base_url,
        json_result["payload"]["assistant_message"]["content"],
    )
    print("OK  SSE persistence and JSON parity")
    legacy_ms = test_legacy_ignores_history_and_skips_log(args.base_url)
    print("OK  legacy current-message-only routing and privacy")
    test_ownership_rejection(args.base_url)
    print("OK  user/thread ownership rejection")
    race = test_same_thread_conflict(args.base_url)
    print(
        "OK  same-thread one-winner checks: "
        f"{race['successes']} success, {race['conflicts']} conflicts"
    )
    test_multi_user_isolation(args.base_url)
    print("OK  two-worker multi-user/thread isolation: 8/8")
    latency = test_warm_endpoint_latency(args.base_url)
    print(
        "PERF warm emergency endpoint "
        f"p50={latency['p50_ms']:.2f}ms p90={latency['p90_ms']:.2f}ms "
        f"max={latency['max_ms']:.2f}ms; "
        f"single JSON={json_result['latency_ms']:.2f}ms "
        f"SSE={sse_ms:.2f}ms legacy={legacy_ms:.2f}ms"
    )
    print("\nEmergency live validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
