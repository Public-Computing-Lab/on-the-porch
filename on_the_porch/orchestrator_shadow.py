"""Off-by-default shadow diagnostics for the future orchestrator.

This module does not import or call the resident API. It makes the authority
boundary explicit for a later, deliberately small integration phase.
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional


class OrchestratorMode(str, Enum):
    OFF = "off"
    OFFLINE = "offline"
    SHADOW = "shadow"


@dataclass(frozen=True)
class ShadowDiagnostic:
    mode: OrchestratorMode
    attempted: bool
    completed: bool
    strategy: Optional[str]
    relationship: Optional[str]
    tool_ids: tuple[str, ...]
    error_type: Optional[str]


def parse_orchestrator_mode(raw: Any) -> OrchestratorMode:
    try:
        return OrchestratorMode(str(raw or "off").strip().lower())
    except ValueError:
        return OrchestratorMode.OFF


def preserve_authoritative_result(
    authoritative_result: Any,
    *,
    mode: OrchestratorMode,
    shadow_call: Optional[Callable[[], Mapping[str, Any]]] = None,
    timeout_seconds: float = 1.5,
) -> tuple[Any, ShadowDiagnostic]:
    """Run isolated diagnostics while returning the authoritative object unchanged."""
    if mode is not OrchestratorMode.SHADOW or shadow_call is None:
        return authoritative_result, ShadowDiagnostic(mode, False, False, None, None, (), None)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orchestrator-shadow")
    future = executor.submit(shadow_call)
    try:
        decision = copy.deepcopy(dict(future.result(timeout=max(0.05, min(3.0, timeout_seconds)))))
        tools = decision.get("tool_requests") if isinstance(decision.get("tool_requests"), list) else []
        tool_ids = tuple(
            str(item.get("tool_id"))[:80]
            for item in tools[:4]
            if isinstance(item, Mapping) and item.get("tool_id")
        )
        diagnostic = ShadowDiagnostic(
            mode=mode,
            attempted=True,
            completed=True,
            strategy=str(decision.get("strategy"))[:32] if decision.get("strategy") else None,
            relationship=str(decision.get("relationship"))[:32] if decision.get("relationship") else None,
            tool_ids=tool_ids,
            error_type=None,
        )
    except FutureTimeoutError:
        future.cancel()
        diagnostic = ShadowDiagnostic(
            mode=mode,
            attempted=True,
            completed=False,
            strategy=None,
            relationship=None,
            tool_ids=(),
            error_type="TimeoutError",
        )
    except Exception as exc:
        diagnostic = ShadowDiagnostic(
            mode=mode,
            attempted=True,
            completed=False,
            strategy=None,
            relationship=None,
            tool_ids=(),
            error_type=type(exc).__name__[:80],
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return authoritative_result, diagnostic


__all__ = [
    "OrchestratorMode",
    "ShadowDiagnostic",
    "parse_orchestrator_mode",
    "preserve_authoritative_result",
]
