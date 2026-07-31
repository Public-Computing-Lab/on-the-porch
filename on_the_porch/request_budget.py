"""Authoritative monotonic budget for one logical conversation turn."""

from __future__ import annotations

import contextvars
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_ms(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return max(minimum, default)


TURN_BUDGET_ENABLED = _env_bool("TURN_BUDGET_ENABLED", True)
TURN_TOTAL_BUDGET_MS = _env_ms("TURN_TOTAL_BUDGET_MS", 25_000, 1_000)
TURN_FINALIZATION_RESERVE_MS = _env_ms(
    "TURN_FINALIZATION_RESERVE_MS", 2_000, 100
)
TURN_MIN_SYNTHESIS_BUDGET_MS = _env_ms(
    "TURN_MIN_SYNTHESIS_BUDGET_MS", 1_500, 0
)
TURN_MIN_RETRIEVAL_BUDGET_MS = _env_ms(
    "TURN_MIN_RETRIEVAL_BUDGET_MS", 500, 0
)
TURN_TIMEOUT_GRACE_MS = _env_ms("TURN_TIMEOUT_GRACE_MS", 500, 0)


class BudgetExhausted(TimeoutError):
    """Raised when a stage cannot safely fit in the logical-turn budget."""

    def __init__(self, stage: str = "turn") -> None:
        self.stage = (stage or "turn")[:80]
        super().__init__(f"turn_budget_exhausted:{self.stage}")


@dataclass
class TurnBudget:
    """Thread-safe absolute budget shared by every branch of one turn."""

    turn_id: Optional[str]
    total_seconds: float = TURN_TOTAL_BUDGET_MS / 1000.0
    finalization_reserve_seconds: float = TURN_FINALIZATION_RESERVE_MS / 1000.0
    started_monotonic: float = field(default_factory=lambda: time.monotonic())
    enabled: bool = TURN_BUDGET_ENABLED
    _timeout_stage: Optional[str] = field(default=None, init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.total_seconds = max(0.001, float(self.total_seconds))
        self.finalization_reserve_seconds = max(
            0.0,
            min(float(self.finalization_reserve_seconds), self.total_seconds * 0.5),
        )

    @property
    def deadline(self) -> float:
        return self.started_monotonic + self.total_seconds

    @property
    def working_deadline(self) -> float:
        return self.deadline - self.finalization_reserve_seconds

    @property
    def timeout_stage(self) -> Optional[str]:
        with self._lock:
            return self._timeout_stage

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic)

    def remaining_seconds(self, *, reserve_finalization: bool = True) -> float:
        if not self.enabled:
            return self.total_seconds
        boundary = self.working_deadline if reserve_finalization else self.deadline
        return max(0.0, boundary - time.monotonic())

    def remaining_ms(self, *, reserve_finalization: bool = True) -> int:
        return int(self.remaining_seconds(reserve_finalization=reserve_finalization) * 1000)

    def is_exhausted(self, *, reserve_finalization: bool = True) -> bool:
        return self.cancelled or self.remaining_seconds(
            reserve_finalization=reserve_finalization
        ) <= 0.0

    def record_timeout(self, stage: str) -> None:
        safe_stage = (stage or "turn")[:80]
        with self._lock:
            if self._timeout_stage is None:
                self._timeout_stage = safe_stage

    def cancel(self, stage: str = "cancelled") -> None:
        with self._lock:
            self._cancelled = True
        self.record_timeout(stage)

    def raise_if_exhausted(
        self,
        stage: str,
        *,
        reserve_finalization: bool = True,
    ) -> None:
        if self.is_exhausted(reserve_finalization=reserve_finalization):
            self.record_timeout(stage)
            raise BudgetExhausted(stage)

    def can_start(
        self,
        stage: str,
        *,
        minimum_required: float = 0.0,
        reserve_finalization: bool = True,
    ) -> bool:
        if self.cancelled:
            return False
        allowed = self.remaining_seconds(reserve_finalization=reserve_finalization)
        if allowed < max(0.0, float(minimum_required)):
            return False
        return True

    def timeout_for(
        self,
        stage: str,
        stage_cap: float,
        *,
        minimum_required: float = 0.0,
        reserve_finalization: bool = True,
    ) -> float:
        if not self.can_start(
            stage,
            minimum_required=minimum_required,
            reserve_finalization=reserve_finalization,
        ):
            self.record_timeout(stage)
            raise BudgetExhausted(stage)
        remaining = self.remaining_seconds(reserve_finalization=reserve_finalization)
        timeout = min(max(0.0, float(stage_cap)), remaining)
        if timeout <= 0.0:
            self.record_timeout(stage)
            raise BudgetExhausted(stage)
        return timeout

    def reserve_for_finalization(self) -> float:
        return self.finalization_reserve_seconds

    def snapshot(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "total_budget_ms": int(self.total_seconds * 1000),
            "remaining_budget_ms": self.remaining_ms(),
            "absolute_remaining_ms": self.remaining_ms(reserve_finalization=False),
            "elapsed_ms": int(self.elapsed_seconds() * 1000),
            "finalization_reserve_ms": int(self.finalization_reserve_seconds * 1000),
            "timeout_stage": self.timeout_stage,
            "cancelled": self.cancelled,
        }


_current_budget: contextvars.ContextVar[Optional[TurnBudget]] = contextvars.ContextVar(
    "on_the_porch_turn_budget",
    default=None,
)


def create_turn_budget(
    turn_id: Optional[str],
    *,
    total_seconds: Optional[float] = None,
    finalization_reserve_seconds: Optional[float] = None,
) -> TurnBudget:
    return TurnBudget(
        turn_id=turn_id,
        total_seconds=(
            TURN_TOTAL_BUDGET_MS / 1000.0
            if total_seconds is None
            else total_seconds
        ),
        finalization_reserve_seconds=(
            TURN_FINALIZATION_RESERVE_MS / 1000.0
            if finalization_reserve_seconds is None
            else finalization_reserve_seconds
        ),
    )


def current_budget() -> Optional[TurnBudget]:
    return _current_budget.get()


@contextmanager
def use_turn_budget(budget: Optional[TurnBudget]) -> Iterator[Optional[TurnBudget]]:
    token = _current_budget.set(budget)
    try:
        yield budget
    finally:
        _current_budget.reset(token)


def remaining_seconds(
    *,
    reserve_finalization: bool = True,
    default: float = 60.0,
) -> float:
    budget = current_budget()
    if budget is None:
        return max(0.0, float(default))
    return budget.remaining_seconds(reserve_finalization=reserve_finalization)


def timeout_for(
    stage: str,
    stage_cap: float,
    *,
    minimum_required: float = 0.0,
    reserve_finalization: bool = True,
) -> float:
    budget = current_budget()
    if budget is None:
        return max(0.001, float(stage_cap))
    return budget.timeout_for(
        stage,
        stage_cap,
        minimum_required=minimum_required,
        reserve_finalization=reserve_finalization,
    )


def raise_if_exhausted(
    stage: str,
    *,
    reserve_finalization: bool = True,
) -> None:
    budget = current_budget()
    if budget is not None:
        budget.raise_if_exhausted(
            stage,
            reserve_finalization=reserve_finalization,
        )


__all__ = [
    "BudgetExhausted",
    "TURN_BUDGET_ENABLED",
    "TURN_FINALIZATION_RESERVE_MS",
    "TURN_MIN_RETRIEVAL_BUDGET_MS",
    "TURN_MIN_SYNTHESIS_BUDGET_MS",
    "TURN_TIMEOUT_GRACE_MS",
    "TURN_TOTAL_BUDGET_MS",
    "TurnBudget",
    "create_turn_budget",
    "current_budget",
    "raise_if_exhausted",
    "remaining_seconds",
    "timeout_for",
    "use_turn_budget",
]

# The legacy runtime imports modules both as top-level files and through the
# namespace package. Keep one ContextVar identity across both import styles.
sys.modules.setdefault("request_budget", sys.modules[__name__])
sys.modules.setdefault("on_the_porch.request_budget", sys.modules[__name__])
