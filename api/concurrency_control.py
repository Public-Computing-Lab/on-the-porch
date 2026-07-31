"""Process-local, deadline-aware concurrency gates for expensive request work."""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, Optional

try:
    from on_the_porch.request_budget import TurnBudget, current_budget
except ImportError:  # API runtime adds on_the_porch directly to sys.path.
    from request_budget import TurnBudget, current_budget


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return max(minimum, default)


DEFAULT_QUEUE_WAIT_SECONDS = _env_int(
    "EXPENSIVE_QUEUE_MAX_WAIT_MS", 750, 0
) / 1000.0
OVERLOAD_RETRY_AFTER_SECONDS = _env_int("OVERLOAD_RETRY_AFTER_SECONDS", 2, 1)


class CapacityUnavailable(RuntimeError):
    """Raised before provider invocation when bounded capacity cannot be acquired."""

    def __init__(self, gate_name: str, queue_wait_seconds: float) -> None:
        self.gate_name = gate_name[:80]
        self.queue_wait_seconds = max(0.0, float(queue_wait_seconds))
        super().__init__("capacity_unavailable")


@dataclass(frozen=True)
class GateSnapshot:
    name: str
    limit: int
    active: int
    waiting: int
    max_active: int
    acquisitions: int
    rejections: int


class ConcurrencyGate:
    """Bounded FIFO gate with deadline-aware queueing and observable counters."""

    def __init__(
        self,
        name: str,
        limit: int,
        *,
        max_queue_wait_seconds: float = DEFAULT_QUEUE_WAIT_SECONDS,
        observer: Optional[Callable[..., None]] = None,
    ) -> None:
        self.name = name[:80]
        self.limit = max(1, int(limit))
        self.max_queue_wait_seconds = max(0.0, float(max_queue_wait_seconds))
        self.observer = observer
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[str] = deque()
        self._active = 0
        self._max_active = 0
        self._acquisitions = 0
        self._rejections = 0

    def _emit(self, **fields: object) -> None:
        if self.observer is not None:
            try:
                self.observer(gate_name=self.name, **fields)
            except Exception:
                pass

    def snapshot(self) -> GateSnapshot:
        with self._condition:
            return GateSnapshot(
                self.name,
                self.limit,
                self._active,
                len(self._queue),
                self._max_active,
                self._acquisitions,
                self._rejections,
            )

    def acquire(
        self,
        *,
        budget: Optional[TurnBudget] = None,
        max_wait_seconds: Optional[float] = None,
        minimum_execution_seconds: float = 0.05,
    ) -> float:
        active_budget = budget or current_budget()
        configured_wait = (
            self.max_queue_wait_seconds
            if max_wait_seconds is None
            else max(0.0, float(max_wait_seconds))
        )
        if active_budget is not None:
            allowed = max(
                0.0,
                active_budget.remaining_seconds() - max(0.0, minimum_execution_seconds),
            )
            configured_wait = min(configured_wait, allowed)
        started = time.monotonic()
        deadline = started + configured_wait
        ticket = uuid.uuid4().hex
        with self._condition:
            self._queue.append(ticket)
            self._emit(
                gate_active=self._active,
                gate_waiting=len(self._queue),
                gate_result="waiting",
            )
            while True:
                is_front = bool(self._queue) and self._queue[0] == ticket
                if is_front and self._active < self.limit:
                    self._queue.popleft()
                    self._active += 1
                    self._max_active = max(self._max_active, self._active)
                    self._acquisitions += 1
                    waited = time.monotonic() - started
                    self._emit(
                        gate_active=self._active,
                        gate_waiting=len(self._queue),
                        gate_queue_wait_ms=round(waited * 1000.0, 3),
                        gate_result="acquired",
                    )
                    return waited
                remaining = deadline - time.monotonic()
                if active_budget is not None:
                    remaining = min(remaining, active_budget.remaining_seconds())
                if remaining <= 0.0:
                    try:
                        self._queue.remove(ticket)
                    except ValueError:
                        pass
                    self._rejections += 1
                    waited = time.monotonic() - started
                    self._emit(
                        gate_active=self._active,
                        gate_waiting=len(self._queue),
                        gate_queue_wait_ms=round(waited * 1000.0, 3),
                        gate_result="rejected",
                        overload_result="capacity_unavailable",
                    )
                    self._condition.notify_all()
                    raise CapacityUnavailable(self.name, waited)
                self._condition.wait(timeout=remaining)

    def release(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError(f"concurrency gate {self.name} released without permit")
            self._active -= 1
            self._emit(
                gate_active=self._active,
                gate_waiting=len(self._queue),
                gate_result="released",
            )
            self._condition.notify_all()

    @contextmanager
    def lease(
        self,
        *,
        budget: Optional[TurnBudget] = None,
        max_wait_seconds: Optional[float] = None,
        minimum_execution_seconds: float = 0.05,
    ) -> Iterator[float]:
        waited = self.acquire(
            budget=budget,
            max_wait_seconds=max_wait_seconds,
            minimum_execution_seconds=minimum_execution_seconds,
        )
        try:
            yield waited
        finally:
            self.release()


def _record_gate(**fields: object) -> None:
    try:
        from turn_observability import record_gate_event

        record_gate_event(**fields)
    except Exception:
        pass


_LIMITS = {
    "llm": _env_int("LLM_MAX_CONCURRENCY", 2),
    "embedding": _env_int("EMBEDDING_MAX_CONCURRENCY", 2),
    "heavy_rag": _env_int("HEAVY_RAG_MAX_CONCURRENCY", 4),
    "dynamic_sql": _env_int("DYNAMIC_SQL_MAX_CONCURRENCY", 2),
    "enrichment": _env_int("ENRICHMENT_MAX_CONCURRENCY", 2),
}


class ConcurrencyRegistry:
    def __init__(self, limits: Optional[Dict[str, int]] = None) -> None:
        selected = dict(_LIMITS if limits is None else limits)
        self._gates = {
            name: ConcurrencyGate(name, limit, observer=_record_gate)
            for name, limit in selected.items()
        }

    def get(self, name: str) -> ConcurrencyGate:
        try:
            return self._gates[name]
        except KeyError as exc:
            raise KeyError(f"unknown concurrency gate: {name}") from exc

    def snapshots(self) -> Dict[str, GateSnapshot]:
        return {name: gate.snapshot() for name, gate in self._gates.items()}


REGISTRY = ConcurrencyRegistry()


def gate(name: str) -> ConcurrencyGate:
    return REGISTRY.get(name)


__all__ = [
    "CapacityUnavailable",
    "ConcurrencyGate",
    "ConcurrencyRegistry",
    "DEFAULT_QUEUE_WAIT_SECONDS",
    "GateSnapshot",
    "OVERLOAD_RETRY_AFTER_SECONDS",
    "REGISTRY",
    "gate",
]

sys.modules.setdefault("concurrency_control", sys.modules[__name__])
sys.modules.setdefault("api.concurrency_control", sys.modules[__name__])
