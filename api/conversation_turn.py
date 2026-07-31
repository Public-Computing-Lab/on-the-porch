"""Transport-neutral turn lifecycle shared by JSON and SSE delivery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional

from turn_repository import (
    ReservationKind,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    TurnRepository,
    TurnReservation,
)
from concurrency_control import CapacityUnavailable, OVERLOAD_RETRY_AFTER_SECONDS
from on_the_porch.request_budget import (
    BudgetExhausted,
    TurnBudget,
    create_turn_budget,
    use_turn_budget,
)


class TurnEventKind(str, Enum):
    DELTA = "delta"
    CORRECTION = "correction"
    FINAL = "final"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class TurnRequest:
    thread_id: str
    user_id: str
    session_id: str
    client_turn_id: str
    client_idempotent: bool
    request_fingerprint: str
    message: str
    transport: str
    trace_id: Optional[str] = None


@dataclass(frozen=True)
class TurnContext:
    request: TurnRequest
    reservation: TurnReservation
    thread: Mapping[str, Any]
    history_rows: list[dict[str, Any]]
    conversation_history: list[dict[str, str]]
    thread_state: dict[str, Any]
    budget: Optional[TurnBudget] = None


@dataclass(frozen=True)
class GeneratedTurn:
    answer: str
    mode: str
    sources: list[dict[str, Any]]
    next_thread_state: dict[str, Any]
    result_payload: dict[str, Any]


@dataclass(frozen=True)
class TurnEvent:
    kind: TurnEventKind
    payload: Any


Producer = Callable[[TurnContext], Iterable[TurnEvent]]
ContextLoader = Callable[[TurnRequest, TurnReservation], TurnContext]
Finalizer = Callable[[TurnContext, GeneratedTurn], Mapping[str, Any]]


def build_sse_final_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve the legacy SSE fields around the authoritative stored payload."""
    result = dict(payload)
    assistant = result.get("assistant_message")
    assistant = assistant if isinstance(assistant, Mapping) else {}
    result.setdefault("answer", assistant.get("content", ""))
    result.setdefault("mode", assistant.get("response_mode", ""))
    result.setdefault("sources", assistant.get("sources") or [])
    return {"type": "final", **result}


class ConversationTurnService:
    """Coordinates one execution while leaving conversational semantics untouched."""

    def __init__(
        self,
        repository: TurnRepository,
        *,
        load_context: ContextLoader,
        finalize: Finalizer,
        normalize_error: Callable[[BaseException], str],
        observe: Optional[Callable[..., None]] = None,
    ) -> None:
        self.repository = repository
        self.load_context = load_context
        self.finalize = finalize
        self.normalize_error = normalize_error
        self.observe = observe or (lambda **_fields: None)

    @staticmethod
    def _with_replay_metadata(
        payload: Mapping[str, Any],
        reservation: TurnReservation,
        *,
        replayed: bool,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        result = dict(payload)
        result["turn"] = {
            "client_turn_id": reservation.client_turn_id,
            "turn_id": reservation.turn_id,
            "status": (status or reservation.status or STATUS_COMPLETED).lower(),
            "replayed": replayed,
            "client_idempotent": reservation.client_idempotent,
        }
        return result

    def execute(
        self,
        request: TurnRequest,
        producer: Producer,
    ) -> Iterable[TurnEvent]:
        reservation = self.repository.reserve(
            thread_id=request.thread_id,
            user_id=request.user_id,
            client_turn_id=request.client_turn_id,
            request_fingerprint=request.request_fingerprint,
            transport_origin=request.transport,
            client_idempotent=request.client_idempotent,
            trace_id=request.trace_id,
        )
        self.observe(
            client_turn_id=request.client_turn_id,
            internal_turn_id=reservation.turn_id,
            reservation_result=reservation.kind.value,
            turn_state=(reservation.status or "").lower() or None,
            execution_owner=reservation.owns_execution,
            client_idempotent=request.client_idempotent,
        )
        if reservation.kind is ReservationKind.COMPLETED_REPLAY:
            self.observe(
                replayed=True,
                replay_source="mysql_turn_payload",
                duplicate_suppression_count=1,
                replay_bypassed_expensive_work=True,
            )
            yield TurnEvent(
                TurnEventKind.FINAL,
                self._with_replay_metadata(
                    reservation.response_payload or {},
                    reservation,
                    replayed=True,
                ),
            )
            return
        if reservation.kind in {
            ReservationKind.RUNNING_EXISTING,
            ReservationKind.THREAD_BUSY,
        }:
            self.observe(
                same_thread_contention=True,
                duplicate_suppression_count=1,
            )
            yield TurnEvent(
                TurnEventKind.IN_PROGRESS,
                {
                    "code": "turn_in_progress",
                    "client_turn_id": request.client_turn_id,
                    "status": (reservation.status or "running").lower(),
                    "retry_after": 2,
                },
            )
            return
        if reservation.kind is ReservationKind.FAILED_REPLAY:
            self.observe(
                replayed=True,
                replay_source="mysql_failed_turn",
                duplicate_suppression_count=1,
            )
            yield TurnEvent(
                TurnEventKind.FAILED,
                self._failure_payload(
                    request.client_turn_id,
                    reservation.error_category or "turn_failed",
                    replayed=True,
                ),
            )
            return
        if reservation.kind is ReservationKind.FINGERPRINT_CONFLICT:
            self.observe(fingerprint_conflict=True)
            yield TurnEvent(
                TurnEventKind.CONFLICT,
                {
                    "code": "client_turn_id_conflict",
                    "error": "This message identifier was already used.",
                },
            )
            return
        if reservation.kind is ReservationKind.OWNERSHIP_FAILURE:
            yield TurnEvent(
                TurnEventKind.NOT_FOUND,
                {"code": "conversation_not_found", "error": "Conversation not found."},
            )
            return
        if not reservation.owns_execution:
            yield TurnEvent(
                TurnEventKind.IN_PROGRESS,
                {"code": "turn_in_progress", "retry_after": 2},
            )
            return

        budget = create_turn_budget(reservation.turn_id)
        self.observe(
            total_budget_ms=int(budget.total_seconds * 1000),
            initial_remaining_budget_ms=budget.remaining_ms(),
            finalization_reserve_ms=int(budget.finalization_reserve_seconds * 1000),
        )
        context: Optional[TurnContext] = None
        with use_turn_budget(budget):
            try:
                budget.raise_if_exhausted("context_load")
                context = replace(
                    self.load_context(request, reservation),
                    budget=budget,
                )
                if not self.repository.mark_running(reservation, user_id=request.user_id):
                    raise RuntimeError("turn_execution_ownership_lost")
                self.observe(turn_state=STATUS_RUNNING.lower())
                generated: Optional[GeneratedTurn] = None
                for event in producer(context):
                    if event.kind is TurnEventKind.FINAL:
                        if not isinstance(event.payload, GeneratedTurn):
                            raise TypeError("producer final payload must be GeneratedTurn")
                        generated = event.payload
                    else:
                        yield event
                if generated is None:
                    raise RuntimeError("turn_generation_empty")
                budget.raise_if_exhausted(
                    "final_persistence",
                    reserve_finalization=False,
                )
                final_payload = self.finalize(context, generated)
                self.observe(
                    turn_state=STATUS_COMPLETED.lower(),
                    remaining_budget_ms=budget.remaining_ms(
                        reserve_finalization=False
                    ),
                    completion_within_budget=not budget.is_exhausted(
                        reserve_finalization=False
                    ),
                )
                yield TurnEvent(
                    TurnEventKind.FINAL,
                    self._with_replay_metadata(
                        final_payload,
                        reservation,
                        replayed=False,
                        status=STATUS_COMPLETED,
                    ),
                )
            except BaseException as exc:
                category = self.normalize_error(exc)
                if isinstance(exc, BudgetExhausted):
                    category = "turn_timeout"
                    budget.record_timeout(exc.stage)
                elif isinstance(exc, CapacityUnavailable):
                    category = "capacity_unavailable"
                self.repository.mark_failed(
                    reservation,
                    user_id=request.user_id,
                    error_category=category,
                )
                self.observe(
                    turn_state=STATUS_FAILED.lower(),
                    error_category=category,
                    timeout_category=(
                        "turn_budget" if isinstance(exc, BudgetExhausted) else None
                    ),
                    budget_exhaustion_stage=budget.timeout_stage,
                    remaining_budget_ms=budget.remaining_ms(
                        reserve_finalization=False
                    ),
                    completion_within_budget=not budget.is_exhausted(
                        reserve_finalization=False
                    ),
                    overload_result=(
                        "capacity_unavailable"
                        if isinstance(exc, CapacityUnavailable)
                        else None
                    ),
                )
                if isinstance(exc, (BudgetExhausted, CapacityUnavailable)):
                    yield TurnEvent(
                        TurnEventKind.FAILED,
                        self._failure_payload(
                            request.client_turn_id,
                            category,
                            replayed=False,
                        ),
                    )
                    return
                raise

    @staticmethod
    def _failure_payload(
        client_turn_id: str,
        category: str,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        if category in {"turn_timeout", "timeout", "turn_budget"}:
            code = "turn_timed_out"
            error = (
                "I couldn't complete that within the available time. "
                "Please try again."
            )
        elif category == "capacity_unavailable":
            code = "capacity_unavailable"
            error = (
                "The service is handling a lot of requests right now. "
                "Please try again shortly."
            )
        else:
            code = "turn_failed"
            error = "This message could not be completed. Please try again."
        payload: dict[str, Any] = {
            "code": code,
            "client_turn_id": client_turn_id,
            "status": STATUS_FAILED.lower(),
            "error": error,
            "replayed": replayed,
        }
        if code == "capacity_unavailable":
            payload["retry_after"] = OVERLOAD_RETRY_AFTER_SECONDS
        return payload
