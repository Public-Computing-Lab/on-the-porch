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


def reset_request_metrics() -> None:
    try:
        from unified_chatbot import reset_llm_call_counter  # type: ignore

        reset_llm_call_counter()
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
    if extra:
        payload.update(extra)
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
