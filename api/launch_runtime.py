"""Authoritative production launch-runtime contract and privacy-safe observability.

Single source of truth for PRODUCTION_MODE fail-closed validation.
Do not print secret values. Process-local counters are non-authoritative diagnostics only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Banned Flask/session secrets (exact match after strip).
_BANNED_SECRETS = frozenset(
    {
        "",
        "agent-api-secret-2025",
        "change-me-in-production",
        "changeme",
        "secret",
        "password",
        "flask-secret",
        "dev-secret",
        "test-secret",
    }
)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_REQUEST_ID_HEADER = "X-Request-ID"

# Non-authoritative per-process diagnostics only (not shared across workers).
_local_diag = threading.local()
_process_counters_lock = threading.Lock()
_process_counters: Dict[str, int] = {}


@dataclass
class LaunchValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _truthy(raw: Optional[str], default: str = "false") -> bool:
    return (raw if raw is not None else default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_banned_secret(value: str) -> bool:
    text = (value or "").strip()
    if text in _BANNED_SECRETS:
        return True
    lowered = text.lower()
    if lowered.startswith("change-me"):
        return True
    if len(text) < 24:
        return True
    # Reject low-entropy repeats like "aaaaaaaaaaaaaaaaaaaaaaaa"
    if len(set(text)) < 8:
        return True
    return False


def validate_launch_runtime(
    *,
    production_mode: Optional[bool] = None,
    env: Optional[Dict[str, str]] = None,
) -> LaunchValidationResult:
    """Fail-closed production contract. Categories only — never echo secret values."""
    source = env if env is not None else os.environ
    prod = (
        bool(production_mode)
        if production_mode is not None
        else _truthy(source.get("PRODUCTION_MODE"), "false")
    )
    result = LaunchValidationResult(ok=True)
    if not prod:
        return result

    errors: List[str] = []
    warnings: List[str] = []

    secret = source.get("FLASK_SECRET_KEY", source.get("SECRET_KEY", ""))
    if _is_banned_secret(secret):
        errors.append(
            "FLASK_SECRET_KEY must be a non-default secret with sufficient length/entropy"
        )

    pepper = source.get("TOKEN_PEPPER", "")
    if not (pepper or "").strip() or _is_banned_secret(pepper) or pepper == secret:
        errors.append(
            "TOKEN_PEPPER must be set to a distinct non-default secret in production"
        )

    if _truthy(source.get("FLASK_DEBUG"), "false"):
        errors.append("FLASK_DEBUG must be false in production")
    if _truthy(source.get("TESTING"), "false") or _truthy(
        source.get("FLASK_TESTING"), "false"
    ):
        errors.append("TESTING/FLASK_TESTING must be false in production")
    if not _truthy(source.get("FLASK_SESSION_COOKIE_SECURE"), "false"):
        errors.append("FLASK_SESSION_COOKIE_SECURE must be true in production")

    cache_backend = (source.get("CACHE_BACKEND") or "mysql").strip().lower()
    if cache_backend != "mysql":
        errors.append("CACHE_BACKEND must be 'mysql' in production")
    semantic_backend = (
        source.get("SEMANTIC_CACHE_BACKEND") or cache_backend
    ).strip().lower()
    if semantic_backend != "mysql":
        errors.append("SEMANTIC_CACHE_BACKEND must be 'mysql' in production")
    if _truthy(source.get("ALLOW_SINGLE_PROCESS_MEMORY_BACKEND"), "false"):
        errors.append(
            "ALLOW_SINGLE_PROCESS_MEMORY_BACKEND must be false in production"
        )

    if _truthy(source.get("DYNAMIC_SQL_ENABLED"), "false"):
        errors.append("DYNAMIC_SQL_ENABLED must be false in production")
    if _truthy(source.get("CHAT_VECTOR_WRITES_ENABLED"), "false"):
        errors.append("CHAT_VECTOR_WRITES_ENABLED must be false in production")

    civic_hybrid_mode = (source.get("CIVIC_311_HYBRID_MODE") or "auto").strip().lower()
    if civic_hybrid_mode not in {"disabled", "auto", "required"}:
        errors.append("CIVIC_311_HYBRID_MODE must be disabled, auto, or required")
    if civic_hybrid_mode == "required" and not (source.get("GEMINI_API_KEY") or "").strip():
        errors.append("CIVIC_311_HYBRID_MODE=required needs a configured model provider")

    if (source.get("OTP_TURN_IDEMPOTENCY_FAULTS") or "").strip() == "1":
        errors.append(
            "OTP_TURN_IDEMPOTENCY_FAULTS must be disabled in production"
        )

    mysql_host = (source.get("MYSQL_HOST") or "").strip()
    mysql_db = (source.get("MYSQL_DB") or "").strip()
    mysql_user = (source.get("MYSQL_USER") or "").strip()
    if not mysql_host or not mysql_db or not mysql_user:
        errors.append("MySQL connection settings (host/db/user) must be present")

    # Unsupported Redis activation must not be possible.
    if cache_backend == "redis" or semantic_backend == "redis":
        errors.append("Redis backends are not supported")
    if _truthy(source.get("REDIS_ENABLED"), "false"):
        errors.append("REDIS_ENABLED must not activate Redis (unsupported)")

    rate_limit_set = source.get("RATE_LIMIT_ENABLED")
    if rate_limit_set is None or str(rate_limit_set).strip() == "":
        errors.append("RATE_LIMIT_ENABLED must be set explicitly in production")

    origins_raw = (source.get("ALLOWED_ORIGINS") or "").strip()
    if not origins_raw:
        errors.append("ALLOWED_ORIGINS must be set explicitly when CORS is used")
    else:
        origins = [p.strip() for p in origins_raw.split(",") if p.strip()]
        if any("*" in o for o in origins):
            errors.append("ALLOWED_ORIGINS must not use wildcard origins in production")

    legacy = _truthy(source.get("LEGACY_CHAT_ENABLED"), "false")
    if legacy:
        warnings.append(
            "LEGACY_CHAT_ENABLED=true: unsupported for the public web client"
        )

    if not _truthy(source.get("SEMANTIC_CACHE_ENABLED"), "false"):
        warnings.append(
            "SEMANTIC_CACHE_ENABLED is false; consider enabling to reduce repeat LLM cost"
        )

    result.errors = errors
    result.warnings = warnings
    result.ok = len(errors) == 0
    return result


def assert_launch_runtime(*, production_mode: Optional[bool] = None) -> None:
    """Raise RuntimeError before serving if production contract fails."""
    result = validate_launch_runtime(production_mode=production_mode)
    for msg in result.warnings:
        print(f"WARNING: Launch runtime: {msg}", file=sys.stderr)
    for msg in result.errors:
        print(f"ERROR: Launch runtime: {msg}", file=sys.stderr)
    if not result.ok:
        raise RuntimeError(
            "Invalid production configuration (PRODUCTION_MODE=true). "
            "Fix environment settings (categories logged above), then restart."
        )


def legacy_chat_enabled(*, production_mode: bool, env: Optional[Dict[str, str]] = None) -> bool:
    source = env if env is not None else os.environ
    if production_mode:
        return _truthy(source.get("LEGACY_CHAT_ENABLED"), "false")
    # Development: allow unless explicitly disabled.
    if "LEGACY_CHAT_ENABLED" in source:
        return _truthy(source.get("LEGACY_CHAT_ENABLED"), "true")
    return True


def require_client_turn_id(
    *, production_mode: bool, env: Optional[Dict[str, str]] = None
) -> bool:
    source = env if env is not None else os.environ
    if "REQUIRE_CLIENT_TURN_ID" in source:
        return _truthy(source.get("REQUIRE_CLIENT_TURN_ID"), "false")
    return bool(production_mode)


def normalize_request_id(raw: Any) -> str:
    """Accept bounded incoming ID or generate a server-owned ID."""
    if raw is None:
        return str(uuid.uuid4())
    text = str(raw).strip()
    if _REQUEST_ID_RE.match(text):
        return text
    return str(uuid.uuid4())


def request_id_header_name() -> str:
    return _REQUEST_ID_HEADER


# ---------------------------------------------------------------------------
# Privacy-safe structured completion events
# ---------------------------------------------------------------------------

IDEMPOTENCY_OUTCOMES = frozenset(
    {
        "NEW_TURN",
        "COMPLETED_REPLAY",
        "TURN_IN_PROGRESS",
        "IDEMPOTENCY_KEY_REUSED",
        "EXPIRED_LEASE_RECLAIMED",
        "FAILED_ATTEMPT_RETRIED",
        "DEADLOCK_MAPPED_TO_RETRY",
        "POOL_PRESSURE_MAPPED_TO_RETRY",
        "PERSISTENCE_COMPLETED",
        "PERSISTENCE_FAILED",
        "ABSENT",
        "NOT_APPLICABLE",
    }
)


def bump_process_counter(name: str, amount: int = 1) -> None:
    """Non-authoritative per-process diagnostic counter (not shared across workers)."""
    with _process_counters_lock:
        _process_counters[name] = int(_process_counters.get(name, 0)) + amount


def get_process_counters_snapshot() -> Dict[str, Any]:
    with _process_counters_lock:
        return {
            "authoritative": False,
            "scope": "process_local_diagnostics_only",
            "counters": dict(_process_counters),
        }


def note_timing(bucket: str, elapsed_ms: float) -> None:
    store = getattr(_local_diag, "timings", None)
    if store is None:
        store = {}
        _local_diag.timings = store
    store[bucket] = int(max(0, elapsed_ms))


def get_timings() -> Dict[str, int]:
    return dict(getattr(_local_diag, "timings", {}) or {})


def reset_request_observability() -> None:
    _local_diag.timings = {}
    _local_diag.completion_emitted = False
    _local_diag.idempotency_outcome = "ABSENT"


def set_idempotency_outcome(outcome: str) -> None:
    if outcome not in IDEMPOTENCY_OUTCOMES:
        outcome = "NOT_APPLICABLE"
    _local_diag.idempotency_outcome = outcome
    bump_process_counter(f"idempotency_{outcome.lower()}")


def get_idempotency_outcome() -> str:
    return str(getattr(_local_diag, "idempotency_outcome", "ABSENT") or "ABSENT")


def _pool_wait_bucket(ms: Optional[float]) -> str:
    if ms is None:
        return "unknown"
    value = float(ms)
    if value < 1:
        return "lt_1ms"
    if value < 5:
        return "lt_5ms"
    if value < 20:
        return "lt_20ms"
    if value < 100:
        return "lt_100ms"
    return "gte_100ms"


def emit_request_completion(
    *,
    enabled: bool,
    request_id: Optional[str],
    endpoint_family: str,
    route_or_mode: str,
    http_status: int,
    completion_status: str,
    latency_ms: int,
    worker_pid: int,
    authenticated_or_guest: str,
    idempotency_outcome: Optional[str] = None,
    mysql_pool_wait_ms: Optional[float] = None,
    llm_calls: int = 0,
    embedding_calls: int = 0,
    source_freshness_status: Optional[str] = None,
    error_code: Optional[str] = None,
    streaming: bool = False,
) -> None:
    """Emit exactly one privacy-safe completion event per request when possible."""
    if not enabled:
        return
    if getattr(_local_diag, "completion_emitted", False):
        return
    _local_diag.completion_emitted = True
    outcome = idempotency_outcome or get_idempotency_outcome()
    if outcome not in IDEMPOTENCY_OUTCOMES:
        outcome = "NOT_APPLICABLE"
    payload: Dict[str, Any] = {
        "event_name": "request_completion",
        "request_id": request_id,
        "endpoint_family": endpoint_family,
        "route": route_or_mode,
        "http_status": int(http_status),
        "completion_status": completion_status,
        "latency_ms": int(latency_ms),
        "worker_pid": int(worker_pid),
        "authenticated_or_guest": authenticated_or_guest,
        "idempotency_outcome": outcome,
        "mysql_pool_wait_bucket": _pool_wait_bucket(mysql_pool_wait_ms),
        "llm_calls": max(0, int(llm_calls)),
        "embedding_calls": max(0, int(embedding_calls)),
        "streaming": bool(streaming),
        "counters_authoritative": False,
    }
    timings = get_timings()
    if timings:
        # Bound timing keys to approved operational categories.
        allowed = {
            "mysql_checkout_ms",
            "idempotency_reservation_ms",
            "replay_ms",
            "freshness_lookup_ms",
            "model_ms",
            "persistence_ms",
            "interaction_log_ms",
        }
        payload["latency_buckets"] = {
            k: timings[k] for k in allowed if k in timings
        }
    if source_freshness_status:
        payload["source_freshness_status"] = str(source_freshness_status)[:64]
    if error_code:
        payload["error_code"] = str(error_code)[:64]
    try:
        print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), file=sys.stderr, flush=True)
    except Exception:
        pass


def pool_sizing_worksheet(
    *,
    workers: int,
    threads_per_worker: int,
    mysql_pool_size: int,
    mysql_max_connections: int,
    ingestion_admin_headroom: int = 10,
    safety_reserve: int = 10,
) -> Dict[str, Any]:
    """Documented connection-budget model (not a claim about DreamHost limits)."""
    per_process = max(1, int(mysql_pool_size))
    web_capacity = max(1, int(workers)) * per_process
    total_potential = web_capacity + max(0, int(ingestion_admin_headroom))
    available = max(0, int(mysql_max_connections) - max(0, int(safety_reserve)))
    return {
        "workers": workers,
        "threads_per_worker": threads_per_worker,
        "mysql_pool_size_per_process": per_process,
        "web_pool_capacity_total": web_capacity,
        "ingestion_admin_headroom": ingestion_admin_headroom,
        "safety_reserve": safety_reserve,
        "total_potential_app_connections": total_potential,
        "mysql_max_connections": mysql_max_connections,
        "available_after_reserve": available,
        "fits": total_potential <= available,
        "note": (
            "total potential connections = web workers × per-worker pool "
            "+ ingestion/admin + headroom; pool size is per-process, not global"
        ),
    }
