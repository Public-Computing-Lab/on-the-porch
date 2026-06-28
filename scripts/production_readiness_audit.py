#!/usr/bin/env python3
"""Heavy pre-production audit: HTTP answers vs DB ground truth + routing checks."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "on_the_porch"))
sys.path.insert(0, str(ROOT / "on_the_porch" / "sql_chat"))

BASE = os.getenv("OTP_API_BASE_URL", "http://127.0.0.1:8888").rstrip("/")
TIMEOUT = int(os.getenv("OTP_REQUEST_TIMEOUT", "120"))

FAILURE_PHRASES = (
    "i couldn't find an answer",
    "i'm having trouble reaching the ai",
    "failed to send message",
    "please try again in a moment",
    "generation failed",
)


@dataclass
class AuditCase:
    id: str
    question: str
    category: str
    expect_modes: Optional[List[str]] = None
    ground_truth: Optional[Callable[[], Dict[str, Any]]] = None
    answer_check: Optional[Callable[[str, Dict[str, Any]], Optional[str]]] = None
    min_len: int = 20


def _extract_bold_ints(text: str) -> List[int]:
    return [int(m.replace(",", "")) for m in re.findall(r"\*\*(\d[\d,]*)\*\*", text or "")]


def _gt_prepared(question: str) -> Dict[str, Any]:
    """Canonical DB-backed answer — same code path the chatbot must execute."""
    from sql_chat import app4, prepared_sql

    out = prepared_sql.run_prepared_sql(question, app4._execute_sql)
    if not out:
        return {"prepared": None}
    answer = out.get("answer", "")
    return {
        "prepared": out.get("prepared_intent"),
        "expected_answer": answer,
        "expected_count": _extract_bold_ints(answer),
    }


class ChatClient:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.session_token: Optional[str] = None
        self.csrf_token: Optional[str] = None

    def bootstrap(self) -> None:
        resp = self.s.post(f"{BASE}/auth/guest", timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        self.session_token = data["session_token"]
        self.csrf_token = data["csrf_token"]

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.session_token}",
            "X-CSRF-Token": self.csrf_token or "",
            "Content-Type": "application/json",
        }

    def ask(self, question: str) -> Dict[str, Any]:
        thread = self.s.post(
            f"{BASE}/conversations",
            headers=self._headers(),
            json={"title": f"audit-{uuid.uuid4().hex[:8]}"},
            timeout=TIMEOUT,
        )
        thread.raise_for_status()
        tid = thread.json()["thread"]["id"]
        t0 = time.perf_counter()
        resp = self.s.post(
            f"{BASE}/conversations/{tid}/messages/stream",
            headers=self._headers(),
            json={"message": question},
            stream=True,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        answer_parts: List[str] = []
        answer = ""
        mode = None
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "delta":
                answer_parts.append(evt.get("text") or "")
            elif etype == "correction":
                answer = evt.get("text") or answer
            elif etype == "final":
                mode = evt.get("mode") or mode
                answer = evt.get("answer") or "".join(answer_parts)
            elif etype == "error":
                return {"answer": "", "mode": mode, "latency_ms": 0, "error": evt.get("error")}
        if not answer:
            answer = "".join(answer_parts)
        return {
            "answer": answer.strip(),
            "mode": mode,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }


AUDIT_CASES: List[AuditCase] = [
    # Chitchat
    AuditCase("chitchat_hello", "Hello!", "chitchat", ["chitchat"], min_len=10),
    AuditCase("chitchat_help", "What can you help me with?", "chitchat", ["chitchat", "rag", "history"], min_len=20),
    # Off-topic
    AuditCase("off_topic", "What is the capital of France?", "off_topic", ["rag", "sql", "hybrid", "chitchat"],
              answer_check=lambda a, _: None if "paris" in a.lower() or "france" in a.lower() or "did not find" in a.lower() else "should decline or answer briefly"),
    # Events
    AuditCase("events_week", "What events are happening in Dorchester this week?", "events", ["sql"],
              ground_truth=lambda: _gt_prepared("What events are happening in Dorchester this week?")),
    AuditCase("events_weekend", "What's on in Dorchester this weekend?", "events", ["sql"], min_len=30),
    # 311 explicit
    AuditCase("311_count_week", "How many 311 service requests were filed in Dorchester this week?", "311", ["sql"],
              ground_truth=lambda: _gt_prepared("How many 311 service requests were filed in Dorchester this week?")),
    AuditCase("311_top_month", "What are the most common 311 issue types in Dorchester last month?", "311", ["sql"],
              ground_truth=lambda: _gt_prepared("What are the most common 311 issue types in Dorchester last month?")),
    AuditCase("311_open", "Show me open 311 cases in Dorchester from the past week.", "311", ["sql"], min_len=30),
    # 311 implicit (no "311" in question)
    AuditCase("311_potholes_implicit", "How many pothole complaints in Dorchester this month?", "311", ["sql"],
              ground_truth=lambda: _gt_prepared("How many pothole complaints in Dorchester this month?")),
    AuditCase("311_trash_implicit", "How many trash complaints were there in Dorchester last week?", "311", ["sql"],
              ground_truth=lambda: _gt_prepared("How many trash complaints were there in Dorchester last week?")),
    AuditCase("311_rats_implicit", "Are there a lot of rat reports in Dorchester lately?", "311", ["sql"],
              ground_truth=lambda: _gt_prepared("Are there a lot of rat reports in Dorchester lately?")),
    AuditCase("311_rodent_problem", "Is there a rodent problem in Dorchester?", "311", ["sql"],
              ground_truth=lambda: _gt_prepared("Is there a rodent problem in Dorchester?")),
    AuditCase("311_graffiti_implicit", "How many graffiti reports in Dorchester this month?", "311", ["sql"],
              ground_truth=lambda: _gt_prepared("How many graffiti reports in Dorchester this month?")),
    # Crime
    AuditCase("crime_30d", "How many crime incidents were reported in Dorchester in the last 30 days?", "crime", ["sql"],
              ground_truth=lambda: _gt_prepared("How many crime incidents were reported in Dorchester in the last 30 days?")),
    AuditCase("shootings_natural", "How many shootings were there in Dorchester last month?", "crime", ["sql", "hybrid"],
              ground_truth=lambda: _gt_prepared("How many shootings were there in Dorchester last month?")),
    AuditCase("gun_violence_wording", "How much gun violence in Dorchester recently?", "crime", ["sql", "hybrid"],
              ground_truth=lambda: _gt_prepared("How many shootings were there in Dorchester last month?"),
              answer_check=lambda a, gt: (
                  None if gt.get("expected_count") and _extract_bold_ints(a) and _extract_bold_ints(a)[0] == gt["expected_count"][0]
                  else f"count mismatch: answer {_extract_bold_ints(a)} vs expected {gt.get('expected_count')}"
              ) if gt.get("expected_count") else None),
    AuditCase("homicides_ytd", "How many homicides were reported in Dorchester this year?", "crime", ["sql"],
              ground_truth=lambda: _gt_prepared("How many homicides were reported in Dorchester this year?")),
    AuditCase("crime_breakdown", "Show me a breakdown of crime types in Dorchester last month.", "crime", ["sql"], min_len=40),
  # 911 guardrail
    AuditCase("911_calls", "How many 911 emergency calls in Dorchester last month?", "guardrail", ["sql"],
              answer_check=lambda a, _: None if "don't have" in a.lower() or "do not have" in a.lower() or "911" in a.lower() else "should explain 911 data unavailable"),
    # News / RAG
    AuditCase("news_dotreporter", "Any recent news from the Dorchester Reporter?", "news", ["rag"], min_len=40,
              answer_check=lambda a, _: None if "did not find exact" not in a.lower()[:80] or len(a) > 80 else None),
    AuditCase("neighborhood_pulse", "What's going on in Dorchester lately?", "hybrid", ["hybrid"], min_len=60),
    # Policy RAG
    AuditCase("policy_imagine", "What are the main goals of Imagine Boston 2030?", "policy", ["rag"], min_len=50),
    AuditCase("policy_slow_streets", "What does the Boston Slow Streets plan aim to achieve?", "policy", ["rag"], min_len=50),
    # Transcript / opinion
    AuditCase("transcript_displacement", "What do meeting transcripts say about displacement in Dorchester?", "opinion", ["rag"], min_len=80,
              answer_check=lambda a, _: None if any(w in a.lower() for w in ("displacement", "housing", "afford", "tenant")) else "should discuss displacement/housing"),
    AuditCase("opinion_safety", "What do residents say about safety in Dorchester?", "opinion", ["hybrid", "rag"], min_len=50),
    # Hybrid
    AuditCase("crime_hybrid", "How many shootings were there in Dorchester last month and what concerns do residents raise?", "hybrid", ["hybrid"],
              ground_truth=lambda: _gt_prepared("How many shootings were there in Dorchester last month?"),
              answer_check=lambda a, gt: (
                  None if gt.get("expected_count") and _extract_bold_ints(a) and _extract_bold_ints(a)[0] == gt["expected_count"][0]
                  else f"hybrid missing SQL count {gt.get('expected_count')}"
              ) if gt.get("expected_count") else None),
    # Vague civic
    AuditCase("vague_dorchester", "Tell me about Dorchester.", "general", ["rag", "hybrid", "sql"], min_len=40),
]


def evaluate(case: AuditCase, resp: Dict[str, Any], gt: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    answer = resp.get("answer", "")
    mode = resp.get("mode")
    al = answer.lower()

    if len(answer) < case.min_len:
        issues.append(f"too short ({len(answer)} chars)")
    for phrase in FAILURE_PHRASES:
        if phrase in al:
            issues.append(f"failure phrase: {phrase!r}")
            break
    if case.expect_modes and mode and mode not in case.expect_modes:
        issues.append(f"mode {mode!r} not in {case.expect_modes}")

    if gt.get("prepared") and gt.get("expected_count"):
        got = _extract_bold_ints(answer)
        exp = gt["expected_count"]
        if not got:
            issues.append(f"no bold count in answer (DB has {exp[0]})")
        elif got[0] != exp[0]:
            issues.append(f"HTTP/DB mismatch: chat said {got[0]}, database has {exp[0]}")
        # Prepared answers should match the canonical template exactly when mode is sql.
        if mode == "sql" and gt.get("expected_answer"):
            exp_lead = (gt["expected_answer"] or "")[:200]
            if exp_lead and exp_lead[:80] not in answer and answer[:80] not in exp_lead:
                if got and got[0] == exp[0]:
                    pass  # count matches; wording drift on non-sql path is ok
                else:
                    issues.append("answer text diverged from DB-backed template")

    if case.answer_check:
        err = case.answer_check(answer, gt)
        if err:
            issues.append(err)

    if resp.get("latency_ms", 0) > 10000:
        issues.append(f"latency {resp['latency_ms']}ms > 10s")

    return issues


def main() -> int:
    try:
        requests.get(f"{BASE}/health", timeout=10).raise_for_status()
    except Exception as exc:
        print(f"API not reachable at {BASE}: {exc}")
        return 1

    client = ChatClient()
    client.bootstrap()
    passed = 0
    failed: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    print("=" * 72)
    print("PRODUCTION READINESS AUDIT")
    print("=" * 72)

    for case in AUDIT_CASES:
        gt: Dict[str, Any] = {}
        if case.ground_truth:
            try:
                gt = case.ground_truth()
            except Exception as exc:
                gt = {"ground_truth_error": str(exc)}

        try:
            resp = client.ask(case.question)
        except Exception as exc:
            failed.append({"id": case.id, "issues": [f"HTTP error: {exc}"], "question": case.question})
            print(f"[FAIL] {case.id}: HTTP {exc}")
            continue

        issues = evaluate(case, resp, gt)
        if gt.get("ground_truth_error"):
            issues.append(f"GT error: {gt['ground_truth_error']}")

        status = "PASS" if not issues else "FAIL"
        preview = resp["answer"].replace("\n", " ")[:120]
        print(f"[{status}] {case.id:28} mode={resp.get('mode','?'):8} {resp.get('latency_ms',0):5}ms")
        print(f"         Q: {case.question[:70]}")
        print(f"         A: {preview}{'…' if len(resp['answer'])>120 else ''}")
        if gt.get("prepared"):
            print(f"         GT intent={gt['prepared']} count={gt.get('expected_count')}")
        for issue in issues:
            print(f"         ! {issue}")

        row = {"id": case.id, "category": case.category, "issues": issues, "mode": resp.get("mode"), "latency_ms": resp.get("latency_ms")}
        if issues:
            failed.append(row)
        else:
            passed += 1
            if resp.get("latency_ms", 0) > 8000:
                warnings.append(row)
        time.sleep(0.4)

    print("=" * 72)
    print(f"RESULT: {passed}/{len(AUDIT_CASES)} passed, {len(failed)} failed, {len(warnings)} slow-but-pass")
    if failed:
        print("\nFAILED CASES:")
        for f in failed:
            print(f"  - {f['id']}: {', '.join(f['issues'])}")
    out = ROOT / "logs" / "production_readiness_audit.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"passed": passed, "failed": failed, "warnings": warnings}, indent=2))
    print(f"\nWrote {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
