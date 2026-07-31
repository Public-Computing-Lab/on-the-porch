from __future__ import annotations

import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from on_the_porch.request_budget import (
    BudgetExhausted,
    TurnBudget,
    create_turn_budget,
    current_budget,
    timeout_for,
    use_turn_budget,
)


def test_budget_uses_monotonic_clock(monkeypatch) -> None:
    values = iter([100.0, 101.0, 101.25])
    monkeypatch.setattr("on_the_porch.request_budget.time.monotonic", lambda: next(values))
    budget = TurnBudget("turn", total_seconds=10, finalization_reserve_seconds=2)
    assert budget.started_monotonic == 100.0
    assert budget.elapsed_seconds() == 1.0
    assert budget.remaining_seconds() == 6.75


def test_remaining_time_decreases() -> None:
    budget = create_turn_budget("turn", total_seconds=1, finalization_reserve_seconds=0.1)
    before = budget.remaining_seconds()
    time.sleep(0.01)
    assert budget.remaining_seconds() < before


def test_stage_timeout_is_capped_by_remaining() -> None:
    budget = create_turn_budget("turn", total_seconds=0.2, finalization_reserve_seconds=0.05)
    assert 0 < budget.timeout_for("model", 10) <= 0.15


def test_finalization_reserve_is_protected() -> None:
    budget = create_turn_budget("turn", total_seconds=5, finalization_reserve_seconds=2)
    assert budget.remaining_seconds(reserve_finalization=False) - budget.remaining_seconds() == pytest.approx(2, abs=0.02)


def test_stage_requires_minimum_budget() -> None:
    budget = create_turn_budget("turn", total_seconds=1, finalization_reserve_seconds=0.5)
    assert not budget.can_start("retrieval", minimum_required=0.75)
    assert budget.timeout_stage is None
    with pytest.raises(BudgetExhausted):
        budget.timeout_for("retrieval", 1, minimum_required=0.75)
    assert budget.timeout_stage == "retrieval"


def test_raise_records_correct_stage() -> None:
    budget = create_turn_budget("turn", total_seconds=0.01, finalization_reserve_seconds=0)
    time.sleep(0.02)
    with pytest.raises(BudgetExhausted):
        budget.raise_if_exhausted("routing")
    assert budget.timeout_stage == "routing"


def test_context_scope_restores_previous_budget() -> None:
    outer = create_turn_budget("outer")
    inner = create_turn_budget("inner")
    with use_turn_budget(outer):
        assert current_budget() is outer
        with use_turn_budget(inner):
            assert current_budget() is inner
        assert current_budget() is outer
    assert current_budget() is None


def test_executor_context_receives_same_absolute_deadline() -> None:
    budget = create_turn_budget("turn")
    with use_turn_budget(budget):
        context = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            child = pool.submit(context.run, lambda: current_budget()).result()
    assert child is budget
    assert child.deadline == budget.deadline


def test_parallel_branches_share_budget() -> None:
    budget = create_turn_budget("turn")
    results = []
    with use_turn_budget(budget):
        contexts = [contextvars.copy_context(), contextvars.copy_context()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ctx.run, lambda: current_budget()) for ctx in contexts]
            results = [future.result() for future in futures]
    assert results == [budget, budget]


def test_module_timeout_uses_active_budget() -> None:
    budget = create_turn_budget("turn", total_seconds=0.3, finalization_reserve_seconds=0.1)
    with use_turn_budget(budget):
        assert timeout_for("provider", 10) <= 0.2


def test_cancel_exhausts_budget() -> None:
    budget = create_turn_budget("turn")
    budget.cancel("disconnect")
    assert budget.is_exhausted()
    assert budget.timeout_stage == "disconnect"


def test_persistence_can_use_reserved_time() -> None:
    budget = create_turn_budget("turn", total_seconds=0.2, finalization_reserve_seconds=0.1)
    time.sleep(0.11)
    assert budget.is_exhausted()
    assert not budget.is_exhausted(reserve_finalization=False)
    budget.raise_if_exhausted("persistence", reserve_finalization=False)


def test_snapshot_contains_no_wall_clock_or_content() -> None:
    snapshot = create_turn_budget("turn-1").snapshot()
    assert snapshot["turn_id"] == "turn-1"
    assert "deadline" not in snapshot
    assert "message" not in snapshot


def test_thread_context_without_copy_has_no_budget() -> None:
    budget = create_turn_budget("turn")
    found = []
    with use_turn_budget(budget):
        thread = threading.Thread(target=lambda: found.append(current_budget()))
        thread.start()
        thread.join()
    assert found == [None]
