from __future__ import annotations

import sys
import time
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from concurrency_control import CapacityUnavailable  # noqa: E402
from conversation_turn import (  # noqa: E402
    ConversationTurnService,
    GeneratedTurn,
    TurnContext,
    TurnEvent,
    TurnEventKind,
    TurnRequest,
)
from turn_repository import ReservationKind, TurnReservation  # noqa: E402
from on_the_porch.request_budget import create_turn_budget  # noqa: E402


def _request(transport: str = "sse") -> TurnRequest:
    return TurnRequest(
        "thread", "user", "session", "client-turn-1234", True, "f" * 64,
        "question", transport,
    )


def _reservation(kind=ReservationKind.NEW_EXECUTION_OWNER, category=None) -> TurnReservation:
    return TurnReservation(
        kind,
        "turn",
        "client-turn-1234",
        "f" * 64,
        "RESERVED" if kind is ReservationKind.NEW_EXECUTION_OWNER else "FAILED",
        lease_owner="lease" if kind is ReservationKind.NEW_EXECUTION_OWNER else None,
        error_category=category,
    )


class Repository:
    def __init__(self, result=None) -> None:
        self.result = result or _reservation()
        self.failed_category = None

    def reserve(self, **_kwargs):
        return self.result

    def mark_running(self, *_args, **_kwargs):
        return True

    def mark_failed(self, *_args, error_category, **_kwargs):
        self.failed_category = error_category
        return True


def _service(repository: Repository) -> ConversationTurnService:
    def load(request, reservation):
        return TurnContext(request, reservation, {}, [], [], {})
    return ConversationTurnService(
        repository,
        load_context=load,
        finalize=lambda _context, generated: {
            "assistant_message": {
                "content": generated.answer,
                "response_mode": generated.mode,
                "sources": generated.sources,
            }
        },
        normalize_error=lambda exc: exc.__class__.__name__.lower(),
    )


def _final(answer: str = "answer") -> TurnEvent:
    return TurnEvent(
        TurnEventKind.FINAL,
        GeneratedTurn(answer, "rag", [], {}, {"answer": answer}),
    )


def test_stream_timeout_is_terminal_and_resident_safe(monkeypatch) -> None:
    import conversation_turn

    monkeypatch.setattr(
        conversation_turn,
        "create_turn_budget",
        lambda _turn_id: create_turn_budget(
            "turn", total_seconds=0.03, finalization_reserve_seconds=0,
        ),
    )
    repository = Repository()
    service = _service(repository)
    def slow(_context):
        yield TurnEvent(TurnEventKind.DELTA, "partial")
        time.sleep(0.05)
        yield _final()
    events = list(service.execute(_request(), slow))
    assert events[-1].kind is TurnEventKind.FAILED
    assert events[-1].payload["code"] == "turn_timed_out"
    assert "provider" not in events[-1].payload["error"].lower()
    assert repository.failed_category == "turn_timeout"


def test_capacity_failure_is_terminal_with_retry_after() -> None:
    repository = Repository()
    service = _service(repository)
    def overloaded(_context):
        raise CapacityUnavailable("llm", 0.1)
        yield  # pragma: no cover
    events = list(service.execute(_request(), overloaded))
    assert events[-1].kind is TurnEventKind.FAILED
    assert events[-1].payload["code"] == "capacity_unavailable"
    assert events[-1].payload["retry_after"] >= 1
    assert repository.failed_category == "capacity_unavailable"


def test_timeout_replay_does_not_regenerate() -> None:
    repository = Repository(
        _reservation(ReservationKind.FAILED_REPLAY, "turn_timeout")
    )
    service = _service(repository)
    calls = []
    events = list(service.execute(_request("json"), lambda _context: calls.append(1)))
    assert calls == []
    assert events[0].payload["code"] == "turn_timed_out"
    assert events[0].payload["replayed"] is True


def test_capacity_replay_does_not_regenerate() -> None:
    repository = Repository(
        _reservation(ReservationKind.FAILED_REPLAY, "capacity_unavailable")
    )
    service = _service(repository)
    calls = []
    events = list(service.execute(_request(), lambda _context: calls.append(1)))
    assert calls == []
    assert events[0].payload["code"] == "capacity_unavailable"


def test_full_stream_finalizes_normally_inside_budget() -> None:
    repository = Repository()
    service = _service(repository)
    events = list(service.execute(_request(), lambda _context: iter([_final()])))
    assert events[-1].kind is TurnEventKind.FINAL
    assert events[-1].payload["turn"]["status"] == "completed"
