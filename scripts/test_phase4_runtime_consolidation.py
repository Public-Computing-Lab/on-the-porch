#!/usr/bin/env python3
"""Focused Phase 4 architecture gates (no sealed holdout)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
PORCH = ROOT / "on_the_porch"
sys.path[:0] = [str(ROOT), str(API), str(PORCH)]


def test_shared_conversation_turn_module_exists():
    from conversation_turn import (
        build_authoritative_turn_proposal,
        model_led_finalization_proposal,
    )

    assert callable(build_authoritative_turn_proposal)
    assert callable(model_led_finalization_proposal)


def test_api_uses_shared_authoritative_executor():
    src = (API / "api_v2.py").read_text(encoding="utf-8")
    assert "build_authoritative_turn_proposal" in src
    assert "_execute_authoritative_resident_turn" in src
    # Authoritative path must not prepare legacy semantics after model-led miss.
    assert "model_led_legacy_handoff" not in src or src.count("_log_model_led_legacy_handoff") <= 1
    # The live handoff call sites should be gone from generation.
    tree = ast.parse(src)
    call_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    # Helper may remain defined for rollback tooling, but generation must use the
    # shared executor name.
    assert "_execute_authoritative_resident_turn" in src


def test_turn_service_has_no_active_not_yet_migrated_path():
    src = (PORCH / "model_led_turn_service.py").read_text(encoding="utf-8")
    assert "DOMAIN_UNSUPPORTED" in src
    assert "SemanticDomain.LOCATION" in src
    # Status enum may remain for offline compat, but resident path clarifies.
    assert "_unsupported_domain" in src


def test_repair_llm_disabled_on_resident_311_rollback_helper():
    src = (API / "api_v2.py").read_text(encoding="utf-8")
    assert "repair_llm=None" in src
    assert "repair_llm=synthesis" not in src


def test_orchestrator_shadow_is_offline_only():
    assert not (PORCH / "orchestrator_shadow.py").exists()
    assert (ROOT / "archive/offline_evaluation/orchestrator_shadow.py").exists()


def test_authoritative_proposal_has_no_legacy_fields():
    from conversation_state import ConversationState, ProposedTransition, ThreadStateEnvelope
    from conversation_turn import build_authoritative_turn_proposal
    from emergency_routing import (
        EmergencyCategory,
        EmergencyDecision,
        EmergencyDecisionSource,
        EmergencyReasonCode,
    )
    from model_led_turn_service import TurnServiceStatus

    class FakeResult:
        status = TurnServiceStatus.COMPLETED
        update_civic_311_context = False
        next_civic_311_context = None
        next_dialogue_context = None
        update_dialogue_context = False

        def __init__(self, state):
            self.state_transition = ProposedTransition.preserve(state)

        def to_agent_result(self, retrieval_cache):
            return {
                "answer": "hello",
                "mode": "model_led_direct",
                "sources": [],
                "result": {"answer": "hello"},
                "retrieval_cache": retrieval_cache,
            }

    envelope = ThreadStateEnvelope(retrieval_cache={})
    fake = FakeResult(envelope.conversation_state)

    def run_model_led(*_a, **_k):
        return fake

    def emergency_agent(*_a, **_k):
        raise AssertionError("emergency path unexpected")

    def model_led_agent(result, *, retrieval_cache):
        return result.to_agent_result(retrieval_cache=retrieval_cache)

    def emergency_transition(_state):
        raise AssertionError("emergency transition unexpected")

    proposal = build_authoritative_turn_proposal(
        emergency_decision=EmergencyDecision(
            is_active_emergency=False,
            category=EmergencyCategory.NONE,
            reason_code=EmergencyReasonCode.NO_MATCH,
            decision_source=EmergencyDecisionSource.RULE,
            confidence=0.0,
        ),
        detector_latency_ms=0.0,
        retrieval_cache={},
        conversation_state=envelope.conversation_state,
        state_envelope=envelope,
        event_resolution=None,
        message="hi",
        request_started_at=0.0,
        activation_mode="authoritative",
        run_model_led=run_model_led,
        emergency_agent_result=emergency_agent,
        model_led_agent_result=model_led_agent,
        emergency_transition=emergency_transition,
        unsupported_status=TurnServiceStatus.NOT_YET_MIGRATED,
    )
    assert proposal.path == "model_led"
    assert proposal.agent_result["answer"] == "hello"
    assert proposal.civic_311_request is False
    assert proposal.relationship is None
    assert proposal.dialogue_decision is None


def main() -> int:
    tests = [
        test_shared_conversation_turn_module_exists,
        test_api_uses_shared_authoritative_executor,
        test_turn_service_has_no_active_not_yet_migrated_path,
        test_repair_llm_disabled_on_resident_311_rollback_helper,
        test_orchestrator_shadow_is_offline_only,
        test_authoritative_proposal_has_no_legacy_fields,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
