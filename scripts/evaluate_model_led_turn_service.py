#!/usr/bin/env python3
"""Evaluate the Phase 2A turn service with synthetic, privacy-audited cases.

The runner never persists state. Expected labels remain in this process and are
never included in provider prompts. Civic tools may read only the local demo DB.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for candidate in (str(ROOT), str(PORCH)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import config  # noqa: E402
from conversation_dimensions import (  # noqa: E402
    CanonicalTimeRange,
    CanonicalTopic,
    TimeGranularity,
    TimeLabelCode,
    resolve_registered_geography,
)
from conversation_state import (  # noqa: E402
    ActiveEntity,
    ConversationState,
    ThreadStateEnvelope,
    merge_for_commit,
    with_dialogue_context,
)
from gemini_semantic_planner_provider import GeminiSemanticPlannerProvider  # noqa: E402
from model_led_orchestrator import normalize_resident_input  # noqa: E402
from model_led_turn_service import (  # noqa: E402
    ModelLedTurnService,
    TurnRequest,
    TurnResult,
    _trusted_planner_context,
)
from sanitized_orchestrator_protocol import (  # noqa: E402
    FROZEN_MODEL_ID,
    FROZEN_OUTPUT_MODE,
    FROZEN_PLANNER_TIMEOUT_SECONDS,
    SanitizedPublicEvaluationProtocol,
    audit_sanitized_provider_payload,
    build_sanitized_provider_payload,
)
from semantic_dialogue_controller import validate_dialogue_context  # noqa: E402


DEFAULT_DATASET = ROOT / "evaluation/model_led_turn_service/phase2a_generalization.json"
FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0)
PRIVATE_PATTERN = re.compile(
    r"(?:/Users/|/home/|[A-Za-z]:\\|GEMINI_API_KEY|MYSQL_PASSWORD|DATABASE_URL|"
    r"sentiment_demo|expected|expected_label|holdout_answer|SELECT\s|INSERT\s|UPDATE\s|"
    r"DELETE\s|CREATE\s+TABLE|information_schema|performance_schema)",
    re.IGNORECASE,
)
INTERNAL_ANSWER_PATTERN = re.compile(
    r"\b(?:prepared_sql|chroma|qdrant|rag pipeline|sql execution|retrieval failure|tool call)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    message: str
    expected: Mapping[str, Any]
    state_fixture: str
    scenario_id: Optional[str] = None
    turn_index: Optional[int] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--phase2b-gates", action="store_true")
    return parser.parse_args()


def _previous_month_range() -> CanonicalTimeRange:
    return CanonicalTimeRange(
        label_code=TimeLabelCode.LAST_MONTH,
        start_date="2026-06-01",
        end_date_exclusive="2026-07-01",
        timezone="America/New_York",
        granularity=TimeGranularity.MONTH,
    )


def _current_week_range() -> CanonicalTimeRange:
    return CanonicalTimeRange(
        label_code=TimeLabelCode.THIS_WEEK,
        start_date="2026-07-20",
        end_date_exclusive="2026-07-27",
        timezone="America/New_York",
        granularity=TimeGranularity.WEEK,
    )


def _dialogue_context(
    *,
    domain: str,
    capability: str,
    summary: Mapping[str, Any],
    canonical: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return validate_dialogue_context({
        "semantic_revision": 1,
        "previous_turn_kind": "FACTUAL_TOOL_QUERY",
        "previous_domain": domain,
        "previous_capability_intent": capability,
        "pending_clarification_type": None,
        "pending_clarification_domain": None,
        "canonical_parameters": dict(canonical or {}),
        "previous_result_summary": dict(summary),
    })


def state_fixture(name: str) -> ThreadStateEnvelope:
    dorchester = resolve_registered_geography("dorchester")
    if dorchester is None:
        raise RuntimeError("registered Dorchester geography is unavailable")
    if name == "empty":
        return ThreadStateEnvelope({})
    if name == "event_summary":
        return ThreadStateEnvelope(
            {},
            ConversationState(
                active_topic=CanonicalTopic.EVENTS,
                location=dorchester,
                time_range=_current_week_range(),
                previous_route="model_led",
            ),
            dialogue_context=_dialogue_context(
                domain="EVENTS",
                capability="LIST_EVENTS",
                summary={"kind": "EVENT_LIST", "status": "EVENTS_FOUND", "returned_count": 2},
                canonical={
                    "event_id": None,
                    "start_date": "2026-07-20",
                    "end_date_exclusive": "2026-07-27",
                    "semantic_time": "THIS_WEEK",
                },
            ),
        )
    if name == "active_event":
        return ThreadStateEnvelope(
            {},
            ConversationState(
                active_topic=CanonicalTopic.EVENTS,
                active_entity=ActiveEntity.event("437"),
                location=dorchester,
                time_range=_current_week_range(),
                previous_route="model_led",
            ),
            dialogue_context=_dialogue_context(
                domain="EVENTS",
                capability="EVENT_DETAILS",
                summary={"kind": "EVENT_LIST", "status": "EVENTS_FOUND", "returned_count": 1},
                canonical={
                    "event_id": "437",
                    "start_date": "2026-07-20",
                    "end_date_exclusive": "2026-07-27",
                    "semantic_time": "THIS_WEEK",
                },
            ),
        )
    if name == "active_311_all":
        period = _previous_month_range()
        return ThreadStateEnvelope(
            {},
            ConversationState(
                active_topic=CanonicalTopic.CITY_SERVICES,
                location=dorchester,
                time_range=period,
                previous_route="model_led",
            ),
            dialogue_context=_dialogue_context(
                domain="311",
                capability="QUERY_311",
                summary={
                    "kind": "CIVIC_311_COUNT",
                    "status": "PARTIAL",
                    "count": 100,
                    "request_label": "Boston 311 service requests",
                    "geography_label": "Dorchester",
                    "executed_start_date": "2026-06-01",
                    "executed_end_date_exclusive": "2026-06-28",
                    "requested_end_date_exclusive": "2026-07-01",
                    "data_through_date": "2026-06-27",
                },
                canonical={
                    "issue_scope": "ALL_311",
                    "issue_family": None,
                    "geography": {"label": "Dorchester"},
                    "period": {
                        "start_date": "2026-06-01",
                        "end_date_exclusive": "2026-07-01",
                    },
                },
            ),
        )
    raise ValueError(f"unknown state fixture: {name}")


def load_dataset(path: Path) -> tuple[Sequence[Case], Sequence[Mapping[str, Any]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported turn-service dataset")
    cases: list[Case] = []
    for family in payload.get("families") or []:
        for index, message in enumerate(family.get("messages") or [], 1):
            cases.append(Case(
                case_id=f"{family['family_id']}:{index}",
                category=str(family.get("category") or "unknown"),
                message=str(message),
                expected=copy.deepcopy(family["expected"]),
                state_fixture=str(family.get("state_fixture") or "empty"),
            ))
    scenarios = tuple(copy.deepcopy(payload.get("scenarios") or []))
    return tuple(cases), scenarios, str(payload.get("dataset_name") or path.stem)


def _connection_factory():
    import mysql.connector

    return mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DB,
        connection_timeout=config.MYSQL_CONNECT_TIMEOUT,
    )


def verify_demo_database() -> Dict[str, Any]:
    if config.MYSQL_DB != "sentiment_demo":
        raise RuntimeError("refusing turn-service evaluation outside sentiment_demo")
    if config.MYSQL_HOST not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("refusing turn-service evaluation against a non-local MySQL host")
    conn = _connection_factory()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT DATABASE(), CURRENT_USER()")
            database, user = cursor.fetchone()
            if database != "sentiment_demo":
                raise RuntimeError("connected database is not sentiment_demo")
            cursor.execute("SELECT COUNT(*) FROM weekly_events")
            event_count = int(cursor.fetchone()[0])
        finally:
            cursor.close()
    finally:
        conn.close()
    return {"database": database, "user": str(user).split("@", 1)[0], "event_count": event_count}


def audit_case(case: Case, envelope: ThreadStateEnvelope) -> Dict[str, Any]:
    context, tokens = _trusted_planner_context(envelope)
    resident = normalize_resident_input(case.message)
    payload = build_sanitized_provider_payload(
        resident,
        trusted_context=context,
        available_trusted_reference_tokens=tokens,
        output_mode=FROZEN_OUTPUT_MODE,
    )
    audit = audit_sanitized_provider_payload(payload).to_safe_dict()
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    if PRIVATE_PATTERN.search(encoded):
        raise RuntimeError(f"outbound privacy audit failed for {case.case_id}")
    expected_values = tuple(
        value for value in _flatten_strings(case.expected)
        if len(value) >= 8 and value not in {"COMPLETED", "CLARIFICATION"}
    )
    # Labels may naturally equal public enum words in the fixed prompt. The
    # expected object itself must never be serialized or attached to payloads.
    if "\"expected\"" in encoded or "expected_label" in encoded:
        raise RuntimeError(f"expected labels leaked for {case.case_id}")
    audit["expected_labels_local_only"] = True
    audit["local_label_count"] = len(expected_values)
    return audit


def _flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _flatten_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _flatten_strings(item)


def _actual_tool(result: TurnResult) -> Mapping[str, Any]:
    for tool in result.compiled_tools:
        if tool.get("tool_id") in {
            "query_events", "aggregate_311", "search_311_records", "get_source_status",
        }:
            return tool
    return {}


def _expected_contains(expected: Any, actual: Optional[str]) -> bool:
    choices = expected if isinstance(expected, list) else [expected]
    return actual in choices


def score_case(case: Case, result: TurnResult, prior: ThreadStateEnvelope) -> Dict[str, Any]:
    expected = case.expected
    tool = _actual_tool(result)
    args = tool.get("arguments") if isinstance(tool.get("arguments"), Mapping) else {}
    time_scope = args.get("time_scope") if isinstance(args.get("time_scope"), Mapping) else {}
    next_state = result.state_transition.next_state or prior.conversation_state
    outcome_action = result.planner_action
    if result.status.value in {"CLARIFICATION", "INVALID_UNSAFE"}:
        outcome_action = "ask_clarification"
    actual = {
        "status": result.status.value,
        "action": outcome_action,
        "tool_id": tool.get("tool_id"),
        "tool_operation": args.get("operation"),
        "location_id": args.get("location_id"),
        "time_period": time_scope.get("period"),
        "state_topic": next_state.active_topic.value if next_state.active_topic else None,
        "evidence_status": (
            result.evidence.get("status") if isinstance(result.evidence, Mapping) else None
        ),
    }
    checks: Dict[str, bool] = {
        "status": _expected_contains(expected.get("status"), actual["status"]),
        "action": _expected_contains(expected.get("action"), actual["action"]),
        "one_planner_call": result.planner_call_count == 1,
        "zero_synthesis_calls": result.synthesis_call_count == 0,
        "latency_gate": result.latency_ms < 15_000,
        "resident_safe": not INTERNAL_ANSWER_PATTERN.search(result.answer or ""),
        "no_duplicate_tools": all(
            int(count) <= 1 for count in result.tool_execution_counts.values()
        ),
        "no_unrelated_rag": "rag" not in result.mode.lower(),
    }
    for key in (
        "tool_id", "tool_operation", "location_id", "time_period", "state_topic",
        "evidence_status",
    ):
        if key in expected:
            checks[key] = _expected_contains(expected[key], actual[key])
    if (
        "time_period" in expected
        and actual.get("time_period") == "RETAIN_TRUSTED_PERIOD"
        and prior.conversation_state.time_range is not None
    ):
        # Retaining the trusted period is correct for scope-only corrections.
        checks["time_period"] = True
    if expected.get("no_tools"):
        checks["no_tools"] = result.tool_call_count == 0
    if expected.get("preserve_state"):
        checks["preserve_state"] = (
            result.state_transition.next_state in {None, prior.conversation_state}
        )
    checks["capability_correct"] = all(
        checks.get(key, True)
        for key in (
            "status", "action", "tool_id", "tool_operation", "location_id", "time_period",
            "evidence_status",
        )
    )
    checks["geography_time_correct"] = all(
        checks.get(key, True) for key in ("location_id", "time_period")
    )
    _, trusted_tokens = _trusted_planner_context(prior)
    trusted_token_set = set(trusted_tokens)
    referenced_tokens = {
        str(value)
        for value in _flatten_strings(args)
        if isinstance(value, str)
        and value.startswith(("event:", "location:", "time:", "result:"))
    }
    checks["trusted_references_valid"] = referenced_tokens.issubset(trusted_token_set)
    checks["unsafe_tools_zero"] = (
        checks["no_duplicate_tools"]
        and not (
            result.status.value in {"CLARIFICATION", "INVALID_UNSAFE", "DEGRADED"}
            and any(
                name != "resolve_location" and int(count) > 0
                for name, count in result.tool_execution_counts.items()
            )
        )
    )
    checks["safe_outcome"] = all((
        checks["resident_safe"],
        checks["unsafe_tools_zero"],
        checks["trusted_references_valid"],
        checks["no_unrelated_rag"],
    ))
    if expected.get("safe_only"):
        checks["safe_only"] = (
            checks["safe_outcome"]
            and result.status.value != "DEGRADED"
            and (
                result.planner_action != "gather_evidence"
                or checks["capability_correct"]
            )
        )
    checks["useful_outcome"] = _useful_outcome(case, result, checks)
    return {"actual": actual, "checks": checks}


def _useful_outcome(case: Case, result: TurnResult, checks: Mapping[str, bool]) -> bool:
    category = case.category
    if category in {"events", "311"}:
        return (
            result.status.value == "COMPLETED"
            and result.planner_action == "gather_evidence"
            and (result.tool_call_count > 0 or bool(case.expected.get("no_tools")))
            and checks.get("capability_correct", True)
        )
    if category == "direct":
        return result.status.value == "COMPLETED" and result.planner_action == "respond_directly"
    if category == "clarification":
        return result.status.value in {"CLARIFICATION", "INVALID_UNSAFE"}
    if category == "not_migrated":
        return result.status.value == "NOT_YET_MIGRATED" and result.answer is None
    if category == "provider_failure":
        return (
            result.status.value == "DEGRADED"
            and result.tool_call_count == 0
            and result.state_transition.next_state in {None, case.expected.get("prior_state")}
        )
    if category == "safety":
        return checks.get("safe_only", False) and result.status.value != "DEGRADED"
    return checks.get("status", False) and checks.get("action", False)


def _synthetic_next_envelope(
    prior: ThreadStateEnvelope,
    result: TurnResult,
    *,
    turn_number: int,
) -> ThreadStateEnvelope:
    merged = merge_for_commit(
        prior,
        prior,
        next_retrieval_cache=prior.retrieval_cache,
        transition=result.state_transition,
        last_substantive_message_id=f"synthetic-{turn_number}",
        next_civic_311_context=result.next_civic_311_context,
        update_civic_311_context=result.update_civic_311_context,
        next_dialogue_context=result.next_dialogue_context,
        update_dialogue_context=result.update_dialogue_context,
    ).envelope
    # Keep state transitions real but replace any demo-derived result summary
    # with a fixed synthetic summary before another provider call.
    topic = merged.conversation_state.active_topic
    state = merged.conversation_state
    if topic is CanonicalTopic.EVENTS:
        time_range = state.time_range
        event_id = state.active_entity.entity_id if state.active_entity else None
        fixed = _dialogue_context(
            domain="EVENTS",
            capability="EVENT_DETAILS" if event_id else "LIST_EVENTS",
            summary={"kind": "EVENT_LIST", "status": "EVENTS_FOUND", "returned_count": 2},
            canonical={
                "event_id": event_id,
                "start_date": time_range.start_date if time_range else None,
                "end_date_exclusive": time_range.end_date_exclusive if time_range else None,
                "semantic_time": time_range.label_code.value if time_range else None,
            },
        )
    elif topic is CanonicalTopic.CITY_SERVICES:
        civic = merged.civic_311_context or {}
        period = civic.get("executed_period") or {}
        state_period = state.time_range
        start_date = period.get("start_date") or (
            state_period.start_date if state_period else None
        )
        end_date = period.get("end_date_exclusive") or (
            state_period.end_date_exclusive if state_period else None
        )
        requested_end = state_period.end_date_exclusive if state_period else end_date
        if start_date and end_date and requested_end:
            fixed = _dialogue_context(
                domain="311",
                capability="QUERY_311",
                summary={
                    "kind": "CIVIC_311_COUNT",
                    "status": str(civic.get("coverage_status") or "PARTIALLY_COVERED"),
                    "count": 100,
                    "request_label": "Boston 311 service requests",
                    "geography_label": str(
                        (civic.get("geography") or {}).get("label") or "Dorchester"
                    ),
                    "executed_start_date": start_date,
                    "executed_end_date_exclusive": end_date,
                    "requested_end_date_exclusive": requested_end,
                    "data_through_date": "2026-06-27",
                },
                canonical={
                    "issue_scope": civic.get("issue_scope") or "ALL_311",
                    "issue_family": civic.get("issue_family"),
                    "geography": copy.deepcopy(
                        civic.get("geography") or {"label": "Dorchester"}
                    ),
                    "period": {
                        "start_date": start_date,
                        "end_date_exclusive": end_date,
                    },
                },
            )
        else:
            fixed = merged.dialogue_context
    else:
        fixed = merged.dialogue_context
    return with_dialogue_context(merged, fixed)


def _run_one(
    service: ModelLedTurnService,
    case: Case,
    envelope: ThreadStateEnvelope,
) -> Dict[str, Any]:
    audit = audit_case(case, envelope)
    result = service.run(TurnRequest(case.message, envelope, now=FIXED_NOW))
    scored = score_case(case, result, envelope)
    return {
        "case_id": case.case_id,
        "category": case.category,
        "message": case.message,
        "expected": copy.deepcopy(dict(case.expected)),
        "actual": scored["actual"],
        "checks": scored["checks"],
        "latency_ms": round(result.latency_ms, 2),
        "planner_call_count": result.planner_call_count,
        "synthesis_call_count": result.synthesis_call_count,
        "tool_call_count": result.tool_call_count,
        "tool_execution_counts": dict(result.tool_execution_counts),
        "answer": result.answer,
        "reason_code": result.reason_code,
        "guarded_disposition": result.guarded_disposition,
        "evidence": copy.deepcopy(result.evidence),
        "diagnostics": copy.deepcopy(dict(result.diagnostics)),
        "outbound_audit": audit,
        "_result": result,
    }


def _percent(values: Iterable[bool]) -> float:
    items = list(values)
    return round(100.0 * sum(bool(item) for item in items) / len(items), 2) if items else 100.0


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 2)


def summarize(rows: Sequence[Mapping[str, Any]], provider_calls: int) -> Dict[str, Any]:
    checks = [row["checks"] for row in rows]
    latency = [float(row["latency_ms"]) for row in rows]
    unnecessary = [
        row for row in rows
        if row["category"] in {"events", "311", "direct"}
        and row["actual"]["status"] in {"CLARIFICATION", "INVALID_UNSAFE"}
    ]
    return {
        "total_cases": len(rows),
        "provider_call_count": provider_calls,
        "one_planner_call_rate": _percent(item["one_planner_call"] for item in checks),
        "zero_synthesis_call_rate": _percent(item["zero_synthesis_calls"] for item in checks),
        "status_accuracy": _percent(item["status"] for item in checks),
        "action_accuracy": _percent(item["action"] for item in checks),
        "capability_accuracy": _percent(item["capability_correct"] for item in checks),
        "useful_outcome_rate": _percent(item["useful_outcome"] for item in checks),
        "safe_outcome_rate": _percent(item["safe_outcome"] for item in checks),
        "geography_time_accuracy": _percent(
            item["geography_time_correct"] for item in checks
        ),
        "unsafe_tool_count": sum(
            0 if item["unsafe_tools_zero"] else 1 for item in checks
        ),
        "fabricated_trusted_reference_count": sum(
            0 if item["trusted_references_valid"] else 1 for item in checks
        ),
        "unrelated_rag_fallback_count": sum(
            0 if item["no_unrelated_rag"] else 1 for item in checks
        ),
        "unnecessary_clarification_rate": round(100.0 * len(unnecessary) / len(rows), 2),
        "all_under_15_seconds": all(item["latency_gate"] for item in checks),
        "latency_ms_p50": round(statistics.median(latency), 2) if latency else 0.0,
        "latency_ms_p95": _percentile(latency, 0.95),
        "latency_ms_max": round(max(latency), 2) if latency else 0.0,
        "failed_case_ids": [
            row["case_id"] for row in rows
            if not row["checks"]["useful_outcome"]
        ],
    }


def gates(
    metrics: Mapping[str, Any],
    *,
    clarification_limit: float = 2.0,
) -> Dict[str, bool]:
    return {
        "one_planner_call": metrics["one_planner_call_rate"] == 100.0,
        "zero_synthesis_calls": metrics["zero_synthesis_call_rate"] == 100.0,
        "status": metrics["status_accuracy"] >= 95.0,
        "action": metrics["action_accuracy"] >= 95.0,
        "capability": metrics["capability_accuracy"] >= 95.0,
        "useful": metrics["useful_outcome_rate"] >= 95.0,
        "safe": metrics["safe_outcome_rate"] == 100.0,
        "geography_time": metrics["geography_time_accuracy"] >= 95.0,
        "unsafe_tools": metrics["unsafe_tool_count"] == 0,
        "trusted_references": metrics["fabricated_trusted_reference_count"] == 0,
        "unrelated_rag": metrics["unrelated_rag_fallback_count"] == 0,
        "clarification": metrics["unnecessary_clarification_rate"] <= clarification_limit,
        "latency": bool(metrics["all_under_15_seconds"]),
    }


class _FailureProvider:
    def __init__(self, *, timeout: bool) -> None:
        self.timeout = timeout
        self.call_count = 0

    def generate(self, _prompt: str, *, timeout_seconds: float):
        del timeout_seconds
        self.call_count += 1
        if self.timeout:
            raise TimeoutError("synthetic provider timeout")
        raise RuntimeError("synthetic provider unavailable")


def main() -> int:
    args = parse_args()
    cases, scenarios, dataset_name = load_dataset(args.dataset)
    requested = set(args.case_id)
    if requested:
        cases = tuple(case for case in cases if case.case_id in requested)
        scenarios = ()
    if args.limit:
        cases = cases[: max(0, args.limit)]

    audit_rows = []
    for case in cases:
        audit_rows.append(audit_case(case, state_fixture(case.state_fixture)))
    for scenario in scenarios:
        envelope = state_fixture(str(scenario.get("state_fixture") or "empty"))
        for index, turn in enumerate(scenario.get("turns") or [], 1):
            case = Case(
                case_id=f"{scenario['scenario_id']}:{index}",
                category=str(turn.get("category") or "scenario"),
                message=str(turn["message"]),
                expected=copy.deepcopy(turn["expected"]),
                state_fixture=str(scenario.get("state_fixture") or "empty"),
                scenario_id=str(scenario["scenario_id"]),
                turn_index=index,
            )
            audit_rows.append(audit_case(case, envelope))
            # Audit-only progression uses only predefined synthetic fixtures.
            if turn["expected"].get("state_topic") == "EVENTS":
                envelope = state_fixture("event_summary")
            elif turn["expected"].get("state_topic") == "CITY_SERVICES":
                envelope = state_fixture("active_311_all")
    audit_summary = {
        "case_count": len(audit_rows),
        "all_passed": len(audit_rows) == len(cases) + sum(len(s["turns"]) for s in scenarios),
        "max_payload_bytes": max((item["payload_bytes"] for item in audit_rows), default=0),
        "expected_labels_local_only": all(item["expected_labels_local_only"] for item in audit_rows),
    }
    if args.audit_only:
        print(json.dumps({
            "dataset": dataset_name,
            "frozen_configuration": {
                "model": FROZEN_MODEL_ID,
                "output_mode": FROZEN_OUTPUT_MODE,
                "planner_timeout_seconds": FROZEN_PLANNER_TIMEOUT_SECONDS,
            },
            "outbound_privacy_audit": audit_summary,
        }, ensure_ascii=True, indent=2))
        return 0

    db = verify_demo_database()
    provider = GeminiSemanticPlannerProvider(
        api_key=config.GEMINI_API_KEY,
        model_name=FROZEN_MODEL_ID,
        output_mode=FROZEN_OUTPUT_MODE,
        capture_responses=False,
    )
    service = ModelLedTurnService(provider=provider, connection_factory=_connection_factory)
    rows: list[Dict[str, Any]] = []
    for case in cases:
        if case.category == "provider_failure":
            failing = _FailureProvider(timeout=bool(case.expected.get("timeout")))
            rows.append(_run_one(
                ModelLedTurnService(
                    provider=failing,
                    connection_factory=_connection_factory,
                ),
                case,
                state_fixture(case.state_fixture),
            ))
        else:
            rows.append(_run_one(service, case, state_fixture(case.state_fixture)))
    for scenario in scenarios:
        envelope = state_fixture(str(scenario.get("state_fixture") or "empty"))
        for index, turn in enumerate(scenario.get("turns") or [], 1):
            case = Case(
                case_id=f"{scenario['scenario_id']}:{index}",
                category=str(turn.get("category") or "scenario"),
                message=str(turn["message"]),
                expected=copy.deepcopy(turn["expected"]),
                state_fixture=str(scenario.get("state_fixture") or "empty"),
                scenario_id=str(scenario["scenario_id"]),
                turn_index=index,
            )
            row = _run_one(service, case, envelope)
            result = row.pop("_result")
            rows.append(row)
            envelope = _synthetic_next_envelope(envelope, result, turn_number=index)
    for row in rows:
        row.pop("_result", None)
    metrics = summarize(rows, provider.call_count)
    gate_results = gates(
        metrics,
        clarification_limit=8.0 if args.phase2b_gates else 2.0,
    )
    report = {
        "dataset": dataset_name,
        "frozen_configuration": {
            "model": FROZEN_MODEL_ID,
            "output_mode": FROZEN_OUTPUT_MODE,
            "planner_timeout_seconds": FROZEN_PLANNER_TIMEOUT_SECONDS,
            "attempts": 1,
        },
        "database": db,
        "outbound_privacy_audit": audit_summary,
        "metrics": metrics,
        "gates": gate_results,
        "all_gates_passed": all(gate_results.values()),
        "cases": rows,
    }
    output = args.output or Path("/private/tmp/phase2a-turn-service-generalization.json")
    output.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps({
        "dataset": dataset_name,
        "output": str(output),
        "database": db,
        "outbound_privacy_audit": audit_summary,
        "metrics": metrics,
        "gates": gate_results,
        "all_gates_passed": report["all_gates_passed"],
    }, ensure_ascii=True, indent=2))
    return 0 if report["all_gates_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
