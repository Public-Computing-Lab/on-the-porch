#!/usr/bin/env python3
"""Exercise Stage 3 atomic persistence against the isolated demo database."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import mysql.connector
import requests


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from config import Config  # noqa: E402
from conversation_turn import (  # noqa: E402
    ConversationTurnService,
    GeneratedTurn,
    TurnEventKind,
    TurnRequest,
)
from turn_idempotency import build_request_fingerprint  # noqa: E402
from turn_repository import ReservationKind, TurnRepository  # noqa: E402


def require_demo_database() -> None:
    if str(Config.MYSQL_HOST).lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Refusing non-local MySQL")
    if str(Config.MYSQL_DB).lower() != "sentiment_demo":
        raise RuntimeError("Refusing non-demo MySQL")
    if Config.PRODUCTION_MODE:
        raise RuntimeError("Refusing production mode")


def connection():
    return mysql.connector.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB,
    )


def bootstrap(base_url: str) -> tuple[requests.Session, dict[str, str], str, str]:
    session = requests.Session()
    guest = session.post(f"{base_url}/auth/guest", timeout=10)
    guest.raise_for_status()
    guest_payload = guest.json()
    headers = {
        "Authorization": f"Bearer {guest_payload['session_token']}",
        "X-CSRF-Token": guest_payload["csrf_token"],
        "Content-Type": "application/json",
    }
    thread = session.post(
        f"{base_url}/conversations",
        headers=headers,
        json={"title": "Stage 3 persistence verification"},
        timeout=10,
    )
    thread.raise_for_status()
    return (
        session,
        headers,
        str(guest_payload["user"]["id"]),
        str(thread.json()["thread"]["id"]),
    )


def make_request(
    *,
    user_id: str,
    thread_id: str,
    client_turn_id: str,
    message: str,
) -> TurnRequest:
    return TurnRequest(
        thread_id=thread_id,
        user_id=user_id,
        session_id="stage3-persistence",
        client_turn_id=client_turn_id,
        client_idempotent=True,
        request_fingerprint=build_request_fingerprint(
            owner_id=user_id,
            thread_id=thread_id,
            message=message,
        ),
        message=message,
        transport="test",
        trace_id="stage3-persistence",
    )


def reserve_running(
    repository: TurnRepository,
    request: TurnRequest,
):
    reservation = repository.reserve(
        thread_id=request.thread_id,
        user_id=request.user_id,
        client_turn_id=request.client_turn_id,
        request_fingerprint=request.request_fingerprint,
        transport_origin=request.transport,
        client_idempotent=request.client_idempotent,
        trace_id=request.trace_id,
    )
    assert reservation.kind is ReservationKind.NEW_EXECUTION_OWNER
    assert repository.mark_running(reservation, user_id=request.user_id)
    return reservation


def inspect(
    *,
    thread_id: str,
    client_turn_id: str,
) -> dict[str, Any]:
    conn = connection()
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT status, response_payload_json
                FROM conversation_turns
                WHERE thread_id = %s AND client_turn_id = %s
                """,
                (thread_id, client_turn_id),
            )
            turn = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM conversation_messages
                WHERE thread_id = %s
                  AND JSON_UNQUOTE(JSON_EXTRACT(message_meta_json, '$.client_turn_id')) = %s
                """,
                (thread_id, client_turn_id),
            )
            count = cursor.fetchone()
            cursor.execute(
                """
                SELECT thread_state_json, thread_state_version, active_turn_id
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
        "response_payload_present": bool(turn and turn["response_payload_json"]),
        "message_count": int(count["count"] if count else 0),
        "thread_state": json.loads(thread["thread_state_json"] or "{}"),
        "thread_state_version": int(thread["thread_state_version"]),
        "active_turn_id": thread["active_turn_id"],
    }


def run(base_url: str) -> dict[str, Any]:
    require_demo_database()
    _session, _headers, user_id, thread_id = bootstrap(base_url.rstrip("/"))

    # Importing the production finalizer is intentionally deferred until after
    # the local/demo guard has passed.
    import api_v2  # noqa: WPS433

    repository = TurnRepository(api_v2.get_db_connection, lease_seconds=60)

    rollback_request = make_request(
        user_id=user_id,
        thread_id=thread_id,
        client_turn_id=str(uuid.uuid4()),
        message="Rollback verification",
    )
    rollback_reservation = reserve_running(repository, rollback_request)
    rollback_context = api_v2._load_conversation_turn_context(
        rollback_request,
        rollback_reservation,
    )
    original_complete = api_v2._turn_repository.complete_on_connection

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("injected_completion_failure")

    api_v2._turn_repository.complete_on_connection = fail_completion
    try:
        try:
            api_v2._finalize_conversation_turn(
                rollback_context,
                GeneratedTurn(
                    answer="This result must roll back.",
                    mode="test",
                    sources=[],
                    next_thread_state={"mode": "test"},
                    result_payload={},
                ),
            )
            raise AssertionError("Injected completion failure unexpectedly committed")
        except RuntimeError as exc:
            assert str(exc) == "injected_completion_failure"
    finally:
        api_v2._turn_repository.complete_on_connection = original_complete
    rollback_state = inspect(
        thread_id=thread_id,
        client_turn_id=rollback_request.client_turn_id,
    )
    assert rollback_state["message_count"] == 0
    assert rollback_state["turn_status"] == "RUNNING"
    assert rollback_state["response_payload_present"] is False
    assert repository.mark_failed(
        rollback_reservation,
        user_id=user_id,
        error_category="test_rollback",
    )

    conflict_request = make_request(
        user_id=user_id,
        thread_id=thread_id,
        client_turn_id=str(uuid.uuid4()),
        message="State conflict verification",
    )
    conflict_reservation = reserve_running(repository, conflict_request)
    conflict_context = api_v2._load_conversation_turn_context(
        conflict_request,
        conflict_reservation,
    )
    conn = connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE conversation_threads
                SET thread_state_json = %s,
                    thread_state_version = thread_state_version + 1
                WHERE id = %s
                """,
                (json.dumps({"newer": "preserved"}), thread_id),
            )
            conn.commit()
        finally:
            cursor.close()
    finally:
        conn.close()
    persistence_started = time.perf_counter()
    final_payload = api_v2._finalize_conversation_turn(
        conflict_context,
        GeneratedTurn(
            answer="The persisted answer.",
            mode="test",
            sources=[],
            next_thread_state={"older": "must_not_replace"},
            result_payload={},
        ),
    )
    persistence_ms = (time.perf_counter() - persistence_started) * 1000.0
    conflict_state = inspect(
        thread_id=thread_id,
        client_turn_id=conflict_request.client_turn_id,
    )
    assert conflict_state["turn_status"] == "COMPLETED"
    assert conflict_state["message_count"] == 2
    assert conflict_state["response_payload_present"] is True
    assert conflict_state["thread_state"] == {"newer": "preserved"}
    assert conflict_state["active_turn_id"] is None

    callback_counts = {"load": 0, "finalize": 0, "produce": 0}

    def should_not_load(*_args):
        callback_counts["load"] += 1
        raise AssertionError("completed replay loaded context")

    def should_not_finalize(*_args):
        callback_counts["finalize"] += 1
        raise AssertionError("completed replay finalized again")

    def should_not_produce(_context):
        callback_counts["produce"] += 1
        raise AssertionError("completed replay regenerated")
        yield  # pragma: no cover

    restarted_service = ConversationTurnService(
        TurnRepository(api_v2.get_db_connection, lease_seconds=60),
        load_context=should_not_load,
        finalize=should_not_finalize,
        normalize_error=lambda exc: exc.__class__.__name__,
    )
    replay_events = list(restarted_service.execute(conflict_request, should_not_produce))
    assert len(replay_events) == 1
    assert replay_events[0].kind is TurnEventKind.FINAL
    assert replay_events[0].payload["turn"]["replayed"] is True
    assert replay_events[0].payload["assistant_message"]["id"] == (
        final_payload["assistant_message"]["id"]
    )
    assert callback_counts == {"load": 0, "finalize": 0, "produce": 0}

    return {
        "status": "pass",
        "environment": {
            "mysql_host": Config.MYSQL_HOST,
            "mysql_database": Config.MYSQL_DB,
            "production_mode": Config.PRODUCTION_MODE,
        },
        "rollback": {
            "message_count_after_failure": rollback_state["message_count"],
            "turn_status_before_normalized_failure": rollback_state["turn_status"],
            "partial_pair_prevented": True,
        },
        "state_conflict": {
            "message_count": conflict_state["message_count"],
            "turn_status": conflict_state["turn_status"],
            "newer_state_preserved": conflict_state["thread_state"],
            "active_turn_cleared": conflict_state["active_turn_id"] is None,
            "final_transaction_ms": round(persistence_ms, 3),
        },
        "restart_replay": {
            "callbacks": callback_counts,
            "replayed": replay_events[0].payload["turn"]["replayed"],
        },
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
