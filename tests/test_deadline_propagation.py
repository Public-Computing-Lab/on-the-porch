from __future__ import annotations

import threading
import time

import pytest

from api.concurrency_control import gate
from on_the_porch.external_call import run_bounded_call, stream_bounded_call
from on_the_porch.request_budget import (
    BudgetExhausted,
    create_turn_budget,
    current_budget,
    use_turn_budget,
)


def test_bounded_worker_receives_same_budget() -> None:
    budget = create_turn_budget("turn", total_seconds=1, finalization_reserve_seconds=0.1)
    with use_turn_budget(budget):
        found = run_bounded_call(
            lambda _timeout: current_budget(),
            gate_name="llm",
            stage="test_provider",
            stage_cap_seconds=0.5,
        )
    assert found is budget


def test_provider_timeout_returns_at_budget_boundary() -> None:
    release = threading.Event()
    budget = create_turn_budget("turn", total_seconds=0.05, finalization_reserve_seconds=0)
    started = time.monotonic()
    with use_turn_budget(budget), pytest.raises(BudgetExhausted):
        run_bounded_call(
            lambda _timeout: release.wait(1),
            gate_name="llm",
            stage="slow_provider",
            stage_cap_seconds=1,
        )
    assert time.monotonic() - started < 0.15
    release.set()


def test_noncancelable_call_holds_permit_until_exit() -> None:
    release = threading.Event()
    selected_gate = gate("llm")
    baseline = selected_gate.snapshot().active
    budget = create_turn_budget("turn", total_seconds=0.08, finalization_reserve_seconds=0)
    with use_turn_budget(budget), pytest.raises(BudgetExhausted):
        run_bounded_call(
            lambda _timeout: release.wait(1),
            gate_name="llm",
            stage="slow_provider",
            stage_cap_seconds=1,
        )
    assert selected_gate.snapshot().active == baseline + 1
    release.set()
    deadline = time.monotonic() + 1
    while selected_gate.snapshot().active != baseline and time.monotonic() < deadline:
        time.sleep(0.01)
    assert selected_gate.snapshot().active == baseline


def test_stream_is_deadline_bound_after_first_piece() -> None:
    release = threading.Event()
    def operation(_timeout, emit) -> None:
        emit("first")
        release.wait(1)
    budget = create_turn_budget("turn", total_seconds=0.08, finalization_reserve_seconds=0)
    with use_turn_budget(budget):
        stream = stream_bounded_call(
            operation,
            gate_name="llm",
            stage="stream",
            stage_cap_seconds=1,
        )
        assert next(stream) == "first"
        with pytest.raises(BudgetExhausted):
            next(stream)
    release.set()


def test_stream_completes_normally_inside_budget() -> None:
    def operation(_timeout, emit) -> None:
        emit("a")
        emit("b")
    budget = create_turn_budget("turn", total_seconds=1, finalization_reserve_seconds=0.1)
    with use_turn_budget(budget):
        assert list(stream_bounded_call(
            operation,
            gate_name="llm",
            stage="stream",
            stage_cap_seconds=0.5,
        )) == ["a", "b"]


def test_stream_consumer_close_stops_bounded_producer() -> None:
    stopped = threading.Event()

    def operation(_timeout, emit) -> None:
        try:
            for _ in range(10_000):
                emit("piece")
        except BudgetExhausted:
            stopped.set()
            raise

    selected_gate = gate("llm")
    baseline = selected_gate.snapshot().active
    budget = create_turn_budget("turn", total_seconds=1, finalization_reserve_seconds=0)
    with use_turn_budget(budget):
        stream = stream_bounded_call(
            operation,
            gate_name="llm",
            stage="stream_disconnect",
            stage_cap_seconds=0.8,
        )
        assert next(stream) == "piece"
        stream.close()
    assert stopped.wait(timeout=1)
    deadline = time.monotonic() + 1
    while selected_gate.snapshot().active != baseline and time.monotonic() < deadline:
        time.sleep(0.01)
    assert selected_gate.snapshot().active == baseline
