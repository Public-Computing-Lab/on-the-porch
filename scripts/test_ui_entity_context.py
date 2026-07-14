#!/usr/bin/env python3
"""Focused tests for authoritative UI-selected event context."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time as time_module
import uuid
from datetime import date, time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from event_context import (  # noqa: E402
    EventContextValidationError,
    EventResolution,
    ValidatedEventContext,
    build_selected_event_response,
    resolve_event_context,
    validate_event_ui_context,
)


VALID_CONTEXT = {
    "entity_type": "event",
    "entity_id": "417",
    "entity_title": "CREW & CADET STREET PARADE",
    "entity_date": "2026-07-13",
    "source": "events_sidebar",
}

AUTHORITATIVE_EVENT = {
    "id": 417,
    "event_name": "CREW & CADET STREET PARADE",
    "event_date": "Monday, July 13",
    "start_date": date(2026, 7, 13),
    "end_date": date(2026, 7, 13),
    "start_time": time(14, 0),
    "end_time": time(16, 0),
    "raw_text": (
        "CREW & CADET STREET PARADE\nMonday, July 13\n"
        "Location: Town Field\nContact: events@example.org\n"
        "Cost: Free\nRegistration: https://official.example.org/parade\n"
        "A neighborhood parade for residents."
    ),
    "source_pdf": "Dorchester Reporter",
}


class FakeCursor:
    def __init__(
        self,
        *,
        exact: Optional[Dict[str, Any]] = None,
        fallback: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.exact = exact
        self.fallback = fallback or []
        self.calls: List[tuple[str, tuple[Any, ...]]] = []
        self.current = ""
        self.closed = False

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.calls.append((sql, params))
        self.current = "exact" if "WHERE `id` = %s" in sql else "fallback"

    def fetchone(self):
        return self.exact if self.current == "exact" else None

    def fetchall(self):
        return list(self.fallback) if self.current == "fallback" else []

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(
        self,
        *,
        exact: Optional[Dict[str, Any]] = None,
        fallback: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.cursor_instance = FakeCursor(exact=exact, fallback=fallback)

    def cursor(self, *, dictionary: bool = False) -> FakeCursor:
        assert dictionary is True
        return self.cursor_instance


def _assert_invalid(raw: Any) -> None:
    try:
        validate_event_ui_context(raw)
    except EventContextValidationError:
        return
    raise AssertionError(f"expected invalid UI context: {raw!r}")


def test_validator_accepts_only_bounded_contract() -> None:
    validated = validate_event_ui_context(VALID_CONTEXT)
    assert validated == ValidatedEventContext(
        entity_id="417",
        entity_title="CREW & CADET STREET PARADE",
        entity_date="2026-07-13",
    )
    assert validated.user_message_metadata() == {
        "ui_context": {
            "entity_type": "event",
            "submitted_entity_id": "417",
            "source": "events_sidebar",
            "validation_status": "valid",
        }
    }
    assert len(json.dumps(validated.user_message_metadata())) < 1024


def test_validator_rejects_bad_shapes_and_extra_data() -> None:
    invalid = [
        None,
        [],
        "event",
        {**VALID_CONTEXT, "entity_type": "place"},
        {**VALID_CONTEXT, "source": "client"},
        {**VALID_CONTEXT, "entity_title": {"nested": True}},
        {**VALID_CONTEXT, "entity_title": ["nested"]},
        {**VALID_CONTEXT, "unknown": "value"},
        {**VALID_CONTEXT, "url": "https://client.invalid"},
        {**VALID_CONTEXT, "description": "client fact"},
        {**VALID_CONTEXT, "location": "client fact"},
        {**VALID_CONTEXT, "entity_title": "x" * 256},
        {**VALID_CONTEXT, "entity_title": "Bad\nTitle"},
        {**VALID_CONTEXT, "entity_date": "07/13/2026"},
        {**VALID_CONTEXT, "entity_date": "2026-02-30"},
    ]
    for raw in invalid:
        _assert_invalid(raw)


def test_validator_rejects_unsafe_ids() -> None:
    for event_id in (0, -1, 2_147_483_648, True, False, "0", "-1", "01", "1 OR 1=1"):
        _assert_invalid({**VALID_CONTEXT, "entity_id": event_id})


def test_validator_rejects_oversized_serialized_payload() -> None:
    _assert_invalid({**VALID_CONTEXT, "entity_title": "x" * 1100})


def test_exact_id_uses_authoritative_values_and_bound_sql() -> None:
    context = validate_event_ui_context({
        **VALID_CONTEXT,
        "entity_title": "FAKE CLIENT TITLE",
        "entity_date": "2026-07-14",
    })
    conn = FakeConnection(exact=AUTHORITATIVE_EVENT)
    resolution = resolve_event_context(conn, context)
    assert resolution.status == "exact_id"
    assert resolution.event == AUTHORITATIVE_EVENT
    assert resolution.diagnostic_mismatches == ("title", "date")
    assert resolution.resolved_entity()["title"] == AUTHORITATIVE_EVENT["event_name"]
    assert resolution.resolved_entity()["date"] == "2026-07-13"
    calls = conn.cursor_instance.calls
    assert len(calls) == 1
    sql, params = calls[0]
    assert "FROM `weekly_events`" in sql
    assert "WHERE `id` = %s LIMIT 1" in sql
    assert params == (417,)
    assert "FAKE CLIENT TITLE" not in sql
    assert "2026-07-14" not in sql
    assert conn.cursor_instance.closed


def test_similar_titles_resolve_by_id() -> None:
    similar = {**AUTHORITATIVE_EVENT, "id": 418, "event_name": "Crew and Cadet Parade Meeting"}
    context = validate_event_ui_context({**VALID_CONTEXT, "entity_id": "418"})
    resolution = resolve_event_context(FakeConnection(exact=similar), context)
    assert resolution.event["id"] == 418
    assert resolution.event["event_name"] == "Crew and Cadet Parade Meeting"


def test_unique_title_date_fallback_is_bound() -> None:
    context = validate_event_ui_context(VALID_CONTEXT)
    conn = FakeConnection(fallback=[AUTHORITATIVE_EVENT])
    resolution = resolve_event_context(conn, context)
    assert resolution.status == "fallback_title_date"
    assert resolution.event["id"] == 417
    assert len(conn.cursor_instance.calls) == 2
    sql, params = conn.cursor_instance.calls[1]
    assert "`normalized_name` = %s" in sql
    assert "`start_date` = %s" in sql
    assert "LIMIT 2" in sql
    assert params == ("crew & cadet street parade", "2026-07-13")


def test_fallback_ambiguous_stale_and_title_only() -> None:
    context = validate_event_ui_context(VALID_CONTEXT)
    duplicate = {**AUTHORITATIVE_EVENT, "id": 999}
    ambiguous = resolve_event_context(
        FakeConnection(fallback=[AUTHORITATIVE_EVENT, duplicate]),
        context,
    )
    assert ambiguous.status == "ambiguous"

    stale = resolve_event_context(FakeConnection(), context)
    assert stale.status == "stale"

    title_only = ValidatedEventContext(
        entity_id="417",
        entity_title="CREW & CADET STREET PARADE",
        entity_date=None,
    )
    conn = FakeConnection(fallback=[AUTHORITATIVE_EVENT])
    assert resolve_event_context(conn, title_only).status == "stale"
    assert len(conn.cursor_instance.calls) == 1


def test_deterministic_answer_and_authoritative_source() -> None:
    resolution = EventResolution(
        status="exact_id",
        context=validate_event_ui_context(VALID_CONTEXT),
        event=AUTHORITATIVE_EVENT,
    )
    response = build_selected_event_response("Tell me more about this event", resolution)
    assert response.mode == "sql"
    assert response.answer.count("CREW & CADET STREET PARADE") == 1
    assert "Monday, July 13" in response.answer
    assert "Town Field" in response.answer
    assert "A neighborhood parade for residents." in response.answer
    assert "https://official.example.org/parade" in response.answer
    assert len(response.sources) == 1
    assert response.sources[0]["entity_ref"] == "event:417"
    assert response.sources[0]["path"] == "https://official.example.org/parade"
    assert "client.invalid" not in json.dumps(response.sources)
    assert response.resolved_entity["source_ref"] == "event:417"


def test_partial_stale_and_ambiguous_answers_are_resident_safe() -> None:
    partial = {
        **AUTHORITATIVE_EVENT,
        "raw_text": "CREW & CADET STREET PARADE\nMonday, July 13",
    }
    exact = build_selected_event_response(
        "Tell me more",
        EventResolution(status="exact_id", context=None, event=partial),
    )
    assert "does not include a detailed description" in exact.answer
    assert "does not provide a separate location field" in exact.answer

    for status in ("stale", "ambiguous"):
        response = build_selected_event_response(
            "Tell me more",
            EventResolution(status=status, context=None),
        )
        assert response.resolved_entity == {"type": "event", "resolution": status}
        lowered = response.answer.lower()
        for internal in ("event_by_name", "weekly_events", "sql", status):
            assert internal not in lowered


class LiveGuestClient:
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
        return self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json=payload,
            timeout=45,
        )

    def send_stream(self, thread_id: str, payload: Dict[str, Any]):
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

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "demo_user"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "sentiment_demo"),
    )


def _scan_internal_terms(answer: str) -> List[str]:
    terms = (
        "event_by_name",
        "prepared_sql",
        "chroma",
        "qdrant",
        "sql execution",
        "retrieval failure",
        "tool call",
    )
    lowered = answer.lower()
    found = [term for term in terms if term in lowered]
    if re.search(r"\bRAG\b", answer):
        found.append("RAG")
    return found


def _live_record(
    name: str,
    *,
    status: int,
    answer: str,
    payload: Dict[str, Any],
    latency_ms: int,
    deterministic: bool,
) -> Dict[str, Any]:
    resolved = payload.get("resolved_entity") or {}
    record = {
        "case": name,
        "http_status": status,
        "answer": answer,
        "resolution": resolved.get("resolution"),
        "resolved_entity": resolved or None,
        "sources": payload.get("sources") or [],
        "mode": payload.get("mode") or payload.get("assistant_message", {}).get("response_mode"),
        "latency_ms": latency_ms,
        "llm_call_count": 0 if deterministic else "not asserted",
        "internal_terms": _scan_internal_terms(answer),
    }
    print(json.dumps(record, default=str, ensure_ascii=False))
    return record


def _create_authenticated_client(base_url: str, label: str):
    import requests

    session_client = requests.Session()
    session_client.get(f"{base_url}/auth/me", timeout=15).raise_for_status()
    csrf = session_client.cookies.get("otp_csrf")
    unique = uuid.uuid4().hex[:12]
    response = session_client.post(
        f"{base_url}/auth/signup",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        json={
            "email": f"ui-context-{label}-{unique}@example.test",
            "username": f"ctx_{label}_{unique}",
            "password": f"UiContext!{unique}9Z",
        },
        timeout=15,
    )
    response.raise_for_status()
    return session_client, session_client.cookies.get("otp_csrf")


def run_live_api_tests(base_url: str) -> None:
    """Exercise modern JSON/SSE and legacy contracts against local demo MySQL."""
    import requests

    base_url = base_url.rstrip("/")
    db = _live_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, event_name, start_date FROM weekly_events WHERE id = %s LIMIT 1",
            (417,),
        )
        event = cursor.fetchone()
    finally:
        cursor.close()
        db.close()
    assert event, "local demo DB must contain weekly_events.id=417"

    context = {
        **VALID_CONTEXT,
        "entity_title": "FAKE CLIENT TITLE",
        "entity_date": "2026-07-14",
    }
    message = "Tell me more about this event"
    client_a = LiveGuestClient(base_url)
    client_b = LiveGuestClient(base_url)
    client_a.bootstrap()
    client_b.bootstrap()
    events_response = client_a.session.get(
        f"{base_url}/events?days_ahead=365&limit=100",
        headers=client_a.headers(),
        timeout=15,
    )
    events_response.raise_for_status()
    sidebar_event = next(
        (item for item in events_response.json().get("events", []) if item.get("id") == 417),
        None,
    )
    assert sidebar_event is not None
    assert sidebar_event["event_name"] == event["event_name"]
    assert sidebar_event["start_date"] == str(event["start_date"])
    json_thread = client_a.create_thread("UI event JSON")
    stream_thread = client_a.create_thread("UI event stream")

    started = time_module.perf_counter()
    response = client_a.send_json(json_thread, {"message": message, "ui_context": context})
    latency = round((time_module.perf_counter() - started) * 1000)
    assert response.status_code == 201, response.text
    json_payload = response.json()
    json_answer = json_payload["assistant_message"]["content"]
    json_summary = {
        **json_payload,
        "answer": json_answer,
        "sources": json_payload["assistant_message"]["sources"],
        "mode": json_payload["assistant_message"]["response_mode"],
    }
    record = _live_record(
        "selected_event_nonstream",
        status=response.status_code,
        answer=json_answer,
        payload=json_summary,
        latency_ms=latency,
        deterministic=True,
    )
    assert event["event_name"] in json_answer
    assert "FAKE CLIENT TITLE" not in json_answer
    assert record["resolution"] == "exact_id"
    assert record["mode"] == "sql"
    assert record["internal_terms"] == []
    assert record["sources"][0]["entity_ref"] == "event:417"
    assert json_payload["assistant_message"]["model_name"] is None

    started = time_module.perf_counter()
    stream_response, stream_payload = client_a.send_stream(
        stream_thread,
        {"message": message, "ui_context": context},
    )
    latency = round((time_module.perf_counter() - started) * 1000)
    assert stream_response.status_code == 200
    assert stream_payload is not None
    stream_record = _live_record(
        "selected_event_stream",
        status=stream_response.status_code,
        answer=stream_payload["answer"],
        payload=stream_payload,
        latency_ms=latency,
        deterministic=True,
    )
    assert stream_payload["answer"] == json_answer
    assert stream_payload["resolved_entity"] == json_payload["resolved_entity"]
    assert stream_record["sources"] == record["sources"]
    assert stream_record["internal_terms"] == []

    stale_context = {
        **VALID_CONTEXT,
        "entity_id": "2147483647",
        "entity_title": "Definitely Missing Event 2147483647",
    }
    started = time_module.perf_counter()
    stale_response = client_a.send_json(
        json_thread,
        {"message": message, "ui_context": stale_context},
    )
    latency = round((time_module.perf_counter() - started) * 1000)
    assert stale_response.status_code == 201
    stale_payload = stale_response.json()
    stale_answer = stale_payload["assistant_message"]["content"]
    stale_record = _live_record(
        "stale_event",
        status=stale_response.status_code,
        answer=stale_answer,
        payload={
            **stale_payload,
            "answer": stale_answer,
            "sources": stale_payload["assistant_message"]["sources"],
            "mode": stale_payload["assistant_message"]["response_mode"],
        },
        latency_ms=latency,
        deterministic=True,
    )
    assert stale_record["resolution"] == "stale"
    assert stale_record["internal_terms"] == []

    invalid_response = client_a.send_json(
        json_thread,
        {"message": message, "ui_context": {**context, "url": "https://client.invalid"}},
    )
    assert invalid_response.status_code == 400
    assert invalid_response.json() == {
        "error": "Invalid UI context.",
        "code": "invalid_ui_context",
    }
    invalid_stream_response, invalid_stream_payload = client_a.send_stream(
        stream_thread,
        {"message": message, "ui_context": {**context, "description": "client fact"}},
    )
    assert invalid_stream_response.status_code == 400
    assert invalid_stream_payload is None
    assert invalid_stream_response.json()["code"] == "invalid_ui_context"

    started = time_module.perf_counter()
    typed_response = client_a.send_json(
        json_thread,
        {"message": "What events are happening this week?"},
    )
    typed_latency = round((time_module.perf_counter() - started) * 1000)
    assert typed_response.status_code == 201
    typed_payload = typed_response.json()
    typed_answer = typed_payload["assistant_message"]["content"]
    typed_record = _live_record(
        "typed_event_without_context",
        status=typed_response.status_code,
        answer=typed_answer,
        payload={
            **typed_payload,
            "answer": typed_answer,
            "sources": typed_payload["assistant_message"]["sources"],
            "mode": typed_payload["assistant_message"]["response_mode"],
        },
        latency_ms=typed_latency,
        deterministic=False,
    )
    assert typed_payload.get("resolved_entity") is None
    assert typed_payload["assistant_message"].get("resolved_entity") is None
    assert typed_record["internal_terms"] == []

    legacy_response = client_a.session.post(
        f"{base_url}/chat",
        headers=client_a.headers(),
        json={"message": message, "ui_context": context},
        timeout=45,
    )
    assert legacy_response.status_code == 200, legacy_response.text
    legacy_payload = legacy_response.json()
    legacy_record = _live_record(
        "selected_event_legacy",
        status=legacy_response.status_code,
        answer=legacy_payload["response"],
        payload={**legacy_payload, "answer": legacy_payload["response"]},
        latency_ms=0,
        deterministic=True,
    )
    assert legacy_payload["response"] == json_answer
    assert legacy_payload["resolved_entity"] == json_payload["resolved_entity"]
    assert legacy_record["internal_terms"] == []

    # Guest and authenticated ownership checks remain enforced by thread lookup.
    cross_guest = client_b.session.get(
        f"{base_url}/conversations/{json_thread}/messages",
        headers=client_b.headers(),
        timeout=15,
    )
    assert cross_guest.status_code == 404

    auth_a, csrf_a = _create_authenticated_client(base_url, "a")
    auth_b, csrf_b = _create_authenticated_client(base_url, "b")
    thread_response = auth_a.post(
        f"{base_url}/conversations",
        headers={"X-CSRF-Token": csrf_a, "Content-Type": "application/json"},
        json={"title": "Owned context"},
        timeout=15,
    )
    thread_response.raise_for_status()
    auth_thread = thread_response.json()["thread"]["id"]
    auth_send = auth_a.post(
        f"{base_url}/conversations/{auth_thread}/messages",
        headers={"X-CSRF-Token": csrf_a, "Content-Type": "application/json"},
        json={"message": message, "ui_context": context},
        timeout=45,
    )
    assert auth_send.status_code == 201, auth_send.text
    assert auth_send.json()["assistant_message"]["resolved_entity"]["id"] == "417"
    cross_auth = auth_b.get(
        f"{base_url}/conversations/{auth_thread}/messages",
        headers={"X-CSRF-Token": csrf_b},
        timeout=15,
    )
    assert cross_auth.status_code == 404

    # Verify bounded persistence and no ui_context contamination of thread state.
    db = _live_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        user_id = json_payload["user_message"]["id"]
        assistant_id = json_payload["assistant_message"]["id"]
        cursor.execute(
            "SELECT id, message_meta_json, sources_json, model_name "
            "FROM conversation_messages WHERE id IN (%s, %s)",
            (user_id, assistant_id),
        )
        rows = {row["id"]: row for row in cursor.fetchall()}
        user_meta = json.loads(rows[user_id]["message_meta_json"])
        assistant_meta = json.loads(rows[assistant_id]["message_meta_json"])
        sources = json.loads(rows[assistant_id]["sources_json"])
        assert user_meta == validate_event_ui_context(context).user_message_metadata()
        assert "entity_title" not in json.dumps(user_meta)
        assert "entity_date" not in json.dumps(user_meta)
        assert len(json.dumps(user_meta).encode("utf-8")) < 1024
        assert assistant_meta["resolved_entity"] == json_payload["resolved_entity"]
        assert sources[0]["entity_ref"] == "event:417"
        assert rows[assistant_id]["model_name"] is None
        cursor.execute(
            "SELECT thread_state_json FROM conversation_threads WHERE id = %s",
            (json_thread,),
        )
        thread_state = cursor.fetchone()["thread_state_json"] or "{}"
        assert "ui_context" not in thread_state
        assert "resolved_entity" not in thread_state
    finally:
        cursor.close()
        db.close()

    print("OK  live API parity, persistence, isolation, and no-context leakage")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="Also run live tests against the local API.")
    args = parser.parse_args()
    tests = [
        test_validator_accepts_only_bounded_contract,
        test_validator_rejects_bad_shapes_and_extra_data,
        test_validator_rejects_unsafe_ids,
        test_validator_rejects_oversized_serialized_payload,
        test_exact_id_uses_authoritative_values_and_bound_sql,
        test_similar_titles_resolve_by_id,
        test_unique_title_date_fallback_is_bound,
        test_fallback_ambiguous_stale_and_title_only,
        test_deterministic_answer_and_authoritative_source,
        test_partial_stale_and_ambiguous_answers_are_resident_safe,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"OK  {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if not failed and args.base_url:
        try:
            run_live_api_tests(args.base_url)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL live API tests: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
