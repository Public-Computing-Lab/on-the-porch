#!/usr/bin/env python3
"""Small local-provider Stage 4 JSON/SSE canary against sentiment_demo only."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "api", ROOT / "on_the_porch"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from config import Config  # noqa: E402
from request_budget import TURN_TIMEOUT_GRACE_MS, TURN_TOTAL_BUDGET_MS  # noqa: E402


def require_local_demo(base_url: str) -> None:
    if str(Config.MYSQL_HOST).lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("refusing non-local MySQL")
    if str(Config.MYSQL_DB).lower() != "sentiment_demo":
        raise RuntimeError("refusing non-demo MySQL")
    if Config.PRODUCTION_MODE:
        raise RuntimeError("refusing production mode")
    if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise RuntimeError("refusing non-loopback API")


def _thread(session: requests.Session, base_url: str, headers: dict[str, str], title: str) -> str:
    response = session.post(
        f"{base_url}/conversations", headers=headers, json={"title": title}, timeout=10
    )
    response.raise_for_status()
    return str(response.json()["thread"]["id"])


def _messages(session: requests.Session, base_url: str, headers: dict[str, str], thread_id: str) -> int:
    response = session.get(
        f"{base_url}/conversations/{thread_id}/messages", headers=headers, timeout=10
    )
    response.raise_for_status()
    return len(response.json()["messages"])


def run(base_url: str) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    require_local_demo(base_url)
    session = requests.Session()
    guest = session.post(f"{base_url}/auth/guest", timeout=10)
    guest.raise_for_status()
    guest_payload = guest.json()
    headers = {
        "Authorization": f"Bearer {guest_payload['session_token']}",
        "X-CSRF-Token": guest_payload["csrf_token"],
        "Content-Type": "application/json",
    }

    json_thread = _thread(session, base_url, headers, "Stage 4 JSON canary")
    json_turn = str(uuid.uuid4())
    json_payload = {
        "message": "How many 311 service requests were filed in Dorchester this week?",
        "client_turn_id": json_turn,
    }
    started = time.perf_counter()
    response = session.post(
        f"{base_url}/conversations/{json_thread}/messages",
        headers=headers,
        json=json_payload,
        timeout=30,
    )
    json_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    json_result = response.json()
    assert json_result["turn"]["status"] == "completed"

    replay_started = time.perf_counter()
    replay_response = session.post(
        f"{base_url}/conversations/{json_thread}/messages",
        headers=headers,
        json=json_payload,
        timeout=10,
    )
    replay_ms = (time.perf_counter() - replay_started) * 1000
    replay_response.raise_for_status()
    replay_result = replay_response.json()
    assert replay_result["turn"]["replayed"] is True
    assert _messages(session, base_url, headers, json_thread) == 2

    sse_thread = _thread(session, base_url, headers, "Stage 4 SSE canary")
    sse_turn = str(uuid.uuid4())
    sse_started = time.perf_counter()
    sse_response = session.post(
        f"{base_url}/conversations/{sse_thread}/messages/stream",
        headers=headers,
        json={
            "message": "What did residents say about displacement in meetings?",
            "client_turn_id": sse_turn,
        },
        stream=True,
        timeout=30,
    )
    sse_response.raise_for_status()
    first_delta_ms = None
    terminal = None
    for line in sse_response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        event = json.loads(line[5:].strip())
        if event.get("type") == "delta" and first_delta_ms is None:
            first_delta_ms = (time.perf_counter() - sse_started) * 1000
        if event.get("type") in {"final", "error"}:
            terminal = event
    sse_ms = (time.perf_counter() - sse_started) * 1000
    assert terminal is not None and terminal.get("type") == "final"
    assert terminal.get("turn", {}).get("status") == "completed"
    assert terminal.get("mode") == "rag"
    assert _messages(session, base_url, headers, sse_thread) == 2

    budget_limit_ms = int(TURN_TOTAL_BUDGET_MS) + int(TURN_TIMEOUT_GRACE_MS)
    assert json_ms <= budget_limit_ms
    assert sse_ms <= budget_limit_ms
    return {
        "status": "pass",
        "environment": {
            "api": base_url,
            "mysql_host": Config.MYSQL_HOST,
            "mysql_database": Config.MYSQL_DB,
            "production_mode": Config.PRODUCTION_MODE,
        },
        "json": {
            "status": response.status_code,
            "latency_ms": round(json_ms, 3),
            "mode": json_result["assistant_message"].get("response_mode"),
            "answer_chars": len(json_result["assistant_message"].get("content") or ""),
            "sources": len(json_result["assistant_message"].get("sources") or []),
        },
        "sse": {
            "status": sse_response.status_code,
            "latency_ms": round(sse_ms, 3),
            "first_delta_ms": round(first_delta_ms, 3) if first_delta_ms is not None else None,
            "mode": terminal.get("mode"),
            "answer_chars": len(terminal.get("answer") or ""),
            "sources": len(terminal.get("sources") or []),
        },
        "replay": {
            "latency_ms": round(replay_ms, 3),
            "replayed": True,
            "expensive_work_bypassed": True,
        },
        "budget_limit_ms": budget_limit_ms,
        "message_pairs_duplicated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8894")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "stage4" / "live_canary.json"
    )
    args = parser.parse_args()
    result = run(args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
