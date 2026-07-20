#!/usr/bin/env python3
"""Isolation, scoring, dataset, and shadow tests for semantic evaluation."""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
sys.path[:0] = [str(PORCH), str(ROOT)]

import config  # noqa: E402
from orchestrator_evaluation import (  # noqa: E402
    EvaluationError,
    ProviderResponse,
    assert_disjoint_paraphrase_families,
    evaluate_orchestrator,
    load_evaluation_dataset,
)
from orchestrator_shadow import OrchestratorMode, parse_orchestrator_mode, preserve_authoritative_result  # noqa: E402

DATA = ROOT / "evaluation" / "model_led_orchestrator"


def direct_payload() -> dict:
    return {
        "schema_version": 1,
        "strategy": "DIRECT_RESPONSE",
        "relationship": "SMALL_TALK",
        "user_goal": "Respond politely.",
        "tasks": [],
        "trusted_summary_reference": None,
        "state_intent": "PRESERVE",
        "direct_response": "You're welcome.",
        "clarification_question": None,
        "confidence": 0.99,
    }


class StaticProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def generate(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        self.calls += 1
        assert "PLAN_OBJECT=" in prompt and "Registered tools" not in prompt and timeout_seconds > 0
        return ProviderResponse(json.dumps(self.payload), 100, 30, "static")


class FailingProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def generate(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        self.calls += 1
        raise self.error


class SlowProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        self.calls += 1
        time.sleep(timeout_seconds + 0.25)
        return ProviderResponse(json.dumps(direct_payload()))


def test_dataset_shape_and_separation() -> None:
    _, _, smoke = load_evaluation_dataset(DATA / "contract_smoke.json")
    assert 18 <= len(smoke) <= 22
    _, sealed_dev, dev = load_evaluation_dataset(DATA / "development.json")
    assert not sealed_dev and len(dev) == 100 and len({item.family_id for item in dev}) == 25
    try:
        load_evaluation_dataset(DATA / "sealed_holdout.json")
    except EvaluationError:
        pass
    else:
        raise AssertionError("holdout labels loaded without explicit access")
    _, sealed_hold, hold = load_evaluation_dataset(
        DATA / "sealed_holdout.json", allow_sealed_holdout=True,
    )
    assert sealed_hold and len(hold) == 60 and len({item.family_id for item in hold}) == 20
    assert_disjoint_paraphrase_families(dev, hold)


def test_scoring_provider_parity_and_no_mutation() -> None:
    _, _, dev = load_evaluation_dataset(DATA / "development.json")
    case = next(item for item in dev if item.family_id == "dev_smalltalk_thanks")
    original = copy.deepcopy(case.trusted_context)
    left = StaticProvider(direct_payload())
    right = StaticProvider(direct_payload())
    left_report = evaluate_orchestrator(left, [case], dataset_name="test", sealed=False, model_name="left")
    right_report = evaluate_orchestrator(right, [case], dataset_name="test", sealed=False, model_name="right")
    assert left.calls == right.calls == 1
    assert left_report.metrics["strategy_accuracy"] == 1.0
    assert left_report.metrics["compiled_tool_set_accuracy"] == 1.0
    assert left_report.case_results[0].scores == right_report.case_results[0].scores
    assert case.trusted_context == original
    assert left_report.case_results[0].model_calls == 1


def test_provider_failure_and_timeout_are_single_call() -> None:
    _, _, dev = load_evaluation_dataset(DATA / "development.json")
    case = dev[0]
    failed = FailingProvider(RuntimeError("provider unavailable"))
    report = evaluate_orchestrator(failed, [case], dataset_name="test", sealed=False, model_name="failed")
    assert failed.calls == 1 and report.metrics["provider_failure_count"] == 1
    assert report.case_results[0].model_calls == 1

    timed = FailingProvider(TimeoutError("deadline"))
    report = evaluate_orchestrator(timed, [case], dataset_name="test", sealed=False, model_name="timed")
    assert timed.calls == 1 and report.metrics["provider_timeout_count"] == 1

    slow = SlowProvider()
    started = time.perf_counter()
    report = evaluate_orchestrator(
        slow, [case], dataset_name="test", sealed=False, model_name="slow",
        per_case_deadline_seconds=2.0,
    )
    assert slow.calls == 1 and report.metrics["provider_timeout_count"] == 1
    assert time.perf_counter() - started < 1.3


def test_trusted_summary_fact_safety_metric() -> None:
    _, _, holdout = load_evaluation_dataset(
        DATA / "sealed_holdout.json", allow_sealed_holdout=True,
    )
    case = next(item for item in holdout if item.family_id == "holdout_trusted_summary_rephrase")
    payload = direct_payload()
    payload.update({
        "relationship": "FOLLOW_UP",
        "user_goal": "Rephrase the trusted previous result.",
        "trusted_summary_reference": "summary:311:42",
        "direct_response": "There were 99 requests in the covered period.",
    })
    report = evaluate_orchestrator(
        StaticProvider(payload), [case], dataset_name="test", sealed=True, model_name="static",
    )
    assert report.metrics["direct_response_fact_safety_failure_count"] == 1
    assert report.metrics["unsafe_decision_rate"] == 1.0


def test_no_tool_execution_surface() -> None:
    import orchestrator_evaluation as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "execute_tool" not in source
    assert "mysql" not in source.lower()
    assert ".execute(" not in source


def test_shadow_non_authority_and_default_off() -> None:
    authoritative = {"answer": "authoritative", "sources": ["one"]}
    calls = {"count": 0}

    def shadow() -> dict:
        calls["count"] += 1
        return {"strategy": "GATHER_EVIDENCE", "tasks": []}

    returned, diagnostic = preserve_authoritative_result(
        authoritative, mode=OrchestratorMode.SHADOW, shadow_call=shadow,
    )
    assert returned is authoritative and calls["count"] == 1 and diagnostic.completed
    returned, diagnostic = preserve_authoritative_result(
        authoritative, mode=OrchestratorMode.OFF, shadow_call=shadow,
    )
    assert returned is authoritative and calls["count"] == 1 and not diagnostic.attempted
    assert parse_orchestrator_mode("invalid") is OrchestratorMode.OFF
    configured = os.getenv("MODEL_LED_ORCHESTRATOR_MODE", "off").lower()
    assert config.MODEL_LED_ORCHESTRATOR_MODE == (configured if configured in {"off", "offline", "shadow"} else "off")

    started = time.perf_counter()
    returned, diagnostic = preserve_authoritative_result(
        authoritative,
        mode=OrchestratorMode.SHADOW,
        shadow_call=lambda: (time.sleep(0.25) or {}),
        timeout_seconds=0.05,
    )
    assert returned is authoritative and diagnostic.error_type == "TimeoutError"
    assert time.perf_counter() - started < 0.2


def test_current_runtime_not_wired() -> None:
    api_source = (ROOT / "api" / "api_v2.py").read_text(encoding="utf-8")
    assert "semantic_planner" not in api_source
    assert "semantic_plan_compiler" not in api_source
    assert "model_led_orchestrator" not in api_source


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"model-led semantic evaluation tests passed: {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
