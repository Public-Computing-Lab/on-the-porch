#!/usr/bin/env python3
"""Local production-simulation verifier for the stabilization branch.

The API must already be running. This script drives the real public guest +
conversation + SSE path and records latency, errors, response modes, and API
process RSS.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import requests
except ImportError:
    print("Install requests first: pip install requests", file=sys.stderr)
    raise


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = os.getenv("OTP_API_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8888")).rstrip("/")
DEFAULT_HTTP_TIMEOUT = float(os.getenv("OTP_PROD_SIM_HTTP_TIMEOUT", "60"))
DEFAULT_MAX_LATENCY = float(os.getenv("OTP_PROD_SIM_MAX_LATENCY", "40"))
DEFAULT_MEMORY_GROWTH_MB = float(os.getenv("OTP_MEMORY_GROWTH_FAIL_MB", "300"))
DEFAULT_MEMORY_WARMUP_SECONDS = float(os.getenv("OTP_MEMORY_WARMUP_SECONDS", "30"))


@dataclass(frozen=True)
class QueryCase:
    id: str
    question: str
    category: str
    expect_modes: tuple[str, ...]


@dataclass
class ChatResult:
    user: str
    case_id: str
    category: str
    question: str
    mode: str = ""
    http_status: int = 0
    latency_ms: int = 0
    first_token_ms: Optional[int] = None
    answer_chars: int = 0
    error: str = ""
    timed_out: bool = False
    failure_reason: str = ""

    @property
    def ok_status(self) -> bool:
        return self.http_status in (200, 201)


@dataclass
class MemorySample:
    elapsed_s: float
    rss_mb: Optional[float]


SMOKE_CASES = [
    QueryCase("prepared_311", "How many 311 requests were filed last week?", "sql", ("sql",)),
    QueryCase("rag_meetings", "Tell me about meetings in Dorchester", "rag", ("rag",)),
    QueryCase("hybrid_safety", "Is Dorchester safe right now?", "hybrid", ("hybrid",)),
]

USER_A_CASES = [
    QueryCase("safety_now", "Is Dorchester safe right now?", "hybrid", ("hybrid",)),
    QueryCase("codman_concerns", "What are residents concerned about near Codman Square?", "rag", ("rag",)),
    QueryCase("pothole_residents", "What do residents say about potholes?", "hybrid", ("hybrid", "rag")),
    QueryCase(
        "shootings_and_concerns",
        "How many shootings were there last month and what concerns do residents raise?",
        "hybrid",
        ("hybrid",),
    ),
    QueryCase("meetings", "Tell me about meetings in Dorchester", "rag", ("rag",)),
]

USER_B_CASES = [
    QueryCase("events_weekend", "What events are happening this weekend?", "sql", ("sql",)),
    QueryCase("311_week", "How many 311 requests were filed last week?", "sql", ("sql",)),
    QueryCase("311_types", "What are the most common 311 issue types last month?", "sql", ("sql",)),
    QueryCase("crime_count", "How many crime incidents were reported in Dorchester last month?", "sql", ("sql",)),
    QueryCase("events_dorchester", "Tell me about events in Dorchester this week", "sql", ("sql",)),
]

BURST_CASES = USER_A_CASES + USER_B_CASES + [
    QueryCase("news_recent", "Any recent news from the Dorchester Reporter?", "rag", ("rag",)),
    QueryCase("policy_slow_streets", "What does the Boston Slow Streets plan say?", "rag", ("rag",)),
]


class GuestClient:
    """Minimal public guest-session client."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        streaming: bool = True,
        synthetic_ip: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.streaming = streaming
        self.synthetic_ip = synthetic_ip
        self.session = requests.Session()
        self.session_token: Optional[str] = None
        self.csrf_token: Optional[str] = None

    def base_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.synthetic_ip:
            headers["X-Forwarded-For"] = self.synthetic_ip
        return headers

    def bootstrap(self) -> None:
        resp = self.session.post(
            f"{self.base_url}/auth/guest",
            headers=self.base_headers(),
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        self.session_token = data["session_token"]
        self.csrf_token = data.get("csrf_token", "")

    def headers(self) -> Dict[str, str]:
        if not self.session_token:
            raise RuntimeError("guest client not bootstrapped")
        headers = {
            **self.base_headers(),
            "Authorization": f"Bearer {self.session_token}",
            "Content-Type": "application/json",
        }
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        return headers

    def create_thread(self, title: str) -> str:
        resp = self.session.post(
            f"{self.base_url}/conversations",
            headers=self.headers(),
            json={"title": title},
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        return resp.json()["thread"]["id"]

    def send(self, thread_id: str, case: QueryCase, user: str) -> ChatResult:
        if self.streaming:
            return self._send_stream(thread_id, case, user)
        return self._send_blocking(thread_id, case, user)

    def _send_stream(self, thread_id: str, case: QueryCase, user: str) -> ChatResult:
        started = time.perf_counter()
        first_token_at: Optional[float] = None
        answer_parts: List[str] = []
        mode = ""
        final_answer = ""
        try:
            resp = self.session.post(
                f"{self.base_url}/conversations/{thread_id}/messages/stream",
                headers=self.headers(),
                json={"message": case.question},
                stream=True,
                timeout=self.timeout_s,
            )
            if resp.status_code != 200:
                return self._error_result(user, case, started, resp.status_code, _response_error(resp))
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                payload = raw_line[5:].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "delta":
                    text = event.get("text") or ""
                    if text and first_token_at is None:
                        first_token_at = time.perf_counter()
                    answer_parts.append(text)
                elif event_type == "final":
                    final_answer = event.get("answer") or "".join(answer_parts)
                    mode = event.get("mode") or ""
                elif event_type == "correction":
                    final_answer = event.get("text") or final_answer
                elif event_type == "error":
                    return self._error_result(
                        user,
                        case,
                        started,
                        200,
                        event.get("error") or "stream error",
                        first_token_at,
                        "".join(answer_parts),
                        mode,
                    )
            answer = (final_answer or "".join(answer_parts)).strip()
            return ChatResult(
                user=user,
                case_id=case.id,
                category=case.category,
                question=case.question,
                mode=mode,
                http_status=200,
                latency_ms=_elapsed_ms(started),
                first_token_ms=_point_ms(started, first_token_at),
                answer_chars=len(answer),
            )
        except requests.Timeout as exc:
            return self._error_result(user, case, started, 0, str(exc), timed_out=True)
        except Exception as exc:  # noqa: BLE001
            return self._error_result(user, case, started, 0, f"{exc.__class__.__name__}: {exc}")

    def _send_blocking(self, thread_id: str, case: QueryCase, user: str) -> ChatResult:
        started = time.perf_counter()
        try:
            resp = self.session.post(
                f"{self.base_url}/conversations/{thread_id}/messages",
                headers=self.headers(),
                json={"message": case.question},
                timeout=self.timeout_s,
            )
            if resp.status_code != 201:
                return self._error_result(user, case, started, resp.status_code, _response_error(resp))
            data = resp.json()
            assistant = data.get("assistant_message") or {}
            answer = assistant.get("content") or ""
            return ChatResult(
                user=user,
                case_id=case.id,
                category=case.category,
                question=case.question,
                mode=assistant.get("response_mode") or "",
                http_status=201,
                latency_ms=_elapsed_ms(started),
                first_token_ms=_elapsed_ms(started),
                answer_chars=len(answer),
            )
        except requests.Timeout as exc:
            return self._error_result(user, case, started, 0, str(exc), timed_out=True)
        except Exception as exc:  # noqa: BLE001
            return self._error_result(user, case, started, 0, f"{exc.__class__.__name__}: {exc}")

    def _error_result(
        self,
        user: str,
        case: QueryCase,
        started: float,
        status: int,
        error: str,
        first_token_at: Optional[float] = None,
        answer: str = "",
        mode: str = "",
        timed_out: bool = False,
    ) -> ChatResult:
        return ChatResult(
            user=user,
            case_id=case.id,
            category=case.category,
            question=case.question,
            mode=mode,
            http_status=status,
            latency_ms=_elapsed_ms(started),
            first_token_ms=_point_ms(started, first_token_at),
            answer_chars=len(answer),
            error=error,
            timed_out=timed_out,
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _point_ms(started: float, point: Optional[float]) -> Optional[int]:
    if point is None:
        return None
    return int((point - started) * 1000)


def _response_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return str(data.get("error") or data.get("message") or data)
    except Exception:
        return resp.text[:300]


def check_health(base_url: str) -> Dict[str, Any]:
    started = time.perf_counter()
    resp = requests.get(f"{base_url.rstrip()}/health", timeout=10)
    latency_ms = _elapsed_ms(started)
    resp.raise_for_status()
    data = resp.json()
    data["_latency_ms"] = latency_ms
    return data


def api_rss_mb() -> Optional[float]:
    """Return total RSS for local API/gunicorn processes, in MB."""
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,rss=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    total_kb = 0
    own_pid = os.getpid()
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            rss_kb = int(parts[1])
        except ValueError:
            continue
        if pid == own_pid:
            continue
        command = parts[2].lower()
        is_api = (
            ("gunicorn" in command and "api_v2:app" in command)
            or "api/api_v2.py" in command
            or ("api_v2.py" in command and "python" in command)
        )
        if is_api:
            total_kb += rss_kb
    if total_kb <= 0:
        return None
    return total_kb / 1024.0


def start_memory_sampler(
    samples: List[MemorySample],
    stop_event: threading.Event,
    *,
    interval_s: float,
) -> threading.Thread:
    started = time.perf_counter()

    def _sample() -> None:
        while not stop_event.is_set():
            samples.append(MemorySample(time.perf_counter() - started, api_rss_mb()))
            stop_event.wait(interval_s)
        samples.append(MemorySample(time.perf_counter() - started, api_rss_mb()))

    thread = threading.Thread(target=_sample, daemon=True)
    thread.start()
    return thread


def percentile(values: List[int], pct: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return int(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def memory_growth_failed(samples: List[MemorySample], threshold_mb: float) -> bool:
    rss = [sample.rss_mb for sample in samples if sample.rss_mb is not None]
    if len(rss) < 4:
        return False
    growth = rss[-1] - rss[0]
    increases = sum(1 for before, after in zip(rss, rss[1:]) if after >= before)
    ratio = increases / max(1, len(rss) - 1)
    return growth > threshold_mb and ratio >= 0.8


def warm_memory_samples(
    samples: List[MemorySample],
    warmup_seconds: float,
) -> List[MemorySample]:
    warm = [sample for sample in samples if sample.elapsed_s >= warmup_seconds]
    return warm or samples


def failure_kind(result: ChatResult) -> str:
    if result.http_status == 429:
        return "rate_limit"
    if result.timed_out:
        return "timeout"
    if result.http_status == 0:
        return "transport_error"
    return "application_error"


def exception_status(exc: Exception) -> int:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", 0)
    try:
        return int(status_code or 0)
    except (TypeError, ValueError):
        return 0


def exception_failure_kind(exc: Exception) -> str:
    status = exception_status(exc)
    if status == 429:
        return "rate_limit"
    if status:
        return "application_error"
    return "transport_error"


def result_failed(result: ChatResult, case: QueryCase, max_latency_s: float) -> Optional[str]:
    if result.http_status == 429:
        return "rate-limit rejection"
    if result.timed_out:
        return "request timeout"
    if result.error:
        return result.error
    if not result.ok_status:
        return f"bad status {result.http_status}"
    if result.latency_ms > int(max_latency_s * 1000):
        return f"latency {result.latency_ms / 1000:.1f}s > {max_latency_s:.1f}s"
    if result.mode and case.expect_modes and result.mode not in case.expect_modes:
        return f"mode {result.mode!r} not in {case.expect_modes}"
    if not result.mode:
        return "missing response mode"
    return None


def print_summary(
    name: str,
    results: List[ChatResult],
    failures: List[Dict[str, Any]],
    memory_samples: List[MemorySample],
    *,
    max_latency_s: float,
    fail_memory_growth: bool = True,
    memory_growth_threshold_mb: float = DEFAULT_MEMORY_GROWTH_MB,
    memory_warmup_seconds: float = DEFAULT_MEMORY_WARMUP_SECONDS,
) -> int:
    successful_results = [r for r in results if not r.failure_reason]
    latencies = [r.latency_ms for r in successful_results]
    warm_samples = warm_memory_samples(memory_samples, memory_warmup_seconds)
    rss_values = [s.rss_mb for s in warm_samples if s.rss_mb is not None]
    mode_counts = Counter(r.mode or "(missing)" for r in successful_results)
    failure_counts = Counter(item.get("kind") or "application_error" for item in failures)
    timeout_count = failure_counts["timeout"]
    rate_limit_count = failure_counts["rate_limit"]
    transport_error_count = failure_counts["transport_error"]
    application_error_count = failure_counts["application_error"]
    status_error_count = sum(1 for r in results if (not r.ok_status or r.error) and r.http_status != 429)
    over_budget_count = sum(1 for r in results if r.latency_ms > int(max_latency_s * 1000))
    memory_failed = fail_memory_growth and memory_growth_failed(warm_samples, memory_growth_threshold_mb)
    success_count = len(successful_results)

    summary = {
        "test": name,
        "total_requests": len(results),
        "success_count": success_count,
        "application_error_count": application_error_count,
        "rate_limit_count": rate_limit_count,
        "timeout_count": timeout_count,
        "transport_error_count": transport_error_count,
        "error_count": len(failures),
        "status_or_server_error_count": status_error_count,
        "over_latency_budget_count": over_budget_count,
        "successful_latency_ms": {
            "p50": percentile(latencies, 50),
            "p90": percentile(latencies, 90),
            "p99": percentile(latencies, 99),
            "max": max(latencies) if latencies else None,
        },
        "memory_rss_mb": {
            "warmup_seconds": memory_warmup_seconds,
            "min": round(min(rss_values), 1) if rss_values else None,
            "max": round(max(rss_values), 1) if rss_values else None,
            "growth": round(rss_values[-1] - rss_values[0], 1) if len(rss_values) >= 2 else None,
            "continuous_growth_failed": memory_failed,
        },
        "successful_route_distribution": dict(sorted(mode_counts.items())),
        "failed_query_examples": failures[:10],
    }

    print("\n" + "=" * 72)
    print(f"{name} summary")
    print("=" * 72)
    print(f"total requests: {summary['total_requests']}")
    print(f"success count:  {summary['success_count']}")
    print(f"application errors: {summary['application_error_count']}")
    print(f"rate limits:        {summary['rate_limit_count']}")
    print(f"timeout count:  {summary['timeout_count']}")
    print(f"transport errors:   {summary['transport_error_count']}")
    print(f"successful latency p50/p90/p99/max ms: {summary['successful_latency_ms']}")
    print(f"warm steady-state RSS min/max/growth MB: {summary['memory_rss_mb']}")
    print(f"successful route distribution: {summary['successful_route_distribution']}")
    if failures:
        print("failed query examples:")
        for item in failures[:10]:
            print(f"  - {item['user']} {item['case_id']}: {item['reason']}")
    print("\nJSON summary:")
    print(json.dumps(summary, indent=2, sort_keys=True))

    return 1 if failures or memory_failed else 0


def run_case_once(
    client: GuestClient,
    thread_id: str,
    case: QueryCase,
    user: str,
    *,
    max_latency_s: float,
) -> tuple[ChatResult, Optional[Dict[str, Any]]]:
    result = client.send(thread_id, case, user)
    reason = result_failed(result, case, max_latency_s)
    failure = None
    if reason:
        result.failure_reason = reason
        failure = {
            "user": user,
            "case_id": case.id,
            "category": case.category,
            "question": case.question,
            "reason": reason,
            "kind": failure_kind(result),
            "latency_ms": result.latency_ms,
            "mode": result.mode,
            "http_status": result.http_status,
            "error": result.error,
            "timed_out": result.timed_out,
        }
    return result, failure


def run_smoke(args: argparse.Namespace) -> int:
    memory_samples: List[MemorySample] = []
    stop_memory = threading.Event()
    sampler = start_memory_sampler(memory_samples, stop_memory, interval_s=args.memory_interval)
    failures: List[Dict[str, Any]] = []
    results: List[ChatResult] = []

    try:
        try:
            health = check_health(args.base_url)
            print(f"health status: {health.get('status')} latency={health.get('_latency_ms')}ms")
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "user": "health",
                "case_id": "health",
                "category": "health",
                "question": "GET /health",
                "reason": f"health request failed: {exc}",
                "kind": exception_failure_kind(exc),
                "latency_ms": 0,
                "mode": "",
                "http_status": exception_status(exc),
                "error": str(exc),
            })
            return print_summary(
                "local smoke",
                results,
                failures,
                memory_samples,
                max_latency_s=args.max_latency,
                memory_warmup_seconds=args.memory_warmup_seconds,
            )

        client = GuestClient(args.base_url, args.http_timeout, streaming=args.streaming)
        client.bootstrap()
        for case in SMOKE_CASES:
            thread_id = client.create_thread(f"local-smoke-{case.id}-{uuid.uuid4().hex[:6]}")
            result, failure = run_case_once(client, thread_id, case, "smoke", max_latency_s=args.max_latency)
            results.append(result)
            print(
                f"{case.id}: status={result.http_status} mode={result.mode} "
                f"latency={result.latency_ms}ms error={result.error or '-'}"
            )
            if failure:
                failures.append(failure)
    finally:
        stop_memory.set()
        sampler.join(timeout=2)

    return print_summary(
        "local smoke",
        results,
        failures,
        memory_samples,
        max_latency_s=args.max_latency,
        memory_warmup_seconds=args.memory_warmup_seconds,
    )


def run_concurrent_profile(
    name: str,
    profiles: Dict[str, List[QueryCase]],
    args: argparse.Namespace,
) -> int:
    results: List[ChatResult] = []
    failures: List[Dict[str, Any]] = []
    memory_samples: List[MemorySample] = []
    results_lock = threading.Lock()
    stop_memory = threading.Event()
    stop_at = time.perf_counter() + args.duration
    sampler = start_memory_sampler(memory_samples, stop_memory, interval_s=args.memory_interval)

    def _synthetic_ip(user: str, sequence: int) -> Optional[str]:
        if not args.synthetic_client_ips:
            return None
        base = (sum(ord(ch) for ch in user) * 10000) + sequence
        second = 18 + ((base // 62500) % 2)
        third = (base // 250) % 250
        fourth = 1 + (base % 250)
        return f"198.{second}.{third}.{fourth}"

    def _bootstrap_thread(user: str, sequence: int) -> tuple[GuestClient, str]:
        client = GuestClient(
            args.base_url,
            args.http_timeout,
            streaming=args.streaming,
            synthetic_ip=_synthetic_ip(user, sequence),
        )
        client.bootstrap()
        thread_id = client.create_thread(f"{name}-{user}-{uuid.uuid4().hex[:6]}")
        return client, thread_id

    def _worker(user: str, cases: List[QueryCase], offset: int = 0) -> None:
        client: Optional[GuestClient] = None
        thread_id = ""
        if not args.fresh_session_per_request:
            try:
                client, thread_id = _bootstrap_thread(user, offset)
            except Exception as exc:  # noqa: BLE001
                with results_lock:
                    failures.append({
                        "user": user,
                        "case_id": "bootstrap",
                        "category": "auth",
                        "question": "guest bootstrap/create conversation",
                        "reason": f"{exc.__class__.__name__}: {exc}",
                        "kind": exception_failure_kind(exc),
                        "latency_ms": 0,
                        "mode": "",
                        "http_status": exception_status(exc),
                        "error": str(exc),
                    })
                return

        index = offset
        while time.perf_counter() < stop_at:
            case = cases[index % len(cases)]
            request_client = client
            request_thread_id = thread_id
            if args.fresh_session_per_request:
                try:
                    request_client, request_thread_id = _bootstrap_thread(user, index)
                except Exception as exc:  # noqa: BLE001
                    with results_lock:
                        failures.append({
                            "user": user,
                            "case_id": "bootstrap",
                            "category": "auth",
                            "question": "guest bootstrap/create conversation",
                            "reason": f"{exc.__class__.__name__}: {exc}",
                            "kind": exception_failure_kind(exc),
                            "latency_ms": 0,
                            "mode": "",
                            "http_status": exception_status(exc),
                            "error": str(exc),
                        })
                    index += 1
                    time.sleep(min(args.pause, max(0.0, stop_at - time.perf_counter())))
                    continue
            if request_client is None:
                break
            result, failure = run_case_once(
                request_client,
                request_thread_id,
                case,
                user,
                max_latency_s=args.max_latency,
            )
            with results_lock:
                results.append(result)
                if failure:
                    failures.append(failure)
            if args.fresh_session_per_request:
                request_client.session.close()
            index += 1
            remaining = stop_at - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(args.pause, remaining))

    try:
        try:
            health = check_health(args.base_url)
            print(f"health status: {health.get('status')} latency={health.get('_latency_ms')}ms")
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "user": "health",
                "case_id": "health",
                "category": "health",
                "question": "GET /health",
                "reason": f"health request failed: {exc}",
                "kind": exception_failure_kind(exc),
                "latency_ms": 0,
                "mode": "",
                "http_status": exception_status(exc),
                "error": str(exc),
            })
            return print_summary(
                name,
                results,
                failures,
                memory_samples,
                max_latency_s=args.max_latency,
                memory_warmup_seconds=args.memory_warmup_seconds,
            )

        with ThreadPoolExecutor(max_workers=len(profiles)) as executor:
            futures = [
                executor.submit(_worker, user, cases, idx)
                for idx, (user, cases) in enumerate(profiles.items())
            ]
            wait(futures)
    finally:
        stop_memory.set()
        sampler.join(timeout=2)

    return print_summary(
        name,
        results,
        failures,
        memory_samples,
        max_latency_s=args.max_latency,
        fail_memory_growth=True,
        memory_growth_threshold_mb=args.memory_growth_fail_mb,
        memory_warmup_seconds=args.memory_warmup_seconds,
    )


def run_soak(args: argparse.Namespace) -> int:
    return run_concurrent_profile(
        "local two-user soak",
        {"user_a": USER_A_CASES, "user_b": USER_B_CASES},
        args,
    )


def run_burst(args: argparse.Namespace) -> int:
    profiles = {
        f"user_{idx + 1}": BURST_CASES[idx:] + BURST_CASES[:idx]
        for idx in range(args.users)
    }
    return run_concurrent_profile("local five-user burst", profiles, args)


def frontend_timeout_ms() -> Optional[int]:
    config_path = ROOT / "public" / "config.js"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"chatTimeoutMs\s*=\s*(\d+)", text)
    if not match:
        return None
    return int(match.group(1))


def run_env_check(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    import config  # noqa: WPS433

    def _cfg(name: str, default: Any = None) -> Any:
        return getattr(config.Config, name, getattr(config, name, default))

    health: Dict[str, Any] = {}
    try:
        health = check_health(args.base_url)
    except Exception as exc:  # noqa: BLE001
        print(f"health unavailable: {exc}")

    rows = [
        ("API health", health.get("status", "unavailable")),
        ("Gemini model", health.get("gemini_model") or _cfg("GEMINI_MODEL")),
        ("Gemini fast model", health.get("gemini_fast_model") or _cfg("GEMINI_FAST_MODEL")),
        ("Dynamic SQL disabled", not _cfg("DYNAMIC_SQL_ENABLED")),
        ("Public chat vector writes disabled", not _cfg("CHAT_VECTOR_WRITES_ENABLED")),
        ("Admin auto vector ingest enabled", _cfg("ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED")),
        ("Vector DB provider", health.get("vector_db_provider") or _cfg("VECTOR_DB_PROVIDER")),
        ("Embed provider", health.get("embed_provider") or _cfg("EMBED_PROVIDER")),
        ("Frontend chat timeout ms", frontend_timeout_ms()),
        ("Request budget seconds", _cfg("REQUEST_BUDGET_SECONDS")),
        ("Gemini request timeout seconds", _cfg("GEMINI_REQUEST_TIMEOUT")),
        ("Gemini stream timeout seconds", _cfg("GEMINI_STREAM_TIMEOUT")),
        ("Gemini max attempts", _cfg("GEMINI_MAX_ATTEMPTS")),
        ("Chroma max attempts", _cfg("CHROMA_MAX_ATTEMPTS")),
        ("MySQL pool size", _cfg("MYSQL_POOL_SIZE")),
        ("Gunicorn workers", _cfg("GUNICORN_WORKERS")),
        ("Gunicorn threads", _cfg("GUNICORN_THREADS")),
        ("Gunicorn timeout seconds", _cfg("GUNICORN_TIMEOUT")),
    ]
    print("Local production-simulation environment check")
    print("=" * 60)
    for key, value in rows:
        print(f"{key:38} {value}")

    failures = []
    if _cfg("DYNAMIC_SQL_ENABLED"):
        failures.append("DYNAMIC_SQL_ENABLED should be false")
    if _cfg("CHAT_VECTOR_WRITES_ENABLED"):
        failures.append("CHAT_VECTOR_WRITES_ENABLED should be false")
    if frontend_timeout_ms() != 55000:
        failures.append("public/config.js chatTimeoutMs should be 55000")
    if int(_cfg("REQUEST_BUDGET_SECONDS")) != 30:
        failures.append("REQUEST_BUDGET_SECONDS should be 30")
    if _cfg("MYSQL_POOL_SIZE") > 6:
        failures.append("MYSQL_POOL_SIZE should be 4-6 for DreamHost simulation")

    if failures:
        print("\nChecklist failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nChecklist passed.")
    return 0


def vectordb_latest_mtime() -> Optional[float]:
    sys.path.insert(0, str(ROOT))
    import config  # noqa: WPS433

    path = Path(getattr(config.Config, "ACTIVE_VECTORDB_DIR", config.ACTIVE_VECTORDB_DIR))
    if not path.exists():
        return None
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            pass
    return latest


def run_chroma_contention(args: argparse.Namespace) -> int:
    print("Run this while an ingestion/vector write job is active if you want contention coverage.")
    before_mtime = vectordb_latest_mtime()
    args.duration = max(args.duration, 60)
    args.users = 1
    exit_code = run_concurrent_profile(
        "local chroma contention read check",
        {"rag_user": USER_A_CASES},
        args,
    )
    after_mtime = vectordb_latest_mtime()
    if before_mtime and after_mtime and after_mtime > before_mtime and not args.allow_vector_mtime_change:
        print("\nVector DB files changed during resident chat. If no ingestion was running, this is a failure.")
        return 1
    print("\nVector DB mtime before/after:", before_mtime, after_mtime)
    return exit_code


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--http-timeout", type=float, default=DEFAULT_HTTP_TIMEOUT)
    parser.add_argument("--max-latency", type=float, default=DEFAULT_MAX_LATENCY)
    parser.add_argument("--memory-interval", type=float, default=5.0)
    parser.add_argument("--memory-growth-fail-mb", type=float, default=DEFAULT_MEMORY_GROWTH_MB)
    parser.add_argument("--memory-warmup-seconds", type=float, default=DEFAULT_MEMORY_WARMUP_SECONDS)
    parser.add_argument(
        "--reuse-session",
        action="store_false",
        dest="fresh_session_per_request",
        default=True,
        help="Reuse one guest session per synthetic user instead of isolating each request.",
    )
    parser.add_argument(
        "--no-synthetic-client-ips",
        action="store_false",
        dest="synthetic_client_ips",
        default=True,
        help="Do not rotate X-Forwarded-For for local synthetic guest creation.",
    )
    parser.add_argument("--no-streaming", action="store_false", dest="streaming", default=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    smoke = sub.add_parser("smoke", help="Run health + SQL/RAG/hybrid smoke checks")
    add_common_args(smoke)

    soak = sub.add_parser("soak", help="Run two-user 10-minute production simulation")
    add_common_args(soak)
    soak.add_argument("--duration", type=float, default=600.0)
    soak.add_argument("--pause", type=float, default=1.0)

    burst = sub.add_parser("burst", help="Run five-user 2-3 minute burst simulation")
    add_common_args(burst)
    burst.add_argument("--duration", type=float, default=180.0)
    burst.add_argument("--pause", type=float, default=0.2)
    burst.add_argument("--users", type=int, default=5)

    env_check = sub.add_parser("env", help="Print local production-simulation env checklist")
    add_common_args(env_check)

    chroma = sub.add_parser("chroma", help="Run RAG reads during optional Chroma contention")
    add_common_args(chroma)
    chroma.add_argument("--duration", type=float, default=120.0)
    chroma.add_argument("--pause", type=float, default=0.5)
    chroma.add_argument("--allow-vector-mtime-change", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "smoke":
        return run_smoke(args)
    if args.command == "soak":
        return run_soak(args)
    if args.command == "burst":
        return run_burst(args)
    if args.command == "env":
        return run_env_check(args)
    if args.command == "chroma":
        return run_chroma_contention(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
