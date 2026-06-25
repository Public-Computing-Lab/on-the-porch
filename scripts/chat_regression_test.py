#!/usr/bin/env python3
"""
20-question chat regression harness for On The Porch.

Mirrors the browser guest flow:
  1. POST /auth/guest
  2. POST /conversations  (new thread per question — isolated first-turn path)
  3. POST /conversations/<id>/messages/stream  (SSE, same as public/api.js)

Measures latency (first token + total) and runs lightweight quality checks.
Does NOT call Gemini or the orchestrator directly — only the live HTTP API.

Usage (API must be running):
    python scripts/chat_regression_test.py

Environment:
    OTP_API_BASE_URL     default http://127.0.0.1:8888
    OTP_USE_STREAMING    default 1 (set 0 for non-streaming POST)
    OTP_REQUEST_TIMEOUT  default 120 (seconds per question)
    OTP_LATENCY_P95_MS   default 30000 (warn threshold for total latency)
    OTP_CASE_LIMIT       optional — run only first N questions (e.g. 3 for smoke)
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    print("Install requests: pip install requests", file=sys.stderr)
    raise

BASE_URL = os.getenv("OTP_API_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8888")).rstrip("/")
USE_STREAMING = os.getenv("OTP_USE_STREAMING", "1").strip().lower() in ("1", "true", "yes")
REQUEST_TIMEOUT = int(os.getenv("OTP_REQUEST_TIMEOUT", "120"))
LATENCY_WARN_MS = int(os.getenv("OTP_LATENCY_P95_MS", "30000"))
CASE_LIMIT = int(os.getenv("OTP_CASE_LIMIT", "0"))  # 0 = all 20

FAILURE_PHRASES = (
    "i couldn't find an answer",
    "i'm having trouble reaching the ai",
    "failed to send message",
    "generation failed",
    "please try again in a moment",
)


@dataclass
class QuestionCase:
    id: str
    question: str
    category: str
    expect_mode: Optional[str] = None  # sql | rag | hybrid | chitchat
    accept_modes: Optional[List[str]] = None  # if set, overrides expect_mode
    min_length: int = 40
    any_of: List[str] = field(default_factory=list)
    max_latency_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# 20 civic regression questions (edit expectations as the product evolves)
# ---------------------------------------------------------------------------
REGRESSION_CASES: List[QuestionCase] = [
    QuestionCase(
        "events_week",
        "What events are happening in Dorchester this week?",
        "events",
        expect_mode="sql",
        any_of=["event", "week", "calendar", "happening", "no upcoming", "didn't find"],
        max_latency_ms=25000,
    ),
    QuestionCase(
        "events_csndc",
        "What CSNDC events are coming up this month?",
        "events",
        accept_modes=["sql", "rag"],
        any_of=["csndc", "event", "codman", "no upcoming", "didn't find", "couldn't find"],
        max_latency_ms=45000,
    ),
    QuestionCase(
        "311_count",
        "How many 311 service requests were filed in Dorchester this week?",
        "311",
        expect_mode="sql",
        any_of=["311", "request", "service", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
    ),
    QuestionCase(
        "311_top",
        "What are the most common 311 issue types in Dorchester last month?",
        "311",
        expect_mode="sql",
        any_of=["311", "issue", "type", "top", "common", "didn't find", "no "],
    ),
    QuestionCase(
        "crime_count",
        "How many crime incidents were reported in Dorchester in the last 30 days?",
        "crime",
        expect_mode="sql",
        any_of=["crime", "incident", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
    ),
    QuestionCase(
        "crime_breakdown",
        "Show me a breakdown of crime types in Dorchester last month.",
        "crime",
        expect_mode="sql",
        any_of=["crime", "offense", "incident", "type", "didn't find"],
    ),
    QuestionCase(
        "neighborhood_pulse",
        "What's going on in Dorchester lately?",
        "news",
        expect_mode="hybrid",
        min_length=60,
        any_of=["dorchester", "311", "news", "community", "recent", "service"],
        max_latency_ms=35000,
    ),
    QuestionCase(
        "news_rss",
        "Any recent news from the Dorchester Reporter?",
        "news",
        expect_mode="rag",
        any_of=["dorchester", "news", "reporter", "article", "didn't find", "no relevant"],
    ),
    QuestionCase(
        "policy_slow_streets",
        "What does the Boston Slow Streets plan aim to achieve?",
        "policy",
        expect_mode="rag",
        any_of=["slow street", "traffic", "safety", "neighborhood", "speed", "plan"],
    ),
    QuestionCase(
        "policy_displacement",
        "What strategies does the Boston Anti-Displacement Plan propose?",
        "policy",
        expect_mode="rag",
        any_of=["displacement", "housing", "anti-displacement", "resident", "plan"],
    ),
    QuestionCase(
        "opinion_safety",
        "What do residents say about safety in Dorchester?",
        "opinion",
        expect_mode="hybrid",
        min_length=50,
        any_of=["safety", "resident", "community", "crime", "feel", "say", "concern"],
        max_latency_ms=35000,
    ),
    QuestionCase(
        "crime_homicides",
        "How many homicides were reported in Dorchester this year?",
        "crime",
        expect_mode="sql",
        any_of=["homicide", "crime", "incident", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
    ),
    QuestionCase(
        "311_recent",
        "List recent 311 requests in Dorchester.",
        "311",
        expect_mode="sql",
        any_of=["311", "request", "recent", "street", "open", "didn't find", "no recent"],
    ),
    QuestionCase(
        "events_weekend",
        "What's happening in Dorchester this weekend?",
        "events",
        expect_mode="sql",
        any_of=["weekend", "event", "saturday", "sunday", "happening", "no upcoming"],
    ),
    QuestionCase(
        "policy_imagine",
        "What are the main goals of Imagine Boston 2030?",
        "policy",
        expect_mode="rag",
        any_of=["imagine boston", "2030", "goal", "growth", "housing", "plan"],
    ),
    QuestionCase(
        "chitchat_hi",
        "Hello!",
        "chitchat",
        expect_mode="chitchat",
        min_length=5,
        any_of=["hello", "hi", "help", "porch", "dorchester"],
        max_latency_ms=3000,
    ),
    QuestionCase(
        "crime_hybrid",
        "How many shootings were there in Dorchester last month and what concerns do residents raise?",
        "hybrid",
        expect_mode="hybrid",
        min_length=80,
        any_of=["shoot", "crime", "resident", "concern", "safety", "incident"],
        max_latency_ms=40000,
    ),
    QuestionCase(
        "transcript_displacement",
        "What do meeting transcripts say about displacement in Dorchester?",
        "opinion",
        expect_mode="rag",
        any_of=["displacement", "resident", "community", "housing", "transcript", "meeting"],
    ),
    QuestionCase(
        "311_open",
        "Show me open 311 cases in Dorchester from the past week.",
        "311",
        expect_mode="sql",
        any_of=["311", "open", "request", "case", "didn't find", "no recent"],
    ),
    QuestionCase(
        "chitchat_thanks",
        "Thanks, that's helpful!",
        "chitchat",
        expect_mode="chitchat",
        min_length=3,
        any_of=["welcome", "glad", "thank", "anytime", "help"],
        max_latency_ms=3000,
    ),
]


@dataclass
class ChatResult:
    answer: str
    mode: str
    latency_first_ms: Optional[int]
    latency_total_ms: int
    had_correction: bool
    http_status: int
    error: Optional[str] = None
    sources: List[Dict[str, Any]] = field(default_factory=list)


class GuestClient:
    """Minimal guest session client matching public/api.js."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_token: Optional[str] = None
        self.csrf_token: Optional[str] = None
        self._session = requests.Session()

    def bootstrap(self) -> None:
        resp = self._session.post(
            f"{self.base_url}/auth/guest",
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        self.session_token = data["session_token"]
        self.csrf_token = data["csrf_token"]

    def _headers(self) -> Dict[str, str]:
        if not self.session_token or not self.csrf_token:
            raise RuntimeError("Guest client not bootstrapped")
        return {
            "Authorization": f"Bearer {self.session_token}",
            "X-CSRF-Token": self.csrf_token,
            "Content-Type": "application/json",
        }

    def create_thread(self, title: str) -> str:
        resp = self._session.post(
            f"{self.base_url}/conversations",
            headers=self._headers(),
            json={"title": title},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["thread"]["id"]

    def send_stream(self, thread_id: str, message: str) -> ChatResult:
        started = time.perf_counter()
        first_token_at: Optional[float] = None
        answer_parts: List[str] = []
        final_answer = ""
        mode = ""
        sources: List[Dict[str, Any]] = []
        had_correction = False

        resp = self._session.post(
            f"{self.base_url}/conversations/{thread_id}/messages/stream",
            headers=self._headers(),
            json={"message": message},
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            try:
                err = resp.json()
                msg = err.get("error") or err.get("message") or str(err)
            except Exception:
                msg = resp.text[:200]
            return ChatResult(
                answer="",
                mode="",
                latency_first_ms=None,
                latency_total_ms=int((time.perf_counter() - started) * 1000),
                had_correction=False,
                http_status=resp.status_code,
                error=msg,
            )

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            payload = raw_line[5:].strip()
            if not payload:
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "delta":
                text = evt.get("text") or ""
                if text and first_token_at is None:
                    first_token_at = time.perf_counter()
                answer_parts.append(text)
            elif etype == "correction":
                had_correction = True
                final_answer = evt.get("text") or final_answer
            elif etype == "final":
                final_answer = evt.get("answer") or "".join(answer_parts)
                mode = evt.get("mode") or ""
                sources = evt.get("sources") or []
            elif etype == "error":
                return ChatResult(
                    answer="".join(answer_parts),
                    mode=mode,
                    latency_first_ms=_ms(started, first_token_at),
                    latency_total_ms=int((time.perf_counter() - started) * 1000),
                    had_correction=had_correction,
                    http_status=200,
                    error=evt.get("error") or "stream error",
                )

        if not final_answer:
            final_answer = "".join(answer_parts)

        return ChatResult(
            answer=final_answer.strip(),
            mode=mode,
            latency_first_ms=_ms(started, first_token_at),
            latency_total_ms=int((time.perf_counter() - started) * 1000),
            had_correction=had_correction,
            http_status=200,
            sources=sources,
        )

    def send_blocking(self, thread_id: str, message: str) -> ChatResult:
        started = time.perf_counter()
        resp = self._session.post(
            f"{self.base_url}/conversations/{thread_id}/messages",
            headers=self._headers(),
            json={"message": message},
            timeout=REQUEST_TIMEOUT,
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 201:
            try:
                err = resp.json()
                msg = err.get("error") or err.get("message") or str(err)
            except Exception:
                msg = resp.text[:200]
            return ChatResult(
                answer="",
                mode="",
                latency_first_ms=None,
                latency_total_ms=total_ms,
                had_correction=False,
                http_status=resp.status_code,
                error=msg,
            )
        data = resp.json()
        assistant = data.get("assistant_message") or {}
        return ChatResult(
            answer=(assistant.get("content") or "").strip(),
            mode=assistant.get("response_mode") or "",
            latency_first_ms=total_ms,
            latency_total_ms=total_ms,
            had_correction=False,
            http_status=201,
            sources=assistant.get("sources") or [],
        )


def _ms(start: float, point: Optional[float]) -> Optional[int]:
    if point is None:
        return None
    return int((point - start) * 1000)


@dataclass
class QualityVerdict:
    passed: bool
    checks: List[str] = field(default_factory=list)


def evaluate_quality(case: QuestionCase, result: ChatResult) -> QualityVerdict:
    checks: List[str] = []
    ok = True

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        checks.append(f"FAIL: {msg}")

    def pass_(msg: str) -> None:
        checks.append(f"OK: {msg}")

    if result.error:
        fail(f"HTTP/stream error: {result.error}")
        return QualityVerdict(False, checks)

    if result.http_status not in (200, 201):
        fail(f"unexpected status {result.http_status}")
        return QualityVerdict(False, checks)

    answer_lower = result.answer.lower()
    if len(result.answer) < case.min_length:
        fail(f"answer too short ({len(result.answer)} < {case.min_length})")
    else:
        pass_(f"length {len(result.answer)} >= {case.min_length}")

    for phrase in FAILURE_PHRASES:
        if phrase in answer_lower:
            fail(f"contains failure phrase {phrase!r}")
            break
    else:
        pass_("no generic failure phrase")

    if case.accept_modes:
        if not result.mode:
            fail("missing response mode")
        elif result.mode not in case.accept_modes:
            fail(f"mode {result.mode!r} not in {case.accept_modes}")
        else:
            pass_(f"mode {result.mode}")
    elif case.expect_mode:
        if not result.mode:
            fail("missing response mode")
        elif result.mode != case.expect_mode:
            fail(f"mode {result.mode!r} != expected {case.expect_mode!r}")
        else:
            pass_(f"mode {result.mode}")

    if case.any_of:
        if any(term.lower() in answer_lower for term in case.any_of):
            pass_("matched expected content keyword")
        else:
            fail(f"none of expected keywords found: {case.any_of[:5]}...")

    limit = case.max_latency_ms or LATENCY_WARN_MS
    if result.latency_total_ms > limit:
        fail(f"latency {result.latency_total_ms}ms > {limit}ms")
    else:
        pass_(f"latency {result.latency_total_ms}ms <= {limit}ms")

    return QualityVerdict(ok, checks)


def check_health() -> bool:
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"API health: {data.get('status')} ({BASE_URL})")
        return data.get("status") in ("ok", "degraded")
    except Exception as exc:
        print(f"Health check failed: {exc}")
        return False


def run_regression() -> int:
    print("=" * 72)
    print("On The Porch — 20-question chat regression")
    print(f"API: {BASE_URL}  streaming={USE_STREAMING}  timeout={REQUEST_TIMEOUT}s")
    print("=" * 72)

    if not check_health():
        print("\nStart the API first: ./start_api.sh")
        return 1

    client = GuestClient(BASE_URL)
    try:
        client.bootstrap()
    except Exception as exc:
        print(f"Guest bootstrap failed: {exc}")
        return 1

    cases = REGRESSION_CASES
    if CASE_LIMIT > 0:
        cases = REGRESSION_CASES[:CASE_LIMIT]
        print(f"Running first {len(cases)} of {len(REGRESSION_CASES)} cases (OTP_CASE_LIMIT)")

    results: List[Dict[str, Any]] = []
    passed = 0

    for idx, case in enumerate(cases, 1):
        print(f"\n[{idx:02d}/{len(cases)}] {case.id} ({case.category})")
        print(f"  Q: {case.question}")

        thread_id = client.create_thread(title=f"regression-{case.id}-{uuid.uuid4().hex[:6]}")
        if USE_STREAMING:
            chat = client.send_stream(thread_id, case.question)
        else:
            chat = client.send_blocking(thread_id, case.question)

        verdict = evaluate_quality(case, chat)
        if verdict.passed:
            passed += 1
            status = "PASS"
        else:
            status = "FAIL"

        ttfb = chat.latency_first_ms if chat.latency_first_ms is not None else "-"
        print(f"  {status} | mode={chat.mode or '?'} | ttfb={ttfb}ms | total={chat.latency_total_ms}ms"
              + (" | correction" if chat.had_correction else ""))
        preview = chat.answer.replace("\n", " ")[:140]
        print(f"  A: {preview}{'…' if len(chat.answer) > 140 else ''}")
        for check in verdict.checks:
            if check.startswith("FAIL"):
                print(f"    {check}")

        results.append(
            {
                "id": case.id,
                "category": case.category,
                "question": case.question,
                "passed": verdict.passed,
                "mode": chat.mode,
                "expect_mode": case.expect_mode,
                "latency_first_ms": chat.latency_first_ms,
                "latency_total_ms": chat.latency_total_ms,
                "had_correction": chat.had_correction,
                "answer_preview": preview,
                "checks": verdict.checks,
            }
        )

        # Brief pause to avoid guest/chat rate limits on local runs
        time.sleep(0.5)

    print("\n" + "=" * 72)
    print(f"SUMMARY: {passed}/{len(cases)} passed")

    latencies = [r["latency_total_ms"] for r in results if r["latency_total_ms"]]
    if latencies:
        latencies_sorted = sorted(latencies)
        p50 = latencies_sorted[len(latencies_sorted) // 2]
        p95_idx = max(0, round(0.95 * (len(latencies_sorted) - 1)))
        p95 = latencies_sorted[p95_idx]
        print(f"Latency total — p50: {p50}ms  p95: {p95}ms  max: {max(latencies)}ms")

    failed = [r["id"] for r in results if not r["passed"]]
    if failed:
        print(f"Failed cases: {', '.join(failed)}")
        return 1

    print("All regression checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_regression())
