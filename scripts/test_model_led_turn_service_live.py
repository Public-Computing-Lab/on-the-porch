#!/usr/bin/env python3
"""Isolated two-worker live acceptance for the Phase 2 model-led service."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for candidate in (str(ROOT), str(PORCH)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from conversation_state import load_thread_state  # noqa: E402


CANARY_HEADER = "model-led-phase2a"
RESULTS = []
AUTHORITATIVE = False


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def demo_connection():
    import mysql.connector

    host = os.getenv("MYSQL_HOST", "")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "")
    database = os.getenv("MYSQL_DB", "")
    check(host in {"localhost", "127.0.0.1"}, f"refusing non-local host: {host}")
    check(port == 3306, f"refusing non-demo port: {port}")
    check(user == "demo_user", f"refusing non-demo user: {user}")
    check(database == "sentiment_demo", f"refusing non-demo database: {database}")
    connection = mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database,
    )
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT DATABASE(), CURRENT_USER()")
        actual_database, actual_user = cursor.fetchone()
        check(actual_database == "sentiment_demo", f"connected to {actual_database!r}")
        check(str(actual_user).split("@", 1)[0] == "demo_user", f"connected as {actual_user!r}")
    finally:
        cursor.close()
    return connection


def thread_snapshot(thread_id: str) -> Dict[str, Any]:
    connection = demo_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT thread_state_json FROM conversation_threads WHERE id = %s",
            (thread_id,),
        )
        row = cursor.fetchone()
        check(row is not None, "missing canary thread")
        envelope = load_thread_state(row.get("thread_state_json"))
        return {
            "state": envelope.conversation_state.to_dict(),
            "civic_311_context": envelope.civic_311_context,
            "dialogue_context": envelope.dialogue_context,
        }
    finally:
        cursor.close()
        connection.close()


def message_count(thread_id: str) -> int:
    connection = demo_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM conversation_messages WHERE thread_id = %s",
            (thread_id,),
        )
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()
        connection.close()


def latest_model_led_diagnostics(thread_id: str) -> Dict[str, Any]:
    connection = demo_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT message_meta_json
            FROM conversation_messages
            WHERE thread_id = %s AND role = 'user'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (thread_id,),
        )
        row = cursor.fetchone()
        check(row is not None, "missing persisted user message diagnostics")
        raw = row.get("message_meta_json")
        metadata = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        diagnostics = metadata.get("model_led_turn") or {}
        check(isinstance(diagnostics, dict), "invalid model-led diagnostics")
        return diagnostics
    finally:
        cursor.close()
        connection.close()


def selected_event_fixture() -> Dict[str, Any]:
    connection = demo_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, event_name, event_date, start_date, start_time, end_time, raw_text, source_pdf
            FROM weekly_events
            WHERE event_name IS NOT NULL AND event_date IS NOT NULL
            ORDER BY event_date, id
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        check(row is not None, "demo event fixture is unavailable")
        return {
            "entity_type": "event",
            "entity_id": str(row["id"]),
            "entity_title": str(row["event_name"]),
            "entity_date": (
                row["start_date"].isoformat()
                if hasattr(row["start_date"], "isoformat")
                else str(row["start_date"])
            ),
            "source": "events_sidebar",
        }
    finally:
        cursor.close()
        connection.close()


class Client:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.token = ""
        self.csrf = ""

    def guest(self) -> None:
        response = self.session.post(f"{self.base}/auth/guest", timeout=15)
        response.raise_for_status()
        payload = response.json()
        self.token = str(payload["session_token"])
        self.csrf = str(payload["csrf_token"])

    def headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-CSRF-Token": self.csrf,
            "Content-Type": "application/json",
            "Connection": "close",
        }
        if not AUTHORITATIVE:
            headers["X-On-The-Porch-Canary"] = CANARY_HEADER
        return headers

    def create_thread(self, title: str) -> str:
        response = self.session.post(
            f"{self.base}/conversations",
            headers=self.headers(),
            json={"title": title},
            timeout=15,
        )
        response.raise_for_status()
        return str(response.json()["thread"]["id"])

    def post_json(
        self,
        thread_id: str,
        message: str,
        *,
        turn_id: Optional[str] = None,
        ui_context: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], float]:
        body: Dict[str, Any] = {
            "message": message,
            "client_turn_id": turn_id or str(uuid.uuid4()),
        }
        if ui_context is not None:
            body["ui_context"] = dict(ui_context)
        started = time.perf_counter()
        response = self.session.post(
            f"{self.base}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json=body,
            timeout=20,
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        check(response.status_code == 201, f"JSON {response.status_code}: {response.text[:500]}")
        return response.json(), elapsed

    def post_sse(
        self,
        thread_id: str,
        message: str,
        *,
        turn_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], float]:
        started = time.perf_counter()
        response = self.session.post(
            f"{self.base}/conversations/{thread_id}/messages/stream",
            headers={**self.headers(), "Accept": "text/event-stream"},
            json={"message": message, "client_turn_id": turn_id or str(uuid.uuid4())},
            stream=True,
            timeout=20,
        )
        check(response.status_code == 200, f"SSE {response.status_code}: {response.text[:500]}")
        final = None
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("type") == "error":
                raise AssertionError(f"SSE error: {event}")
            if event.get("type") == "final":
                final = event
        check(final is not None, "SSE final event missing")
        return final, (time.perf_counter() - started) * 1000.0


def turn(
    client: Client,
    thread_id: str,
    label: str,
    message: str,
    *,
    stream: bool = False,
    ui_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    before = thread_snapshot(thread_id)
    if stream:
        payload, latency = client.post_sse(thread_id, message)
    else:
        payload, latency = client.post_json(thread_id, message, ui_context=ui_context)
    after = thread_snapshot(thread_id)
    model = payload.get("model_led_turn") or latest_model_led_diagnostics(thread_id)
    if AUTHORITATIVE:
        check("model_led_turn" not in payload, f"{label} exposed internal diagnostics")
    answer = str(payload.get("answer") or payload["assistant_message"].get("content") or "")
    check(latency < 15_000, f"{label} exceeded global deadline: {latency:.2f}ms")
    check(model.get("planner_call_count") == 1, f"{label} planner call count")
    check(model.get("synthesis_call_count") == 0, f"{label} synthesis count")
    check(model.get("repair_call_count", 0) == 0, f"{label} repair count")
    check(model.get("embedding_call_count", 0) == 0, f"{label} embedding count")
    check(
        model.get("secondary_planner_call_count", 0) == 0,
        f"{label} secondary planner count",
    )
    record = {
        "label": label,
        "message": message,
        "status": model.get("status"),
        "planner_action": model.get("planner_action"),
        "guarded_disposition": model.get("guarded_disposition"),
        "reason_code": model.get("reason_code"),
        "compiled_tools": model.get("compiled_tools") or [
            {"tool_id": tool_id}
            for tool_id in model.get("compiled_tool_ids") or []
        ],
        "state_transition": model.get("state_transition"),
        "planner_call_count": model.get("planner_call_count"),
        "synthesis_call_count": model.get("synthesis_call_count"),
        "tool_execution_counts": model.get("tool_execution_counts") or {},
        "latency_ms": round(latency, 2),
        "service_latency_ms": model.get("latency_ms"),
        "answer": answer,
        "response_mode": payload.get("mode") or payload["assistant_message"].get("response_mode"),
        "sources": payload.get("sources") or payload["assistant_message"].get("sources") or [],
        "before": before,
        "after": after,
    }
    RESULTS.append(record)
    return record


def compiled_tool_ids(record: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("tool_id"))
        for item in record.get("compiled_tools") or []
        if isinstance(item, Mapping) and item.get("tool_id")
    }


def run(base_url: str) -> None:
    demo_connection().close()
    requested_headers = "content-type" if AUTHORITATIVE else "content-type,x-on-the-porch-canary"
    preflight = requests.options(
        f"{base_url.rstrip('/')}/auth/guest",
        headers={
            "Origin": "http://127.0.0.1:8011",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": requested_headers,
        },
        timeout=10,
    )
    check(preflight.status_code == 204, f"canary CORS preflight returned {preflight.status_code}")
    allowed_headers = preflight.headers.get("Access-Control-Allow-Headers", "").lower()
    if not AUTHORITATIVE:
        check("x-on-the-porch-canary" in allowed_headers, "canary header is missing from CORS preflight")
    fixture = selected_event_fixture()
    client = Client(base_url)
    client.guest()

    direct_thread = client.create_thread("Phase 2A direct and clarification")
    direct = turn(client, direct_thread, "direct", "Hello there.")
    check(direct["status"] == "COMPLETED" and direct["planner_action"] == "respond_directly", "direct strategy")
    check(not direct["compiled_tools"], "direct must not compile tools")
    unclear = turn(
        client,
        direct_thread,
        "clarification",
        "What events are not happening next week, but don't list the ones that are?",
    )
    check(unclear["status"] in {"CLARIFICATION", "INVALID_UNSAFE"}, "ambiguous event must clarify")
    check(not unclear["compiled_tools"], "clarification must not compile tools")

    event_thread = client.create_thread("Phase 2A event dialogue")
    listed = turn(client, event_thread, "events_current_week", "What events are happening this week?")
    check(listed["status"] == "COMPLETED", "event list status")
    check(listed["tool_execution_counts"].get("query_events") == 1, "event list tool count")
    summary = turn(
        client,
        event_thread,
        "events_trusted_summary",
        "If those are already finished, just tell me that.",
    )
    check(summary["status"] == "COMPLETED", "trusted event summary status")
    check(not summary["tool_execution_counts"], "trusted event summary must not repeat lookup")
    check(summary["after"]["state"]["active_topic"] == "EVENTS", "event topic must persist")

    negated_thread = client.create_thread("Phase 2A negated events")
    negated = turn(client, negated_thread, "events_negated", "Are there no events next week?")
    check(negated["tool_execution_counts"].get("query_events") == 1, "negated event must lookup")

    selected_thread = client.create_thread("Phase 2A selected event")
    selected = turn(
        client,
        selected_thread,
        "selected_event",
        "Where is it?",
        ui_context=fixture,
    )
    check(selected["tool_execution_counts"].get("query_events") == 1, "selected event details tool")
    check(selected["after"]["state"].get("active_entity", {}).get("id") == fixture["entity_id"], "selected event state")

    civic_thread = client.create_thread("Phase 2A 311 dialogue")
    potholes = turn(client, civic_thread, "311_potholes", "How many pothole requests were filed last month?")
    check("aggregate_311" in compiled_tool_ids(potholes), "311 count capability")
    check(potholes["tool_execution_counts"].get("get_311_issue_count") == 1, "311 count tool")
    overall = turn(client, civic_thread, "311_overall_correction", "No, I meant everything overall, not potholes.")
    check("aggregate_311" in compiled_tool_ids(overall), "311 correction capability")
    check(overall["tool_execution_counts"].get("get_311_issue_count") == 1, "311 correction tool")
    check("2933" in overall["answer"], "311 correction should use all-request count")
    grouped = turn(client, civic_thread, "311_group_followup", "What types were most common?")
    check("aggregate_311" in compiled_tool_ids(grouped), "311 group capability")
    check(grouped["tool_execution_counts"].get("get_311_category_distribution") == 1, "311 group tool")
    check(grouped["after"]["state"]["active_topic"] == "CITY_SERVICES", "311 topic state")
    lookup_required = turn(client, civic_thread, "311_tool_prohibition", "Can you answer that without looking it up?")
    check(not lookup_required["compiled_tools"], "tool-prohibition explanation must not run tools")
    check("2933" not in lookup_required["answer"] and "54" not in lookup_required["answer"], "civic fact was fabricated")
    if lookup_required["status"] == "COMPLETED":
        grounding_explanation = lookup_required["answer"].lower()
        check(
            any(
                wording in grounding_explanation
                for wording in ("data", "evidence", "source", "look up", "looking up")
            ),
            "completed tool-prohibition answer must explain the evidence requirement",
        )
    else:
        check(lookup_required["status"] == "DEGRADED", "tool-prohibition failure must degrade safely")
        check(
            lookup_required["reason_code"] in {"PLANNER_TIMEOUT", "PLANNER_PROVIDER_ERROR"},
            "only a bounded provider failure may bypass the evidence explanation",
        )
        check(not lookup_required["tool_execution_counts"], "degraded tool-prohibition turn must not run tools")
    check(lookup_required["after"]["state"]["active_topic"] == "CITY_SERVICES", "explanation must preserve 311 state")

    punctuation_thread = client.create_thread("Phase 2A punctuation")
    punctuation = turn(client, punctuation_thread, "punctuation", "How many pothole complants were filed last month??")
    check("aggregate_311" in compiled_tool_ids(punctuation), "misspelled 311 paraphrase")

    other_user = Client(base_url)
    other_user.guest()
    ambiguous_thread = other_user.create_thread("Phase 2A user isolation")
    isolated = turn(other_user, ambiguous_thread, "cross_user_reference", "Where is it?")
    check(isolated["status"] in {"CLARIFICATION", "INVALID_UNSAFE"}, "cross-user event context leaked")
    check(not isolated["compiled_tools"], "cross-user ambiguity must not run tools")

    second_thread = client.create_thread("Phase 2A thread isolation")
    thread_isolated = turn(client, second_thread, "cross_thread_reference", "What types were most common?")
    check(thread_isolated["status"] in {"CLARIFICATION", "INVALID_UNSAFE"}, "cross-thread 311 context leaked")
    check(not thread_isolated["compiled_tools"], "cross-thread ambiguity must not run tools")

    emergency_thread = client.create_thread("Phase 2A emergency precedence")
    emergency_payload, emergency_latency = client.post_json(
        emergency_thread,
        "My house is on fire right now.",
    )
    emergency_answer = str(emergency_payload["assistant_message"]["content"])
    check("911" in emergency_answer, "emergency guidance missing")
    check("model_led_turn" not in emergency_payload, "emergency incorrectly entered canary")
    check(emergency_latency < 15_000, "emergency exceeded deadline")

    if AUTHORITATIVE:
        legacy_thread = client.create_thread("Phase 2 transitional legacy handoff")
        legacy = turn(
            client,
            legacy_thread,
            "legacy_public_safety",
            "What public-safety updates are available for Dorchester?",
        )
        check(legacy["status"] == "NOT_YET_MIGRATED", "legacy handoff disposition")
        check(legacy["response_mode"] != "model_led_not_yet_migrated", "legacy answer was not produced")
        check(bool(legacy["answer"].strip()), "legacy handoff answer is empty")
        check(not legacy["tool_execution_counts"], "model-led service ran migrated tools during legacy handoff")

    parity_thread = client.create_thread("Phase 2A JSON SSE replay parity")
    parity_id = str(uuid.uuid4())
    json_payload, _ = client.post_json(
        parity_thread,
        "How many 311 requests were filed last month?",
        turn_id=parity_id,
    )
    sse_payload, _ = client.post_sse(
        parity_thread,
        "How many 311 requests were filed last month?",
        turn_id=parity_id,
    )
    check(json_payload["assistant_message"]["content"] == sse_payload["answer"], "JSON/SSE replay answer parity")
    check(json_payload["assistant_message"]["sources"] == sse_payload["sources"], "JSON/SSE replay source parity")

    idem_thread = client.create_thread("Phase 2A concurrent idempotency")
    idem_id = str(uuid.uuid4())
    before_count = message_count(idem_thread)

    def submit_duplicate() -> requests.Response:
        return requests.post(
            f"{client.base}/conversations/{idem_thread}/messages",
            headers=client.headers(),
            json={
                "message": "What events are happening this week?",
                "client_turn_id": idem_id,
            },
            timeout=20,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: submit_duplicate(), range(2)))
    check(all(item.status_code == 201 for item in responses), "concurrent duplicate HTTP status")
    duplicate_payloads = [item.json() for item in responses]
    check(message_count(idem_thread) == before_count + 2, "duplicate turn persisted more than once")
    check(
        len({item["assistant_message"]["content"] for item in duplicate_payloads}) == 1,
        "duplicate turn answers diverged",
    )

    direct_latencies = [item["latency_ms"] for item in RESULTS if item["label"] == "direct"]
    tool_latencies = [
        item["latency_ms"]
        for item in RESULTS
        if item["tool_execution_counts"]
    ]
    check(max(direct_latencies) <= 2_500, "direct canary latency exceeds 2.5s")
    check(max(tool_latencies) <= 3_500, "simple event/311 canary latency exceeds 3.5s")
    print(json.dumps({
        "status": "passed",
        "database": "demo_user@localhost:3306/sentiment_demo",
        "browser_canary_preflight": "passed",
        "records": RESULTS,
        "json_sse_parity": "passed",
        "idempotency_concurrency": "passed",
        "emergency_precedence": {
            "answer": emergency_answer,
            "latency_ms": round(emergency_latency, 2),
        },
    }, ensure_ascii=True, indent=2, default=str))


def main() -> None:
    global AUTHORITATIVE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8899")
    parser.add_argument("--authoritative", action="store_true")
    args = parser.parse_args()
    AUTHORITATIVE = bool(args.authoritative)
    run(args.base_url)


if __name__ == "__main__":
    main()
