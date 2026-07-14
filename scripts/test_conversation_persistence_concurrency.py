#!/usr/bin/env python3
"""Focused unit and local-live tests for lock-first conversation persistence."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "on_the_porch"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from conversation_state import (  # noqa: E402
    ActiveEntity,
    ConversationState,
    ProposedTransition,
    ThreadStateEnvelope,
    serialize_thread_state,
)


EVENTS = {
    "435": ("Virtual Baby Cafe", "2026-07-15"),
    "436": (
        "Community Meeting (Hosted by Substance Recovery Services)",
        "2026-07-16",
    ),
}


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


def _empty_envelope(version: int = 0) -> ThreadStateEnvelope:
    return ThreadStateEnvelope(
        retrieval_cache={
            "mode": None,
            "timestamp": None,
            "question": None,
            "sql_result": None,
            "sql_query": None,
            "rag_chunks": None,
            "rag_metadata": None,
            "answer": None,
        },
        conversation_state=ConversationState(state_version=version),
    )


def _persistence_kwargs(api, envelope: ThreadStateEnvelope) -> Dict[str, Any]:
    return {
        "thread_id": "thread-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "expected_envelope": envelope,
        "next_retrieval_cache": envelope.retrieval_cache,
        "transition": ProposedTransition.preserve(envelope.conversation_state),
        "user_content": "Question",
        "user_meta": {},
        "assistant_content": "Answer",
        "assistant_mode": "sql",
        "assistant_sources": [],
        "assistant_model_name": None,
        "resolved_entity": None,
        "set_initial_title": False,
    }


def test_stale_state_conflicts_before_message_insert() -> None:
    api = _import_api()
    expected = _empty_envelope(0)
    current = ThreadStateEnvelope(
        retrieval_cache=expected.retrieval_cache,
        conversation_state=ConversationState(
            state_version=1,
            active_topic="events",
            active_entity=ActiveEntity.event("435"),
            previous_route="sql",
            last_substantive_message_id="message-1",
        ),
    )
    conn = _FakeConnection()
    insert_calls = []
    with _patch(
        api,
        get_db_connection=lambda: conn,
        _lock_thread_for_persistence=lambda *_args: {
            "title": "New conversation",
            "thread_state_json": serialize_thread_state(current),
        },
        _insert_message=lambda *_args, **_kwargs: insert_calls.append(True),
    ):
        try:
            api._persist_conversation_turn(**_persistence_kwargs(api, expected))
        except api.ConversationConflictError:
            pass
        else:
            raise AssertionError("stale turn did not raise ConversationConflictError")
    assert conn.started and conn.rolled_back and conn.closed
    assert not conn.committed and insert_calls == []


def test_failure_after_first_message_rolls_back_pair() -> None:
    api = _import_api()
    expected = _empty_envelope(0)
    conn = _FakeConnection()
    insert_count = 0

    def fail_on_assistant(*_args, **_kwargs):
        nonlocal insert_count
        insert_count += 1
        if insert_count == 1:
            return {"id": "user-message"}
        raise RuntimeError("simulated assistant insert failure")

    with _patch(
        api,
        get_db_connection=lambda: conn,
        _lock_thread_for_persistence=lambda *_args: {
            "title": "New conversation",
            "thread_state_json": serialize_thread_state(expected),
        },
        _insert_message=fail_on_assistant,
    ):
        try:
            api._persist_conversation_turn(**_persistence_kwargs(api, expected))
        except RuntimeError as exc:
            assert "simulated" in str(exc)
        else:
            raise AssertionError("simulated persistence failure was swallowed")
    assert insert_count == 2
    assert conn.started and conn.rolled_back and conn.closed
    assert not conn.committed


def test_interaction_log_failure_is_best_effort_after_core_commit() -> None:
    api = _import_api()
    expected = _empty_envelope(0)
    core_conn = _FakeConnection()
    log_conn = _FakeConnection()
    connection_order = []

    def get_connection():
        connection = core_conn if not connection_order else log_conn
        connection_order.append(connection)
        return connection

    inserted = iter(({"id": "user-message"}, {"id": "assistant-message"}))
    with _patch(
        api,
        get_db_connection=get_connection,
        _lock_thread_for_persistence=lambda *_args: {
            "title": "Existing conversation",
            "thread_state_json": serialize_thread_state(expected),
        },
        _insert_message=lambda *_args, **_kwargs: next(inserted),
        _merge_locked_thread_state=lambda *_args, **_kwargs: {"transition_applied": False},
        _fetch_thread=lambda *_args, **_kwargs: {"id": "thread-1"},
        log_interaction=lambda **_kwargs: None,
    ):
        result = api._persist_conversation_turn(**_persistence_kwargs(api, expected))

    assert result["assistant_message"]["id"] == "assistant-message"
    assert connection_order == [core_conn, log_conn]
    assert core_conn.committed and core_conn.closed and not core_conn.rolled_back
    assert log_conn.rolled_back and log_conn.closed and not log_conn.committed


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
            "SELECT thread_state_json FROM conversation_threads WHERE id = %s",
            (thread_id,),
        )
        row = cursor.fetchone()
        assert row
        envelope = json.loads(row["thread_state_json"])
        cache_json = json.dumps(
            envelope.get("retrieval_cache", {}),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        cursor.execute(
            "SELECT id, role, sources_json FROM conversation_messages "
            "WHERE thread_id = %s ORDER BY created_at, id",
            (thread_id,),
        )
        messages = cursor.fetchall()
        cursor.execute(
            "SELECT id, message_id FROM interaction_log WHERE thread_id = %s ORDER BY id",
            (thread_id,),
        )
        logs = cursor.fetchall()
        return {
            "state": envelope.get("conversation_state", {}),
            "cache_hash": hashlib.sha256(cache_json).hexdigest(),
            "messages": messages,
            "logs": logs,
        }
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

    def create_thread(self, title: str) -> str:
        response = self.requests.post(
            f"{self.base_url}/conversations",
            headers=self.headers(),
            json={"title": title},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["thread"]["id"]

    def event_payload(self, event_id: str) -> Dict[str, Any]:
        title, event_date = EVENTS[event_id]
        return {
            "message": "Tell me more about this event",
            "ui_context": {
                "entity_type": "event",
                "entity_id": event_id,
                "entity_title": title,
                "entity_date": event_date,
                "source": "events_sidebar",
            },
        }

    def send_json(self, thread_id: str, payload: Dict[str, Any]):
        return self.requests.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json=payload,
            timeout=45,
        )

    def send_stream(self, thread_id: str, payload: Dict[str, Any]):
        response = self.requests.post(
            f"{self.base_url}/conversations/{thread_id}/messages/stream",
            headers=self.headers(),
            json=payload,
            stream=True,
            timeout=45,
        )
        events = []
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
        return response, events


def _locked_race(client: _GuestClient, thread_id: str, *, streaming: bool):
    lock_conn = _db_connection()
    lock_cursor = lock_conn.cursor()
    lock_conn.start_transaction()
    lock_cursor.execute(
        "SELECT id FROM conversation_threads WHERE id = %s FOR UPDATE",
        (thread_id,),
    )
    assert lock_cursor.fetchone() == (thread_id,)
    barrier = threading.Barrier(3)

    def send(event_id: str):
        barrier.wait()
        if streaming:
            response, events = client.send_stream(thread_id, client.event_payload(event_id))
            return {"event_id": event_id, "status": response.status_code, "events": events}
        response = client.send_json(thread_id, client.event_payload(event_id))
        return {
            "event_id": event_id,
            "status": response.status_code,
            "payload": response.json(),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send, event_id) for event_id in ("435", "436")]
        barrier.wait()
        time.sleep(0.25)
        lock_conn.commit()
        results = [future.result() for future in futures]
    lock_cursor.close()
    lock_conn.close()
    return results


def _assert_one_winner(thread_id: str, before: Dict[str, Any], results, *, streaming: bool):
    if streaming:
        finals = [
            (result, next((event for event in result["events"] if event.get("type") == "final"), None))
            for result in results
        ]
        winners = [(result, event) for result, event in finals if event]
        conflicts = [
            event
            for result in results
            for event in result["events"]
            if event.get("type") == "error" and event.get("code") == "conversation_conflict"
        ]
        assert all(result["status"] == 200 for result in results), results
        assert len(winners) == 1 and len(conflicts) == 1, results
        winner_id = winners[0][0]["event_id"]
    else:
        winners = [result for result in results if result["status"] == 201]
        conflicts = [result for result in results if result["status"] == 409]
        assert len(winners) == 1 and len(conflicts) == 1, results
        assert conflicts[0]["payload"] == {
            "error": "This conversation changed while your message was being processed. Please try again.",
            "code": "conversation_conflict",
        }
        winner_id = winners[0]["event_id"]

    after = _thread_snapshot(thread_id)
    assert after["state"]["state_version"] == 1, after
    assert after["state"]["active_entity"]["id"] == winner_id, after
    assert after["cache_hash"] == before["cache_hash"], after
    assert [message["role"] for message in after["messages"]] == ["user", "assistant"], after
    assert len(after["logs"]) == 1, after
    assert after["logs"][0]["message_id"] == after["messages"][1]["id"], after
    return winner_id


def run_live_tests(base_url: str, iterations: int) -> None:
    client = _GuestClient(base_url)
    client.bootstrap()

    failed_thread = client.create_thread("Rejected request")
    failed_before = _thread_snapshot(failed_thread)
    invalid_payload = client.event_payload("435")
    invalid_payload["ui_context"]["entity_id"] = "../invalid"
    rejected = client.send_json(failed_thread, invalid_payload)
    assert rejected.status_code == 400, rejected.text
    assert _thread_snapshot(failed_thread) == failed_before
    print("OK  rejected request leaves messages, state, and cache unchanged")

    json_winners = []
    for index in range(iterations):
        thread_id = client.create_thread(f"JSON race {index}")
        before = _thread_snapshot(thread_id)
        results = _locked_race(client, thread_id, streaming=False)
        json_winners.append(_assert_one_winner(thread_id, before, results, streaming=False))
    print(f"OK  repeated JSON races: {iterations}, winners={json_winners}")

    stream_thread = client.create_thread("SSE race")
    stream_before = _thread_snapshot(stream_thread)
    stream_results = _locked_race(client, stream_thread, streaming=True)
    stream_winner = _assert_one_winner(
        stream_thread,
        stream_before,
        stream_results,
        streaming=True,
    )
    print(f"OK  SSE conflict parity, winner={stream_winner}")

    # Equivalent read-only follow-ups may both persist because state does not change.
    follow_thread = client.create_thread("Equivalent followups")
    selected = client.send_json(follow_thread, client.event_payload("435"))
    assert selected.status_code == 201
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        followups = list(
            pool.map(
                lambda _: client.send_json(follow_thread, {"message": "Where is it?"}),
                range(2),
            )
        )
    assert [response.status_code for response in followups] == [201, 201]
    follow_snapshot = _thread_snapshot(follow_thread)
    assert follow_snapshot["state"]["state_version"] == 1
    assert len(follow_snapshot["messages"]) == 6
    print("OK  equivalent concurrent event follow-ups remain valid")

    # Eight independent requests use one DB connection each during persistence.
    clients = [_GuestClient(base_url) for _ in range(8)]
    for other in clients:
        other.bootstrap()
    threads = [other.create_thread(f"Pool pressure {index}") for index, other in enumerate(clients)]
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(
                lambda item: item[0].send_json(
                    item[1],
                    item[0].event_payload("435" if item[2] % 2 == 0 else "436"),
                ),
                [(other, thread_id, index) for index, (other, thread_id) in enumerate(zip(clients, threads))],
            )
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    assert all(response.status_code == 201 for response in responses), [
        response.status_code for response in responses
    ]
    assert all(_thread_snapshot(thread_id)["state"]["state_version"] == 1 for thread_id in threads)
    print(f"OK  eight independent requests: 8/8 in {elapsed_ms}ms")

    # Separate users and threads remain isolated.
    isolated_a, isolated_b = _GuestClient(base_url), _GuestClient(base_url)
    isolated_a.bootstrap(); isolated_b.bootstrap()
    thread_a, thread_b = isolated_a.create_thread("User A"), isolated_b.create_thread("User B")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        response_a, response_b = [future.result() for future in (
            pool.submit(isolated_a.send_json, thread_a, isolated_a.event_payload("435")),
            pool.submit(isolated_b.send_json, thread_b, isolated_b.event_payload("436")),
        )]
    assert response_a.status_code == response_b.status_code == 201
    assert _thread_snapshot(thread_a)["state"]["active_entity"]["id"] == "435"
    assert _thread_snapshot(thread_b)["state"]["active_entity"]["id"] == "436"
    print("OK  different users and threads remain isolated")

    # One user's independent threads must not serialize through a global lock.
    shared_client = _GuestClient(base_url)
    shared_client.bootstrap()
    shared_thread_a = shared_client.create_thread("Same user thread A")
    shared_thread_b = shared_client.create_thread("Same user thread B")
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        shared_responses = [future.result() for future in (
            pool.submit(
                shared_client.send_json,
                shared_thread_a,
                shared_client.event_payload("435"),
            ),
            pool.submit(
                shared_client.send_json,
                shared_thread_b,
                shared_client.event_payload("436"),
            ),
        )]
    same_user_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    assert [response.status_code for response in shared_responses] == [201, 201]
    assert _thread_snapshot(shared_thread_a)["state"]["active_entity"]["id"] == "435"
    assert _thread_snapshot(shared_thread_b)["state"]["active_entity"]["id"] == "436"
    print(f"OK  same user different threads: 2/2 in {same_user_elapsed_ms}ms")

    # Connection: close on every request lets sequential turns cross workers while
    # the persisted state remains authoritative.
    continuity_thread = shared_client.create_thread("Cross-worker continuity")
    selected = shared_client.send_json(continuity_thread, shared_client.event_payload("435"))
    assert selected.status_code == 201
    def send_timed_followup(_index: int):
        started = time.perf_counter()
        followup = shared_client.send_json(continuity_thread, {"message": "Where is it?"})
        latency_ms = (time.perf_counter() - started) * 1000
        assert followup.status_code == 201, followup.text
        payload = followup.json()
        assert payload["assistant_message"]["response_mode"] == "sql", payload
        assert "virtual" in payload["assistant_message"]["content"].lower(), payload
        return latency_ms

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        followup_latencies = list(pool.map(send_timed_followup, range(20)))
    sorted_latencies = sorted(followup_latencies)
    p90_index = max(0, min(len(sorted_latencies) - 1, round(0.9 * len(sorted_latencies) + 0.5) - 1))
    followup_p90_ms = round(sorted_latencies[p90_index], 2)
    assert followup_p90_ms < 500, followup_latencies
    continuity_snapshot = _thread_snapshot(continuity_thread)
    assert continuity_snapshot["state"]["state_version"] == 1
    assert continuity_snapshot["state"]["active_entity"]["id"] == "435"
    print(
        "OK  cross-worker continuity: 20/20 follow-ups, "
        f"p90={followup_p90_ms}ms, thread={continuity_thread}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--iterations", type=int, default=8)
    args = parser.parse_args()

    tests = [
        test_stale_state_conflicts_before_message_insert,
        test_failure_after_first_message_rolls_back_pair,
        test_interaction_log_failure_is_best_effort_after_core_commit,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    if args.base_url:
        run_live_tests(args.base_url, max(2, args.iterations))
    print(f"\nConversation persistence concurrency tests passed: {len(tests)}/{len(tests)} unit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
