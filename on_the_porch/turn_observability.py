"""Fail-open, request-scoped observability for chatbot turns.

This module deliberately records measurements rather than conversational
content. It is disabled by default and has no database dependency.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional


_TRACE_SCHEMA_VERSION = 1
_SENSITIVE_KEY = re.compile(
    r"(authorization|cookie|csrf|session|token|api[_-]?key|password|secret|"
    r"database[_-]?(url|uri)|mysql[_-]?(url|uri))",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_DATABASE_URL = re.compile(
    r"\b(?:mysql|postgres(?:ql)?|redis)://[^\s\"']+",
    re.IGNORECASE,
)
_SENSITIVE_QUERY = re.compile(
    r"([?&](?:token|key|secret|password|session|auth)=)[^&#\s]+",
    re.IGNORECASE,
)
_SQL_STRING = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")
_SQL_SPACE = re.compile(r"\s+")
_WRITE_LOCK = threading.Lock()

_current_trace: contextvars.ContextVar[Optional["TurnTrace"]] = contextvars.ContextVar(
    "on_the_porch_turn_trace",
    default=None,
)
_stage_stack: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "on_the_porch_stage_stack",
    default=(),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sample_rate() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("TURN_OBSERVABILITY_SAMPLE_RATE", "1"))))
    except (TypeError, ValueError):
        return 1.0


def _is_sampled(trace_id: str) -> bool:
    rate = _sample_rate()
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    bucket = int(hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:8], 16)
    return (bucket / 0xFFFFFFFF) < rate


def normalize_error_category(exc: Optional[BaseException]) -> Optional[str]:
    """Return a bounded error category without retaining an exception message."""
    if exc is None:
        return None
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or any(
        marker in name or marker in message
        for marker in ("timeout", "timed out", "deadline")
    ):
        return "timeout"
    if any(marker in name or marker in message for marker in ("auth", "permission", "forbidden")):
        return "authentication"
    if any(marker in name or marker in message for marker in ("mysql", "database", "sql")):
        return "database"
    if any(marker in name or marker in message for marker in ("chroma", "qdrant", "vector")):
        return "vector_store"
    if any(marker in name or marker in message for marker in ("connection", "network", "transport")):
        return "transport"
    if any(marker in name or marker in message for marker in ("gemini", "model", "generation")):
        return "model"
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_")[:80] or "unknown"


def redact(value: Any, *, key: Optional[str] = None) -> Any:
    """Recursively redact common credentials and personal identifiers."""
    if key and _SENSITIVE_KEY.search(str(key)):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        out = _BEARER.sub("Bearer [REDACTED]", value)
        out = _DATABASE_URL.sub("[REDACTED_DATABASE_URL]", out)
        out = _SENSITIVE_QUERY.sub(r"\1[REDACTED]", out)
        out = _EMAIL.sub("[REDACTED_EMAIL]", out)
        return out
    return value


def sql_fingerprint(sql: str) -> str:
    """Return a non-reversible fingerprint of normalized SQL."""
    without_literals = _SQL_STRING.sub("?", sql or "")
    normalized = _SQL_SPACE.sub(" ", without_literals).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class TurnTrace:
    """Mutable, thread-safe trace shared by one request and its executor tasks."""

    def __init__(
        self,
        *,
        trace_id: str,
        request_id: Optional[str],
        conversation_id: Optional[str],
        user_category: str,
        transport: str,
        scenario_id: Optional[str] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._started_perf = time.perf_counter()
        self._completed = False
        self._emitted = False
        self.data: Dict[str, Any] = {
            "schema_version": _TRACE_SCHEMA_VERSION,
            "event": "turn_trace",
            "trace_id": trace_id,
            "request_id": request_id or trace_id[:12],
            "conversation_id": conversation_id,
            "user_category": user_category or "unknown",
            "transport": transport or "unknown",
            "scenario_id": scenario_id,
            "client_turn_id": None,
            "internal_turn_id": None,
            "client_idempotent": None,
            "reservation_result": None,
            "replayed": False,
            "replay_source": None,
            "fingerprint_conflict": False,
            "turn_state": None,
            "execution_owner": False,
            "same_thread_contention": False,
            "version_conflict": False,
            "persistence_transaction_ms": None,
            "duplicate_suppression_count": 0,
            "started_at": _utc_now(),
            "completed_at": None,
            "completion_status": "running",
            "http_status": None,
            "route": None,
            "mode": None,
            "history_message_count": 0,
            "prompt_chars_approx": 0,
            "sql_query_count": 0,
            "sql_fingerprints": [],
            "dynamic_sql_validations": [],
            "model_call_count": 0,
            "models": [],
            "retry_count": 0,
            "retries_by_scope": {},
            "embedding_call_count": 0,
            "retrieved_chunk_count": 0,
            "sql_row_count": 0,
            "source_count": 0,
            "response_char_count": 0,
            "duration_ms": None,
            "error_category": None,
            "timeout_category": None,
            "sse_disconnect": False,
            "sse_fallback": False,
            "stages": [],
            "model_calls": [],
        }

    def update(self, **fields: Any) -> None:
        allowed = {
            "request_id",
            "conversation_id",
            "user_category",
            "transport",
            "scenario_id",
            "http_status",
            "route",
            "mode",
            "history_message_count",
            "prompt_chars_approx",
            "source_count",
            "response_char_count",
            "error_category",
            "timeout_category",
            "sse_disconnect",
            "sse_fallback",
            "client_turn_id",
            "internal_turn_id",
            "client_idempotent",
            "reservation_result",
            "replayed",
            "replay_source",
            "fingerprint_conflict",
            "turn_state",
            "execution_owner",
            "same_thread_contention",
            "version_conflict",
            "persistence_transaction_ms",
            "duplicate_suppression_count",
        }
        with self._lock:
            if self._completed:
                return
            for key, value in fields.items():
                if key in allowed and value is not None:
                    self.data[key] = redact(value, key=key)

    def increment(self, field: str, count: int = 1) -> None:
        allowed = {
            "sql_query_count",
            "model_call_count",
            "retry_count",
            "embedding_call_count",
            "retrieved_chunk_count",
            "sql_row_count",
            "source_count",
            "prompt_chars_approx",
            "response_char_count",
        }
        if field not in allowed:
            return
        with self._lock:
            if self._completed:
                return
            self.data[field] = int(self.data.get(field) or 0) + max(0, int(count or 0))

    def add_stage(self, value: Mapping[str, Any]) -> None:
        with self._lock:
            if not self._completed:
                self.data["stages"].append(redact(dict(value)))

    def add_model_call(self, value: Mapping[str, Any]) -> None:
        with self._lock:
            if self._completed:
                return
            model = str(value.get("model") or "unknown")
            if model not in self.data["models"]:
                self.data["models"].append(model)
            self.data["model_calls"].append(redact(dict(value)))

    def add_retry(self, scope: str, count: int = 1) -> None:
        safe_scope = re.sub(r"[^a-z0-9_.-]+", "_", (scope or "unknown").lower())[:80]
        with self._lock:
            if self._completed:
                return
            amount = max(0, int(count or 0))
            self.data["retry_count"] += amount
            retries = self.data["retries_by_scope"]
            retries[safe_scope] = int(retries.get(safe_scope) or 0) + amount

    def add_sql(self, sql: Optional[str], row_count: Optional[int]) -> None:
        with self._lock:
            if self._completed:
                return
            self.data["sql_query_count"] += 1
            if sql:
                fingerprint = sql_fingerprint(sql)
                if fingerprint not in self.data["sql_fingerprints"]:
                    self.data["sql_fingerprints"].append(fingerprint)
            if row_count is not None:
                self.data["sql_row_count"] += max(0, int(row_count))

    def add_dynamic_sql_validation(self, value: Mapping[str, Any]) -> None:
        """Record bounded SQL-safety diagnostics without raw SQL or literals."""
        with self._lock:
            if self._completed:
                return
            items = self.data["dynamic_sql_validations"]
            if len(items) < 8:
                items.append(redact(dict(value)))

    def finish(
        self,
        *,
        status: str,
        http_status: Optional[int] = None,
        error: Optional[BaseException] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if not self._completed:
                if status == "auto":
                    if self.data.get("sse_disconnect"):
                        status = "client_disconnect"
                    elif self.data.get("error_category"):
                        status = "failure"
                    else:
                        status = "success"
                self.data["completed_at"] = _utc_now()
                self.data["duration_ms"] = round(
                    (time.perf_counter() - self._started_perf) * 1000,
                    3,
                )
                self.data["completion_status"] = status
                if http_status is not None:
                    self.data["http_status"] = int(http_status)
                if error is not None:
                    category = normalize_error_category(error)
                    self.data["error_category"] = category
                    if category == "timeout":
                        self.data["timeout_category"] = "request"
                self._completed = True
            return redact(dict(self.data))


class StageHandle:
    def __init__(
        self,
        trace: Optional[TurnTrace],
        name: str,
        *,
        parent: Optional[str],
        nested: bool,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.trace = trace
        self.name = name
        self.parent = parent
        self.nested = nested
        self.attributes = redact(dict(attributes or {}))
        self.started = time.perf_counter()
        self.start_offset_ms = (
            round((self.started - trace._started_perf) * 1000, 3) if trace else 0
        )
        self.finished = False

    def finish(self, error: Optional[BaseException] = None) -> None:
        if self.finished or self.trace is None:
            return
        self.finished = True
        self.trace.add_stage(
            {
                "name": self.name,
                "parent": self.parent,
                "nested": self.nested,
                "start_offset_ms": self.start_offset_ms,
                "duration_ms": round((time.perf_counter() - self.started) * 1000, 3),
                "success": error is None,
                "error_category": normalize_error_category(error),
                "attributes": self.attributes or None,
            }
        )


class ModelCallHandle:
    def __init__(
        self,
        trace: Optional[TurnTrace],
        *,
        purpose: str,
        model: str,
        attempt: int,
        input_size: int,
    ) -> None:
        self.trace = trace
        self.purpose = purpose or "unspecified"
        self.model = model or "unknown"
        self.attempt = max(1, int(attempt or 1))
        self.input_size = max(0, int(input_size or 0))
        self.output_size = 0
        self.started = time.perf_counter()
        self.finished = False
        if trace is not None:
            trace.increment("model_call_count")
            trace.increment("prompt_chars_approx", self.input_size)

    def set_output_size(self, value: int) -> None:
        self.output_size = max(0, int(value or 0))

    def finish(self, error: Optional[BaseException] = None) -> None:
        if self.finished or self.trace is None:
            return
        self.finished = True
        self.trace.add_model_call(
            {
                "purpose": self.purpose,
                "model": self.model,
                "attempt": self.attempt,
                "duration_ms": round((time.perf_counter() - self.started) * 1000, 3),
                "success": error is None,
                "error_category": normalize_error_category(error),
                "input_chars_approx": self.input_size,
                "output_chars_approx": self.output_size,
            }
        )


def begin_turn(
    *,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    user_category: str = "unknown",
    transport: str = "unknown",
    scenario_id: Optional[str] = None,
) -> Optional[TurnTrace]:
    if not _env_bool("TURN_OBSERVABILITY_ENABLED", False):
        return None
    final_trace_id = trace_id or uuid.uuid4().hex
    if not _is_sampled(final_trace_id):
        return None
    if scenario_id and not _env_bool("TURN_OBSERVABILITY_LOG_TEXT", False):
        scenario_id = None
    return TurnTrace(
        trace_id=final_trace_id,
        request_id=request_id,
        conversation_id=conversation_id,
        user_category=user_category,
        transport=transport,
        scenario_id=scenario_id,
    )


def get_current_trace() -> Optional[TurnTrace]:
    return _current_trace.get()


def activate_trace(trace: Optional[TurnTrace]) -> contextvars.Token:
    return _current_trace.set(trace)


def deactivate_trace(token: contextvars.Token) -> None:
    try:
        _current_trace.reset(token)
    except Exception:
        pass


@contextmanager
def bind_trace(trace: Optional[TurnTrace]) -> Iterator[Optional[TurnTrace]]:
    token = activate_trace(trace)
    try:
        yield trace
    finally:
        deactivate_trace(token)


@contextmanager
def stage(
    name: str,
    *,
    attributes: Optional[Mapping[str, Any]] = None,
) -> Iterator[StageHandle]:
    trace = get_current_trace()
    stack = _stage_stack.get()
    handle = StageHandle(
        trace,
        name,
        parent=stack[-1] if stack else None,
        nested=bool(stack),
        attributes=attributes,
    )
    token = _stage_stack.set(stack + (name,))
    try:
        yield handle
    except BaseException as exc:
        handle.finish(exc)
        raise
    else:
        handle.finish()
    finally:
        _stage_stack.reset(token)


def start_stage(
    name: str,
    *,
    attributes: Optional[Mapping[str, Any]] = None,
) -> StageHandle:
    stack = _stage_stack.get()
    return StageHandle(
        get_current_trace(),
        name,
        parent=stack[-1] if stack else None,
        nested=bool(stack),
        attributes=attributes,
    )


@contextmanager
def model_call(
    purpose: str,
    model: str,
    *,
    attempt: int = 1,
    input_size: int = 0,
) -> Iterator[ModelCallHandle]:
    handle = ModelCallHandle(
        get_current_trace(),
        purpose=purpose,
        model=model,
        attempt=attempt,
        input_size=input_size,
    )
    try:
        yield handle
    except BaseException as exc:
        handle.finish(exc)
        raise
    else:
        handle.finish()


def submit_with_trace(executor: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Submit work with an independent copy of the current context."""
    context = contextvars.copy_context()
    return executor.submit(context.run, fn, *args, **kwargs)


def record_fields(**fields: Any) -> None:
    trace = get_current_trace()
    if trace is not None:
        trace.update(**fields)


def increment(field: str, count: int = 1) -> None:
    trace = get_current_trace()
    if trace is not None:
        trace.increment(field, count)


def record_retry(scope: str, count: int = 1) -> None:
    trace = get_current_trace()
    if trace is not None:
        trace.add_retry(scope, count)


def record_sql(sql: Optional[str] = None, row_count: Optional[int] = None) -> None:
    trace = get_current_trace()
    if trace is not None:
        trace.add_sql(sql, row_count)


def record_dynamic_sql_validation(value: Mapping[str, Any]) -> None:
    trace = get_current_trace()
    if trace is not None:
        trace.add_dynamic_sql_validation(value)


def record_retrieval(chunk_count: int) -> None:
    increment("retrieved_chunk_count", chunk_count)


def record_timeout(category: str) -> None:
    record_fields(timeout_category=(category or "unknown")[:80])


def _emit_trace(record: Mapping[str, Any]) -> None:
    sink = os.getenv("TURN_OBSERVABILITY_SINK", "stdout").strip().lower()
    payload = json.dumps(redact(dict(record)), ensure_ascii=False, sort_keys=True)
    if sink in {"", "none", "off", "disabled"}:
        return
    if sink == "stdout":
        print(payload, file=sys.stderr, flush=True)
        return
    if sink == "jsonl":
        output = Path(
            os.getenv(
                "TURN_OBSERVABILITY_OUTPUT_PATH",
                "logs/turn_observability.jsonl",
            )
        ).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            with output.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
        return
    raise ValueError("Unsupported TURN_OBSERVABILITY_SINK")


def finish_turn(
    trace: Optional[TurnTrace],
    *,
    status: str = "success",
    http_status: Optional[int] = None,
    error: Optional[BaseException] = None,
) -> Optional[Dict[str, Any]]:
    if trace is None:
        return None
    record = trace.finish(status=status, http_status=http_status, error=error)
    with trace._lock:
        if trace._emitted:
            return record
        trace._emitted = True
    try:
        _emit_trace(record)
    except Exception:
        pass
    return record


__all__ = [
    "TurnTrace",
    "activate_trace",
    "begin_turn",
    "bind_trace",
    "deactivate_trace",
    "finish_turn",
    "get_current_trace",
    "increment",
    "model_call",
    "normalize_error_category",
    "record_fields",
    "record_dynamic_sql_validation",
    "record_retrieval",
    "record_retry",
    "record_sql",
    "record_timeout",
    "redact",
    "sql_fingerprint",
    "stage",
    "start_stage",
    "submit_with_trace",
]
