#!/usr/bin/env python3
"""Live local-demo acceptance test for semantic dialogue decisions.

The script refuses any non-demo database, exercises both JSON and SSE on an
isolated API, and reads only the threads/messages it creates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for value in (str(ROOT), str(SCRIPTS), str(ROOT / "on_the_porch")):
    if value not in sys.path:
        sys.path.insert(0, value)

from test_hybrid_311_answering_live import (  # noqa: E402
    Client,
    _demo_connection,
    _message_count,
    _thread_snapshot,
    _user_meta,
    check,
)


REPORTS: List[Dict[str, Any]] = []


def _counts(meta: Dict[str, Any], assistant: Dict[str, Any]) -> Dict[str, int]:
    controller = meta.get("semantic_dialogue_controller") or {}
    civic = meta.get("civic_311") or {}
    event_query = meta.get("event_query_context")
    mode = str(assistant.get("response_mode") or "")
    return {
        "controller_calls": int(controller.get("controller_call_count") or 0),
        "tool_calls": int(civic.get("tool_count") or (1 if mode == "structured_events" and event_query else 0)),
        "model_calls": int(controller.get("controller_call_count") or 0) + int(civic.get("llm_call_count") or 0),
        "embedding_calls": int(civic.get("embedding_call_count") or 0),
    }


def _turn(
    client: Client,
    thread_id: str,
    label: str,
    message: str,
    *,
    transport: str = "json",
    turn_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    before = _thread_snapshot(thread_id)
    if transport == "sse":
        payload, latency_ms = client.post_sse(thread_id, message, turn_id=turn_id)
    else:
        payload, latency_ms = client.post_json(thread_id, message, turn_id=turn_id)
    after = _thread_snapshot(thread_id)
    user = payload["user_message"]
    assistant = payload["assistant_message"]
    meta = _user_meta(str(user["id"]))
    controller = meta.get("semantic_dialogue_controller") or {}
    answer = str(assistant.get("content") or payload.get("answer") or "")
    report = {
        "label": label,
        "transport": transport,
        "message": message,
        "answer": answer,
        "mode": assistant.get("response_mode") or payload.get("mode"),
        "controller": controller,
        "counts": _counts(meta, assistant),
        "model_name": assistant.get("model_name"),
        "latency_ms": round(latency_ms, 2),
        "sources": assistant.get("sources") or payload.get("sources") or [],
        "civic_311": meta.get("civic_311"),
        "event_query_context": meta.get("event_query_context"),
        "before_civic_311_context": before.get("civic_311_context"),
        "after_civic_311_context": after.get("civic_311_context"),
        "before_dialogue_context": before.get("dialogue_context"),
        "after_dialogue_context": after.get("dialogue_context"),
    }
    REPORTS.append(report)
    check(controller, f"{label}: semantic controller metadata missing")
    check(report["counts"]["controller_calls"] <= 1, f"{label}: controller called more than once")
    return payload, report


def _assert_decision(
    report: Dict[str, Any],
    *,
    kind: str,
    domain: str,
    strategy: str,
    capability: str | None = None,
) -> None:
    controller = report["controller"]
    check(controller.get("turn_kind") == kind, f"{report['label']}: turn kind")
    check(controller.get("domain") == domain, f"{report['label']}: domain")
    check(controller.get("response_strategy") == strategy, f"{report['label']}: strategy")
    if capability is not None:
        check(controller.get("capability_intent") == capability, f"{report['label']}: capability")


def run(base_url: str) -> None:
    _demo_connection().close()
    check(os.getenv("SEMANTIC_DIALOGUE_CONTROLLER_LLM_ENABLED", "").lower() in {"0", "false", "no", "off"},
          "live acceptance requires the controller provider-disabled fallback profile")

    client = Client(base_url)
    client.guest()

    aggregate_thread = client.create_thread("Semantic aggregate 311")
    aggregate_payload, aggregate = _turn(
        client,
        aggregate_thread,
        "aggregate_311_json",
        "How many 311 requests were filed last month?",
    )
    _assert_decision(
        aggregate,
        kind="FACTUAL_TOOL_QUERY",
        domain="311",
        strategy="EXECUTE_CAPABILITY",
        capability="COUNT_311",
    )
    check(aggregate["mode"] == "structured_311", "aggregate 311 mode")
    check("June 28 through June 30 are not covered" in aggregate["answer"], "aggregate partial coverage")
    check("not treated as zero" in aggregate["answer"], "aggregate missing days are not zero")
    check(aggregate["counts"]["model_calls"] == 0, "aggregate must use no model")
    check(aggregate["counts"]["embedding_calls"] == 0, "aggregate must use no embeddings")
    check(aggregate_payload["assistant_message"].get("model_name") is None, "aggregate model name")

    aggregate_sse_thread = client.create_thread("Semantic aggregate 311 SSE")
    _payload, aggregate_sse = _turn(
        client,
        aggregate_sse_thread,
        "aggregate_311_sse",
        "How many 311 requests were filed last month?",
        transport="sse",
    )
    check(aggregate_sse["answer"] == aggregate["answer"], "JSON/SSE aggregate answer parity")
    check(aggregate_sse["mode"] == aggregate["mode"], "JSON/SSE aggregate mode parity")
    check(aggregate_sse["controller"] == aggregate["controller"], "JSON/SSE controller parity")

    pending_thread = client.create_thread("Semantic pending 311")
    _payload, pending = _turn(
        client,
        pending_thread,
        "pending_311_clarification",
        "How many city complaints were filed last month?",
    )
    check("Which city-service issue" in pending["answer"], "pending clarification wording")
    check(
        (pending["after_civic_311_context"] or {}).get("coverage_status") == "PENDING_CLARIFICATION",
        "pending 311 context was not persisted",
    )
    _payload, overall = _turn(client, pending_thread, "pending_311_overall", "overall")
    _assert_decision(
        overall,
        kind="CLARIFICATION_RESPONSE",
        domain="311",
        strategy="EXECUTE_CAPABILITY",
        capability="COUNT_311",
    )
    check(overall["mode"] == "structured_311", "pending overall mode")
    check((overall["after_civic_311_context"] or {}).get("issue_scope") == "ALL_311", "pending overall scope")
    check((overall["after_civic_311_context"] or {}).get("issue_family") is None, "pending overall family")
    check(
        (overall["after_civic_311_context"] or {}).get("coverage_status") != "PENDING_CLARIFICATION",
        "pending clarification was not cleared",
    )

    bare_thread = client.create_thread("Semantic bare overall")
    _payload, bare = _turn(client, bare_thread, "bare_overall", "overall")
    _assert_decision(bare, kind="AMBIGUOUS", domain="GENERAL", strategy="ASK_CLARIFICATION")
    check(bare["counts"]["tool_calls"] == 0, "bare overall executed a tool")

    event_thread = client.create_thread("Semantic event rephrase")
    _payload, events = _turn(
        client,
        event_thread,
        "events_this_week",
        "What events are happening this week?",
    )
    _assert_decision(
        events,
        kind="FACTUAL_TOOL_QUERY",
        domain="EVENTS",
        strategy="EXECUTE_CAPABILITY",
        capability="LIST_EVENTS",
    )
    check(events["mode"] == "structured_events", "event factual mode")
    check(events["counts"]["model_calls"] == 0, "event list must use no model")
    check(events["counts"]["embedding_calls"] == 0, "event list must use no embeddings")
    check(
        (events["after_dialogue_context"] or {}).get("previous_result_summary", {}).get("status") == "NO_EVENTS",
        "event fixture must produce the trusted no-events summary",
    )
    before_rephrase_messages = _message_count(event_thread)
    _payload, rephrase = _turn(
        client,
        event_thread,
        "events_rephrase",
        "if this week events are done, just say that",
    )
    _assert_decision(
        rephrase,
        kind="RESPONSE_REPHRASE",
        domain="EVENTS",
        strategy="REPHRASE_PREVIOUS_RESULT",
    )
    check(rephrase["answer"] == "The listed events for this week are over.", "event rephrase answer")
    check(rephrase["counts"]["tool_calls"] == 0, "event rephrase executed a tool")
    check(_message_count(event_thread) == before_rephrase_messages + 2, "event rephrase did not persist once")

    contradiction_thread = client.create_thread("Semantic event contradiction")
    _payload, contradiction = _turn(
        client,
        contradiction_thread,
        "event_contradiction",
        "what events are not happening next week. dont say those which are",
    )
    _assert_decision(
        contradiction,
        kind="UNSATISFIABLE",
        domain="EVENTS",
        strategy="ASK_CLARIFICATION",
    )
    check("countless events" in contradiction["answer"], "event contradiction explanation")
    check(contradiction["counts"]["tool_calls"] == 0, "contradictory event request executed a tool")

    zoning_thread = client.create_thread("Semantic zoning clarification")
    _payload, zoning = _turn(
        client,
        zoning_thread,
        "zoning_location",
        "What zoning rules apply to mixed-use development proposals in Dorchester?",
    )
    _assert_decision(
        zoning,
        kind="FACTUAL_TOOL_QUERY",
        domain="ZONING_AND_DEVELOPMENT",
        strategy="ASK_CLARIFICATION",
    )
    check("address or intersection" in zoning["answer"], "zoning location clarification")
    check(zoning["counts"]["tool_calls"] == 0, "zoning clarification executed a tool")

    hello_thread = client.create_thread("Semantic direct greeting")
    direct_turn_id = str(uuid.uuid4())
    _payload, hello = _turn(
        client,
        hello_thread,
        "small_talk",
        "Hello, what can you help me with?",
        turn_id=direct_turn_id,
    )
    _assert_decision(hello, kind="SMALL_TALK", domain="GENERAL", strategy="DIRECT_RESPONSE")
    check(hello["counts"]["tool_calls"] == 0, "small talk executed a tool")
    before_replay_count = _message_count(hello_thread)
    replay_payload, _elapsed = client.post_json(
        hello_thread,
        "Hello, what can you help me with?",
        turn_id=direct_turn_id,
    )
    check(replay_payload["assistant_message"]["content"] == hello["answer"], "idempotent replay answer")
    check(_message_count(hello_thread) == before_replay_count, "idempotent replay persisted duplicates")

    other_client = Client(base_url)
    other_client.guest()
    isolated_thread = other_client.create_thread("Semantic cross-user isolation")
    _payload, isolated = _turn(other_client, isolated_thread, "cross_user_overall", "overall")
    check(isolated["controller"].get("domain") == "GENERAL", "cross-user state leaked")
    check(isolated["counts"]["tool_calls"] == 0, "cross-user bare overall executed a tool")

    print(json.dumps({"base_url": base_url, "reports": REPORTS}, indent=2, default=str))
    print(f"{len(REPORTS)} live semantic dialogue turns passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8898")
    args = parser.parse_args()
    run(args.base_url)


if __name__ == "__main__":
    main()
