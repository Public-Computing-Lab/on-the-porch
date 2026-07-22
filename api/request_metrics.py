"""Per-request chat metrics for production observability (Iteration 0).

Tracks LLM call counts via a thread-local counter reset at the start of each
chat request. Structured logs are emitted as single-line JSON for grep/ELK.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

_metrics_local = threading.local()


def reset_request_metrics(*, reset_budget: bool = True) -> None:
    """Reset per-request LLM counters.

    When ``reset_budget`` is false, preserve any already-installed wall-clock
    deadline so an authoritative 15-second turn budget is not replaced by the
    legacy 30-second default mid-request.
    """
    try:
        from unified_chatbot import reset_llm_call_counter  # type: ignore

        reset_llm_call_counter()
    except Exception:
        pass
    if reset_budget:
        try:
            from request_budget import reset_request_budget  # type: ignore

            reset_request_budget()
        except Exception:
            pass
    _metrics_local.llm_calls = 0
    _metrics_local.started_at = time.perf_counter()


def increment_llm_calls(count: int = 1) -> None:
    current = getattr(_metrics_local, "llm_calls", 0)
    _metrics_local.llm_calls = current + max(0, count)


def get_llm_calls() -> int:
    try:
        from unified_chatbot import get_llm_call_count  # type: ignore

        return int(get_llm_call_count())
    except Exception:
        return int(getattr(_metrics_local, "llm_calls", 0))


def resolve_execution_model_name(
    result: Dict[str, Any],
    *,
    configured_model: Optional[str],
    llm_calls: Optional[int] = None,
) -> Optional[str]:
    """Return persisted model metadata from what this request actually executed."""
    if "model_name" in result:
        explicit = result.get("model_name")
        if explicit is None:
            return None
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

    calls = get_llm_calls() if llm_calls is None else max(0, int(llm_calls))
    if calls == 0:
        return None

    model = (configured_model or "").strip()
    return model or None


def elapsed_ms() -> int:
    started = getattr(_metrics_local, "started_at", None)
    if started is None:
        return 0
    return int((time.perf_counter() - started) * 1000)


@contextmanager
def track_chat_request() -> Iterator[None]:
    """Reset counters at the start of a chat/agent path."""
    reset_request_metrics()
    try:
        yield
    finally:
        pass


def get_request_id() -> Optional[str]:
    try:
        from flask import g

        return getattr(g, "request_id", None)
    except Exception:
        return None


def build_chat_metrics(
    *,
    mode: str,
    cache_hit: bool = False,
    intent: Optional[str] = None,
    thread_id: Optional[str] = None,
    streaming: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "event": "chat_request",
        "mode": mode,
        "intent": intent or mode,
        "llm_calls": get_llm_calls(),
        "latency_ms": elapsed_ms(),
        "cache_hit": cache_hit,
        "streaming": streaming,
    }
    if thread_id:
        payload["thread_id"] = thread_id
    for key in ("request_id", "tier", "cache_layer", "embed_provider"):
        if extra and extra.get(key) is not None:
            payload[key] = extra[key]
    if extra:
        for k, v in extra.items():
            if k not in payload:
                payload[k] = v
    return payload


def log_chat_metrics(**kwargs: Any) -> None:
    """Emit one structured JSON line to stderr (picked up by gunicorn error log)."""
    if not kwargs.get("enabled", True):
        return
    payload = build_chat_metrics(**{k: v for k, v in kwargs.items() if k != "enabled"})
    try:
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
    except Exception:
        pass
