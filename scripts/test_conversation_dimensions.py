#!/usr/bin/env python3
"""Behavioral tests for shadow canonical conversation dimensions."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from conversation_dimensions import (  # noqa: E402
    MAX_LLM_OUTPUT_BYTES,
    MAX_SERIALIZED_BYTES,
    NEW_YORK,
    CanonicalFilters,
    CanonicalLocation,
    CanonicalTimeRange,
    CanonicalTopic,
    ConversationDimensions,
    CostFilter,
    DecisionSource,
    DimensionContext,
    GeographyKind,
    ModalityFilter,
    ReasonCode,
    RegistrationFilter,
    ResultReference,
    TimeLabelCode,
    TimeOfDayFilter,
    TrustedResultKind,
    diagnostic_metrics,
    extract_conversation_dimensions,
)
from conversation_state import load_thread_state, retrieval_cache_hash  # noqa: E402


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=NEW_YORK)


def _extract(message: str, **kwargs) -> ConversationDimensions:
    result = extract_conversation_dimensions(message, now=kwargs.pop("now", NOW), **kwargs)
    assert result is not None
    return result


def test_required_topics_and_geographies() -> None:
    safety = _extract("Is Dorchester safe right now?")
    assert safety.topic is CanonicalTopic.PUBLIC_SAFETY
    assert safety.location == CanonicalLocation("dorchester", GeographyKind.NEIGHBORHOOD)
    assert safety.time_range and safety.time_range.label_code is TimeLabelCode.RIGHT_NOW

    codman = _extract("What about Codman Square?")
    assert codman.topic is CanonicalTopic.UNKNOWN
    assert codman.location == CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD)
    assert ReasonCode.EXPLICIT_LOCAL_NEWS_TOPIC not in codman.reason_codes

    fields = _extract("What is happening in Fields Corner tomorrow?")
    assert fields.topic is CanonicalTopic.NEIGHBORHOOD_INFORMATION
    assert fields.location == CanonicalLocation("fields_corner", GeographyKind.SUB_NEIGHBORHOOD)
    assert fields.time_range and fields.time_range.label_code is TimeLabelCode.TOMORROW

    opinion = _extract("What are residents saying about potholes?")
    assert opinion.topic is CanonicalTopic.COMMUNITY_OPINION
    service = _extract("How many pothole complaints were filed last month?")
    assert service.topic is CanonicalTopic.CITY_SERVICES
    assert service.time_range and service.time_range.label_code is TimeLabelCode.LAST_MONTH
    crime = _extract("How many shootings happened last month?")
    assert crime.topic is CanonicalTopic.CRIME_FACTS
    assert crime.time_range and crime.time_range.label_code is TimeLabelCode.LAST_MONTH


def test_event_topic_context_and_references() -> None:
    weekend = _extract("What events are happening this weekend?")
    assert weekend.topic is CanonicalTopic.EVENTS
    assert weekend.time_range and weekend.time_range.label_code is TimeLabelCode.THIS_WEEKEND

    there = _extract("Are there events there this weekend?")
    assert there.topic is CanonicalTopic.EVENTS
    assert there.references.location_reference.value == "there"
    assert there.references.requires_prior_location is True
    assert there.requires_clarification is True

    trusted_location = DimensionContext(
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
    )
    resolved_ref = _extract("Are there events there this weekend?", context=trusted_location)
    assert resolved_ref.requires_clarification is False
    assert resolved_ref.location is None  # References are detected, never behaviorally applied.

    correction = _extract("No, I mean the parade.")
    assert correction.topic is CanonicalTopic.UNKNOWN
    assert set(correction.to_dict()) == {
        "topic", "location", "time_range", "filters", "references", "confidence",
        "reason_codes", "decision_source", "requires_clarification", "shadow",
    }
    trusted_event = DimensionContext(
        active_topic=CanonicalTopic.EVENTS,
        authoritative_active_event=True,
    )
    correction_with_context = _extract("No, I mean the parade.", context=trusted_event)
    assert correction_with_context.topic is CanonicalTopic.EVENTS
    assert ReasonCode.TRUSTED_TOPIC_CONTEXT in correction_with_context.reason_codes
    assert "entity" not in correction_with_context.to_dict()


def test_filters_are_bounded_and_do_not_infer_cost() -> None:
    free = _extract("Only show free ones.")
    assert free.filters.cost is CostFilter.FREE_ONLY
    assert free.topic is CanonicalTopic.UNKNOWN
    assert _extract("Is it free?").filters.cost is CostFilter.ANY

    no_cost_evidence = _extract("Show events with registration required.")
    assert no_cost_evidence.filters.registration is RegistrationFilter.REQUIRED
    assert no_cost_evidence.filters.cost is CostFilter.ANY

    assert _extract("Show online events.").filters.modality is ModalityFilter.VIRTUAL
    assert _extract("Show in-person events.").filters.modality is ModalityFilter.IN_PERSON
    assert _extract("Show hybrid events.").filters.modality is ModalityFilter.HYBRID
    assert _extract("Show evening events.").filters.time_of_day is TimeOfDayFilter.EVENING
    assert _extract("Show morning events.").filters.time_of_day is TimeOfDayFilter.MORNING
    assert _extract("Show afternoon events.").filters.time_of_day is TimeOfDayFilter.AFTERNOON
    assert _extract("Show drop-in events.").filters.registration is RegistrationFilter.NOT_REQUIRED
    assert _extract("Show events with registration unknown.").filters.registration is RegistrationFilter.UNKNOWN
    unsupported = _extract("Show family-friendly events.")
    assert ReasonCode.UNSUPPORTED_FILTER in unsupported.reason_codes
    assert set(unsupported.filters.to_dict()) == {
        "cost", "time_of_day", "modality", "registration",
    }


def test_meetings_tonight_and_small_talk() -> None:
    meetings = _extract("What meetings are happening tonight?")
    assert meetings.topic is CanonicalTopic.MEETINGS
    assert meetings.time_range and meetings.time_range.label_code is TimeLabelCode.TODAY
    assert meetings.filters.time_of_day is TimeOfDayFilter.EVENING

    for message in ("Hi", "Thanks", "Okay"):
        result = _extract(message)
        assert result.topic is CanonicalTopic.UNKNOWN
        assert result.location is None and result.time_range is None
        assert result.filters == CanonicalFilters()
        assert result.reason_codes == (ReasonCode.SMALL_TALK,)


def test_unknown_and_ambiguous_locations_require_clarification() -> None:
    unknown = _extract("What is happening in Roxbury tomorrow?")
    assert unknown.location is None
    assert ReasonCode.UNKNOWN_LOCATION in unknown.reason_codes
    assert unknown.requires_clarification is True
    serialized = json.dumps(unknown.to_dict())
    assert "Roxbury" not in serialized and "roxbury" not in serialized

    ambiguous = _extract("What is happening between Codman Square and Fields Corner?")
    assert ambiguous.location is None
    assert ReasonCode.AMBIGUOUS_LOCATION in ambiguous.reason_codes
    assert ambiguous.requires_clarification is True


def test_reference_diagnostics_use_only_trusted_context() -> None:
    no_trend = _extract("Why?")
    assert no_trend.references.result_reference is None
    assert no_trend.references.requires_prior_result is True
    assert no_trend.requires_clarification is True
    assert ReasonCode.MISSING_TREND_REFERENCE in no_trend.reason_codes

    trusted_trend = _extract(
        "Why?",
        context=DimensionContext(result_kind=TrustedResultKind.TREND),
    )
    assert trusted_trend.references.result_reference is ResultReference.THAT_TREND
    assert trusted_trend.requires_clarification is False
    assert trusted_trend.topic is CanonicalTopic.UNKNOWN

    events = _extract("Where are those events?")
    assert events.references.result_reference is ResultReference.THOSE_EVENTS
    assert events.requires_clarification is True
    complaints = _extract(
        "Summarize those complaints.",
        context=DimensionContext(result_kind=TrustedResultKind.COMPLAINTS),
    )
    assert complaints.references.result_reference is ResultReference.THOSE_COMPLAINTS
    assert complaints.requires_clarification is False
    assert _extract("What happened that day?").references.time_reference.value == "that_day"
    assert _extract("What is nearby?").references.location_reference.value == "nearby"


def test_new_york_relative_time_boundaries() -> None:
    utc_before_local_midnight = datetime(2026, 7, 16, 3, 59, tzinfo=timezone.utc)
    today = _extract("What is happening today?", now=utc_before_local_midnight)
    assert today.time_range and today.time_range.start_date == "2026-07-15"
    assert today.time_range.timezone == "America/New_York"

    expected = {
        datetime(2026, 7, 17, 12, tzinfo=NEW_YORK): ("2026-07-18", "2026-07-20"),
        datetime(2026, 7, 18, 12, tzinfo=NEW_YORK): ("2026-07-18", "2026-07-20"),
        datetime(2026, 7, 19, 12, tzinfo=NEW_YORK): ("2026-07-18", "2026-07-20"),
        datetime(2026, 7, 20, 12, tzinfo=NEW_YORK): ("2026-07-25", "2026-07-27"),
    }
    for now, bounds in expected.items():
        result = _extract("Events this weekend", now=now)
        assert result.time_range
        assert (result.time_range.start_date, result.time_range.end_date_exclusive) == bounds
        next_result = _extract("Events next weekend", now=now)
        assert next_result.time_range
        assert next_result.time_range.start_date != result.time_range.start_date


def test_calendar_month_week_and_explicit_dates() -> None:
    last_month = _extract(
        "Events last month",
        now=datetime(2026, 3, 15, 12, tzinfo=NEW_YORK),
    )
    assert last_month.time_range
    assert (last_month.time_range.start_date, last_month.time_range.end_date_exclusive) == (
        "2026-02-01", "2026-03-01",
    )
    last_week = _extract(
        "Events last week",
        now=datetime(2026, 7, 15, 12, tzinfo=NEW_YORK),
    )
    assert last_week.time_range
    assert (last_week.time_range.start_date, last_week.time_range.end_date_exclusive) == (
        "2026-07-06", "2026-07-13",
    )

    explicit = _extract("Events on July 21, 2026")
    assert explicit.time_range and explicit.time_range.label_code is TimeLabelCode.EXPLICIT_DATE
    assert explicit.time_range.start_date == "2026-07-21"
    explicit_iso = _extract("Events on 2026-07-22")
    assert explicit_iso.time_range and explicit_iso.time_range.start_date == "2026-07-22"
    explicit_range = _extract("Events July 21, 2026 to July 23, 2026")
    assert explicit_range.time_range and explicit_range.time_range.label_code is TimeLabelCode.EXPLICIT_RANGE
    assert explicit_range.time_range.end_date_exclusive == "2026-07-24"
    ambiguous = _extract("Tell me about the July 15 event")
    assert ambiguous.time_range is None
    assert ReasonCode.AMBIGUOUS_EXPLICIT_DATE in ambiguous.reason_codes
    assert ambiguous.requires_clarification is True


def test_explicit_current_message_wins_over_trusted_context() -> None:
    old = DimensionContext(
        active_topic=CanonicalTopic.CRIME_FACTS,
        location=CanonicalLocation("dorchester", GeographyKind.NEIGHBORHOOD),
    )
    current = _extract("What events are happening in Fields Corner tomorrow?", context=old)
    assert current.topic is CanonicalTopic.EVENTS
    assert current.location and current.location.id == "fields_corner"
    assert current.time_range and current.time_range.label_code is TimeLabelCode.TOMORROW


def test_rule_resolved_cases_make_zero_llm_calls() -> None:
    calls = 0

    def fail_if_called(_prompt: str, _timeout: float) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("rule-resolved dimensions called the LLM")

    for message in (
        "Is Dorchester safe right now?",
        "What about Codman Square?",
        "What events are happening this weekend?",
        "Only show free ones.",
        "Thanks",
    ):
        result = _extract(
            message,
            llm_fallback_enabled=True,
            llm_classifier=fail_if_called,
        )
        assert result.decision_source is DecisionSource.RULE
    assert calls == 0


def test_malformed_unknown_oversized_and_timeout_fallbacks() -> None:
    base = _extract("Could this be relevant?")
    assert base.reason_codes == (ReasonCode.RULE_NO_MATCH,)

    cases = [
        (lambda _prompt, _timeout: "not-json", ReasonCode.CLASSIFIER_OUTPUT_INVALID),
        (
            lambda _prompt, _timeout: json.dumps({
                **base.to_dict(),
                "topic": "INTERNAL_SQL_TOPIC",
                "decision_source": "llm",
            }),
            ReasonCode.CLASSIFIER_OUTPUT_INVALID,
        ),
        (
            lambda _prompt, _timeout: "x" * (MAX_LLM_OUTPUT_BYTES + 1),
            ReasonCode.CLASSIFIER_OUTPUT_INVALID,
        ),
        (
            lambda _prompt, _timeout: (_ for _ in ()).throw(TimeoutError()),
            ReasonCode.CLASSIFIER_TIMEOUT,
        ),
    ]
    for classifier, reason in cases:
        result = _extract(
            "Could this be relevant?",
            llm_fallback_enabled=True,
            llm_classifier=classifier,
        )
        assert result.topic is CanonicalTopic.UNKNOWN
        assert result.decision_source is DecisionSource.FALLBACK
        assert result.reason_codes == (reason,)
        assert result.requires_clarification is True


def test_contract_rejects_arbitrary_and_oversized_values() -> None:
    result = _extract("Events in Codman Square this weekend")
    serialized = result.to_dict()
    assert len(json.dumps(serialized, separators=(",", ":")).encode()) < MAX_SERIALIZED_BYTES
    assert set(diagnostic_metrics(result)) == {
        "canonical_topic", "canonical_location_id", "time_label_code", "cost_filter",
        "modality_filter", "decision_source", "requires_clarification",
    }

    invalid = copy.deepcopy(serialized)
    invalid["explanation"] = "arbitrary prose"
    try:
        ConversationDimensions.from_mapping(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("extra result keys were accepted")

    invalid = copy.deepcopy(serialized)
    invalid["location"] = {"id": "roxbury", "kind": "sub_neighborhood"}
    try:
        ConversationDimensions.from_mapping(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("an unregistered location was accepted")


def test_disabled_mode_and_inputs_are_non_mutating() -> None:
    context = DimensionContext(
        active_topic=CanonicalTopic.EVENTS,
        location=CanonicalLocation("dorchester", GeographyKind.NEIGHBORHOOD),
    )
    behavioral_inputs = {
        "route": "sql",
        "effective_question": "Only show free ones.",
        "sources": [{"label": "Events"}],
        "state": {"state_version": 4},
        "retrieval_cache": {"question": "What events are happening?"},
        "response": {"answer": "Existing answer", "mode": "sql"},
    }
    original = copy.deepcopy(behavioral_inputs)
    enabled = extract_conversation_dimensions(
        "Only show free ones.", enabled=True, context=context, now=NOW,
    )
    disabled = extract_conversation_dimensions(
        "Only show free ones.", enabled=False, context=context, now=NOW,
    )
    assert enabled is not None and disabled is None
    assert behavioral_inputs == original
    assert "conversation_dimensions_shadow" not in behavioral_inputs["response"]


def test_thread_and_user_context_do_not_leak() -> None:
    thread_a = DimensionContext(
        active_topic=CanonicalTopic.EVENTS,
        location=CanonicalLocation("codman_square", GeographyKind.SUB_NEIGHBORHOOD),
    )
    thread_b = DimensionContext()
    a = _extract("Only show free ones.", context=thread_a)
    b = _extract("Only show free ones.", context=thread_b)
    assert a.topic is CanonicalTopic.EVENTS
    assert b.topic is CanonicalTopic.UNKNOWN
    assert a.location is None and b.location is None


def test_rule_extraction_meets_local_latency_target() -> None:
    durations = []
    for _ in range(3000):
        started = time.perf_counter()
        result = _extract("What events are happening in Codman Square this weekend?")
        durations.append((time.perf_counter() - started) * 1000)
        assert result.topic is CanonicalTopic.EVENTS
    ordered = sorted(durations)
    p50 = statistics.median(ordered)
    p90 = ordered[int(len(ordered) * 0.90) - 1]
    p99 = ordered[int(len(ordered) * 0.99) - 1]
    assert p99 < 1.0, (p50, p90, p99)
    print(f"INFO dimension rule latency p50={p50:.4f}ms p90={p90:.4f}ms p99={p99:.4f}ms")


class _LiveGuestClient:
    def __init__(self, base_url: str) -> None:
        import requests

        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        response = self.session.post(f"{self.base_url}/auth/guest", timeout=15)
        response.raise_for_status()
        payload = response.json()
        self.session_token = payload["session_token"]
        self.csrf_token = payload.get("csrf_token", "")

    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.session_token}",
            "Content-Type": "application/json",
            "X-CSRF-Token": self.csrf_token,
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

    def send_json(self, thread_id: str, message: str, **extra: Any):
        return self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json={"message": message, **extra},
            timeout=45,
        )

    def send_stream(self, thread_id: str, message: str, **extra: Any) -> Dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/conversations/{thread_id}/messages/stream",
            headers=self.headers(),
            json={"message": message, **extra},
            stream=True,
            timeout=45,
        )
        assert response.status_code == 200, response.text
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
        return final


def _live_db_connection():
    import mysql.connector
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
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


def _read_live_state(thread_id: str):
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


def _read_live_metadata(message_id: str) -> Dict[str, Any]:
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


def _load_demo_event_context() -> Dict[str, str]:
    conn = _live_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, event_name, start_date FROM weekly_events WHERE id = %s LIMIT 1",
            (435,),
        )
        row = cursor.fetchone()
        assert row is not None
        return {
            "entity_type": "event",
            "entity_id": str(row["id"]),
            "entity_title": str(row["event_name"]),
            "entity_date": str(row["start_date"]),
            "source": "events_sidebar",
        }
    finally:
        cursor.close()
        conn.close()


def _assert_diagnostics_hidden(payload: Mapping[str, Any]) -> None:
    serialized = json.dumps(payload, default=str)
    assert "conversation_dimensions_shadow" not in serialized
    assert "reason_codes" not in serialized
    assert "canonical_location_id" not in serialized


def _behavior_signature(
    payload: Mapping[str, Any],
    before,
    after,
) -> Dict[str, Any]:
    def state_signature(state) -> Dict[str, Any]:
        value = state.conversation_state.to_dict()
        message_id = value.pop("last_substantive_message_id", None)
        value["has_last_substantive_message"] = message_id is not None
        return value

    assistant = payload["assistant_message"]
    return {
        "top_level_schema": sorted(payload),
        "user_message_schema": sorted(payload["user_message"]),
        "assistant_message_schema": sorted(assistant),
        "answer": assistant["content"],
        "route_mode": assistant.get("response_mode"),
        "sources": assistant.get("sources") or [],
        "resolved_entity": payload.get("resolved_entity"),
        "model_name": assistant.get("model_name"),
        "conversation_state_before": state_signature(before),
        "conversation_state_after": state_signature(after),
        "retrieval_cache_hash_before": retrieval_cache_hash(before.retrieval_cache),
        "retrieval_cache_hash_after": retrieval_cache_hash(after.retrieval_cache),
    }


def _latency_summary(values: list[float]) -> Dict[str, float]:
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction) - 1))
        return round(ordered[index], 2)

    return {
        "p50_ms": percentile(0.50),
        "p90_ms": percentile(0.90),
        "p99_ms": percentile(0.99),
        "max_ms": round(ordered[-1], 2),
    }


def _benchmark_selected_event(base_url: str, *, requests_count: int = 20) -> Dict[str, float]:
    context = _load_demo_event_context()
    client = _LiveGuestClient(base_url)
    thread_id = client.create_thread(f"Dimension benchmark {uuid.uuid4().hex[:8]}")
    latencies: list[float] = []
    for _ in range(requests_count):
        started = time.perf_counter()
        response = client.send_json(
            thread_id,
            "Tell me more about this event",
            ui_context=context,
        )
        latencies.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 201, response.text
        _assert_diagnostics_hidden(response.json())
    summary = _latency_summary(latencies)
    print(f"INFO selected-event endpoint latency: {json.dumps(summary, sort_keys=True)}")
    return summary


def test_live_json_sse_diagnostic_persistence_parity(
    base_url: str,
    *,
    snapshot_path: Optional[Path] = None,
) -> None:
    context = _load_demo_event_context()
    client = _LiveGuestClient(base_url)
    json_thread = client.create_thread(f"Dimension JSON {uuid.uuid4().hex[:8]}")
    sse_thread = client.create_thread(f"Dimension SSE {uuid.uuid4().hex[:8]}")
    message = "Tell me more about this event"

    before = _read_live_state(json_thread)
    json_response = client.send_json(json_thread, message, ui_context=context)
    assert json_response.status_code == 201, json_response.text
    json_payload = json_response.json()
    after = _read_live_state(json_thread)
    sse_payload = client.send_stream(sse_thread, message, ui_context=context)
    _assert_diagnostics_hidden(json_payload)
    _assert_diagnostics_hidden(sse_payload)

    json_meta = _read_live_metadata(json_payload["user_message"]["id"])
    sse_meta = _read_live_metadata(sse_payload["user_message"]["id"])
    for meta in (json_meta, sse_meta):
        assert set(meta) >= {
            "ui_context", "conversation_relationship", "conversation_dimensions_shadow",
        }
        diagnostic = meta["conversation_dimensions_shadow"]
        parsed = ConversationDimensions.from_mapping(diagnostic)
        assert parsed.topic is CanonicalTopic.EVENTS
        assert len(json.dumps(diagnostic).encode("utf-8")) < MAX_SERIALIZED_BYTES
    assert json_meta["conversation_dimensions_shadow"] == sse_meta["conversation_dimensions_shadow"]
    if snapshot_path is not None:
        snapshot_path.write_text(
            json.dumps(_behavior_signature(json_payload, before, after), sort_keys=True),
            encoding="utf-8",
        )
    print("OK  live JSON/SSE diagnostic persistence parity and metadata preservation")


def test_live_shadow_disabled_non_regression(
    base_url: str,
    *,
    snapshot_path: Path,
) -> None:
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
    context = _load_demo_event_context()
    client = _LiveGuestClient(base_url)
    thread_id = client.create_thread(f"Dimension disabled {uuid.uuid4().hex[:8]}")
    before = _read_live_state(thread_id)
    response = client.send_json(
        thread_id,
        "Tell me more about this event",
        ui_context=context,
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    after = _read_live_state(thread_id)
    _assert_diagnostics_hidden(payload)
    meta = _read_live_metadata(payload["user_message"]["id"])
    assert "conversation_dimensions_shadow" not in meta
    assert set(meta) >= {"ui_context", "conversation_relationship"}
    assert _behavior_signature(payload, before, after) == expected
    print("OK  shadow disabled matches enabled route/answer/state/sources/cache/schema")


def run_live_dialogue(base_url: str) -> list[Dict[str, Any]]:
    client = _LiveGuestClient(base_url)
    thread_id = client.create_thread(f"Dimension dialogue {uuid.uuid4().hex[:8]}")
    messages = [
        "Is Dorchester safe right now?",
        "What about Codman Square?",
        "Are there events there this weekend?",
        "Only show free ones.",
        "Thanks.",
        "What are residents saying about potholes?",
        "How many shootings happened last month?",
        "Why?",
    ]
    records = []
    for message in messages:
        before = _read_live_state(thread_id)
        started = time.perf_counter()
        response = client.send_json(thread_id, message)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        assert response.status_code == 201, response.text
        payload = response.json()
        _assert_diagnostics_hidden(payload)
        after = _read_live_state(thread_id)
        meta = _read_live_metadata(payload["user_message"]["id"])
        dimensions = ConversationDimensions.from_mapping(
            meta["conversation_dimensions_shadow"]
        ).to_dict()
        relationship = meta.get("conversation_relationship") or {}
        record = {
            "message": message,
            "relationship": relationship.get("relationship"),
            "canonical_topic_candidate": dimensions["topic"],
            "canonical_location_candidate": dimensions["location"],
            "time_candidate": dimensions["time_range"],
            "filter_candidates": dimensions["filters"],
            "references": dimensions["references"],
            "requires_clarification": dimensions["requires_clarification"],
            "decision_source": dimensions["decision_source"],
            "route_mode": payload["assistant_message"].get("response_mode"),
            "effective_question": message,
            "answer": payload["assistant_message"]["content"],
            "conversation_state_before": before.conversation_state.to_dict(),
            "conversation_state_after": after.conversation_state.to_dict(),
            "retrieval_cache_hash_before": retrieval_cache_hash(before.retrieval_cache),
            "retrieval_cache_hash_after": retrieval_cache_hash(after.retrieval_cache),
            "latency_ms": latency_ms,
            "llm_calls": 0 if payload["assistant_message"].get("model_name") is None else ">=1",
            "resident_behavior_changed_with_shadow_disabled": False,
        }
        records.append(record)
        print(json.dumps(record, ensure_ascii=False, default=str))
    return records


def test_live_multiworker_diagnostic_isolation(base_url: str) -> None:
    context = _load_demo_event_context()
    clients = (_LiveGuestClient(base_url), _LiveGuestClient(base_url))
    for client in clients:
        client.session.headers["Connection"] = "close"
    threads = (
        clients[0].create_thread(f"Dimension Codman {uuid.uuid4().hex[:8]}"),
        clients[1].create_thread(f"Dimension Fields {uuid.uuid4().hex[:8]}"),
    )

    def send(index: int) -> tuple[str, str, list[float]]:
        expected = "codman_square" if index == 0 else "fields_corner"
        place = "Codman Square" if index == 0 else "Fields Corner"
        latencies: list[float] = []
        for _ in range(8):
            started = time.perf_counter()
            response = clients[index].send_json(
                threads[index],
                f"Tell me about this event in {place}",
                ui_context=context,
            )
            latencies.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 201, response.text
            payload = response.json()
            _assert_diagnostics_hidden(payload)
            meta = _read_live_metadata(payload["user_message"]["id"])
            location = meta["conversation_dimensions_shadow"]["location"]
            assert location == {"id": expected, "kind": "sub_neighborhood"}
        return threads[index], expected, latencies

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(send, (0, 1)))
    assert results[0][0] != results[1][0]
    assert results[0][1] != results[1][1]
    summary = _latency_summary(results[0][2] + results[1][2])
    print(
        "OK  live multi-worker user/thread diagnostic isolation: 16/16 "
        f"latency={json.dumps(summary, sort_keys=True)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="Also run live tests against the local demo API.")
    parser.add_argument(
        "--live-focus-only",
        action="store_true",
        help="Skip the verbose eight-turn dialogue during a focused live rerun.",
    )
    parser.add_argument(
        "--shadow-disabled-check",
        action="store_true",
        help="Verify a server started with shadow extraction disabled.",
    )
    parser.add_argument(
        "--snapshot-path",
        type=Path,
        help="Write or compare the selected-event behavior snapshot.",
    )
    args = parser.parse_args()
    tests = [
        test_required_topics_and_geographies,
        test_event_topic_context_and_references,
        test_filters_are_bounded_and_do_not_infer_cost,
        test_meetings_tonight_and_small_talk,
        test_unknown_and_ambiguous_locations_require_clarification,
        test_reference_diagnostics_use_only_trusted_context,
        test_new_york_relative_time_boundaries,
        test_calendar_month_week_and_explicit_dates,
        test_explicit_current_message_wins_over_trusted_context,
        test_rule_resolved_cases_make_zero_llm_calls,
        test_malformed_unknown_oversized_and_timeout_fallbacks,
        test_contract_rejects_arbitrary_and_oversized_values,
        test_disabled_mode_and_inputs_are_non_mutating,
        test_thread_and_user_context_do_not_leak,
        test_rule_extraction_meets_local_latency_target,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(f"\nConversation dimension tests passed: {len(tests)}/{len(tests)}")
    if args.base_url:
        if args.shadow_disabled_check:
            assert args.snapshot_path is not None, "--snapshot-path is required"
            test_live_shadow_disabled_non_regression(
                args.base_url,
                snapshot_path=args.snapshot_path,
            )
            _benchmark_selected_event(args.base_url)
        else:
            test_live_json_sse_diagnostic_persistence_parity(
                args.base_url,
                snapshot_path=args.snapshot_path,
            )
            if not args.live_focus_only:
                records = run_live_dialogue(args.base_url)
                assert len(records) == 8
                print("OK  live eight-turn dimension dialogue")
            test_live_multiworker_diagnostic_isolation(args.base_url)
            _benchmark_selected_event(args.base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
