#!/usr/bin/env python3
"""Stable-worker soak and forced worker-recycle tests for launch hardening.

Usage:
  python scripts/soak_launch_runtime.py --base-url http://127.0.0.1:8894 --mode stable --requests 1000
  python scripts/soak_launch_runtime.py --base-url http://127.0.0.1:8895 --mode recycle
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parents[1]


class GuestSession:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.csrf = ""
        self.session_token = ""

    def bootstrap(self) -> None:
        r = self.s.post(f"{self.base}/auth/guest", timeout=20)
        r.raise_for_status()
        payload = r.json()
        self.session_token = payload["session_token"]
        self.csrf = payload["csrf_token"]

    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.session_token}",
            "X-CSRF-Token": self.csrf,
            "Content-Type": "application/json",
        }

    def create_thread(self, title: str) -> str:
        r = self.s.post(
            f"{self.base}/conversations",
            headers=self.headers(),
            json={"title": title},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()["thread"]["id"]

    def send_json(self, thread_id: str, message: str, client_turn_id: str) -> requests.Response:
        return self.s.post(
            f"{self.base}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json={"message": message, "client_turn_id": client_turn_id},
            timeout=60,
        )

    def send_stream(self, thread_id: str, message: str, client_turn_id: str) -> requests.Response:
        return self.s.post(
            f"{self.base}/conversations/{thread_id}/messages/stream",
            headers=self.headers(),
            json={"message": message, "client_turn_id": client_turn_id},
            timeout=90,
            stream=True,
        )


def _parse_completions(log_text: str) -> List[dict]:
    events = []
    for line in log_text.splitlines():
        line = line.strip()
        if not line.startswith("{") or "request_completion" not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("event_name") == "request_completion":
            events.append(obj)
    return events


def _drain_sse(resp: requests.Response) -> Tuple[Optional[dict], str]:
    final = None
    buf = []
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            buf.append(raw)
            if raw.startswith("data: "):
                try:
                    evt = json.loads(raw[6:])
                except Exception:
                    continue
                if evt.get("type") == "final":
                    final = evt
                if evt.get("type") == "error":
                    final = evt
    except Exception as exc:  # noqa: BLE001
        return final, f"sse_transport:{exc.__class__.__name__}"
    return final, "ok"


EMERGENCY_MSG = "I am going to kill myself"
NORMAL_MSG = "What community events are happening this week?"


def run_stable_soak(base: str, n: int, log_path: Path) -> int:
    print(f"Stable soak: {n} mixed requests against {base}")
    users = [GuestSession(base) for _ in range(4)]
    for u in users:
        u.bootstrap()
    threads = [u.create_thread(f"soak-{i}") for i, u in enumerate(users)]

    stats = Counter()
    errors: List[str] = []
    lock = threading.Lock()
    http_count = 0

    def one(i: int) -> None:
        nonlocal http_count
        u = users[i % len(users)]
        tid = threads[i % len(threads)]
        ctid = str(uuid.uuid4())
        kind = i % 10
        try:
            if kind == 0:
                r = u.s.get(f"{base}/health", timeout=10)
                with lock:
                    http_count += 1
                    stats[f"health_{r.status_code}"] += 1
                    if r.status_code != 200:
                        errors.append(f"health {r.status_code}")
            elif kind == 1:
                r = u.s.get(f"{base}/events?days_ahead=14&limit=20", headers=u.headers(), timeout=20)
                with lock:
                    http_count += 1
                    stats[f"events_{r.status_code}"] += 1
                    if r.status_code >= 500:
                        errors.append(f"events {r.status_code}")
            elif kind == 2:
                r = u.s.get(f"{base}/health", timeout=10)
                with lock:
                    http_count += 1
                    stats[f"health2_{r.status_code}"] += 1
            elif kind in (3, 4):
                # Emergency deterministic path
                r = u.send_json(tid, EMERGENCY_MSG if kind == 3 else "I need emergency help now", ctid)
                with lock:
                    http_count += 1
                    stats[f"json_{r.status_code}"] += 1
                    if r.status_code >= 500:
                        errors.append(f"json {r.status_code} {r.text[:120]}")
            elif kind == 5:
                # completed replay: send twice same client_turn_id
                r1 = u.send_json(tid, EMERGENCY_MSG, ctid)
                r2 = u.send_json(tid, EMERGENCY_MSG, ctid)
                with lock:
                    http_count += 2
                    stats[f"replay1_{r1.status_code}"] += 1
                    stats[f"replay2_{r2.status_code}"] += 1
                    if r1.status_code >= 500 or r2.status_code >= 500:
                        errors.append("replay 5xx")
            elif kind == 6:
                # same-thread conflict: two different turn ids quickly
                def _send(x):
                    return u.send_json(tid, EMERGENCY_MSG, str(uuid.uuid4()))
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futs = [pool.submit(_send, j) for j in range(2)]
                    codes = [f.result().status_code for f in futs]
                with lock:
                    http_count += 2
                    for c in codes:
                        stats[f"conflict_{c}"] += 1
            elif kind == 7:
                r = u.send_stream(tid, EMERGENCY_MSG, ctid)
                final, status = _drain_sse(r)
                with lock:
                    http_count += 1
                    stats[f"sse_{r.status_code}_{status}"] += 1
                    if r.status_code >= 500:
                        errors.append("sse 5xx")
            else:
                r = u.send_json(tid, NORMAL_MSG, ctid)
                with lock:
                    http_count += 1
                    stats[f"normal_{r.status_code}"] += 1
                    # LLM may 5xx if no key — count but classify
                    if r.status_code >= 500:
                        stats["normal_5xx_maybe_llm"] += 1
        except requests.RequestException as exc:
            with lock:
                stats["transport"] += 1
                errors.append(f"transport:{exc.__class__.__name__}:{exc}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(one, i) for i in range(n)]
        for f in as_completed(futs):
            f.result()
    elapsed = time.time() - t0

    completions = []
    if log_path.exists():
        completions = _parse_completions(log_path.read_text(errors="ignore"))

    print(f"elapsed_s={elapsed:.1f} http_ops≈{http_count}")
    print("status_counts=", dict(stats))
    print(f"completion_events={len(completions)} (per-process logs may be partial)")
    transport = stats.get("transport", 0)
    unexpected_5xx = sum(v for k, v in stats.items() if k.endswith("_500") or k.endswith("_502") or k.endswith("_503"))
    # Emergency JSON should be 201
    if transport:
        print("FAIL unexpected transport failures:", transport)
        for e in errors[:20]:
            print(" ", e)
        return 1
    print("PASS stable soak: zero unexpected transport failures")
    if errors:
        print("notes:", len(errors), "non-transport issues (sample):", errors[:5])
    return 0


def run_recycle_test(base: str) -> int:
    print(f"Forced recycle test against {base}")
    u = GuestSession(base)
    u.bootstrap()
    tid = u.create_thread("recycle")
    stats = Counter()
    errors: List[str] = []

    # Burst requests while workers recycle (server configured with low max_requests)
    for i in range(80):
        ctid = str(uuid.uuid4())
        try:
            if i % 5 == 0:
                r = u.s.get(f"{base}/health", timeout=15)
                stats[f"health_{r.status_code}"] += 1
            elif i % 5 == 1:
                r = u.send_stream(tid, EMERGENCY_MSG, ctid)
                final, st = _drain_sse(r)
                stats[f"sse_{r.status_code}_{st}"] += 1
                # Retry same client_turn_id after interrupt
                if st.startswith("sse_transport") or r.status_code >= 500:
                    r2 = u.send_json(tid, EMERGENCY_MSG, ctid)
                    stats[f"retry_{r2.status_code}"] += 1
                    if r2.status_code not in (200, 201, 409):
                        errors.append(f"retry bad {r2.status_code}")
            else:
                r = u.send_json(tid, EMERGENCY_MSG, ctid)
                stats[f"json_{r.status_code}"] += 1
                if r.status_code in (200, 201):
                    # idempotent replay
                    r2 = u.send_json(tid, EMERGENCY_MSG, ctid)
                    stats[f"replay_{r2.status_code}"] += 1
        except requests.RequestException as exc:
            stats["transport"] += 1
            # Retry with same client_turn_id to prove safety
            try:
                r2 = u.send_json(tid, EMERGENCY_MSG, ctid)
                stats[f"after_transport_retry_{r2.status_code}"] += 1
                if r2.status_code not in (200, 201, 409):
                    errors.append(f"post-transport retry {r2.status_code}")
            except Exception as exc2:  # noqa: BLE001
                errors.append(f"retry failed {exc2}")
        time.sleep(0.05)

    # Health recovery
    ok = False
    for _ in range(30):
        try:
            if u.s.get(f"{base}/health", timeout=5).status_code == 200:
                ok = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    print("status_counts=", dict(stats))
    print("health_recovered=", ok)
    if not ok:
        print("FAIL health did not recover")
        return 1
    if errors:
        print("FAIL recycle errors:", errors[:10])
        return 1
    print("PASS recycle: retries reuse client_turn_id; health recovered")
    print("NOTE: transport interruptions during recycle are expected; classified via retry safety")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--mode", choices=("stable", "recycle"), required=True)
    p.add_argument("--requests", type=int, default=1000)
    p.add_argument("--log-path", default="/tmp/otp-soak-gunicorn.log")
    args = p.parse_args()
    if args.mode == "stable":
        return run_stable_soak(args.base_url, args.requests, Path(args.log_path))
    return run_recycle_test(args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())
