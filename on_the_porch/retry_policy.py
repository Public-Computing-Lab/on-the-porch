"""Central deadline-aware retry policy for resident request dependencies."""

from __future__ import annotations

import errno
import random
import socket
import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

try:
    from .request_budget import BudgetExhausted, TurnBudget, current_budget
except ImportError:  # Direct script loading keeps the legacy import path.
    from request_budget import BudgetExhausted, TurnBudget, current_budget


T = TypeVar("T")


TRANSIENT = frozenset(
    {"rate_limited", "provider_5xx", "connection_reset", "network", "timeout"}
)
NONRETRYABLE = frozenset(
    {
        "authentication",
        "authorization",
        "invalid_input",
        "sql_safety",
        "capacity_unavailable",
        "deadline_exhausted",
        "provider_4xx",
        "parser",
        "permanent",
    }
)


def classify_retry_error(exc: BaseException) -> str:
    """Classify errors without retaining provider messages in state or traces."""
    if isinstance(exc, BudgetExhausted):
        return "deadline_exhausted"
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if name == "capacityunavailable" or "capacity_unavailable" in message:
        return "capacity_unavailable"
    if name == "sqlsafetyerror" or "sql_safety" in message:
        return "sql_safety"
    if any(token in name or token in message for token in ("auth", "unauthorized")):
        return "authentication"
    if any(token in message for token in ("permission denied", "forbidden", "ownership")):
        return "authorization"
    if any(token in name for token in ("validation", "valueerror", "typeerror")):
        return "invalid_input"
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    try:
        status_value = int(status)
    except (TypeError, ValueError):
        status_value = 0
    if status_value == 429 or any(
        token in message for token in ("429", "rate limit", "resource exhausted")
    ):
        return "rate_limited"
    if 500 <= status_value <= 599 or any(
        token in message for token in ("500", "502", "503", "504", "service unavailable")
    ):
        return "provider_5xx"
    if 400 <= status_value <= 499:
        return "provider_4xx"
    if isinstance(exc, (TimeoutError, socket.timeout)) or any(
        token in name or token in message for token in ("timeout", "timed out", "deadline")
    ):
        return "timeout"
    error_number = getattr(exc, "errno", None)
    if error_number in {errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE} or any(
        token in message for token in ("connection reset", "broken pipe")
    ):
        return "connection_reset"
    if isinstance(exc, (ConnectionError, OSError)) or any(
        token in name or token in message for token in ("network", "transport", "connection")
    ):
        return "network"
    if "parse" in name or "parser" in message:
        return "parser"
    return "permanent"


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    category: str
    attempt: int
    backoff_seconds: float = 0.0
    reason: str = ""


class RetryPolicy(Generic[T]):
    """At most one transient retry, constrained by the shared turn budget."""

    def __init__(
        self,
        *,
        max_attempts: int = 2,
        base_backoff_seconds: float = 0.25,
        max_backoff_seconds: float = 1.0,
        minimum_attempt_seconds: float = 0.75,
        jitter_ratio: float = 0.15,
        random_source: Optional[random.Random] = None,
    ) -> None:
        self.max_attempts = max(1, min(int(max_attempts), 2))
        self.base_backoff_seconds = max(0.0, float(base_backoff_seconds))
        self.max_backoff_seconds = max(0.0, float(max_backoff_seconds))
        self.minimum_attempt_seconds = max(0.0, float(minimum_attempt_seconds))
        self.jitter_ratio = max(0.0, min(float(jitter_ratio), 0.5))
        self.random_source = random_source or random.Random()

    def decision(
        self,
        exc: BaseException,
        *,
        attempt: int,
        budget: Optional[TurnBudget] = None,
        stage: str = "external_call",
    ) -> RetryDecision:
        category = classify_retry_error(exc)
        if category not in TRANSIENT:
            return RetryDecision(False, category, attempt, reason="nonretryable")
        if attempt >= self.max_attempts:
            return RetryDecision(False, category, attempt, reason="attempt_limit")
        active_budget = budget or current_budget()
        base = min(
            self.max_backoff_seconds,
            self.base_backoff_seconds * (2 ** max(0, attempt - 1)),
        )
        jitter = base * self.jitter_ratio * self.random_source.random()
        backoff = base + jitter
        if active_budget is not None:
            required = backoff + self.minimum_attempt_seconds
            if not active_budget.can_start(stage, minimum_required=required):
                return RetryDecision(
                    False,
                    category,
                    attempt,
                    reason="insufficient_budget",
                )
            backoff = min(backoff, max(0.0, active_budget.remaining_seconds() - self.minimum_attempt_seconds))
        return RetryDecision(True, category, attempt, backoff, "transient")

    def run(
        self,
        operation: Callable[[int], T],
        *,
        stage: str,
        budget: Optional[TurnBudget] = None,
        observe: Optional[Callable[[RetryDecision], None]] = None,
    ) -> T:
        active_budget = budget or current_budget()
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            if active_budget is not None:
                active_budget.raise_if_exhausted(stage)
            try:
                return operation(attempt)
            except BaseException as exc:
                last_error = exc
                decision = self.decision(
                    exc,
                    attempt=attempt,
                    budget=active_budget,
                    stage=stage,
                )
                if observe is not None:
                    observe(decision)
                if not decision.retry:
                    raise
                if decision.backoff_seconds:
                    time.sleep(decision.backoff_seconds)
        assert last_error is not None
        raise last_error


DEFAULT_RETRY_POLICY: RetryPolicy[object] = RetryPolicy()


__all__ = [
    "DEFAULT_RETRY_POLICY",
    "NONRETRYABLE",
    "RetryDecision",
    "RetryPolicy",
    "TRANSIENT",
    "classify_retry_error",
]
