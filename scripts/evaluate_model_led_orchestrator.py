#!/usr/bin/env python3
"""Run repeated health, smoke, and isolated semantic-action evaluations."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for candidate in (str(ROOT), str(PORCH)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import config  # noqa: E402
from model_led_orchestrator import normalize_resident_input  # noqa: E402
from gemini_semantic_planner_provider import (  # noqa: E402
    GeminiSemanticPlannerProvider,
    response_text as _response_text,
)
from orchestrator_evaluation import (  # noqa: E402
    EvaluationCase,
    evaluate_orchestrator,
    load_evaluation_dataset,
)
from sanitized_orchestrator_protocol import (  # noqa: E402
    FROZEN_MODEL_ID,
    FROZEN_OUTPUT_MODE,
    FROZEN_PLANNER_TIMEOUT_SECONDS,
    FROZEN_PROVIDER_MESSAGE_ADAPTER,
    PUBLIC_PROTOCOL_VERSION,
    SanitizedPublicEvaluationProtocol,
    audit_sanitized_provider_payload,
    build_sanitized_provider_payload,
    sanitized_generation_config,
)
from semantic_planner import SemanticPlan  # noqa: E402


DATASETS = {
    "smoke": ROOT / "evaluation" / "model_led_orchestrator" / "contract_smoke.json",
    "development": ROOT / "evaluation" / "model_led_orchestrator" / "development.json",
    "holdout": (
        ROOT / "evaluation" / "model_led_orchestrator"
        / "sealed_holdout_role_separated_v2.json"
    ),
}


class GeminiEvaluationProvider(GeminiSemanticPlannerProvider):
    """Evaluation wrapper that explicitly captures provider outputs off-runtime."""

    def __init__(self, model_name: str, *, output_mode: str) -> None:
        super().__init__(
            api_key=config.GEMINI_API_KEY,
            model_name=model_name,
            output_mode=output_mode,
            capture_responses=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASETS))
    parser.add_argument("--model")
    parser.add_argument("--output-mode", choices=("function", "plain"), default="function")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--health-repetitions", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--allow-sealed-holdout", action="store_true")
    parser.add_argument("--deadline-seconds", type=float, default=8.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--include-decisions", action="store_true")
    parser.add_argument("--raw-response-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.health_check:
        if not args.model:
            raise SystemExit("provider health check requires --model")
        return _run_health_check(args)
    if not args.dataset:
        raise SystemExit("evaluation requires --dataset")
    if args.dataset == "holdout" and not args.allow_sealed_holdout:
        raise SystemExit("holdout evaluation requires --allow-sealed-holdout")
    if args.dataset == "holdout" and (
        args.model != FROZEN_MODEL_ID
        or args.output_mode != FROZEN_OUTPUT_MODE
        or abs(args.deadline_seconds - FROZEN_PLANNER_TIMEOUT_SECONDS) > 0.001
    ):
        raise SystemExit(
            "holdout evaluation requires the frozen model, encoding, and planner timeout"
        )
    if not args.audit_only and not args.model:
        raise SystemExit("real evaluation requires --model")
    dataset_name, sealed, cases = load_evaluation_dataset(
        DATASETS[args.dataset],
        allow_sealed_holdout=args.allow_sealed_holdout,
    )
    if args.case_id:
        requested = set(args.case_id)
        cases = tuple(case for case in cases if case.case_id in requested)
        missing = requested.difference(case.case_id for case in cases)
        if missing:
            raise SystemExit(f"unknown case IDs: {', '.join(sorted(missing))}")
    if args.limit:
        cases = cases[: max(0, args.limit)]
    repetitions = max(1, int(args.repetitions))
    if args.dataset == "smoke" and not args.audit_only:
        repetitions = max(3, repetitions)
    expanded_cases = _repeat_cases(cases, repetitions)
    payloads, audit_summary = _audit_cases(cases, output_mode=args.output_mode)
    audit_summary["independent_repetitions"] = repetitions
    if args.audit_output:
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.write_text(json.dumps({
            "dataset_name": dataset_name,
            "protocol_version": PUBLIC_PROTOCOL_VERSION,
            "output_mode": args.output_mode,
            "generation_config": sanitized_generation_config(output_mode=args.output_mode),
            "exact_outbound_payloads": payloads,
        }, ensure_ascii=True, indent=2), encoding="utf-8")
    if args.audit_only:
        print(json.dumps({
            "dataset": dataset_name,
            "audit_output": str(args.audit_output) if args.audit_output else None,
            "outbound_audit": audit_summary,
        }, ensure_ascii=True, indent=2))
        return 0
    provider = GeminiEvaluationProvider(args.model, output_mode=args.output_mode)
    report = evaluate_orchestrator(
        provider,
        expanded_cases,
        dataset_name=dataset_name,
        sealed=sealed,
        model_name=args.model,
        per_case_deadline_seconds=max(3.0, min(12.0, args.deadline_seconds)),
        include_decisions=args.include_decisions,
        protocol=SanitizedPublicEvaluationProtocol(output_mode=args.output_mode),
    )
    payload = report.to_safe_dict(include_decisions=args.include_decisions)
    if args.raw_response_output:
        args.raw_response_output.parent.mkdir(parents=True, exist_ok=True)
        args.raw_response_output.write_text(json.dumps({
            "responses": provider.raw_responses,
            "provider_errors": provider.provider_errors,
        }, ensure_ascii=True, indent=2), encoding="utf-8")
    output = args.output or Path(
        f"/private/tmp/{args.dataset}-{args.model.replace('/', '_')}-{args.output_mode}-actions.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps({
        "dataset": dataset_name,
        "model": args.model,
        "output_mode": args.output_mode,
        "output": str(output),
        "protocol_version": PUBLIC_PROTOCOL_VERSION,
        "outbound_audit": audit_summary,
        "metrics": payload["metrics"],
    }, ensure_ascii=True, indent=2))
    return 0


def _repeat_cases(
    cases: Iterable[EvaluationCase], repetitions: int,
) -> tuple[EvaluationCase, ...]:
    return tuple(
        replace(case, case_id=f"{case.case_id}#trial{trial}")
        for trial in range(1, repetitions + 1)
        for case in cases
    )


def _run_health_check(args: argparse.Namespace) -> int:
    repetitions = max(3, int(args.health_repetitions))
    categories: Dict[str, list[dict[str, Any]]] = {
        "plain_text": [], "small_json": [], "function_selection": [], "gather_shape": [],
    }
    for _ in range(repetitions):
        _record_health(categories["plain_text"], lambda: _health_generate_basic(
            args.model, "Reply with only READY.", json_mode=False,
            timeout_seconds=args.deadline_seconds,
        ), lambda text: text.strip().rstrip(".!?").strip().upper() == "READY")
        _record_health(categories["small_json"], lambda: _health_generate_basic(
            args.model,
            'Return only JSON: {"status":"READY","kind":"HEALTH","confidence":1.0}',
            json_mode=True,
            timeout_seconds=args.deadline_seconds,
        ), _valid_small_health_json)
        _record_action_health(
            categories["function_selection"],
            args.model,
            "The resident said thanks. Choose the appropriate planner action.",
            expected_action="respond_directly",
            timeout_seconds=args.deadline_seconds,
            trusted_context=_health_context(),
        )
        gather_prompt = SanitizedPublicEvaluationProtocol(output_mode="function").build_prompt(
            normalize_resident_input("How many 311 requests were filed this week?"),
            trusted_context=_health_context(),
            available_trusted_reference_tokens=(),
        )
        _record_action_health(
            categories["gather_shape"],
            args.model,
            gather_prompt,
            expected_action="gather_evidence",
            timeout_seconds=args.deadline_seconds,
            trusted_context=_health_context(),
        )
    summary = {name: _health_summary(items) for name, items in categories.items()}
    all_results = [item for items in categories.values() for item in items]
    overall = _health_summary(all_results)
    eligible = (
        overall["availability"] >= 0.98
        and overall["structural_compliance_completed"] >= 0.99
        and overall["latency_ms_p95"] < 2500
    )
    print(json.dumps({
        "model": args.model,
        "repetitions_per_category": repetitions,
        "categories": summary,
        "overall": overall,
        "operationally_eligible": eligible,
    }, ensure_ascii=True, indent=2))
    return 0 if eligible else 2


def _record_health(target, operation, validator) -> None:
    started = time.perf_counter()
    try:
        text = operation()
    except Exception as exc:
        target.append({
            "provider_success": False,
            "structurally_valid": False,
            "semantically_expected": False,
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(exc).__name__[:80],
            "error": str(exc)[:300],
        })
        return
    try:
        valid = bool(validator(text))
    except Exception as exc:
        target.append({
            "provider_success": True,
            "structurally_valid": False,
            "semantically_expected": False,
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(exc).__name__[:80],
            "error": str(exc)[:300],
        })
        return
    target.append({
        "provider_success": True,
        "structurally_valid": valid,
        "semantically_expected": valid,
        "latency_ms": (time.perf_counter() - started) * 1000,
    })


def _record_action_health(
    target,
    model: str,
    prompt: str,
    *,
    expected_action: str,
    timeout_seconds: float,
    trusted_context: Mapping[str, Any],
) -> None:
    provider = GeminiEvaluationProvider(model, output_mode="function")
    started = time.perf_counter()
    response = None
    try:
        response = provider.generate(prompt, timeout_seconds=timeout_seconds)
    except Exception as exc:
        target.append({
            "provider_success": False,
            "structurally_valid": False,
            "semantically_expected": False,
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(exc).__name__[:80],
            "error": str(exc)[:300],
        })
        return
    try:
        decision = SemanticPlan.from_json(
            response.text,
            trusted_context=trusted_context,
            available_trusted_reference_tokens=(),
        )
        target.append({
            "provider_success": True,
            "structurally_valid": True,
            "semantically_expected": decision.action.value == expected_action,
            "latency_ms": (time.perf_counter() - started) * 1000,
        })
    except Exception as exc:
        target.append({
            "provider_success": True,
            "structurally_valid": False,
            "semantically_expected": False,
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(exc).__name__[:80],
            "error": str(exc)[:300],
        })


def _health_generate_basic(
    model: str,
    prompt: str,
    *,
    json_mode: bool,
    timeout_seconds: float,
) -> str:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    client = genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(
            timeout=max(10_000, int(max(1.0, timeout_seconds - 0.5) * 1000)),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=100,
                response_mime_type="application/json" if json_mode else "text/plain",
            ),
        )
        return _response_text(response)
    finally:
        client.close()


def _valid_small_health_json(text: str) -> bool:
    parsed = json.loads(text)
    return set(parsed) == {"status", "kind", "confidence"} \
        and parsed["status"] == "READY" and parsed["kind"] == "HEALTH"


def _health_context() -> Dict[str, Any]:
    return {
        "default_geography": {
            "scope": "NEIGHBORHOOD", "location_id": "dorchester", "name": "Dorchester",
        },
        "active_topic": None,
        "active_entity": None,
        "time_scope": None,
        "previous_result_summary": None,
    }


def _health_summary(results: list[dict[str, Any]]) -> Dict[str, Any]:
    calls = len(results)
    completed = [item for item in results if item["provider_success"]]
    valid = [item for item in completed if item["structurally_valid"]]
    semantically_expected = [item for item in valid if item["semantically_expected"]]
    latencies = sorted(item["latency_ms"] for item in results)
    return {
        "calls": calls,
        "successes": len(completed),
        "failures": calls - len(completed),
        "availability": round(len(completed) / calls, 4) if calls else 0.0,
        "structural_compliance_completed": (
            round(len(valid) / len(completed), 4) if completed else 0.0
        ),
        "semantic_expectation_accuracy_valid": (
            round(len(semantically_expected) / len(valid), 4) if valid else 0.0
        ),
        "latency_ms_p50": round(_percentile(latencies, 50), 2),
        "latency_ms_p95": round(_percentile(latencies, 95), 2),
        "error_types": sorted({item.get("error_type") for item in results if item.get("error_type")}),
    }


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile / 100
    lower = int(position)
    upper = min(len(values) - 1, lower + 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _audit_cases(cases, *, output_mode: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    payload_bytes: list[int] = []
    payload_hashes: list[str] = []
    generation_audit = audit_sanitized_provider_payload({
        "generation_config": sanitized_generation_config(output_mode=output_mode),
    })
    for case in cases:
        payload = build_sanitized_provider_payload(
            normalize_resident_input(case.resident_message),
            trusted_context=case.trusted_context,
            available_trusted_reference_tokens=case.trusted_reference_tokens,
            output_mode=output_mode,
        )
        audit = audit_sanitized_provider_payload(payload)
        payloads.append(payload)
        payload_bytes.append(audit.payload_bytes)
        payload_hashes.append(audit.payload_sha256)
    return payloads, {
        "case_count": len(payloads),
        "minimum_payload_bytes": min(payload_bytes) if payload_bytes else 0,
        "maximum_payload_bytes": max(payload_bytes) if payload_bytes else 0,
        "unique_payload_hashes": len(set(payload_hashes)),
        "generation_config_sha256": generation_audit.payload_sha256,
        "provider_message_adapter": FROZEN_PROVIDER_MESSAGE_ADAPTER,
        "private_marker_failures": 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())
