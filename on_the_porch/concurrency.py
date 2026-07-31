"""Compatibility import for the authoritative API concurrency registry."""

from __future__ import annotations

import sys
from pathlib import Path


_API_DIR = Path(__file__).resolve().parents[1] / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from concurrency_control import (  # noqa: E402,F401
    CapacityUnavailable,
    ConcurrencyGate,
    ConcurrencyRegistry,
    DEFAULT_QUEUE_WAIT_SECONDS,
    GateSnapshot,
    OVERLOAD_RETRY_AFTER_SECONDS,
    REGISTRY,
    gate,
)


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
