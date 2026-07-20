#!/usr/bin/env python3
"""Run compact semantic-plan health, smoke, and isolated evaluations."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for candidate in (str(ROOT), str(PORCH)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import config  # noqa: E402
from model_led_orchestrator import normalize_resident_input  # noqa: E402
from orchestrator_evaluation import (  # noqa: E402
    ProviderResponse,
    evaluate_orchestrator,
    load_evaluation_dataset,
)
from sanitized_orchestrator_protocol import (  # noqa: E402
    PUBLIC_PROTOCOL_VERSION,
    SanitizedPublicEvaluationProtocol,
    audit_sanitized_provider_payload,
    build_sanitized_provider_payload,
    sanitized_generation_config,
)


DATASETS = {
    "smoke": ROOT / "evaluation" / "model_led_orchestrator" / "contract_smoke.json",
    "development": ROOT / "evaluation" / "model_led_orchestrator" / "development.json",
    "holdout": ROOT / "evaluation" / "model_led_orchestrator" / "sealed_holdout.json",
}


class GeminiEvaluationProvider:
    """Maintained-SDK one-call, no-retry adapter used only by offline evaluation."""

    def __init__(self, model_name: str, *, output_mode: str) -> None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as exc:
            raise RuntimeError("the maintained google-genai SDK is not installed") from exc
        self._genai = genai
        self._types = types
        self.model_name = model_name
        self.output_mode = output_mode
        self.raw_responses: list[str] = []
        self.provider_errors: list[dict[str, Any]] = []
        self.call_count = 0
        self._result_lock = threading.Lock()

    def generate(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        with self._result_lock:
            self.call_count += 1
            call_index = self.call_count
        timeout_ms = max(1_000, int(max(1.0, float(timeout_seconds) - 0.5) * 1000))
        client = self._genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=self._types.HttpOptions(
                timeout=timeout_ms,
                retry_options=self._types.HttpRetryOptions(attempts=1),
            ),
        )
        generation = sanitized_generation_config(output_mode=self.output_mode)
        generation_config = self._types.GenerateContentConfig(
            temperature=generation["temperature"],
            max_output_tokens=generation["max_output_tokens"],
            response_mime_type=generation["response_mime_type"],
            response_schema=generation.get("response_schema"),
        )
        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=generation_config,
            )
        except Exception as exc:
            with self._result_lock:
                self.provider_errors.append({
                    "call_index": call_index,
                    "error_type": type(exc).__name__[:80],
                    "message": str(exc)[:500],
                })
            raise
        finally:
            client.close()
        usage = getattr(response, "usage_metadata", None)
        text = _response_text(response)
        with self._result_lock:
            self.raw_responses.append(text)
        return ProviderResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
            provider_model=self.model_name,
        )


def _response_text(response: Any) -> str:
    try:
        if response.text:
            return str(response.text).strip()
    except Exception:
        pass
    parts: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                parts.append(str(part.text))
    return "".join(parts).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASETS))
    parser.add_argument("--model")
    parser.add_argument("--output-mode", choices=("native", "plain"), default="native")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--allow-sealed-holdout", action="store_true")
    parser.add_argument("--deadline-seconds", type=float, default=12.0)
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
    payloads, audit_summary = _audit_cases(cases, output_mode=args.output_mode)
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
        cases,
        dataset_name=dataset_name,
        sealed=sealed,
        model_name=args.model,
        per_case_deadline_seconds=max(10.0, min(15.0, args.deadline_seconds)),
        include_decisions=args.include_decisions,
        protocol=SanitizedPublicEvaluationProtocol(),
    )
    payload = report.to_safe_dict(include_decisions=args.include_decisions)
    if args.raw_response_output:
        args.raw_response_output.parent.mkdir(parents=True, exist_ok=True)
        args.raw_response_output.write_text(json.dumps({
            "responses": provider.raw_responses,
            "provider_errors": provider.provider_errors,
        }, ensure_ascii=True, indent=2), encoding="utf-8")
    output = args.output or Path(
        f"/private/tmp/{args.dataset}-{args.model.replace('/', '_')}-{args.output_mode}-semantic.json"
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


def _run_health_check(args: argparse.Namespace) -> int:
    cases = [
        ("plain", "Reply with only READY.", "plain"),
        (
            "simple_json",
            'Return only JSON: {"status":"READY","kind":"HEALTH","confidence":1.0}',
            "plain",
        ),
        (
            "enum_json",
            'Return only JSON: {"strategy":"DIRECT_RESPONSE","confidence":1.0}',
            "native",
        ),
    ]
    results: list[Dict[str, Any]] = []
    for name, prompt, mode in cases:
        started = time.perf_counter()
        try:
            result = _health_generate(args.model, prompt, mode=mode, timeout_seconds=args.deadline_seconds)
            valid = bool(result)
            if name == "simple_json":
                parsed = json.loads(result)
                valid = set(parsed) == {"status", "kind", "confidence"}
            elif name == "enum_json":
                parsed = json.loads(result)
                valid = parsed.get("strategy") == "DIRECT_RESPONSE" and set(parsed) == {
                    "strategy", "confidence",
                }
            results.append({
                "name": name,
                "provider_success": True,
                "contract_valid": valid,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            })
        except Exception as exc:
            results.append({
                "name": name,
                "provider_success": False,
                "contract_valid": False,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            })
    print(json.dumps({"model": args.model, "health": results}, ensure_ascii=True, indent=2))
    return 0 if all(item["provider_success"] and item["contract_valid"] for item in results) else 2


def _health_generate(model: str, prompt: str, *, mode: str, timeout_seconds: float) -> str:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    client = genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(
            timeout=max(10_000, int(timeout_seconds * 1000)),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    if mode == "native":
        schema = {
            "type": "object",
            "properties": {
                "strategy": {"type": "string", "enum": ["DIRECT_RESPONSE", "GATHER_EVIDENCE"]},
                "confidence": {"type": "number"},
            },
            "required": ["strategy", "confidence"],
        }
    else:
        schema = None
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=100,
                response_mime_type="application/json" if name_requires_json(prompt) else "text/plain",
                response_schema=schema,
            ),
        )
        return _response_text(response)
    finally:
        client.close()


def name_requires_json(prompt: str) -> bool:
    return "JSON" in prompt


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
        "private_marker_failures": 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())
