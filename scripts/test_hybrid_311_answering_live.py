#!/usr/bin/env python3
"""Live local-demo verification for the bounded hybrid 311 answer path.

This script only accepts the Docker demo database.  It creates guest threads,
exercises JSON and SSE through the isolated API URL, and reads its own durable
turn state from local MySQL to verify persistence and idempotency.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from conversation_state import load_thread_state  # noqa: E402


FORBIDDEN_PATTERNS = (
    re.compile(r"\bservice_requests_311\b", re.IGNORECASE),
    re.compile(r"\bget_311_[a-z0-9_]+\b", re.IGNORECASE),
    re.compile(r"\bprepared_sql\b", re.IGNORECASE),
    re.compile(r"\bRAG\b"),
    re.compile(r"\bChroma\b", re.IGNORECASE),
    re.compile(r"\bQdrant\b", re.IGNORECASE),
    re.compile(r"\bSQL execution\b", re.IGNORECASE),
    re.compile(r"\bretrieval failure\b", re.IGNORECASE),
    re.compile(r"\btool call\b", re.IGNORECASE),
)
REPORTS: List[Dict[str, Any]] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _demo_connection():
    import mysql.connector

    host = os.getenv("MYSQL_HOST", "")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "")
    database = os.getenv("MYSQL_DB", "")
    check(host in {"localhost", "127.0.0.1"}, f"refusing non-local host: {host}")
    check(port == 3306, f"refusing non-demo port: {port}")
    check(user == "demo_user", f"refusing non-demo user: {user}")
    check(database == "sentiment_demo", f"refusing non-demo database: {database}")
    return mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database,
    )


def _thread_snapshot(thread_id: str) -> Dict[str, Any]:
    conn = _demo_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT thread_state_json FROM conversation_threads WHERE id = %s", (thread_id,))
        row = cursor.fetchone()
        check(row is not None, "missing test thread")
        envelope = load_thread_state(row.get("thread_state_json"))
        return {
            "raw": row.get("thread_state_json"),
            "civic_311_context": envelope.civic_311_context,
            "state": envelope.conversation_state.to_dict(),
        }
    finally:
        cursor.close()
        conn.close()


def _user_meta(message_id: str) -> Dict[str, Any]:
    conn = _demo_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT message_meta_json FROM conversation_messages WHERE id = %s", (message_id,))
        row = cursor.fetchone()
        check(row is not None, "missing persisted user message")
        return json.loads(row.get("message_meta_json") or "{}")
    finally:
        cursor.close()
        conn.close()


def _message_count(thread_id: str) -> int:
    conn = _demo_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM conversation_messages WHERE thread_id = %s", (thread_id,))
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()
        conn.close()


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
        return {
            "Authorization": f"Bearer {self.token}",
            "X-CSRF-Token": self.csrf,
            "Content-Type": "application/json",
            "Connection": "close",
        }

    def create_thread(self, title: str) -> str:
        response = self.session.post(f"{self.base}/conversations", headers=self.headers(), json={"title": title}, timeout=15)
        response.raise_for_status()
        return str(response.json()["thread"]["id"])

    def post_json(self, thread_id: str, message: str, *, turn_id: str | None = None) -> Tuple[Dict[str, Any], float]:
        started = time.perf_counter()
        response = self.session.post(
            f"{self.base}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json={"message": message, "client_turn_id": turn_id or str(uuid.uuid4())},
            timeout=40,
        )
        elapsed = (time.perf_counter() - started) * 1000
        check(response.status_code == 201, f"JSON {response.status_code}: {response.text[:400]}")
        return response.json(), elapsed

    def post_sse(
        self,
        thread_id: str,
        message: str,
        *,
        turn_id: str | None = None,
    ) -> Tuple[Dict[str, Any], float]:
        started = time.perf_counter()
        response = self.session.post(
            f"{self.base}/conversations/{thread_id}/messages/stream",
            headers=self.headers(),
            json={"message": message, "client_turn_id": turn_id or str(uuid.uuid4())},
            stream=True,
            timeout=40,
        )
        check(response.status_code == 200, f"SSE {response.status_code}: {response.text[:400]}")
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
        return final, (time.perf_counter() - started) * 1000


def _assert_resident_safe(answer: str, *, allow_911: bool = False) -> None:
    for pattern in FORBIDDEN_PATTERNS:
        check(pattern.search(answer) is None, f"internal term leaked: {pattern.pattern}")
    if not allow_911:
        check("call 911" not in answer.lower(), "ordinary 311 answer included 911")


def _record(label: str, payload: Dict[str, Any], latency_ms: float, before: Dict[str, Any], after: Dict[str, Any]) -> None:
    user = payload["user_message"]
    meta = _user_meta(user["id"])
    assistant = payload["assistant_message"]
    answer = str(assistant.get("content") or payload.get("answer") or "")
    civic = meta.get("civic_311") or {}
    REPORTS.append({
        "label": label,
        "mode": assistant.get("response_mode") or payload.get("mode"),
        "answer": answer,
        "sources": assistant.get("sources") or payload.get("sources") or [],
        "latency_ms": round(latency_ms, 2),
        "metadata": civic,
        "before_context": before["civic_311_context"],
        "after_context": after["civic_311_context"],
    })


def _turn(client: Client, thread_id: str, label: str, message: str) -> Dict[str, Any]:
    before = _thread_snapshot(thread_id)
    payload, latency_ms = client.post_json(thread_id, message)
    after = _thread_snapshot(thread_id)
    _record(label, payload, latency_ms, before, after)
    answer = str(payload["assistant_message"]["content"])
    return {"payload": payload, "answer": answer, "before": before, "after": after}


def run(base_url: str) -> None:
    _demo_connection().close()
    client = Client(base_url)
    client.guest()
    thread = client.create_thread("Hybrid 311 live verification")

    first = _turn(client, thread, "A_potholes", "Any pothole reports lately?")
    check(first["payload"]["assistant_message"]["response_mode"] == "structured_311", "A must use structured_311")
    _assert_resident_safe(first["answer"])
    check(first["after"]["civic_311_context"] is not None, "A must persist trusted 311 context")
    check(first["payload"]["assistant_message"]["sources"][0]["source"] == "Boston 311 service-request data", "A source label")
    check(first["payload"]["assistant_message"]["model_name"] is None, "A should not invoke a model by default")

    second = _turn(client, thread, "B_status_followup", "Are these reports still open?")
    check(second["payload"]["assistant_message"]["response_mode"] == "structured_311", "B must use structured_311")
    _assert_resident_safe(second["answer"])
    check("open or in-progress" in second["answer"].lower(), "B must report status evidence")

    third = _turn(client, thread, "C_subarea_reference", "What do those 311 reports look like in Codman Square?")
    check(third["payload"]["assistant_message"]["response_mode"] == "structured_311", "C must use structured_311")
    _assert_resident_safe(third["answer"])
    check("does not include a verified Codman Square boundary" in third["answer"], "C must not invent subarea filtering")
    thanks = _turn(client, thread, "C_small_talk_preserves_context", "Thanks")
    check(thanks["after"]["civic_311_context"] is not None, "small talk must preserve trusted 311 context")

    # The supplied demo's pothole records contain no street/address fields.
    # Use a real Dorchester street name to verify that the product reports this
    # evidence limitation rather than returning a misleading zero.
    street = "Dorchester Ave"
    street_thread = client.create_thread("Hybrid 311 street verification")
    fourth = _turn(client, street_thread, "D_street_last_month", f"How many pothole reports were filed on {street} last month?")
    check(fourth["payload"]["assistant_message"]["response_mode"] == "structured_311", "D must use structured_311")
    _assert_resident_safe(fourth["answer"])
    check(street.lower() in fourth["answer"].lower(), "D must retain the specified street")
    check("cannot verify a street-specific count" in fourth["answer"].lower(), "D must not turn missing street coverage into zero")

    category_thread = client.create_thread("Hybrid 311 category verification")
    fifth = _turn(client, category_thread, "E_category_overview", "Most common 311 street problems this month?")
    check(fifth["payload"]["assistant_message"]["response_mode"] == "structured_311", "E must use structured_311")
    _assert_resident_safe(fifth["answer"])
    check("common supported street-service requests" in fifth["answer"].lower(), "E category answer")

    flooding_thread = client.create_thread("Hybrid 311 freshness verification")
    sixth = _turn(client, flooding_thread, "F_flooding_freshness", "How serious is current flooding near Fields Corner?")
    check(sixth["payload"]["assistant_message"]["response_mode"] == "structured_311", "F must use structured_311")
    _assert_resident_safe(sixth["answer"])
    check("records through" in sixth["answer"].lower(), "F must disclose source freshness")

    emergency_thread = client.create_thread("Emergency precedence verification")
    seventh = _turn(client, emergency_thread, "G_emergency_precedence", "My house is on fire right now")
    check(seventh["payload"]["assistant_message"]["response_mode"] != "structured_311", "G emergency must precede 311")
    check("911" in seventh["answer"], "G must preserve emergency guidance")

    eighth = _turn(client, thread, "H_non_311_baseline", "What events are happening this weekend?")
    check(eighth["payload"]["assistant_message"]["response_mode"] != "structured_311", "H must not be captured by 311")
    check(eighth["after"]["civic_311_context"] is None, "unrelated substantive topic must clear 311 context")

    parity_client = Client(base_url)
    parity_client.guest()
    json_thread = parity_client.create_thread("311 JSON parity")
    sse_thread = parity_client.create_thread("311 SSE parity")
    json_payload, _ = parity_client.post_json(json_thread, "Any pothole reports lately?")
    sse_payload, _ = parity_client.post_sse(sse_thread, "Any pothole reports lately?")
    check(json_payload["assistant_message"]["response_mode"] == sse_payload["mode"] == "structured_311", "JSON/SSE mode parity")
    check(json_payload["assistant_message"]["content"] == sse_payload["answer"], "JSON/SSE deterministic answer parity")
    check(json_payload["assistant_message"]["sources"] == sse_payload["sources"], "JSON/SSE source parity")

    idem_client = Client(base_url)
    idem_client.guest()
    idem_thread = idem_client.create_thread("311 idempotency")
    turn_id = str(uuid.uuid4())
    before_count = _message_count(idem_thread)

    def submit_once() -> Dict[str, Any]:
        response = requests.post(
            f"{idem_client.base}/conversations/{idem_thread}/messages",
            headers=idem_client.headers(),
            json={"message": "Any pothole reports lately?", "client_turn_id": turn_id},
            timeout=40,
        )
        check(response.status_code == 201, f"idempotency HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _unused: submit_once(), range(2)))
    check(_message_count(idem_thread) == before_count + 2, "idempotent retry must persist one user/assistant pair")
    answers = {item["assistant_message"]["content"] for item in responses}
    check(len(answers) == 1, "idempotent responses must match")

    for record in REPORTS:
        civic = record["metadata"]
        if record["mode"] == "structured_311":
            check(civic.get("embedding_call_count") == 0, f"{record['label']} embedding count")
            check(civic.get("llm_call_count") == 0, f"{record['label']} unexpected model call")
            check(set(civic).issubset({"planner_source", "planner_ms", "tool_execution_ms", "evidence_status", "answer_mode", "tool_count", "synthesis_ms", "validation_ms", "repair_ms", "persistence_ms", "total_latency_ms", "llm_call_count", "planner_call_count", "synthesis_call_count", "repair_call_count", "embedding_call_count", "fallback_used", "timeout_stage"}), "metadata scope")
    print(json.dumps({"status": "passed", "records": REPORTS, "idempotency": "passed"}, ensure_ascii=True, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8894")
    args = parser.parse_args()
    run(args.base_url)


if __name__ == "__main__":
    main()
