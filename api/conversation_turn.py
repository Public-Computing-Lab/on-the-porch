"""Transport-neutral resident turn execution for JSON and SSE.

Handlers in ``api_v2`` own auth, ownership, HTTP/SSE framing, and response
serialization. This module owns the shared generation and finalization proposal
used by both transports under authoritative mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional


@dataclass(frozen=True)
class ConversationTurnProposal:
    """Persistence-ready proposal shared by JSON and SSE handlers."""

    agent_result: Dict[str, Any]
    model_led_turn_result: Optional[Any]
    model_led_diagnostics_result: Optional[Any]
    conversation_transition: Optional[Any]
    transition: Any
    persisted_cache: Mapping[str, Any]
    invalidate_retrieval_cache: bool
    civic_311_context_update: bool
    civic_311_next_context: Optional[Mapping[str, Any]]
    dialogue_next_context: Optional[Mapping[str, Any]]
    dialogue_context_update: bool
    relationship: Optional[Any]
    dimensions: Optional[Any]
    dialogue_decision: Optional[Any]
    event_query_plan: Optional[Any]
    civic_311_request: bool
    active_event_applied: bool
    path: str  # emergency | model_led | legacy_rollback


def model_led_finalization_proposal(
    result: Any,
    *,
    next_cache: Mapping[str, Any],
) -> Dict[str, Any]:
    """Map a completed model-led turn into persistence fields."""
    return {
        "transition": result.state_transition,
        "persisted_cache": next_cache,
        "invalidate_retrieval_cache": False,
        "civic_311_context_update": result.update_civic_311_context,
        "civic_311_next_context": result.next_civic_311_context,
        "dialogue_next_context": result.next_dialogue_context,
        "dialogue_context_update": result.update_dialogue_context,
        "conversation_transition": None,
    }


def build_authoritative_turn_proposal(
    *,
    emergency_decision: Any,
    detector_latency_ms: float,
    retrieval_cache: Mapping[str, Any],
    conversation_state: Any,
    state_envelope: Any,
    event_resolution: Optional[Any],
    message: str,
    request_started_at: float,
    activation_mode: str,
    run_model_led: Callable[..., Any],
    emergency_agent_result: Callable[..., Dict[str, Any]],
    model_led_agent_result: Callable[..., Dict[str, Any]],
    emergency_transition: Callable[..., Any],
    unsupported_status: Any,
) -> ConversationTurnProposal:
    """Run the authoritative lifecycle without legacy semantic authority."""
    if emergency_decision.is_active_emergency:
        agent_result = emergency_agent_result(
            emergency_decision,
            retrieval_cache,
            detector_latency_ms=detector_latency_ms,
        )
        conversation_transition = emergency_transition(conversation_state)
        return ConversationTurnProposal(
            agent_result=agent_result,
            model_led_turn_result=None,
            model_led_diagnostics_result=None,
            conversation_transition=conversation_transition,
            transition=conversation_transition.state_proposal(conversation_state),
            persisted_cache=retrieval_cache,
            invalidate_retrieval_cache=False,
            civic_311_context_update=False,
            civic_311_next_context=None,
            dialogue_next_context=None,
            dialogue_context_update=False,
            relationship=None,
            dimensions=None,
            dialogue_decision=None,
            event_query_plan=None,
            civic_311_request=False,
            active_event_applied=False,
            path="emergency",
        )

    model_led_turn_result = run_model_led(
        message,
        state_envelope=state_envelope,
        event_resolution=event_resolution,
        request_started_at=request_started_at,
        activation_mode=activation_mode,
    )
    # Defensive: the turn service no longer emits NOT_YET_MIGRATED for resident
    # traffic, but never hand off to legacy phrase routers if it appears.
    if getattr(model_led_turn_result, "status", None) is unsupported_status:
        from conversation_state import ProposedTransition  # noqa: WPS433

        agent_result = {
            "answer": (
                "I can help with Dorchester events, Boston 311, public-safety data, "
                "civic documents, or a registered neighborhood. Which should I check?"
            ),
            "mode": "model_led_clarification",
            "sources": [],
            "result": {
                "answer": (
                    "I can help with Dorchester events, Boston 311, public-safety data, "
                    "civic documents, or a registered neighborhood. Which should I check?"
                ),
                "reason_code": "DOMAIN_UNSUPPORTED",
            },
            "retrieval_cache": retrieval_cache,
        }
        preserved = ProposedTransition.preserve(conversation_state)
        return ConversationTurnProposal(
            agent_result=agent_result,
            model_led_turn_result=None,
            model_led_diagnostics_result=model_led_turn_result,
            conversation_transition=None,
            transition=preserved,
            persisted_cache=retrieval_cache,
            invalidate_retrieval_cache=False,
            civic_311_context_update=False,
            civic_311_next_context=state_envelope.civic_311_context,
            dialogue_next_context=state_envelope.dialogue_context,
            dialogue_context_update=False,
            relationship=None,
            dimensions=None,
            dialogue_decision=None,
            event_query_plan=None,
            civic_311_request=False,
            active_event_applied=False,
            path="model_led",
        )

    agent_result = model_led_agent_result(
        model_led_turn_result,
        retrieval_cache=retrieval_cache,
    )
    finals = model_led_finalization_proposal(
        model_led_turn_result,
        next_cache=agent_result["retrieval_cache"],
    )
    return ConversationTurnProposal(
        agent_result=agent_result,
        model_led_turn_result=model_led_turn_result,
        model_led_diagnostics_result=model_led_turn_result,
        conversation_transition=finals["conversation_transition"],
        transition=finals["transition"],
        persisted_cache=finals["persisted_cache"],
        invalidate_retrieval_cache=finals["invalidate_retrieval_cache"],
        civic_311_context_update=finals["civic_311_context_update"],
        civic_311_next_context=finals["civic_311_next_context"],
        dialogue_next_context=finals["dialogue_next_context"],
        dialogue_context_update=finals["dialogue_context_update"],
        relationship=None,
        dimensions=None,
        dialogue_decision=None,
        event_query_plan=None,
        civic_311_request=False,
        active_event_applied=False,
        path="model_led",
    )
