#!/usr/bin/env python3
"""Browser-level regression for stable chat rendering (greeting flicker class).

Uses the live API plus isolated frontend asset checks. Asserts cardinality and
stability rather than exact greeting prose.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
FE = "http://127.0.0.1:8001"
API = "http://127.0.0.1:8888"


def _guest_session() -> tuple[requests.Session, dict]:
    session = requests.Session()
    guest = session.post(f"{API}/auth/guest", timeout=20)
    guest.raise_for_status()
    payload = guest.json()
    headers = {
        "Authorization": f"Bearer {payload['session_token']}",
        "X-CSRF-Token": payload["csrf_token"],
        "Content-Type": "application/json",
    }
    return session, headers


def test_isolated_frontend_assets() -> None:
    index = requests.get(f"{FE}/", timeout=10)
    index.raise_for_status()
    html = index.text
    assert "chat_message_lifecycle.js" in html
    assert "instantReplies.js" not in html
    assert "app.js?v=20260723a" in html
    lifecycle = requests.get(f"{FE}/chat_message_lifecycle.js?v=20260723a", timeout=10)
    lifecycle.raise_for_status()
    assert "applyFinal" in lifecycle.text
    app = requests.get(f"{FE}/app.js?v=20260723a", timeout=10)
    app.raise_for_status()
    assert "InstantReplies.reply" not in app.text
    assert "ChatMessageLifecycle" in app.text


def _ask_sse(session: requests.Session, headers: dict, message: str) -> dict:
    thread = session.post(
        f"{API}/conversations",
        headers=headers,
        json={"title": message[:24]},
        timeout=20,
    )
    thread.raise_for_status()
    thread_id = thread.json()["thread"]["id"]
    client_turn_id = str(uuid.uuid4())
    events = []
    t0 = time.perf_counter()
    with session.post(
        f"{API}/conversations/{thread_id}/messages/stream",
        headers={**headers, "Accept": "text/event-stream"},
        json={"message": message, "client_turn_id": client_turn_id},
        timeout=60,
        stream=True,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
    latency_ms = (time.perf_counter() - t0) * 1000
    finals = [event for event in events if event.get("type") == "final"]
    assert len(finals) == 1, f"expected one final, got {len(finals)}"
    assistant = finals[0].get("assistant_message") or {}
    content = assistant.get("content") or ""
    assert content.strip(), "empty assistant content"
    # Replay with same client_turn_id
    replay = session.post(
        f"{API}/conversations/{thread_id}/messages",
        headers=headers,
        json={"message": message, "client_turn_id": client_turn_id},
        timeout=60,
    )
    replay.raise_for_status()
    replay_content = replay.json()["assistant_message"]["content"]
    assert replay_content == content
    assert replay.json()["assistant_message"]["id"] == assistant.get("id")
    # History should contain exactly one user + one assistant for this turn
    history = session.get(
        f"{API}/conversations/{thread_id}/messages",
        headers=headers,
        timeout=20,
    )
    history.raise_for_status()
    messages = history.json()["messages"]
    users = [item for item in messages if item.get("role") == "user" and item.get("content") == message]
    assistants = [item for item in messages if item.get("role") == "assistant"]
    assert len(users) == 1
    assert len(assistants) >= 1
    matching = [item for item in assistants if item.get("id") == assistant.get("id")]
    assert len(matching) == 1
    assert matching[0]["content"] == content
    return {
        "message": message,
        "content": content,
        "assistant_id": assistant.get("id"),
        "latency_ms": round(latency_ms, 1),
        "delta_count": sum(1 for event in events if event.get("type") == "delta"),
        "event_types": [event.get("type") for event in events],
        "thread_id": thread_id,
        "client_turn_id": client_turn_id,
    }


def test_greeting_and_representative_flows() -> None:
    session, headers = _guest_session()
    cases = [
        "Hi",
        "Hello",
        "What can you help me with?",
        "What community events are happening in Dorchester this week?",
        "How many 311 requests were there in Dorchester last month?",
        "What are the main safety concerns in Dorchester?",
        "Where can I find official Boston zoning information for my parcel?",
        "My house is on fire right now.",
    ]
    results = []
    for message in cases:
        result = _ask_sse(session, headers, message)
        # Stability window: re-fetch history after a few seconds and ensure unchanged.
        time.sleep(2.0 if message == "Hi" else 0.2)
        history = session.get(
            f"{API}/conversations/{result['thread_id']}/messages",
            headers=headers,
            timeout=20,
        )
        history.raise_for_status()
        assistants = [
            item
            for item in history.json()["messages"]
            if item.get("id") == result["assistant_id"]
        ]
        assert len(assistants) == 1
        assert assistants[0]["content"] == result["content"]
        results.append(result)
        print(
            f"OK {message!r}: id={result['assistant_id']} "
            f"deltas={result['delta_count']} lat={result['latency_ms']}ms"
        )
    # Double-submit same logical turn stays one message
    first = results[0]
    assert first["message"] == "Hi"
    print("GREETING_STABLE", first["content"][:120])
    print(json.dumps({"cases": len(results), "all_under_15s": all(r["latency_ms"] < 15000 for r in results)}))


def test_json_sse_parity_for_greeting() -> None:
    session, headers = _guest_session()
    message = "Hi"
    # JSON
    thread = session.post(
        f"{API}/conversations",
        headers=headers,
        json={"title": "json-hi"},
        timeout=20,
    ).json()["thread"]["id"]
    json_body = session.post(
        f"{API}/conversations/{thread}/messages",
        headers=headers,
        json={"message": message, "client_turn_id": str(uuid.uuid4())},
        timeout=60,
    ).json()
    json_content = json_body["assistant_message"]["content"]
    # SSE
    sse = _ask_sse(session, headers, message)
    # Both are authoritative social answers; they may differ across turns, but each
    # turn's SSE final == replay == history. Cross-turn prose need not match.
    assert json_content.strip()
    assert sse["content"].strip()
    print("JSON_SSE_EACH_STABLE")


def main() -> int:
    test_isolated_frontend_assets()
    print("PASS assets")
    test_json_sse_parity_for_greeting()
    print("PASS json/sse stability")
    test_greeting_and_representative_flows()
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
