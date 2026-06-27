#!/usr/bin/env python3
"""Map chatbot paths, latency tiers, and LLM call counts for representative questions.

Usage (API must be running for HTTP phase):
    python scripts/path_latency_audit.py
    python scripts/path_latency_audit.py --in-process-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))
sys.path.insert(0, os.path.join(_ROOT, "on_the_porch"))
sys.path.insert(0, os.path.join(_ROOT, "on_the_porch", "sql_chat"))

import intent_router  # noqa: E402
from unified_chatbot import (  # noqa: E402
    is_small_talk,
    reset_llm_call_counter,
    get_llm_call_count,
    _route_question,
    _run_sql,
    _run_rag,
    _run_hybrid,
    _fix_retrieval_vectordb_path,
)
from prepared_sql import match_prepared_sql  # noqa: E402

try:
    import requests
except ImportError:
    requests = None  # type: ignore


@dataclass
class PathCase:
    id: str
    question: str
    category: str
    expected_mode: Optional[str] = None


PATH_CASES: List[PathCase] = [
    PathCase("chitchat_hi", "Hello!", "chitchat", "chitchat"),
    PathCase("chitchat_thanks", "Thanks, that's helpful!", "chitchat", "chitchat"),
    PathCase("events_week", "What events are happening in Dorchester this week?", "events", "sql"),
    PathCase("events_weekend", "What's happening in Dorchester this weekend?", "events", "sql"),
    PathCase("events_csndc", "What CSNDC events are coming up this month?", "events", "sql"),
    PathCase("311_count", "How many 311 service requests were filed in Dorchester this week?", "311", "sql"),
    PathCase("311_top", "What are the most common 311 issue types in Dorchester last month?", "311", "sql"),
    PathCase("311_open", "Show me open 311 cases in Dorchester from the past week.", "311", "sql"),
    PathCase("crime_count", "How many crime incidents were reported in Dorchester in the last 30 days?", "crime", "sql"),
    PathCase("crime_homicides", "How many homicides were reported in Dorchester this year?", "crime", "sql"),
    PathCase("crime_breakdown", "Show me a breakdown of crime types in Dorchester last month.", "crime", "sql"),
    PathCase("news_rss", "Any recent news from the Dorchester Reporter?", "news", "rag"),
    PathCase("policy_slow", "What does the Boston Slow Streets plan aim to achieve?", "policy", "rag"),
    PathCase("neighborhood_pulse", "What's going on in Dorchester lately?", "news", "hybrid"),
    PathCase("opinion_safety", "What do residents say about safety in Dorchester?", "opinion", "hybrid"),
    PathCase("crime_hybrid", "How many shootings were there in Dorchester last month and what concerns do residents raise?", "hybrid", "hybrid"),
    PathCase("transcript", "What do meeting transcripts say about displacement in Dorchester?", "opinion", "rag"),
    PathCase("off_topic", "What is the capital of France?", "off_topic", None),
]


def analyze_path(question: str) -> Dict[str, Any]:
    """Static path analysis without executing LLM."""
    if is_small_talk(question):
        return {
            "tier": "T0_chitchat",
            "prepared": None,
            "rule_route": None,
            "expected_llm_calls": 0,
            "expected_mode": "chitchat",
        }
    prepared = match_prepared_sql(question)
    rule = intent_router.route_with_rules(question)
    if prepared:
        use_llm = prepared.intent == "event_by_name"
        return {
            "tier": "T1_prepared_sql",
            "prepared": prepared.intent,
            "rule_route": rule.get("intent") if rule else None,
            "expected_llm_calls": 1 if use_llm else 0,
            "expected_mode": "sql",
        }
    if rule:
        mode = rule.get("mode", "hybrid")
        llm = {"sql": 0, "rag": 1, "hybrid": 1}.get(mode, 1)
        if mode == "sql":
            llm = 2  # worst case LLM SQL path
        return {
            "tier": "T2_rule_route",
            "prepared": None,
            "rule_route": rule.get("intent"),
            "expected_llm_calls": llm,
            "expected_mode": mode,
        }
    return {
        "tier": "T3_llm_route",
        "prepared": None,
        "rule_route": None,
        "expected_llm_calls": 2,
        "expected_mode": "unknown",
    }


def run_in_process(case: PathCase) -> Dict[str, Any]:
    reset_llm_call_counter()
    started = time.perf_counter()
    static = analyze_path(case.question)
    mode = static["expected_mode"]
    error = None
    answer_preview = ""
    try:
        if static["tier"] == "T0_chitchat":
            from unified_chatbot import small_talk_response
            answer_preview = small_talk_response(case.question)[:120]
        else:
            plan = _route_question(case.question)
            mode = plan.get("mode", "hybrid")
            if mode == "sql":
                out = _run_sql(case.question)
            elif mode == "rag":
                out = _run_rag(case.question, plan)
            else:
                out = _run_hybrid(case.question, plan)
            answer_preview = (out.get("answer") or "")[:120]
    except Exception as exc:  # noqa: BLE001
        error = exc.__class__.__name__
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        **asdict(case),
        **static,
        "actual_mode": mode,
        "llm_calls": get_llm_call_count(),
        "latency_ms": elapsed_ms,
        "answer_preview": answer_preview,
        "error": error,
        "mode_match": case.expected_mode is None or case.expected_mode == mode,
    }


def run_http(case: PathCase, base_url: str) -> Dict[str, Any]:
    if requests is None:
        return {"id": case.id, "http_error": "requests not installed"}
    started = time.perf_counter()
    sess = requests.Session()
    try:
        boot = sess.post(f"{base_url}/auth/guest", timeout=120)
        boot.raise_for_status()
        data = boot.json()
        headers = {
            "Authorization": f"Bearer {data['session_token']}",
            "X-CSRF-Token": data["csrf_token"],
            "Content-Type": "application/json",
        }
        thread = sess.post(f"{base_url}/conversations", headers=headers, json={"title": case.id}, timeout=120)
        thread.raise_for_status()
        tid = thread.json()["thread"]["id"]
        resp = sess.post(
            f"{base_url}/conversations/{tid}/messages/stream",
            headers=headers,
            json={"message": case.question},
            stream=True,
            timeout=120,
        )
        mode = ""
        answer = ""
        err = None
        if resp.status_code != 200:
            err = f"HTTP {resp.status_code}"
        else:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                evt = json.loads(line[5:].strip())
                if evt.get("type") == "delta":
                    answer += evt.get("text", "")
                elif evt.get("type") == "final":
                    mode = evt.get("mode", "")
                    answer = evt.get("answer", answer)
                elif evt.get("type") == "error":
                    err = evt.get("error")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "id": case.id,
            "http_mode": mode,
            "http_latency_ms": elapsed_ms,
            "http_error": err,
            "http_mode_match": case.expected_mode is None or case.expected_mode == mode,
            "answer_preview": answer[:120],
        }
    except Exception as exc:  # noqa: BLE001
        return {"id": case.id, "http_error": exc.__class__.__name__, "http_latency_ms": int((time.perf_counter() - started) * 1000)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-process-only", action="store_true")
    parser.add_argument("--base-url", default=os.getenv("OTP_API_BASE_URL", "http://127.0.0.1:8888"))
    args = parser.parse_args()

    print("=" * 72)
    print("On The Porch — path / latency audit")
    print("=" * 72)
    _fix_retrieval_vectordb_path()

    results = []
    for case in PATH_CASES:
        row = run_in_process(case)
        if not args.in_process_only:
            row.update(run_http(case, args.base_url.rstrip("/")))
        results.append(row)
        status = "OK" if not row.get("error") and not row.get("http_error") else "FAIL"
        print(
            f"[{status}] {case.id:20} tier={row.get('tier','?'):18} "
            f"mode={row.get('actual_mode','?'):8} llm={row.get('llm_calls',0):2} "
            f"ms={row.get('latency_ms',0):5} "
            f"prepared={row.get('prepared') or '-'}"
        )

    fails = [r for r in results if r.get("error") or r.get("http_error") or r.get("mode_match") is False or r.get("http_mode_match") is False]
    out_path = os.path.join(_ROOT, "logs", "path_latency_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print("-" * 72)
    print(f"Wrote {out_path}")
    print(f"Cases: {len(results)}  Issues: {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
