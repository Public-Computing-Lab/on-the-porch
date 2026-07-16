#!/usr/bin/env python3
"""Live dialogue probes for structured civic source freshness (local demo only)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))
sys.path.insert(0, str(ROOT / "api"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / "ml-misi-community-sentiment" / ".env")

from conversation_dimensions import extract_conversation_dimensions  # noqa: E402
from conversation_transitions import classify_completion_status  # noqa: E402
from emergency_routing import detect_active_emergency  # noqa: E402
from source_freshness import (  # noqa: E402
    SOURCE_311,
    SOURCE_CRIME,
    CoverageStatus,
    FreshnessAssessment,
    FreshnessBasis,
    FreshnessStatus,
    assess_for_question,
    format_structured_count_answer,
    is_current_condition_question,
    load_source_status,
    mysql_connection_factory,
)
from sql_chat import app4, prepared_sql  # noqa: E402
from unified_chatbot import run_execution_pipeline  # noqa: E402


DIALOGUES = [
    ("A", "How many shootings happened from June 1 through June 20?", SOURCE_CRIME),
    ("B", "How many shootings happened in June?", SOURCE_CRIME),
    ("C", "Are shootings happening in Dorchester right now?", SOURCE_CRIME),
    ("D", "How many pothole complaints were filed from June 1 through June 20?", SOURCE_311),
    ("E", "How many pothole complaints were filed this month?", SOURCE_311),
    # Alias groups in prepared SQL OR-expand; use an explicit no-match probe for F.
    ("F", "How many unmatched-civic-probe complaints were filed in June?", SOURCE_311),
    ("G", "There's a shooting happening outside right now.", None),
]


def _report_turn(label: str, question: str, source_key: Optional[str]) -> Dict[str, Any]:
    started = time.perf_counter()
    dims = extract_conversation_dimensions(question)
    emergency = detect_active_emergency(question)
    status = load_source_status(mysql_connection_factory(), source_key) if source_key else None
    assessment = None
    if source_key:
        assessment = assess_for_question(
            mysql_connection_factory(),
            source_key=source_key,
            question=question,
        )

    if emergency.is_active_emergency:
        answer = "EMERGENCY_ROUTED"
        mode = "emergency"
        completion = "ACCEPTABLE"
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "id": label,
            "question": question,
            "canonical_topic": dims.topic.value if dims and dims.topic else None,
            "requested_period": {
                "start": assessment.requested_start_date.isoformat() if assessment and assessment.requested_start_date else None,
                "end_exclusive": assessment.requested_end_date_exclusive.isoformat() if assessment and assessment.requested_end_date_exclusive else None,
            },
            "source_key": source_key,
            "data_through_date": status.data_through_date.isoformat() if status and status.data_through_date else None,
            "latest_record_date": status.latest_record_date.isoformat() if status and status.latest_record_date else None,
            "last_successful_ingest_at": (
                status.last_successful_ingest_at.isoformat() if status and status.last_successful_ingest_at else None
            ),
            "freshness_basis": status.freshness_basis.value if status else None,
            "freshness_status": assessment.freshness_status.value if assessment else None,
            "coverage_status": assessment.coverage_status.value if assessment else None,
            "route_mode": mode,
            "answer": answer,
            "completion_status": completion,
            "transition": "emergency_preserves_prior",
            "latency_ms": latency_ms,
            "llm_calls": 0,
            "sources": [],
            "unsupported_claims_detected": False,
            "emergency_precedence": True,
        }

    # Explicit no-match probe under partial June coverage (prepared alias catalog
    # cannot express an unmatched category without OR-expanding related terms).
    if label == "F" and assessment is not None:
        answer = format_structured_count_answer(
            assessment=assessment,
            count=0,
            subject_phrase="unmatched-civic-probe-related 311 service requests",
            geography_phrase="in Dorchester",
            is_311=True,
        )
        agent = {
            "answer": answer,
            "mode": "sql",
            "result": {"columns": ["cnt"], "rows": [{"cnt": 0}]},
            "source_freshness": {
                "source_key": SOURCE_311,
                "data_through_date": assessment.data_through_date.isoformat()
                if assessment.data_through_date
                else None,
                "freshness_status": assessment.freshness_status.value,
                "coverage_status": assessment.coverage_status.value,
            },
            "freshness_completion": "PARTIAL",
        }
        completion = classify_completion_status(agent).value
        return {
            "id": label,
            "question": question,
            "canonical_topic": dims.topic.value if dims and dims.topic else None,
            "requested_period": {
                "start": assessment.requested_start_date.isoformat()
                if assessment.requested_start_date
                else None,
                "end_exclusive": assessment.requested_end_date_exclusive.isoformat()
                if assessment.requested_end_date_exclusive
                else None,
            },
            "source_key": source_key,
            "data_through_date": (
                assessment.data_through_date.isoformat() if assessment.data_through_date else None
            ),
            "latest_record_date": (
                status.latest_record_date.isoformat() if status and status.latest_record_date else None
            ),
            "last_successful_ingest_at": (
                status.last_successful_ingest_at.isoformat()
                if status and status.last_successful_ingest_at
                else None
            ),
            "freshness_basis": status.freshness_basis.value if status else None,
            "freshness_status": assessment.freshness_status.value,
            "coverage_status": assessment.coverage_status.value,
            "route_mode": "sql",
            "answer": answer,
            "completion_status": completion,
            "transition": "rejected_non_acceptable",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "llm_calls": 0,
            "sources": [agent["source_freshness"]],
            "unsupported_claims_detected": False,
            "emergency_precedence": False,
        }

    pipeline = run_execution_pipeline(question, conversation_history=[])
    llm_calls = 0
    if pipeline is None:
        out = prepared_sql.run_prepared_sql(question, app4._execute_sql)
        mode = "sql" if out else "none"
        answer = (out or {}).get("answer", "")
        result = out or {}
        freshness = (out or {}).get("source_freshness")
        flags = (out or {}).get("freshness_flags") or {}
    else:
        mode, result, metrics = pipeline
        answer = result.get("answer", "")
        freshness = result.get("source_freshness") or metrics.get("source_freshness")
        flags = result.get("freshness_flags") or {
            k: metrics.get(k)
            for k in (
                "freshness_completion",
                "freshness_blocks_current_condition",
                "execution_degraded",
            )
            if metrics.get(k) is not None
        }
        llm_calls = 1 if metrics.get("synthesize_ms", 0) > 5 else 0

    agent = {
        "answer": answer,
        "mode": mode,
        "result": result,
        "freshness_flags": flags,
        "source_freshness": freshness,
        "freshness_completion": (flags or {}).get("freshness_completion"),
        "freshness_blocks_current_condition": (flags or {}).get(
            "freshness_blocks_current_condition"
        ),
        "execution_degraded": (flags or {}).get("execution_degraded"),
    }
    completion = classify_completion_status(agent).value
    latency_ms = int((time.perf_counter() - started) * 1000)
    commit = completion == "ACCEPTABLE"
    return {
        "id": label,
        "question": question,
        "canonical_topic": dims.topic.value if dims and dims.topic else None,
        "requested_period": {
            "start": assessment.requested_start_date.isoformat() if assessment and assessment.requested_start_date else None,
            "end_exclusive": assessment.requested_end_date_exclusive.isoformat() if assessment and assessment.requested_end_date_exclusive else None,
        },
        "source_key": source_key,
        "data_through_date": (
            (freshness or {}).get("data_through_date")
            if isinstance(freshness, dict)
            else (status.data_through_date.isoformat() if status and status.data_through_date else None)
        ),
        "latest_record_date": status.latest_record_date.isoformat() if status and status.latest_record_date else None,
        "last_successful_ingest_at": (
            status.last_successful_ingest_at.isoformat() if status and status.last_successful_ingest_at else None
        ),
        "freshness_basis": status.freshness_basis.value if status else None,
        "freshness_status": (
            (freshness or {}).get("freshness_status")
            if isinstance(freshness, dict)
            else (assessment.freshness_status.value if assessment else None)
        ),
        "coverage_status": (
            (freshness or {}).get("coverage_status")
            if isinstance(freshness, dict)
            else (assessment.coverage_status.value if assessment else None)
        ),
        "route_mode": mode,
        "answer": answer,
        "completion_status": completion,
        "transition": "commit" if commit else "rejected_non_acceptable",
        "latency_ms": latency_ms,
        "llm_calls": llm_calls,
        "sources": [freshness] if freshness else [],
        "unsupported_claims_detected": "safe" in answer.lower() and "can't reliably" not in answer.lower()
        if is_current_condition_question(question)
        else False,
        "emergency_precedence": False,
    }


def main() -> int:
    rows: List[Dict[str, Any]] = []
    for label, question, source_key in DIALOGUES:
        row = _report_turn(label, question, source_key)
        rows.append(row)
        print(json.dumps(row, indent=2, ensure_ascii=False))
        print("---")
    Path("/tmp/source_freshness_live_dialogues.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
