"""Per-request time budget for chat paths (thread-local)."""

from __future__ import annotations

import os
import time
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

_local = threading.local()

DEFAULT_BUDGET_SECONDS = float(os.getenv("REQUEST_BUDGET_SECONDS", "30"))
DEFAULT_SAFETY_MARGIN_SECONDS = float(
    os.getenv("REQUEST_BUDGET_SAFETY_MARGIN_SECONDS", "0.5")
)


def reset_request_budget(seconds: Optional[float] = None) -> None:
    budget = DEFAULT_BUDGET_SECONDS if seconds is None else float(seconds)
    _local.deadline = time.perf_counter() + max(1.0, budget)


def current_deadline() -> Optional[float]:
    """Return the active wall-clock deadline for propagation to worker threads."""
    return getattr(_local, "deadline", None)


def set_request_deadline(deadline: Optional[float]) -> None:
    """Install an existing deadline in the current thread."""
    if deadline is None:
        if hasattr(_local, "deadline"):
            delattr(_local, "deadline")
        return
    _local.deadline = float(deadline)


@contextmanager
def use_request_deadline(deadline: Optional[float]) -> Iterator[None]:
    """Temporarily apply a captured deadline to this worker thread."""
    previous = current_deadline()
    set_request_deadline(deadline)
    try:
        yield
    finally:
        set_request_deadline(previous)


def remaining_seconds(*, floor: float = 0.0) -> float:
    deadline = getattr(_local, "deadline", None)
    if deadline is None:
        return DEFAULT_BUDGET_SECONDS
    return max(floor, deadline - time.perf_counter())


def seconds_until_deadline(
    deadline: Optional[float],
    *,
    floor: float = 0.0,
    safety_margin: float = 0.0,
) -> float:
    """Remaining seconds for a captured deadline, optionally reserving margin."""
    if deadline is None:
        return DEFAULT_BUDGET_SECONDS
    return max(floor, float(deadline) - time.perf_counter() - max(0.0, safety_margin))


def budget_exhausted() -> bool:
    return remaining_seconds(floor=0.0) <= 0.0


def deadline_exhausted(
    deadline: Optional[float],
    *,
    safety_margin: float = DEFAULT_SAFETY_MARGIN_SECONDS,
) -> bool:
    if deadline is None:
        return False
    return seconds_until_deadline(
        deadline,
        floor=0.0,
        safety_margin=safety_margin,
    ) <= 0.0


def wait_timeout_cap(
    default_seconds: float,
    deadline: Optional[float] = None,
    *,
    safety_margin: float = DEFAULT_SAFETY_MARGIN_SECONDS,
) -> float:
    """Timeout for waiting on a worker without consuming the whole budget."""
    default_seconds = max(0.0, float(default_seconds))
    if deadline is None:
        return default_seconds
    return max(
        0.0,
        min(
            default_seconds,
            seconds_until_deadline(
                deadline,
                floor=0.0,
                safety_margin=safety_margin,
            ),
        ),
    )


def gemini_timeout_cap(default_seconds: int) -> int:
    """Cap a Gemini per-call timeout to whatever budget remains."""
    if budget_exhausted():
        return 1
    return max(1, min(int(default_seconds), int(remaining_seconds(floor=1.0))))


def retrieval_timeout_cap(default_seconds: float = 12.0) -> float:
    if budget_exhausted():
        return 0.5
    return max(0.5, min(default_seconds, remaining_seconds(floor=0.5)))
