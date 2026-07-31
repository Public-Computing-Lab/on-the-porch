"""Bounded execution for potentially noncancelable provider operations."""

from __future__ import annotations

import os
import contextvars
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, Iterator, TypeVar

from concurrency import gate
try:
    from .request_budget import BudgetExhausted, current_budget, timeout_for
except ImportError:  # Direct script loading keeps the legacy import path.
    from request_budget import BudgetExhausted, current_budget, timeout_for


T = TypeVar("T")


_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(2, min(int(os.getenv("BOUNDED_CALL_MAX_WORKERS", "12")), 32)),
    thread_name_prefix="otp-bounded-call",
)


def _record_fields(**fields: object) -> None:
    try:
        from turn_observability import record_fields

        record_fields(**fields)
    except Exception:
        pass


def _increment(field: str, count: int = 1) -> None:
    try:
        from turn_observability import increment

        increment(field, count)
    except Exception:
        pass


def run_bounded_call(
    operation: Callable[[float], T],
    *,
    gate_name: str,
    stage: str,
    stage_cap_seconds: float,
    minimum_required_seconds: float = 0.05,
) -> T:
    """Run a call under a gate and return at the shared budget boundary.

    The permit is released by the future completion callback. If the request times out
    while a Python thread remains inside a provider SDK, that call therefore continues to
    consume capacity until it actually exits.
    """
    budget = current_budget()
    call_timeout = timeout_for(
        stage,
        stage_cap_seconds,
        minimum_required=minimum_required_seconds,
    )
    selected_gate = gate(gate_name)
    selected_gate.acquire(
        budget=budget,
        minimum_execution_seconds=minimum_required_seconds,
    )
    released = False

    def _release(_future) -> None:
        nonlocal released
        if not released:
            released = True
            selected_gate.release()

    try:
        context = contextvars.copy_context()
        future = _EXECUTOR.submit(context.run, operation, call_timeout)
    except BaseException:
        selected_gate.release()
        raise
    future.add_done_callback(_release)
    _record_fields(external_timeout_ms=round(call_timeout * 1000.0, 3))
    try:
        return future.result(timeout=call_timeout)
    except FutureTimeout as exc:
        if budget is not None:
            budget.record_timeout(stage)
        cancelled = future.cancel()
        if cancelled:
            _increment("canceled_future_count")
        else:
            _record_fields(noncancelable_call_running=True)
        raise BudgetExhausted(stage) from exc


def stream_bounded_call(
    operation: Callable[[float, Callable[[str], None]], None],
    *,
    gate_name: str,
    stage: str,
    stage_cap_seconds: float,
    minimum_required_seconds: float = 0.05,
) -> Iterator[str]:
    """Stream provider output while retaining the permit until the provider exits."""
    budget = current_budget()
    call_timeout = timeout_for(
        stage,
        stage_cap_seconds,
        minimum_required=minimum_required_seconds,
    )
    selected_gate = gate(gate_name)
    selected_gate.acquire(
        budget=budget,
        minimum_execution_seconds=minimum_required_seconds,
    )
    events: queue.Queue[object] = queue.Queue(
        maxsize=max(1, min(int(os.getenv("BOUNDED_STREAM_QUEUE_SIZE", "64")), 1024))
    )
    done = object()
    consumer_closed = threading.Event()
    released = False

    def _release(_future) -> None:
        nonlocal released
        if not released:
            released = True
            selected_gate.release()
        try:
            events.put_nowait(done)
        except queue.Full:
            pass

    def _emit(piece: str) -> None:
        if not piece:
            return
        while not consumer_closed.is_set():
            try:
                events.put(piece, timeout=0.05)
                return
            except queue.Full:
                if budget is not None:
                    budget.raise_if_exhausted(stage)
        raise BudgetExhausted(stage)

    context = contextvars.copy_context()
    try:
        future = _EXECUTOR.submit(
            context.run,
            operation,
            call_timeout,
            _emit,
        )
    except BaseException:
        selected_gate.release()
        raise
    future.add_done_callback(_release)
    _record_fields(external_timeout_ms=round(call_timeout * 1000.0, 3))
    call_deadline = time.monotonic() + call_timeout
    try:
        while True:
            remaining = call_deadline - time.monotonic()
            if budget is not None:
                remaining = min(remaining, budget.remaining_seconds())
            if remaining <= 0.0:
                if budget is not None:
                    budget.record_timeout(stage)
                cancelled = future.cancel()
                if cancelled:
                    _increment("canceled_future_count")
                else:
                    _record_fields(noncancelable_call_running=True)
                raise BudgetExhausted(stage)
            try:
                item = events.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if future.done():
                    return future.result()
                continue
            if item is done:
                return future.result()
            yield str(item)
    finally:
        if not future.done():
            consumer_closed.set()
            if future.cancel():
                _increment("canceled_future_count")
            else:
                _record_fields(noncancelable_call_running=True)


__all__ = ["run_bounded_call", "stream_bounded_call"]

sys.modules.setdefault("external_call", sys.modules[__name__])
sys.modules.setdefault("on_the_porch.external_call", sys.modules[__name__])
