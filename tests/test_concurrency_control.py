from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from api.concurrency_control import CapacityUnavailable, ConcurrencyGate, ConcurrencyRegistry
from on_the_porch.request_budget import create_turn_budget


def test_active_count_never_exceeds_limit() -> None:
    gate = ConcurrencyGate("test", 2, max_queue_wait_seconds=1)
    observed = []
    def work() -> None:
        with gate.lease():
            observed.append(gate.snapshot().active)
            time.sleep(0.01)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _value: work(), range(20)))
    assert max(observed) == 2
    assert gate.snapshot().active == 0


def test_acquisition_is_deadline_aware() -> None:
    gate = ConcurrencyGate("test", 1, max_queue_wait_seconds=1)
    gate.acquire()
    budget = create_turn_budget("turn", total_seconds=0.03, finalization_reserve_seconds=0)
    started = time.monotonic()
    with pytest.raises(CapacityUnavailable):
        gate.acquire(budget=budget, minimum_execution_seconds=0.02)
    assert time.monotonic() - started < 0.08
    gate.release()


def test_queue_wait_is_bounded() -> None:
    gate = ConcurrencyGate("test", 1, max_queue_wait_seconds=0.02)
    gate.acquire()
    with pytest.raises(CapacityUnavailable):
        gate.acquire()
    gate.release()
    assert gate.snapshot().rejections == 1


def test_permit_released_on_success() -> None:
    gate = ConcurrencyGate("test", 1)
    with gate.lease():
        assert gate.snapshot().active == 1
    assert gate.snapshot().active == 0


def test_permit_released_on_failure() -> None:
    gate = ConcurrencyGate("test", 1)
    with pytest.raises(RuntimeError):
        with gate.lease():
            raise RuntimeError("failure")
    assert gate.snapshot().active == 0


def test_different_conversations_run_within_limit() -> None:
    gate = ConcurrencyGate("test", 2, max_queue_wait_seconds=1)
    barrier = threading.Barrier(2)
    def work() -> int:
        with gate.lease():
            barrier.wait(timeout=1)
            active = gate.snapshot().active
            barrier.wait(timeout=1)
            return active
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _value: work(), range(2)))
    assert results == [2, 2]


def test_fifo_waiters_are_not_overtaken() -> None:
    gate = ConcurrencyGate("test", 1, max_queue_wait_seconds=1)
    order = []
    gate.acquire()
    threads = []
    for value in range(3):
        thread = threading.Thread(
            target=lambda item=value: (
                gate.acquire(), order.append(item), gate.release()
            )
        )
        thread.start()
        threads.append(thread)
        time.sleep(0.01)
    gate.release()
    for thread in threads:
        thread.join(timeout=1)
    assert order == [0, 1, 2]


def test_snapshot_metrics_are_accurate() -> None:
    gate = ConcurrencyGate("test", 1, max_queue_wait_seconds=0)
    gate.acquire()
    with pytest.raises(CapacityUnavailable):
        gate.acquire()
    snapshot = gate.snapshot()
    assert snapshot.acquisitions == 1
    assert snapshot.rejections == 1
    assert snapshot.max_active == 1
    gate.release()


def test_release_without_permit_is_rejected() -> None:
    with pytest.raises(RuntimeError):
        ConcurrencyGate("test", 1).release()


def test_registry_keeps_operation_classes_independent() -> None:
    registry = ConcurrencyRegistry({"llm": 1, "embedding": 2})
    registry.get("llm").acquire()
    registry.get("embedding").acquire()
    assert registry.get("llm").snapshot().active == 1
    assert registry.get("embedding").snapshot().active == 1
    registry.get("llm").release()
    registry.get("embedding").release()


def test_unknown_gate_is_rejected() -> None:
    with pytest.raises(KeyError):
        ConcurrencyRegistry({"llm": 1}).get("unknown")


def test_waiting_count_returns_to_zero_after_rejection() -> None:
    gate = ConcurrencyGate("test", 1, max_queue_wait_seconds=0)
    gate.acquire()
    with pytest.raises(CapacityUnavailable):
        gate.acquire()
    assert gate.snapshot().waiting == 0
    gate.release()


def test_limit_is_never_zero() -> None:
    assert ConcurrencyGate("test", 0).limit == 1
