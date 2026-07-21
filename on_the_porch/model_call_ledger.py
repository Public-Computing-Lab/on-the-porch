"""Request-scoped model-call accounting for the model-led answer lifecycle."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from answering_kernel import GlobalDeadline


MAX_MODEL_CALLS = 2
MIN_CALL_BUDGET_SECONDS = 0.25


class ModelCallRole(str, Enum):
    ORCHESTRATOR = "ORCHESTRATOR"
    SYNTHESIS = "SYNTHESIS"


class ModelCallLedgerError(RuntimeError):
    """Raised when a model call would violate the bounded lifecycle."""


@dataclass(frozen=True)
class ModelCallReservation:
    role: ModelCallRole
    ordinal: int
    reserved_at: float
    available_seconds: float


class ModelCallLedger:
    """Thread-safe one-orchestrator/one-synthesis ledger for one request.

    A provider failure consumes its role reservation. This deliberately prevents
    retries and repair calls from entering the future model-led path.
    """

    def __init__(
        self,
        deadline: GlobalDeadline,
        *,
        completed_replay: bool = False,
    ) -> None:
        if not isinstance(deadline, GlobalDeadline):
            raise ModelCallLedgerError("ledger requires a GlobalDeadline")
        self.deadline = deadline
        self.completed_replay = bool(completed_replay)
        self._counts: Dict[ModelCallRole, int] = {
            ModelCallRole.ORCHESTRATOR: 0,
            ModelCallRole.SYNTHESIS: 0,
        }
        self._lock = threading.Lock()

    def reserve(self, role: ModelCallRole) -> ModelCallReservation:
        if not isinstance(role, ModelCallRole):
            raise ModelCallLedgerError("model call role is not registered")
        with self._lock:
            if self.completed_replay:
                raise ModelCallLedgerError("completed replay cannot reserve model calls")
            remaining = self.deadline.remaining(reserve_for_persistence=True)
            if remaining < MIN_CALL_BUDGET_SECONDS:
                raise ModelCallLedgerError("global deadline has no model-call budget remaining")
            if self._counts[role] >= 1:
                raise ModelCallLedgerError(f"{role.value} call already reserved")
            if self.total_calls >= MAX_MODEL_CALLS:
                raise ModelCallLedgerError("total model-call limit reached")
            self._counts[role] += 1
            return ModelCallReservation(
                role=role,
                ordinal=self.total_calls,
                reserved_at=time.perf_counter(),
                available_seconds=remaining,
            )

    @property
    def total_calls(self) -> int:
        return sum(self._counts.values())

    def count(self, role: ModelCallRole) -> int:
        return self._counts[role]

    def to_safe_dict(self) -> Dict[str, object]:
        return {
            "completed_replay": self.completed_replay,
            "orchestrator_calls": self.count(ModelCallRole.ORCHESTRATOR),
            "synthesis_calls": self.count(ModelCallRole.SYNTHESIS),
            "total_calls": self.total_calls,
            "remaining_seconds": round(
                self.deadline.remaining(reserve_for_persistence=True), 3,
            ),
        }


def completed_replay_ledger(deadline: Optional[GlobalDeadline] = None) -> ModelCallLedger:
    return ModelCallLedger(deadline or GlobalDeadline.now(), completed_replay=True)


__all__ = [
    "MAX_MODEL_CALLS",
    "ModelCallLedger",
    "ModelCallLedgerError",
    "ModelCallReservation",
    "ModelCallRole",
    "completed_replay_ledger",
]
