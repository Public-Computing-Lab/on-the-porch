#!/usr/bin/env python3
"""Verify Stage 3 turn guarantees against the isolated local demo API."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import mysql.connector
import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from config import Config  # noqa: E402
from turn_idempotency import build_request_fingerprint  # noqa: E402
from turn_repository import ReservationKind, TurnRepository  # noqa: E402


def require_demo_database() -> None:
    host = str(Config.MYSQL_HOST).lower()
    database = str(Config.MYSQL_DB).lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"Refusing non-local MySQL host: {Config.MYSQL_HOST}")
    if database != "sentiment_demo":
        raise RuntimeError(f"Refusing non-demo MySQL database: {Config.MYSQL_DB}")
    if Config.PRODUCTION_MODE:
        raise RuntimeError("Refusing to run with PRODUCTION_MODE enabled")


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile_value
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class DemoClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.headers: dict[str, str] = {}
        self.user_id = ""

    def bootstrap(self) -> None:
        response = self.session.post(f"{self.base_url}/auth/guest", timeout=10)
        response.raise_for_status()
        payload = response.json()
        self.user_id = str(payload["user"]["id"])
        self.headers = {
            "Authorization": f"Bearer {payload['session_token']}",
            "X-CSRF-Token": payload["csrf_token"],
            "Content-Type": "application/json",
        }

    def create_thread(self, title: str) -> str:
        response = self.session.post(
            f"{self.base_url}/conversations",
            headers=self.headers,
            json={"title": title},
            timeout=10,
        )
        response.raise_for_status()
        return str(response.json()["thread"]["id"])

    def post_json(
        self,
        thread_id: str,
        *,
        message: str,
        client_turn_id: str | None,
    ) -> tuple[int, dict[str, Any], float]:
        payload: dict[str, Any] = {"message": message}
        if client_turn_id is not None:
            payload["client_turn_id"] = client_turn_id
        started = time.perf_counter()
        response = self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self.headers,
            json=payload,
            timeout=30,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return response.status_code, response.json(), elapsed_ms

    def post_sse(
        self,
        thread_id: str,
        *,
        message: str,
        client_turn_id: str,
    ) -> tuple[int, list[dict[str, Any]], float]:
        started = time.perf_counter()
        response = self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages/stream",
            headers=self.headers,
            json={"message": message, "client_turn_id": client_turn_id},
            stream=True,
            timeout=30,
        )
        events: list[dict[str, Any]] = []
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            events.append(json.loads(line[5:].strip()))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return response.status_code, events, elapsed_ms

    def disconnect_sse(
        self,
        thread_id: str,
        *,
        message: str,
        client_turn_id: str,
    ) -> float:
        started = time.perf_counter()
        response = self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages/stream",
            headers=self.headers,
            json={"message": message, "client_turn_id": client_turn_id},
            stream=True,
            timeout=30,
        )
        response.raise_for_status()
        iterator: Iterable[bytes] = response.iter_lines()
        next(iter(iterator), b"")
        response.close()
        return (time.perf_counter() - started) * 1000.0

    def status(
        self,
        thread_id: str,
        client_turn_id: str,
    ) -> tuple[int, dict[str, Any], float]:
        started = time.perf_counter()
        response = self.session.get(
            f"{self.base_url}/conversations/{thread_id}/turns/{client_turn_id}",
            headers=self.headers,
            timeout=10,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return response.status_code, response.json(), elapsed_ms

    def messages(self, thread_id: str) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self.headers,
            timeout=10,
        )
        response.raise_for_status()
        return list(response.json()["messages"])


def wait_for_completed(
    client: DemoClient,
    thread_id: str,
    client_turn_id: str,
) -> tuple[dict[str, Any], list[float]]:
    latencies: list[float] = []
    for _ in range(60):
        status_code, payload, elapsed_ms = client.status(thread_id, client_turn_id)
        latencies.append(elapsed_ms)
        if status_code == 200 and payload.get("turn", {}).get("status") == "completed":
            return payload, latencies
        if status_code == 200 and payload.get("turn", {}).get("status") == "failed":
            raise AssertionError(f"Disconnected turn failed: {payload}")
        time.sleep(0.05)
    raise AssertionError("Disconnected SSE turn did not complete")


def database_counts(thread_id: str, client_turn_id: str) -> dict[str, Any]:
    conn = mysql.connector.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB,
    )
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT status, user_message_id, assistant_message_id,
                       state_version_at_start, response_payload_json
                FROM conversation_turns
                WHERE thread_id = %s AND client_turn_id = %s
                """,
                (thread_id, client_turn_id),
            )
            turn = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*) AS message_count
                FROM conversation_messages
                WHERE thread_id = %s
                  AND JSON_UNQUOTE(JSON_EXTRACT(message_meta_json, '$.client_turn_id')) = %s
                """,
                (thread_id, client_turn_id),
            )
            count = cursor.fetchone()
            cursor.execute(
                """
                SELECT thread_state_version, active_turn_id
                FROM conversation_threads WHERE id = %s
                """,
                (thread_id,),
            )
            thread = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        conn.close()
    return {
        "turn_status": turn["status"] if turn else None,
        "user_message_id": turn["user_message_id"] if turn else None,
        "assistant_message_id": turn["assistant_message_id"] if turn else None,
        "response_payload_present": bool(turn and turn["response_payload_json"]),
        "message_count": int(count["message_count"] if count else 0),
        "thread_state_version": int(thread["thread_state_version"] if thread else 0),
        "active_turn_cleared": bool(thread and thread["active_turn_id"] is None),
    }


def demo_connection():
    return mysql.connector.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB,
    )


def verify_repository_coordination(
    client: DemoClient,
) -> dict[str, Any]:
    repository = TurnRepository(demo_connection, lease_seconds=60)
    thread_id = client.create_thread("Stage 3 repository coordination")
    first_client_id = str(uuid.uuid4())
    first_message = "First coordinated turn"
    first_fingerprint = build_request_fingerprint(
        owner_id=client.user_id,
        thread_id=thread_id,
        message=first_message,
    )
    first = repository.reserve(
        thread_id=thread_id,
        user_id=client.user_id,
        client_turn_id=first_client_id,
        request_fingerprint=first_fingerprint,
        transport_origin="test",
        client_idempotent=True,
        trace_id="stage3-coordination",
    )
    assert first.kind is ReservationKind.NEW_EXECUTION_OWNER

    busy_client_id = str(uuid.uuid4())
    busy = repository.reserve(
        thread_id=thread_id,
        user_id=client.user_id,
        client_turn_id=busy_client_id,
        request_fingerprint=build_request_fingerprint(
            owner_id=client.user_id,
            thread_id=thread_id,
            message="Second coordinated turn",
        ),
        transport_origin="test",
        client_idempotent=True,
        trace_id="stage3-coordination",
    )
    assert busy.kind is ReservationKind.THREAD_BUSY
    assert repository.mark_failed(
        first,
        user_id=client.user_id,
        error_category="test_cleanup",
    )

    duplicate_client_id = str(uuid.uuid4())
    duplicate_fingerprint = build_request_fingerprint(
        owner_id=client.user_id,
        thread_id=thread_id,
        message="Duplicate race",
    )

    def reserve_duplicate():
        return repository.reserve(
            thread_id=thread_id,
            user_id=client.user_id,
            client_turn_id=duplicate_client_id,
            request_fingerprint=duplicate_fingerprint,
            transport_origin="test",
            client_idempotent=True,
            trace_id="stage3-duplicate-race",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        duplicate_results = list(executor.map(lambda _value: reserve_duplicate(), range(2)))
    duplicate_kinds = sorted(result.kind.value for result in duplicate_results)
    assert duplicate_kinds == sorted(
        [
            ReservationKind.NEW_EXECUTION_OWNER.value,
            ReservationKind.RUNNING_EXISTING.value,
        ]
    )
    duplicate_owner = next(result for result in duplicate_results if result.owns_execution)
    assert repository.mark_failed(
        duplicate_owner,
        user_id=client.user_id,
        error_category="test_cleanup",
    )

    independent_threads = [
        client.create_thread("Stage 3 independent A"),
        client.create_thread("Stage 3 independent B"),
    ]

    def reserve_independent(item: tuple[int, str]):
        index, independent_thread_id = item
        message = f"Independent turn {index}"
        return repository.reserve(
            thread_id=independent_thread_id,
            user_id=client.user_id,
            client_turn_id=str(uuid.uuid4()),
            request_fingerprint=build_request_fingerprint(
                owner_id=client.user_id,
                thread_id=independent_thread_id,
                message=message,
            ),
            transport_origin="test",
            client_idempotent=True,
            trace_id="stage3-independent",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        independent_results = list(
            executor.map(reserve_independent, enumerate(independent_threads))
        )
    assert all(result.kind is ReservationKind.NEW_EXECUTION_OWNER for result in independent_results)
    for result in independent_results:
        assert repository.mark_failed(
            result,
            user_id=client.user_id,
            error_category="test_cleanup",
        )

    reservation_latencies: list[float] = []
    for index in range(20):
        benchmark_client_id = str(uuid.uuid4())
        benchmark_message = f"Reservation benchmark {index}"
        started = time.perf_counter()
        benchmark = repository.reserve(
            thread_id=thread_id,
            user_id=client.user_id,
            client_turn_id=benchmark_client_id,
            request_fingerprint=build_request_fingerprint(
                owner_id=client.user_id,
                thread_id=thread_id,
                message=benchmark_message,
            ),
            transport_origin="test",
            client_idempotent=True,
            trace_id="stage3-reservation-benchmark",
        )
        reservation_latencies.append((time.perf_counter() - started) * 1000.0)
        assert benchmark.kind is ReservationKind.NEW_EXECUTION_OWNER
        assert repository.mark_failed(
            benchmark,
            user_id=client.user_id,
            error_category="test_cleanup",
        )

    return {
        "same_thread_different_turn": busy.kind.value,
        "duplicate_race": duplicate_kinds,
        "different_threads": [result.kind.value for result in independent_results],
        "reservation_latency": {
            "count": len(reservation_latencies),
            "p50_ms": round(statistics.median(reservation_latencies), 3),
            "p95_ms": round(percentile(reservation_latencies, 0.95), 3),
            "max_ms": round(max(reservation_latencies), 3),
        },
    }


def run(base_url: str) -> dict[str, Any]:
    require_demo_database()
    health = requests.get(f"{base_url.rstrip('/')}/health", timeout=10)
    if health.status_code != 200:
        raise RuntimeError(f"Local API health failed with HTTP {health.status_code}")

    primary = DemoClient(base_url)
    primary.bootstrap()
    other = DemoClient(base_url)
    other.bootstrap()
    thread_id = primary.create_thread("Stage 3 lifecycle verification")
    timings: dict[str, list[float]] = {
        "new_json": [],
        "json_replay": [],
        "sse_replay": [],
        "status": [],
    }

    json_turn_id = str(uuid.uuid4())
    status, first, elapsed = primary.post_json(
        thread_id,
        message="Hi",
        client_turn_id=json_turn_id,
    )
    timings["new_json"].append(elapsed)
    assert status == 201
    assert first["turn"]["status"] == "completed"
    assert first["turn"]["replayed"] is False

    status, replay, elapsed = primary.post_json(
        thread_id,
        message="Hi",
        client_turn_id=json_turn_id,
    )
    timings["json_replay"].append(elapsed)
    assert status == 201
    assert replay["turn"]["replayed"] is True
    assert replay["user_message"]["id"] == first["user_message"]["id"]
    assert replay["assistant_message"]["id"] == first["assistant_message"]["id"]

    status, sse_events, elapsed = primary.post_sse(
        thread_id,
        message="Hi",
        client_turn_id=json_turn_id,
    )
    timings["sse_replay"].append(elapsed)
    assert status == 200
    assert [event["type"] for event in sse_events] == ["final"]
    assert sse_events[0]["turn"]["replayed"] is True
    assert sse_events[0]["assistant_message"]["id"] == first["assistant_message"]["id"]
    assert sse_events[0]["answer"] == first["assistant_message"]["content"]
    assert sse_events[0]["mode"] == first["assistant_message"]["response_mode"]
    assert sse_events[0]["sources"] == first["assistant_message"]["sources"]

    status, conflict, _ = primary.post_json(
        thread_id,
        message="Thanks",
        client_turn_id=json_turn_id,
    )
    assert status == 409
    assert conflict["code"] == "client_turn_id_conflict"

    status, owned_status, elapsed = primary.status(thread_id, json_turn_id)
    timings["status"].append(elapsed)
    assert status == 200
    assert owned_status["turn"]["status"] == "completed"
    assert owned_status["result"]["assistant_message"]["id"] == first["assistant_message"]["id"]

    status, not_owned, _ = other.status(thread_id, json_turn_id)
    assert status == 404
    assert not_owned["code"] == "turn_not_found"

    status, legacy, _ = primary.post_json(
        thread_id,
        message="Thanks",
        client_turn_id=None,
    )
    assert status == 201
    assert legacy["turn"]["client_idempotent"] is False
    assert legacy["turn"]["status"] == "completed"

    disconnected_turn_id = str(uuid.uuid4())
    disconnect_ms = primary.disconnect_sse(
        thread_id,
        message="Hello",
        client_turn_id=disconnected_turn_id,
    )
    completed, status_latencies = wait_for_completed(
        primary,
        thread_id,
        disconnected_turn_id,
    )
    timings["status"].extend(status_latencies)
    status, disconnected_replay, elapsed = primary.post_json(
        thread_id,
        message="Hello",
        client_turn_id=disconnected_turn_id,
    )
    timings["json_replay"].append(elapsed)
    assert status == 201
    assert disconnected_replay["turn"]["replayed"] is True
    assert (
        disconnected_replay["assistant_message"]["id"]
        == completed["result"]["assistant_message"]["id"]
    )

    persisted = database_counts(thread_id, json_turn_id)
    assert persisted["turn_status"] == "COMPLETED"
    assert persisted["message_count"] == 2
    assert persisted["response_payload_present"] is True
    assert persisted["active_turn_cleared"] is True
    all_messages = primary.messages(thread_id)
    json_turn_messages = [
        message
        for message in all_messages
        if message["id"]
        in {first["user_message"]["id"], first["assistant_message"]["id"]}
    ]
    assert len(json_turn_messages) == 2

    different_thread = primary.create_thread("Stage 3 scoped turn identity")
    status, scoped, _ = primary.post_json(
        different_thread,
        message="Hi",
        client_turn_id=json_turn_id,
    )
    assert status == 201
    assert scoped["turn"]["replayed"] is False
    coordination = verify_repository_coordination(primary)

    timing_summary = {
        name: {
            "count": len(values),
            "p50_ms": round(statistics.median(values), 3) if values else 0.0,
            "p95_ms": round(percentile(values, 0.95), 3),
            "max_ms": round(max(values), 3) if values else 0.0,
        }
        for name, values in timings.items()
    }
    return {
        "status": "pass",
        "environment": {
            "api": base_url,
            "mysql_host": Config.MYSQL_HOST,
            "mysql_database": Config.MYSQL_DB,
            "production_mode": Config.PRODUCTION_MODE,
        },
        "checks": {
            "json_first_execution": True,
            "json_completed_replay": True,
            "sse_completed_replay_single_final": True,
            "fingerprint_conflict": True,
            "status_ownership": True,
            "legacy_missing_id": True,
            "sse_disconnect_completion": True,
            "json_fallback_same_id": True,
            "message_pair_inserted_once": True,
            "active_turn_cleared": True,
            "cross_thread_id_scope_documented": True,
            "cross_worker_duplicate_suppression": True,
            "same_thread_contention": True,
            "different_thread_independence": True,
        },
        "persistence": persisted,
        "coordination": coordination,
        "disconnect_initial_ms": round(disconnect_ms, 3),
        "timings": timing_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8893")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run(args.base_url)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{rendered}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
