from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest


API = Path(__file__).resolve().parents[1] / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from conversation_turn import (  # noqa: E402
    build_sse_final_payload,
    ConversationTurnService,
    GeneratedTurn,
    TurnContext,
    TurnEvent,
    TurnEventKind,
    TurnRequest,
)
from turn_repository import (  # noqa: E402
    ReservationKind,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    TurnReservation,
)


def request(**changes) -> TurnRequest:
    value = TurnRequest(
        thread_id="thread-1",
        user_id="user-1",
        session_id="session-1",
        client_turn_id="turn-client-1234",
        client_idempotent=True,
        request_fingerprint="f" * 64,
        message="What events are happening?",
        transport="json",
        trace_id="trace",
    )
    return replace(value, **changes)


def reservation(kind=ReservationKind.NEW_EXECUTION_OWNER, **changes):
    value = TurnReservation(
        kind=kind,
        turn_id="turn-row-1",
        client_turn_id="turn-client-1234",
        request_fingerprint="f" * 64,
        status="RESERVED",
        lease_owner="lease-1",
        state_version=3,
        client_idempotent=True,
    )
    return replace(value, **changes)


class FakeRepository:
    def __init__(self, next_reservation=None):
        self.next_reservation = next_reservation or reservation()
        self.reserve_calls = 0
        self.running_calls = 0
        self.failed_calls = 0

    def reserve(self, **_kwargs):
        self.reserve_calls += 1
        return self.next_reservation

    def mark_running(self, *_args, **_kwargs):
        self.running_calls += 1
        return True

    def mark_failed(self, *_args, **_kwargs):
        self.failed_calls += 1
        return True


class Harness:
    def __init__(self, next_reservation=None):
        self.repository = FakeRepository(next_reservation)
        self.context_calls = 0
        self.finalize_calls = 0
        self.producer_calls = 0
        self.observations = []
        self.service = ConversationTurnService(
            self.repository,
            load_context=self.load_context,
            finalize=self.finalize,
            normalize_error=lambda exc: exc.__class__.__name__.lower(),
            observe=lambda **fields: self.observations.append(fields),
        )

    def load_context(self, turn_request, turn_reservation):
        self.context_calls += 1
        return TurnContext(
            request=turn_request,
            reservation=turn_reservation,
            thread={},
            history_rows=[],
            conversation_history=[],
            thread_state={},
        )

    def finalize(self, _context, generated):
        self.finalize_calls += 1
        return {
            "thread": {"id": "thread-1"},
            "user_message": {"id": "user-message"},
            "assistant_message": {
                "id": "assistant-message",
                "content": generated.answer,
                "sources": generated.sources,
                "response_mode": generated.mode,
            },
        }

    def producer(self, _context):
        self.producer_calls += 1
        yield TurnEvent(TurnEventKind.DELTA, "Answer")
        yield TurnEvent(
            TurnEventKind.FINAL,
            GeneratedTurn(
                answer="Answer",
                mode="sql",
                sources=[{"type": "sql"}],
                next_thread_state={"mode": "sql"},
                result_payload={},
            ),
        )


def test_first_request_executes_and_finalizes_once() -> None:
    harness = Harness()
    events = list(harness.service.execute(request(), harness.producer))
    assert [event.kind for event in events] == [
        TurnEventKind.DELTA,
        TurnEventKind.FINAL,
    ]
    assert harness.repository.reserve_calls == 1
    assert harness.repository.running_calls == 1
    assert harness.context_calls == 1
    assert harness.producer_calls == 1
    assert harness.finalize_calls == 1
    assert events[-1].payload["turn"]["replayed"] is False
    assert events[-1].payload["turn"]["status"] == "completed"


def test_completed_replay_does_zero_generation_or_persistence_work() -> None:
    stored = {
        "thread": {"id": "thread-1"},
        "user_message": {"id": "u"},
        "assistant_message": {"id": "a", "content": "Stored"},
    }
    harness = Harness(
        reservation(
            ReservationKind.COMPLETED_REPLAY,
            status=STATUS_COMPLETED,
            response_payload=stored,
            lease_owner=None,
        )
    )
    events = list(harness.service.execute(request(), harness.producer))
    assert len(events) == 1
    assert events[0].kind is TurnEventKind.FINAL
    assert events[0].payload["assistant_message"]["content"] == "Stored"
    assert events[0].payload["turn"]["replayed"] is True
    assert harness.context_calls == 0
    assert harness.producer_calls == 0
    assert harness.finalize_calls == 0
    assert harness.repository.running_calls == 0


@pytest.mark.parametrize(
    ("kind", "event_kind"),
    [
        (ReservationKind.RUNNING_EXISTING, TurnEventKind.IN_PROGRESS),
        (ReservationKind.THREAD_BUSY, TurnEventKind.IN_PROGRESS),
        (ReservationKind.FINGERPRINT_CONFLICT, TurnEventKind.CONFLICT),
        (ReservationKind.OWNERSHIP_FAILURE, TurnEventKind.NOT_FOUND),
    ],
)
def test_non_owner_reservations_never_execute(kind, event_kind) -> None:
    harness = Harness(reservation(kind, status=STATUS_RUNNING))
    events = list(harness.service.execute(request(), harness.producer))
    assert [event.kind for event in events] == [event_kind]
    assert harness.producer_calls == 0
    assert harness.finalize_calls == 0


def test_failed_replay_is_deterministic() -> None:
    harness = Harness(
        reservation(
            ReservationKind.FAILED_REPLAY,
            status=STATUS_FAILED,
            error_category="model",
        )
    )
    first = list(harness.service.execute(request(), harness.producer))
    second = list(harness.service.execute(request(), harness.producer))
    assert first[0].payload == second[0].payload
    assert harness.producer_calls == 0


def test_producer_failure_marks_turn_failed() -> None:
    harness = Harness()

    def broken(_context):
        raise RuntimeError("provider unavailable")
        yield  # pragma: no cover

    with pytest.raises(RuntimeError):
        list(harness.service.execute(request(), broken))
    assert harness.repository.failed_calls == 1
    assert harness.finalize_calls == 0


def test_json_and_sse_use_same_service_and_final_shape() -> None:
    json_harness = Harness()
    sse_harness = Harness()
    json_final = list(
        json_harness.service.execute(request(transport="json"), json_harness.producer)
    )[-1].payload
    sse_final = list(
        sse_harness.service.execute(request(transport="sse"), sse_harness.producer)
    )[-1].payload
    assert json_final == sse_final


def test_sse_final_payload_preserves_legacy_fields() -> None:
    payload = {
        "thread": {"id": "thread-1"},
        "user_message": {"id": "user-message"},
        "assistant_message": {
            "id": "assistant-message",
            "content": "Stored answer",
            "response_mode": "hybrid",
            "sources": [{"type": "sql"}],
        },
        "turn": {"status": "completed"},
    }
    result = build_sse_final_payload(payload)
    assert result["type"] == "final"
    assert result["answer"] == "Stored answer"
    assert result["mode"] == "hybrid"
    assert result["sources"] == [{"type": "sql"}]
    assert result["assistant_message"] == payload["assistant_message"]
