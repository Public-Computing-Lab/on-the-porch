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
    assert "does not provide" in exact.answer
    assert "a detailed description" in exact.answer
    assert "a separate location" in exact.answer

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


def _ny_today():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/New_York")).date()


def _seed_live_event_fixture(db, *, days_ahead: int = 7, today=None):
    """Insert a disposable future weekly_events row relative to NY 'today'."""
    from datetime import timedelta

    anchor = today or _ny_today()
    start = anchor + timedelta(days=days_ahead)
    marker = uuid.uuid4().hex[:12]
    event_name = f"OTP UI LIVE FIXTURE {marker}"
    event_date = f"{start.strftime('%A')}, {start.strftime('%B')} {start.day}"
    cursor = db.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO weekly_events (
              source_pdf, page_number, event_name, event_date,
              start_date, end_date, start_time, end_time, raw_text,
              category, normalized_name
            ) VALUES (
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s
            )
            """,
            (
                "otp-ui-live-fixture.pdf",
                1,
                event_name,
                event_date,
                start,
                start,
                "10:00:00",
                "11:00:00",
                (
                    "Location: Codman Square Library, Dorchester, MA\n"
                    f"Test fixture event for UI live tests ({event_name})"
                ),
                "community",
                event_name.lower(),
            ),
        )
        event_id = int(cursor.lastrowid)
        # /events may read events_materialized when MVs are enabled — mirror row.
        try:
            cursor.execute(
                """
                INSERT INTO events_materialized (
                  id, event_name, event_date, start_date, end_date,
                  start_time, end_time, raw_text, source_pdf, event_url
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                  event_name=VALUES(event_name),
                  event_date=VALUES(event_date),
                  start_date=VALUES(start_date),
                  end_date=VALUES(end_date),
                  start_time=VALUES(start_time),
                  end_time=VALUES(end_time),
                  raw_text=VALUES(raw_text),
                  source_pdf=VALUES(source_pdf)
                """,
                (
                    event_id,
                    event_name,
                    event_date,
                    start,
                    start,
                    "10:00:00",
                    "11:00:00",
                    (
                        "Location: Codman Square Library, Dorchester, MA\n"
                        f"Test fixture event for UI live tests ({event_name})"
                    ),
                    "otp-ui-live-fixture.pdf",
                    None,
                ),
            )
        except Exception:
            # MV table may be absent in some local demos; weekly_events is authoritative
            # for selected-event resolution.
            pass
        db.commit()
    finally:
        cursor.close()
    return {
        "id": event_id,
        "event_name": event_name,
        "start_date": start,
        "event_date": event_date,
    }


def _cleanup_live_event_fixture(db, event_id: int, event_name: str) -> None:
    """Delete only the disposable fixture row; never touch real demo records."""
    if not event_name.startswith("OTP UI LIVE FIXTURE "):
        return
    cursor = db.cursor()
    try:
        try:
            cursor.execute(
                "DELETE FROM events_materialized WHERE id = %s AND event_name = %s",
                (event_id, event_name),
            )
        except Exception:
            pass
        cursor.execute(
            "DELETE FROM weekly_events WHERE id = %s AND event_name = %s",
            (event_id, event_name),
        )
        db.commit()
    finally:
        cursor.close()


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
    # Local HTTP test servers set Secure cookies; requests will not auto-send them
    # on http://. Bootstrap CSRF via guest Bearer, then signup, then continue with
    # Bearer auth using the new session token from Set-Cookie.
    guest = session_client.post(f"{base_url}/auth/guest", timeout=15)
    guest.raise_for_status()
    guest_payload = guest.json()
    unique = uuid.uuid4().hex[:12]
    response = session_client.post(
        f"{base_url}/auth/signup",
        headers={
            "Authorization": f"Bearer {guest_payload['session_token']}",
            "X-CSRF-Token": guest_payload["csrf_token"],
            "Content-Type": "application/json",
        },
        json={
            "email": f"ui-context-{label}-{unique}@example.test",
            "username": f"ctx_{label}_{unique}",
            "password": f"UiContext!{unique}9Z",
        },
        timeout=15,
    )
    response.raise_for_status()
    session_token = session_client.cookies.get("otp_session")
    csrf = session_client.cookies.get("otp_csrf") or guest_payload["csrf_token"]
    if not session_token:
        # Parse Set-Cookie if the jar dropped Secure cookies on http.
        raw = response.headers.get("Set-Cookie") or ""
        match = re.search(r"otp_session=([^;]+)", raw)
        assert match, "signup must return otp_session"
        session_token = match.group(1)
        csrf_match = re.search(r"otp_csrf=([^;]+)", raw)
        if csrf_match:
            csrf = csrf_match.group(1)

    class _AuthClient:
        def __init__(self):
            self.session = session_client
            self._headers = {
                "Authorization": f"Bearer {session_token}",
                "X-CSRF-Token": csrf,
                "Content-Type": "application/json",
            }

        def post(self, url, headers=None, **kwargs):
            merged = dict(self._headers)
            if headers:
                merged.update(headers)
            return self.session.post(url, headers=merged, **kwargs)

        def get(self, url, headers=None, **kwargs):
            merged = dict(self._headers)
            if headers:
                merged.update(headers)
            return self.session.get(url, headers=merged, **kwargs)

    return _AuthClient(), csrf


def run_live_api_tests(base_url: str, *, simulated_today=None) -> None:
    """Exercise modern JSON/SSE against local demo MySQL with a date-stable fixture.

    Seeds a disposable future weekly_events row relative to America/New_York today
    (or simulated_today) so the suite does not depend on a hard-coded real event.
    """
    import requests

    base_url = base_url.rstrip("/")
    db = _live_db_connection()
    fixture = _seed_live_event_fixture(db, days_ahead=7, today=simulated_today)
    event = {
        "id": fixture["id"],
        "event_name": fixture["event_name"],
        "start_date": fixture["start_date"],
    }
    event_id = int(event["id"])
    context = {
        "entity_type": "event",
        "entity_id": str(event_id),
        "entity_title": "FAKE CLIENT TITLE",
        "entity_date": "2026-07-14",
        "source": "events_sidebar",
    }
    message = "Tell me more about this event"
    cleanup_db = db
    cleanup_fixture = fixture
    try:
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
            (item for item in events_response.json().get("events", []) if item.get("id") == event_id),
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
        assert record["sources"][0]["entity_ref"] == f"event:{event_id}"
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
        assert stream_payload["assistant_message"]["model_name"] is None
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
        typed_message = "Tell me more about the 'Virtual Baby Cafe' event happening on Jul 15."
        typed_response = client_a.send_json(
            json_thread,
            {"message": typed_message},
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
            deterministic=True,
        )
        assert typed_payload.get("resolved_entity") is None
        assert typed_payload["assistant_message"].get("resolved_entity") is None
        assert typed_payload["assistant_message"]["model_name"] is None
        assert typed_record["internal_terms"] == []

        typed_stream_thread = client_a.create_thread("Typed event stream")
        typed_stream_response, typed_stream_payload = client_a.send_stream(
            typed_stream_thread,
            {"message": typed_message},
        )
        assert typed_stream_response.status_code == 200
        assert typed_stream_payload is not None
        assert typed_stream_payload["answer"] == typed_answer
        assert typed_stream_payload["assistant_message"]["model_name"] is None
        assert (
            typed_stream_payload["assistant_message"]["model_name"]
            == typed_payload["assistant_message"]["model_name"]
        )

        history_response = client_a.session.get(
            f"{base_url}/conversations/{json_thread}/messages",
            headers=client_a.headers(),
            timeout=15,
        )
        history_response.raise_for_status()
        persisted_assistant = {
            item["id"]: item
            for item in history_response.json()["messages"]
            if item["role"] == "assistant"
        }
        assert persisted_assistant[json_payload["assistant_message"]["id"]]["model_name"] is None
        assert persisted_assistant[typed_payload["assistant_message"]["id"]]["model_name"] is None

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
        assert auth_send.json()["assistant_message"]["resolved_entity"]["id"] == str(event_id)
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
            expected_ui_meta = validate_event_ui_context(context).user_message_metadata()
            assert user_meta["ui_context"] == expected_ui_meta["ui_context"]
            relationship = user_meta["conversation_relationship"]
            assert relationship["relationship"] == "STANDALONE"
            assert relationship["decision_source"] == "rule"
            assert relationship["shadow"] is True
            assert set(relationship) == {
                "relationship",
                "confidence",
                "reason_code",
                "retained",
                "cleared",
                "requires_clarification",
                "decision_source",
                "shadow",
            }
            assert "entity_title" not in json.dumps(user_meta)
            assert "entity_date" not in json.dumps(user_meta)
            assert len(json.dumps(user_meta).encode("utf-8")) < 2048
            assert assistant_meta["resolved_entity"] == json_payload["resolved_entity"]
            assert sources[0]["entity_ref"] == f"event:{event_id}"
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

        print(
            f"OK  live API parity with fixture event:{event_id} "
            f"start_date={event['start_date']} "
            f"(anchor={simulated_today or _ny_today()})"
        )


    finally:
        try:
            _cleanup_live_event_fixture(
                cleanup_db, cleanup_fixture["id"], cleanup_fixture["event_name"]
            )
        except Exception as cleanup_exc:  # noqa: BLE001
            print(f"WARN fixture cleanup failed: {cleanup_exc}", file=sys.stderr)
        try:
            cleanup_db.close()
        except Exception:
            pass


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
        from datetime import timedelta

        anchors = [_ny_today(), _ny_today() - timedelta(days=1)]
        for anchor in anchors:
            try:
                print(f"\n--- live API tests (NY anchor={anchor}) ---")
                run_live_api_tests(args.base_url, simulated_today=anchor)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL live API tests (anchor={anchor}): {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
