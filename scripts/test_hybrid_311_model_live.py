#!/usr/bin/env python3
"""Real-provider live acceptance for bounded hybrid Dorchester 311 answers.

The script refuses non-demo MySQL configuration. Run it against an isolated API
whose CIVIC_311_HYBRID_MODE is either ``auto``/``required`` or ``disabled``.
Hybrid mode requires the configured provider to exercise real planning and
synthesis; disabled mode records deterministic comparison answers.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (str(ROOT), str(SCRIPTS), str(ROOT / "on_the_porch")):
    if path not in sys.path:
        sys.path.insert(0, path)

from test_hybrid_311_answering_live import (  # noqa: E402
    Client,
    _assert_resident_safe,
    _demo_connection,
    _message_count,
    _thread_snapshot,
    _user_meta,
    check,
)


def _turn(client: Client, thread_id: str, label: str, message: str) -> Dict[str, Any]:
    before = _thread_snapshot(thread_id)
    payload, wall_ms = client.post_json(thread_id, message)
    after = _thread_snapshot(thread_id)
    assistant = payload["assistant_message"]
    answer = str(assistant.get("content") or "")
    meta = (_user_meta(payload["user_message"]["id"]).get("civic_311") or {})
    _assert_resident_safe(answer)
    check(wall_ms < 15000, f"{label} exceeded 15 seconds: {wall_ms:.1f}ms")
    if assistant.get("response_mode") == "structured_311":
        check(meta.get("embedding_call_count") == 0, f"{label} used embeddings")
        check(int(meta.get("planner_call_count") or 0) <= 1, f"{label} planner cap")
        check(int(meta.get("synthesis_call_count") or 0) <= 1, f"{label} synthesis cap")
        check(int(meta.get("repair_call_count") or 0) <= 1, f"{label} repair cap")
        check(int(meta.get("llm_call_count") or 0) <= 3, f"{label} total model cap")
    return {
        "label": label,
        "question": message,
        "answer": answer,
        "response_mode": assistant.get("response_mode"),
        "model_name": assistant.get("model_name"),
        "sources": assistant.get("sources") or [],
        "wall_latency_ms": round(wall_ms, 2),
        "diagnostics": meta,
        "before_context": before["civic_311_context"],
        "after_context": after["civic_311_context"],
        "thread_id": thread_id,
    }


def _assert_hybrid_record(record: Dict[str, Any], *, planner: int, synthesis: int) -> None:
    diagnostics = record["diagnostics"]
    check(record["response_mode"] == "structured_311", f"{record['label']} route")
    check(int(diagnostics.get("planner_call_count") or 0) == planner, f"{record['label']} planner calls")
    check(int(diagnostics.get("synthesis_call_count") or 0) == synthesis, f"{record['label']} synthesis calls")
    check(int(diagnostics.get("llm_call_count") or 0) >= planner + synthesis, f"{record['label']} model calls")


def run(base_url: str, expected_mode: str) -> Dict[str, Any]:
    _demo_connection().close()
    hybrid = expected_mode == "hybrid"
    client = Client(base_url)
    client.guest()
    records: List[Dict[str, Any]] = []

    exact_thread = client.create_thread("311 exact zero-model acceptance")
    exact = _turn(client, exact_thread, "exact_count", "How many pothole reports were recorded in June?")
    records.append(exact)
    check(exact["response_mode"] == "structured_311", "exact count route")
    check(int(exact["diagnostics"].get("llm_call_count") or 0) == 0, "exact count must stay zero-model")
    check("June 2026" in exact["answer"], "exact count must retain named month")

    pothole_thread = client.create_thread("311 pothole hybrid acceptance")
    recent = _turn(client, pothole_thread, "A_recent_potholes", "Have people complained about potholes recently?")
    records.append(recent)
    check("June 27, 2026" in recent["answer"], "recent answer must disclose data-through date")
    check("not" in recent["answer"].lower() and "distinct" in recent["answer"].lower(), "recent answer needs distinct-request caveat")
    if hybrid:
        _assert_hybrid_record(recent, planner=0, synthesis=1)
    else:
        check(int(recent["diagnostics"].get("llm_call_count") or 0) == 0, "disabled mode used a model")

    top = _turn(client, pothole_thread, "B_top_streets", "Which streets have the most pothole reports?")
    records.append(top)
    check("period" in top["answer"].lower() or "June" in top["answer"], "top-streets period missing")
    check(
        "usable street breakdown" in top["answer"].lower()
        or bool(re.search(r"\b(?:Street|St|Avenue|Ave|Road|Rd)\b.{0,40}\(\d+\)", top["answer"])),
        "top-streets answer must list evidence or state the coverage limitation",
    )
    check(int(top["diagnostics"].get("synthesis_call_count") or 0) == 0, "top-streets should remain deterministic")

    status = _turn(client, pothole_thread, "C_status", "Are these reports still open?")
    records.append(status)
    check(
        "open or in-progress" in status["answer"].lower()
        or "open or in progress" in status["answer"].lower(),
        "status breakdown missing",
    )
    check("does not show active work" in status["answer"].lower(), "active-work caveat missing")
    check("does not verify" in status["answer"].lower(), "repair caveat missing")
    check(int(status["diagnostics"].get("synthesis_call_count") or 0) == 0, "status should remain deterministic")

    drainage_thread = client.create_thread("311 semantic planner acceptance")
    drainage = _turn(
        client,
        drainage_thread,
        "D_implicit_drainage",
        "Has anything been done about the water collecting on my street, and have other people complained?",
    )
    records.append(drainage)
    check("Which street in Dorchester" in drainage["answer"], "implicit drainage must ask for the street")
    check(drainage["diagnostics"].get("answer_mode") == "CLARIFICATION", "implicit drainage clarification mode")
    if hybrid:
        _assert_hybrid_record(drainage, planner=1, synthesis=0)
        check(drainage["diagnostics"].get("planner_source") == "LLM", "real planner output was not accepted")
        check(
            set((drainage["after_context"] or {}).get("outputs") or ())
            == {"MATCHING_REQUESTS", "STATUS_BREAKDOWN", "RECENT_EXAMPLES", "SOURCE_FRESHNESS"},
            "real planner dropped compound information needs",
        )
    else:
        check(int(drainage["diagnostics"].get("llm_call_count") or 0) == 0, "disabled clarification used model")

    street = _turn(client, drainage_thread, "D_street_continuation", "Dorchester Ave")
    records.append(street)
    check("Dorchester Ave" in street["answer"], "street continuation lost the supplied street")
    street_text = street["answer"].lower()
    check(
        any(phrase in street_text for phrase in ("does not", "do not", "cannot", "can't")),
        "street answer must preserve work/coverage limits",
    )
    if hybrid:
        if "did not find matching" in street_text:
            check(int(street["diagnostics"].get("synthesis_call_count") or 0) == 0, "zero-result street answer must be deterministic")
        else:
            _assert_hybrid_record(street, planner=0, synthesis=1)
            check(
                street["diagnostics"].get("answer_mode") == "EVIDENCE_BOUNDED_SYNTHESIS",
                "nonzero street continuation should accept a grounded synthesis",
            )
    else:
        check(int(street["diagnostics"].get("llm_call_count") or 0) == 0, "disabled street continuation used model")

    comparison_thread = client.create_thread("311 comparison acceptance")
    comparison = _turn(client, comparison_thread, "E_comparison", "Are pothole complaints getting worse?")
    records.append(comparison)
    comparison_text = comparison["answer"].lower()
    check(
        "preceding 7-day period" in comparison_text
        or "preceding comparable period" in comparison_text,
        "equivalent comparison period missing",
    )
    if hybrid:
        _assert_hybrid_record(comparison, planner=0, synthesis=1)
        check("-%" not in comparison["answer"] and not re.search(r"-\d+(?:\.\d+)?%", comparison["answer"]), "signed decrease percentage")
    else:
        check(int(comparison["diagnostics"].get("llm_call_count") or 0) == 0, "disabled comparison used model")

    category_thread = client.create_thread("311 category acceptance")
    category = _turn(client, category_thread, "F_category_overview", "What street problems are people reporting around Dorchester?")
    records.append(category)
    category_text = category["answer"].lower()
    check(
        all(label in category_text for label in ("trash and illegal dumping", "street and pavement condition", "pothole")),
        "humanized category evidence missing",
    )
    if hybrid:
        _assert_hybrid_record(category, planner=0, synthesis=1)
    else:
        check(int(category["diagnostics"].get("llm_call_count") or 0) == 0, "disabled category used model")

    # Completed-turn replay proves JSON/SSE parity without a second model call.
    parity_thread = client.create_thread("311 JSON SSE replay parity")
    parity_turn_id = str(uuid.uuid4())
    json_payload, _ = client.post_json(
        parity_thread,
        "Have people complained about potholes recently?",
        turn_id=parity_turn_id,
    )
    before_replay_count = _message_count(parity_thread)
    sse_payload, _ = client.post_sse(
        parity_thread,
        "Have people complained about potholes recently?",
        turn_id=parity_turn_id,
    )
    check(_message_count(parity_thread) == before_replay_count, "SSE replay persisted a duplicate turn")
    check(json_payload["assistant_message"]["content"] == sse_payload["answer"], "JSON/SSE answer parity")
    check(json_payload["assistant_message"]["sources"] == sse_payload["sources"], "JSON/SSE source parity")
    check(json_payload["assistant_message"]["response_mode"] == sse_payload["mode"], "JSON/SSE mode parity")

    # Concurrent retries of one model-assisted turn must reserve one generation.
    idem_client = Client(base_url)
    idem_client.guest()
    idem_thread = idem_client.create_thread("311 model idempotency")
    idem_turn_id = str(uuid.uuid4())
    before_count = _message_count(idem_thread)

    def submit_once() -> Dict[str, Any]:
        response = requests.post(
            f"{idem_client.base}/conversations/{idem_thread}/messages",
            headers=idem_client.headers(),
            json={
                "message": "Have people complained about potholes recently?",
                "client_turn_id": idem_turn_id,
            },
            timeout=40,
        )
        check(response.status_code == 201, f"idempotency HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        idem_responses = list(executor.map(lambda _unused: submit_once(), range(2)))
    check(_message_count(idem_thread) == before_count + 2, "idempotency persisted duplicate messages")
    check(len({item["assistant_message"]["content"] for item in idem_responses}) == 1, "idempotent answers diverged")

    # Context is scoped by thread and user; a bare follow-up cannot borrow it.
    other_thread = client.create_thread("311 cross-thread isolation")
    cross_thread = _turn(client, other_thread, "isolation_thread", "Which streets have the most?")
    records.append(cross_thread)
    check(cross_thread["diagnostics"].get("answer_mode") == "CLARIFICATION", "cross-thread context leaked")
    check(int(cross_thread["diagnostics"].get("llm_call_count") or 0) == 0, "contextless follow-up should clarify without a model")
    other_user = Client(base_url)
    other_user.guest()
    other_user_thread = other_user.create_thread("311 cross-user isolation")
    cross_user = _turn(other_user, other_user_thread, "isolation_user", "Are these reports still open?")
    records.append(cross_user)
    check(cross_user["diagnostics"].get("answer_mode") == "CLARIFICATION", "cross-user context leaked")
    check(int(cross_user["diagnostics"].get("llm_call_count") or 0) == 0, "contextless status reference should clarify without a model")

    event_thread = client.create_thread("311 event regression")
    event = _turn(client, event_thread, "event_regression", "What events are happening this weekend?")
    records.append(event)
    check(event["response_mode"] != "structured_311", "event request was captured by 311")

    return {
        "status": "passed",
        "expected_mode": expected_mode,
        "records": records,
        "json_sse_parity": "passed",
        "idempotency": "passed",
        "cross_thread_isolation": "passed",
        "cross_user_isolation": "passed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8895")
    parser.add_argument("--expected-mode", choices=("disabled", "hybrid"), required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.base_url, args.expected_mode), ensure_ascii=True, indent=2, default=str))


if __name__ == "__main__":
    main()
