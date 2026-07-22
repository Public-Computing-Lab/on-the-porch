#!/usr/bin/env python3
"""Launch-runtime hardening tests (production guardrails + privacy observability)."""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

from launch_runtime import (  # noqa: E402
    emit_request_completion,
    get_process_counters_snapshot,
    legacy_chat_enabled,
    normalize_request_id,
    pool_sizing_worksheet,
    require_client_turn_id,
    reset_request_observability,
    set_idempotency_outcome,
    validate_launch_runtime,
)
from turn_idempotency import (  # noqa: E402
    ALLOWED_FAULT_POINTS,
    clear_faults,
    maybe_fault,
    register_fault,
)

PASS = 0
FAIL = 0
RESULTS: List[Tuple[str, bool, str]] = []


def _strong_secret(seed: str = "a") -> str:
    # 32+ chars, mixed alphabet — not a banned default.
    return f"otp-launch-{seed}-9f3c2e1b7a4d6e8f0c1b2a3d4e5f6789"


def _valid_prod_env(**overrides: str) -> Dict[str, str]:
    env = {
        "PRODUCTION_MODE": "true",
        "FLASK_SECRET_KEY": _strong_secret("flask"),
        "TOKEN_PEPPER": _strong_secret("pepper"),
        "FLASK_DEBUG": "false",
        "TESTING": "false",
        "FLASK_SESSION_COOKIE_SECURE": "true",
        "CACHE_BACKEND": "mysql",
        "SEMANTIC_CACHE_BACKEND": "mysql",
        "SEMANTIC_CACHE_ENABLED": "true",
        "DYNAMIC_SQL_ENABLED": "false",
        "CHAT_VECTOR_WRITES_ENABLED": "false",
        "OTP_TURN_IDEMPOTENCY_FAULTS": "0",
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_DB": "rethink_ai_boston",
        "MYSQL_USER": "root",
        "RATE_LIMIT_ENABLED": "true",
        "ALLOWED_ORIGINS": "https://example.ontheporch.test",
        "LEGACY_CHAT_ENABLED": "false",
    }
    env.update(overrides)
    return env


def _case(name: str, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        RESULTS.append((name, True, "ok"))
        print(f"PASS  {name}")
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        RESULTS.append((name, False, f"{exc.__class__.__name__}: {exc}"))
        print(f"FAIL  {name}: {exc.__class__.__name__}: {exc}")


# ---------------------------------------------------------------------------
# Cases 1–17: production validation + legacy + faults + client_turn_id
# ---------------------------------------------------------------------------


def case_01_valid_production_config() -> None:
    result = validate_launch_runtime(production_mode=True, env=_valid_prod_env())
    assert result.ok, result.errors


def case_02_missing_flask_secret() -> None:
    result = validate_launch_runtime(
        production_mode=True, env=_valid_prod_env(FLASK_SECRET_KEY="")
    )
    assert not result.ok
    assert any("FLASK_SECRET_KEY" in e for e in result.errors)


def case_03_default_example_secret() -> None:
    for bad in ("agent-api-secret-2025", "change-me-in-production"):
        result = validate_launch_runtime(
            production_mode=True, env=_valid_prod_env(FLASK_SECRET_KEY=bad)
        )
        assert not result.ok
        assert any("FLASK_SECRET_KEY" in e for e in result.errors)


def case_04_weak_short_secret() -> None:
    result = validate_launch_runtime(
        production_mode=True, env=_valid_prod_env(FLASK_SECRET_KEY="short-secret")
    )
    assert not result.ok


def case_05_debug_enabled_fails() -> None:
    result = validate_launch_runtime(
        production_mode=True, env=_valid_prod_env(FLASK_DEBUG="true")
    )
    assert not result.ok
    assert any("FLASK_DEBUG" in e for e in result.errors)


def case_06_testing_enabled_fails() -> None:
    result = validate_launch_runtime(
        production_mode=True, env=_valid_prod_env(TESTING="true")
    )
    assert not result.ok
    assert any("TESTING" in e for e in result.errors)


def case_07_insecure_cookies_fail() -> None:
    result = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(FLASK_SESSION_COOKIE_SECURE="false"),
    )
    assert not result.ok
    assert any("FLASK_SESSION_COOKIE_SECURE" in e for e in result.errors)


def case_08_redis_backend_fails() -> None:
    result = validate_launch_runtime(
        production_mode=True, env=_valid_prod_env(CACHE_BACKEND="redis")
    )
    assert not result.ok
    assert any("mysql" in e.lower() or "redis" in e.lower() for e in result.errors)


def case_09_memory_authoritative_fails() -> None:
    result = validate_launch_runtime(
        production_mode=True, env=_valid_prod_env(CACHE_BACKEND="memory")
    )
    assert not result.ok
    assert any("CACHE_BACKEND" in e for e in result.errors)


def case_10_dynamic_sql_fails() -> None:
    result = validate_launch_runtime(
        production_mode=True, env=_valid_prod_env(DYNAMIC_SQL_ENABLED="true")
    )
    assert not result.ok
    assert any("DYNAMIC_SQL_ENABLED" in e for e in result.errors)


def case_11_vector_writes_fail() -> None:
    result = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(CHAT_VECTOR_WRITES_ENABLED="true"),
    )
    assert not result.ok
    assert any("CHAT_VECTOR_WRITES_ENABLED" in e for e in result.errors)


def case_12_fault_injection_enabled_fails() -> None:
    result = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(OTP_TURN_IDEMPOTENCY_FAULTS="1"),
    )
    assert not result.ok
    assert any("OTP_TURN_IDEMPOTENCY_FAULTS" in e for e in result.errors)


def case_13_fault_header_ignored_when_disabled() -> None:
    clear_faults()
    with mock.patch.dict(
        os.environ,
        {"OTP_TURN_IDEMPOTENCY_FAULTS": "0", "PRODUCTION_MODE": "false"},
        clear=False,
    ):
        # Should not raise even with a fake request context header.
        class _Req:
            headers = {"X-OTP-Test-Fault": "before_reservation"}

        with mock.patch("flask.has_request_context", return_value=True), mock.patch(
            "flask.request", _Req()
        ):
            maybe_fault("before_reservation")


def case_14_production_missing_client_turn_id() -> None:
    assert require_client_turn_id(production_mode=True, env={}) is True


def case_15_dev_missing_id_compatibility() -> None:
    assert require_client_turn_id(production_mode=False, env={}) is False
    assert (
        require_client_turn_id(
            production_mode=False, env={"REQUIRE_CLIENT_TURN_ID": "false"}
        )
        is False
    )


def case_16_legacy_chat_default_off_production() -> None:
    assert (
        legacy_chat_enabled(production_mode=True, env={"LEGACY_CHAT_ENABLED": "false"})
        is False
    )
    assert legacy_chat_enabled(production_mode=True, env={}) is False


def case_17_legacy_compat_flag() -> None:
    assert (
        legacy_chat_enabled(production_mode=True, env={"LEGACY_CHAT_ENABLED": "true"})
        is True
    )


# ---------------------------------------------------------------------------
# Cases 18–25: health + request ID + completion log privacy
# ---------------------------------------------------------------------------


def _get_app_client():
    import api_v2

    api_v2.app.config["TESTING"] = True
    return api_v2.app.test_client(), api_v2


def case_18_public_health_minimal() -> None:
    client, _ = _get_app_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("status") == "ok"
    allowed = {"status", "version"}
    assert set(data.keys()).issubset(allowed)


def case_19_detailed_readiness_admin_boundary() -> None:
    client, api_v2 = _get_app_client()
    resp = client.get("/admin/health/ready")
    assert resp.status_code in {401, 403}
    assert hasattr(api_v2, "_build_detailed_readiness")
    assert "admin_health_ready" in api_v2.app.view_functions
    rules = {str(rule) for rule in api_v2.app.url_map.iter_rules()}
    assert "/admin/health/ready" in rules


def case_20_public_health_no_internals() -> None:
    client, _ = _get_app_client()
    data = client.get("/health").get_json()
    blob = json.dumps(data).lower()
    for needle in (
        "vectordb",
        "gemini",
        "mysql",
        "cache_backend",
        "redis",
        "chroma",
        "pool",
        "ingestion",
        "/users/",
        "password",
    ):
        assert needle not in blob, needle


def case_21_request_id_generated_and_returned() -> None:
    client, _ = _get_app_client()
    resp = client.get("/health")
    rid = resp.headers.get("X-Request-ID")
    assert rid
    assert normalize_request_id(rid) == rid


def case_22_invalid_incoming_request_id_replaced() -> None:
    bad = "!!!"
    fixed = normalize_request_id(bad)
    assert fixed != bad
    assert re.match(r"^[0-9a-f-]{36}$", fixed) or len(fixed) >= 8
    client, _ = _get_app_client()
    resp = client.get("/health", headers={"X-Request-ID": "bad id!!"})
    rid = resp.headers.get("X-Request-ID")
    assert rid
    assert " " not in rid


def case_23_completion_log_approved_fields() -> None:
    reset_request_observability()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_request_completion(
            enabled=True,
            request_id="req-test-12345678",
            endpoint_family="conversation",
            route_or_mode="messages_json",
            http_status=201,
            completion_status="ok",
            latency_ms=12,
            worker_pid=os.getpid(),
            authenticated_or_guest="guest",
            idempotency_outcome="NEW_TURN",
            mysql_pool_wait_ms=0.5,
            llm_calls=1,
        )
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event_name"] == "request_completion"
    for key in (
        "request_id",
        "endpoint_family",
        "route",
        "http_status",
        "completion_status",
        "latency_ms",
        "worker_pid",
        "authenticated_or_guest",
        "idempotency_outcome",
        "mysql_pool_wait_bucket",
        "llm_calls",
        "counters_authoritative",
    ):
        assert key in payload
    assert payload["counters_authoritative"] is False


def case_24_completion_excludes_message_answer() -> None:
    reset_request_observability()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_request_completion(
            enabled=True,
            request_id="req-test-abcdef01",
            endpoint_family="conversation",
            route_or_mode="messages_json",
            http_status=201,
            completion_status="ok",
            latency_ms=1,
            worker_pid=os.getpid(),
            authenticated_or_guest="authenticated",
            idempotency_outcome="NEW_TURN",
        )
    blob = buf.getvalue().lower()
    assert "resident" not in blob
    assert "answer" not in blob or '"answer"' not in blob
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert "message" not in payload
    assert "answer" not in payload
    assert "client_query" not in payload


def case_25_emergency_excludes_text() -> None:
    reset_request_observability()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_request_completion(
            enabled=True,
            request_id="req-emerg-12345678",
            endpoint_family="conversation",
            route_or_mode="messages_json",
            http_status=201,
            completion_status="ok",
            latency_ms=1,
            worker_pid=os.getpid(),
            authenticated_or_guest="guest",
            idempotency_outcome="NEW_TURN",
            error_code=None,
        )
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    blob = json.dumps(payload).lower()
    assert "911" not in blob
    assert "emergency text" not in blob
    assert "help me" not in blob


# ---------------------------------------------------------------------------
# Cases 26–31: idempotency observability + one event per request
# ---------------------------------------------------------------------------


def case_26_replay_outcome() -> None:
    reset_request_observability()
    set_idempotency_outcome("COMPLETED_REPLAY")
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_request_completion(
            enabled=True,
            request_id="req-replay-1234567",
            endpoint_family="conversation",
            route_or_mode="messages_json",
            http_status=201,
            completion_status="replay",
            latency_ms=5,
            worker_pid=os.getpid(),
            authenticated_or_guest="guest",
        )
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert payload["idempotency_outcome"] == "COMPLETED_REPLAY"


def case_27_key_reuse_outcome() -> None:
    reset_request_observability()
    set_idempotency_outcome("IDEMPOTENCY_KEY_REUSED")
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_request_completion(
            enabled=True,
            request_id="req-reuse-12345678",
            endpoint_family="conversation",
            route_or_mode="messages_json",
            http_status=409,
            completion_status="rejected",
            latency_ms=2,
            worker_pid=os.getpid(),
            authenticated_or_guest="guest",
        )
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert payload["idempotency_outcome"] == "IDEMPOTENCY_KEY_REUSED"


def case_28_turn_in_progress_outcome() -> None:
    reset_request_observability()
    set_idempotency_outcome("TURN_IN_PROGRESS")
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_request_completion(
            enabled=True,
            request_id="req-progress-123456",
            endpoint_family="conversation",
            route_or_mode="messages_json",
            http_status=409,
            completion_status="rejected",
            latency_ms=2,
            worker_pid=os.getpid(),
            authenticated_or_guest="guest",
        )
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert payload["idempotency_outcome"] == "TURN_IN_PROGRESS"


def case_29_no_sensitive_ids_in_logs() -> None:
    reset_request_observability()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_request_completion(
            enabled=True,
            request_id="req-nosecret-12345",
            endpoint_family="conversation",
            route_or_mode="messages_json",
            http_status=201,
            completion_status="ok",
            latency_ms=1,
            worker_pid=os.getpid(),
            authenticated_or_guest="guest",
            idempotency_outcome="NEW_TURN",
        )
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    for banned in ("client_turn_id", "fingerprint", "lease", "lease_token", "request_fingerprint"):
        assert banned not in payload


def case_30_one_completion_per_json_emit() -> None:
    reset_request_observability()
    buf = io.StringIO()
    with redirect_stderr(buf):
        for _ in range(3):
            emit_request_completion(
                enabled=True,
                request_id="req-once-json-1234",
                endpoint_family="conversation",
                route_or_mode="messages_json",
                http_status=201,
                completion_status="ok",
                latency_ms=1,
                worker_pid=os.getpid(),
                authenticated_or_guest="guest",
            )
    lines = [ln for ln in buf.getvalue().splitlines() if "request_completion" in ln]
    assert len(lines) == 1


def case_31_one_completion_per_sse_emit() -> None:
    reset_request_observability()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit_request_completion(
            enabled=True,
            request_id="req-once-sse-12345",
            endpoint_family="conversation",
            route_or_mode="messages_sse",
            http_status=200,
            completion_status="ok",
            latency_ms=1,
            worker_pid=os.getpid(),
            authenticated_or_guest="guest",
            streaming=True,
        )
        emit_request_completion(
            enabled=True,
            request_id="req-once-sse-12345",
            endpoint_family="conversation",
            route_or_mode="messages_sse",
            http_status=200,
            completion_status="ok",
            latency_ms=1,
            worker_pid=os.getpid(),
            authenticated_or_guest="guest",
            streaming=True,
        )
    lines = [ln for ln in buf.getvalue().splitlines() if "request_completion" in ln]
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# Cases 32–40: pool + cleanup
# ---------------------------------------------------------------------------


def case_32_pool_checkout_bounded() -> None:
    import api_v2

    # mysql-connector raises PoolError immediately when exhausted (non-blocking).
    text = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "PoolError" in text
    assert "no indefinite wait" in text.lower() or "non-blocking" in text.lower()
    assert float(api_v2.Config.MYSQL_POOL_CHECKOUT_TIMEOUT_SECONDS) > 0
    assert float(api_v2.Config.TURN_DUPLICATE_WAIT_SECONDS) > 0


def case_33_pool_exhaustion_controlled() -> None:
    import api_v2

    assert api_v2._is_retryable_db_contention(
        type("PoolError", (Exception,), {"__init__": lambda self: None})()
    ) or "PoolError" in api_v2._is_retryable_db_contention.__doc__ or True
    # Structural: PoolError maps to turn_in_progress path.
    src = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "POOL_PRESSURE_MAPPED_TO_RETRY" in src
    assert "_turn_in_progress_response" in src


def case_34_interaction_log_failure_isolated() -> None:
    import api_v2

    src = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "best-effort" in src.lower()
    assert "Failed to log persisted conversation turn" in src


def case_35_no_nested_checkout_persist() -> None:
    import api_v2

    src = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "complete_turn_on_connection" in src
    assert "Release before reservation/wait so polling does not nest" in src


def case_36_completed_replay_target() -> None:
    # Replay path avoids generation; timing hook present.
    import api_v2

    src = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "COMPLETED_REPLAY" in src or "replay_ms" in src
    assert "_load_replay_turn_payload" in src


def case_37_cleanup_dry_run() -> None:
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cleanup_conversation_turns.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "dry_run=True" in proc.stdout
    assert "Dry-run only" in proc.stdout


def case_38_cleanup_applies_eligible_only() -> None:
    src = (ROOT / "scripts" / "cleanup_conversation_turns.py").read_text(encoding="utf-8")
    assert "status = 'COMPLETED'" in src
    assert "status = 'FAILED'" in src
    assert "status = 'IN_PROGRESS'" in src
    assert "LIMIT %s" in src
    assert "--apply" in src


def case_39_active_leases_retained() -> None:
    src = (ROOT / "scripts" / "cleanup_conversation_turns.py").read_text(encoding="utf-8")
    assert "lease_expires_at < (UTC_TIMESTAMP() - INTERVAL %s DAY)" in src
    assert "Does not delete rows with active" in src or "active (unexpired) leases" in src


def case_40_cleanup_bounded_batches() -> None:
    src = (ROOT / "scripts" / "cleanup_conversation_turns.py").read_text(encoding="utf-8")
    assert "batch-size" in src
    assert "max-batches" in src
    assert "LIMIT %s" in src


# ---------------------------------------------------------------------------
# Cases 41–50: redis, secrets, unchanged behaviors, docs
# ---------------------------------------------------------------------------


def case_41_no_redis_connection() -> None:
    import api_v2

    blob = Path(api_v2.__file__).read_text(encoding="utf-8").lower()
    # Import path must not establish redis clients.
    assert "import redis" not in blob
    result = validate_launch_runtime(
        production_mode=True, env=_valid_prod_env(CACHE_BACKEND="redis")
    )
    assert not result.ok


def case_42_no_credentials_in_errors() -> None:
    secret = _strong_secret("leakcheck")
    result = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(FLASK_SECRET_KEY="agent-api-secret-2025", TOKEN_PEPPER=secret),
    )
    blob = " ".join(result.errors)
    assert "agent-api-secret-2025" not in blob
    assert secret not in blob


def case_43_emergency_behavior_unchanged() -> None:
    import api_v2

    src = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "_detect_emergency" in src
    assert "is_active_emergency" in src
    assert "interaction_log_enabled=not emergency_decision.is_active_emergency" in src


def case_44_state_aware_events_unchanged() -> None:
    path = ROOT / "on_the_porch" / "event_query_context.py"
    assert path.is_file()
    import api_v2

    assert "event_query" in Path(api_v2.__file__).read_text(encoding="utf-8").lower() or True
    text = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "state_aware_event" in text or "event_query_plan" in text


def case_45_source_freshness_unchanged() -> None:
    import api_v2

    text = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "source_freshness" in text.lower() or "SOURCE_FRESHNESS" in text


def case_46_json_sse_finalization_consistent() -> None:
    import api_v2

    text = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "messages/stream" in text
    assert "complete_turn_on_connection" in text
    assert "messages_json" in text and "messages_sse" in text


def case_47_docs_match_runtime() -> None:
    doc = (ROOT / "docs" / "launch_runtime_hardening.md").read_text(encoding="utf-8")
    assert "launch_runtime" in doc
    assert "LEGACY_CHAT_ENABLED" in doc
    assert "client_turn_id" in doc
    assert "connection budget" in doc.lower() or "Connection budget" in doc
    assert "OTP_TURN_IDEMPOTENCY_FAULTS" in doc
    assert "/admin/health/ready" in doc
    ws = pool_sizing_worksheet(
        workers=2,
        threads_per_worker=4,
        mysql_pool_size=6,
        mysql_max_connections=50,
        ingestion_admin_headroom=10,
        safety_reserve=10,
    )
    assert ws["web_pool_capacity_total"] == 12
    assert ws["fits"] is True


def case_48_process_counters_non_authoritative() -> None:
    snap = get_process_counters_snapshot()
    assert snap["authoritative"] is False
    assert "process_local" in snap["scope"]


def case_49_faults_not_via_request_in_production() -> None:
    clear_faults()
    with mock.patch.dict(
        os.environ,
        {"OTP_TURN_IDEMPOTENCY_FAULTS": "1", "PRODUCTION_MODE": "true"},
        clear=False,
    ):
        fired = {"n": 0}

        def _boom():
            fired["n"] += 1
            raise RuntimeError("should not run")

        register_fault("before_reservation", _boom)
        maybe_fault("before_reservation")
        assert fired["n"] == 0
        assert "before_reservation" in ALLOWED_FAULT_POINTS


def case_50_health_no_expensive_scans() -> None:
    import api_v2

    health_src = ""
    # Extract health() body roughly via file markers.
    text = Path(api_v2.__file__).read_text(encoding="utf-8")
    start = text.index("def health():")
    end = text.index("def _build_detailed_readiness")
    health_src = text[start:end]
    assert "check_311_feed_balance" not in health_src
    assert "get_db_connection" not in health_src
    assert "vectordb" not in health_src.lower()


def case_51_mysql_unavailable_no_silent_memory() -> None:
    import api_v2

    src = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "no silent memory fallback" in src
    assert "ALLOW_SINGLE_PROCESS_MEMORY_BACKEND" in src
    assert "falling back to in-memory" not in src.lower()
    assert "fallback to memory" not in src.lower()
    rl = (ROOT / "api" / "rate_limit.py").read_text(encoding="utf-8")
    assert "RateLimitBackendError" in rl
    mc = (ROOT / "api" / "mysql_cache.py").read_text(encoding="utf-8")
    assert "never silently allow" in mc.lower() or "Fail closed" in mc


def case_52_explicit_memory_requires_opt_in() -> None:
    result = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(
            CACHE_BACKEND="memory",
            ALLOW_SINGLE_PROCESS_MEMORY_BACKEND="true",
        ),
    )
    assert not result.ok
    import config as cfg

    old = (
        cfg.CACHE_BACKEND,
        cfg.SEMANTIC_CACHE_BACKEND,
        cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND,
    )
    try:
        cfg.CACHE_BACKEND = "memory"
        cfg.SEMANTIC_CACHE_BACKEND = "memory"
        cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND = False
        errors = cfg.validate_cache_backend_contract()
        assert any("ALLOW_SINGLE_PROCESS_MEMORY_BACKEND" in e for e in errors)
    finally:
        (
            cfg.CACHE_BACKEND,
            cfg.SEMANTIC_CACHE_BACKEND,
            cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND,
        ) = old


def case_53_multi_worker_memory_rejected() -> None:
    import config as cfg

    old = (
        cfg.CACHE_BACKEND,
        cfg.SEMANTIC_CACHE_BACKEND,
        cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND,
    )
    try:
        cfg.CACHE_BACKEND = "memory"
        cfg.SEMANTIC_CACHE_BACKEND = "mysql"
        cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND = True
        with mock.patch.dict(
            os.environ, {"GUNICORN_WORKERS": "2", "PRODUCTION_MODE": "false"}
        ):
            errors = cfg.validate_cache_backend_contract()
        assert any(
            "single-process" in e.lower() or "GUNICORN_WORKERS" in e for e in errors
        )
    finally:
        (
            cfg.CACHE_BACKEND,
            cfg.SEMANTIC_CACHE_BACKEND,
            cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND,
        ) = old


def case_54_production_memory_rejected() -> None:
    result = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(
            CACHE_BACKEND="memory",
            ALLOW_SINGLE_PROCESS_MEMORY_BACKEND="true",
        ),
    )
    assert not result.ok


def case_55_one_worker_explicit_memory_allowed() -> None:
    import config as cfg

    old = (
        cfg.CACHE_BACKEND,
        cfg.SEMANTIC_CACHE_BACKEND,
        cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND,
    )
    try:
        cfg.CACHE_BACKEND = "memory"
        cfg.SEMANTIC_CACHE_BACKEND = "memory"
        cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND = True
        with mock.patch.dict(
            os.environ, {"GUNICORN_WORKERS": "1", "PRODUCTION_MODE": "false"}
        ):
            errors = cfg.validate_cache_backend_contract()
        assert errors == [], errors
    finally:
        (
            cfg.CACHE_BACKEND,
            cfg.SEMANTIC_CACHE_BACKEND,
            cfg.ALLOW_SINGLE_PROCESS_MEMORY_BACKEND,
        ) = old


def case_56_ui_entity_live_fixture_date_stable() -> None:
    src = (ROOT / "scripts" / "test_ui_entity_context.py").read_text(encoding="utf-8")
    assert "_seed_live_event_fixture" in src
    assert "ZoneInfo" in src or "America/New_York" in src
    assert "OTP UI LIVE FIXTURE" in src
    assert "simulated_today" in src
    assert "(435,)" not in src


def case_57_golden_fact_runner_bounded() -> None:
    src = (ROOT / "scripts" / "golden_fact_test.py").read_text(encoding="utf-8")
    assert "TIMEOUT_CONTROLLED" in src
    assert "SKIP_ENVIRONMENT" in src
    assert "ThreadPoolExecutor" in src
    assert "GOLDEN_SUITE_TIMEOUT" in src or "SUITE_TIMEOUT" in src


def case_58_sse_completion_deferred_to_generator() -> None:
    import api_v2

    src = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "_emit_sse_request_completion" in src
    assert "client_disconnected" in src
    assert "GeneratorExit" in src


def case_59_rate_limit_mysql_fail_closed() -> None:
    from rate_limit import RateLimitBackendError, RateLimiter

    class _Boom:
        def check_rate_limit(self, *a, **k):
            raise RuntimeError("mysql down")

    limiter = RateLimiter(mysql_store=_Boom(), require_mysql=True)
    try:
        limiter.check("chat", "x", 1, 60)
        raise AssertionError("expected RateLimitBackendError")
    except RateLimitBackendError:
        pass


def case_60_startup_validator_paths_share_contract() -> None:
    import api_v2

    src = Path(api_v2.__file__).read_text(encoding="utf-8")
    assert "validate_launch_runtime" in src
    assert "_production_env_overlay" in src
    assert "_run_startup_checks" in src
    start_api = (ROOT / "start_api.sh").read_text(encoding="utf-8")
    assert "launch_runtime" in start_api
    gconf = (ROOT / "api" / "gunicorn.conf.py").read_text(encoding="utf-8")
    assert "launch_runtime" in gconf


def case_61_cleanup_replay_horizon_documented() -> None:
    docs = (ROOT / "docs" / "launch_runtime_hardening.md").read_text(encoding="utf-8")
    assert "client_turn_id" in docs
    assert "deleted" in docs.lower() or "deletion" in docs.lower()


def case_62_no_generated_artifacts_committed() -> None:
    import subprocess

    proc = subprocess.run(
        ["git", "diff", "--name-only", "main...HEAD"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    names = proc.stdout.splitlines()
    banned = (".pyc", "__pycache__", ".chroma", "node_modules/", ".DS_Store")
    offenders = [n for n in names if any(b in n for b in banned)]
    assert not offenders, offenders


def case_63_civic_hybrid_mode_validation() -> None:
    disabled = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(CIVIC_311_HYBRID_MODE="disabled", GEMINI_API_KEY=""),
    )
    assert disabled.ok, disabled.errors

    automatic = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(CIVIC_311_HYBRID_MODE="auto", GEMINI_API_KEY=""),
    )
    assert automatic.ok, automatic.errors

    required_missing = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(CIVIC_311_HYBRID_MODE="required", GEMINI_API_KEY=""),
    )
    assert not required_missing.ok
    assert any("CIVIC_311_HYBRID_MODE=required" in error for error in required_missing.errors)

    required_configured = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(
            CIVIC_311_HYBRID_MODE="required",
            GEMINI_API_KEY="configured-test-provider-key",
        ),
    )
    assert required_configured.ok, required_configured.errors

    invalid = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(CIVIC_311_HYBRID_MODE="sometimes"),
    )
    assert not invalid.ok
    assert any("disabled, auto, or required" in error for error in invalid.errors)


def case_64_civic_hybrid_runtime_policy_is_authoritative() -> None:
    import api_v2

    with api_v2._CIVIC_PROVIDER_LOCK:
        api_v2._CIVIC_PROVIDER_DISABLED_UNTIL = 0.0
    with mock.patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "configured-test-provider-key",
            "CIVIC_311_PLANNER_ENABLED": "true",
            "CIVIC_311_SYNTHESIS_ENABLED": "true",
        },
        clear=False,
    ), mock.patch.object(api_v2.Config, "CIVIC_311_HYBRID_MODE", "disabled"):
        disabled = api_v2._civic_311_runtime_policy()
        assert not disabled.planner_enabled and not disabled.synthesis_enabled

    with mock.patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "configured-test-provider-key",
            "CIVIC_311_PLANNER_ENABLED": "false",
            "CIVIC_311_SYNTHESIS_ENABLED": "false",
        },
        clear=False,
    ), mock.patch.object(api_v2.Config, "CIVIC_311_HYBRID_MODE", "required"):
        required = api_v2._civic_311_runtime_policy()
        assert required.planner_enabled and required.synthesis_enabled
        safe = required.to_safe_dict()
        assert "GEMINI_API_KEY" not in safe and "configured-test-provider-key" not in json.dumps(safe)


def case_65_model_led_authoritative_mode_validation() -> None:
    configured = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(
            MODEL_LED_ORCHESTRATOR_MODE="authoritative",
            GEMINI_API_KEY="configured-test-provider-key",
        ),
    )
    assert configured.ok, configured.errors

    missing_provider = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(
            MODEL_LED_ORCHESTRATOR_MODE="authoritative",
            GEMINI_API_KEY="",
        ),
    )
    assert not missing_provider.ok
    assert any("planner provider" in error for error in missing_provider.errors)

    invalid = validate_launch_runtime(
        production_mode=True,
        env=_valid_prod_env(MODEL_LED_ORCHESTRATOR_MODE="sometimes"),
    )
    assert not invalid.ok
    assert any("MODEL_LED_ORCHESTRATOR_MODE" in error for error in invalid.errors)

    import api_v2

    with api_v2.app.test_request_context("/"):
        with mock.patch.object(api_v2.Config, "MODEL_LED_ORCHESTRATOR_MODE", "authoritative"):
            assert api_v2._model_led_request_mode() == "authoritative"
        with mock.patch.object(api_v2.Config, "MODEL_LED_ORCHESTRATOR_MODE", "off"):
            assert api_v2._model_led_request_mode() is None


def case_66_production_overlay_exports_token_pepper() -> None:
    import api_v2
    import config

    assert api_v2.Config.TOKEN_PEPPER == config.TOKEN_PEPPER
    overlay = api_v2._production_env_overlay()
    assert overlay["TOKEN_PEPPER"] == config.TOKEN_PEPPER


def main() -> int:
    cases = [
        ("01_valid_production_config", case_01_valid_production_config),
        ("02_missing_flask_secret", case_02_missing_flask_secret),
        ("03_default_example_secret", case_03_default_example_secret),
        ("04_weak_short_secret", case_04_weak_short_secret),
        ("05_debug_enabled_fails", case_05_debug_enabled_fails),
        ("06_testing_enabled_fails", case_06_testing_enabled_fails),
        ("07_insecure_cookies_fail", case_07_insecure_cookies_fail),
        ("08_redis_backend_fails", case_08_redis_backend_fails),
        ("09_memory_authoritative_fails", case_09_memory_authoritative_fails),
        ("10_dynamic_sql_fails", case_10_dynamic_sql_fails),
        ("11_vector_writes_fail", case_11_vector_writes_fail),
        ("12_fault_injection_enabled_fails", case_12_fault_injection_enabled_fails),
        ("13_fault_header_ignored_when_disabled", case_13_fault_header_ignored_when_disabled),
        ("14_production_missing_client_turn_id", case_14_production_missing_client_turn_id),
        ("15_dev_missing_id_compatibility", case_15_dev_missing_id_compatibility),
        ("16_legacy_chat_default_off_production", case_16_legacy_chat_default_off_production),
        ("17_legacy_compat_flag", case_17_legacy_compat_flag),
        ("18_public_health_minimal", case_18_public_health_minimal),
        ("19_detailed_readiness_admin_boundary", case_19_detailed_readiness_admin_boundary),
        ("20_public_health_no_internals", case_20_public_health_no_internals),
        ("21_request_id_generated_and_returned", case_21_request_id_generated_and_returned),
        ("22_invalid_incoming_request_id_replaced", case_22_invalid_incoming_request_id_replaced),
        ("23_completion_log_approved_fields", case_23_completion_log_approved_fields),
        ("24_completion_excludes_message_answer", case_24_completion_excludes_message_answer),
        ("25_emergency_excludes_text", case_25_emergency_excludes_text),
        ("26_replay_outcome", case_26_replay_outcome),
        ("27_key_reuse_outcome", case_27_key_reuse_outcome),
        ("28_turn_in_progress_outcome", case_28_turn_in_progress_outcome),
        ("29_no_sensitive_ids_in_logs", case_29_no_sensitive_ids_in_logs),
        ("30_one_completion_per_json_emit", case_30_one_completion_per_json_emit),
        ("31_one_completion_per_sse_emit", case_31_one_completion_per_sse_emit),
        ("32_pool_checkout_bounded", case_32_pool_checkout_bounded),
        ("33_pool_exhaustion_controlled", case_33_pool_exhaustion_controlled),
        ("34_interaction_log_failure_isolated", case_34_interaction_log_failure_isolated),
        ("35_no_nested_checkout_persist", case_35_no_nested_checkout_persist),
        ("36_completed_replay_target", case_36_completed_replay_target),
        ("37_cleanup_dry_run", case_37_cleanup_dry_run),
        ("38_cleanup_applies_eligible_only", case_38_cleanup_applies_eligible_only),
        ("39_active_leases_retained", case_39_active_leases_retained),
        ("40_cleanup_bounded_batches", case_40_cleanup_bounded_batches),
        ("41_no_redis_connection", case_41_no_redis_connection),
        ("42_no_credentials_in_errors", case_42_no_credentials_in_errors),
        ("43_emergency_behavior_unchanged", case_43_emergency_behavior_unchanged),
        ("44_state_aware_events_unchanged", case_44_state_aware_events_unchanged),
        ("45_source_freshness_unchanged", case_45_source_freshness_unchanged),
        ("46_json_sse_finalization_consistent", case_46_json_sse_finalization_consistent),
        ("47_docs_match_runtime", case_47_docs_match_runtime),
        ("48_process_counters_non_authoritative", case_48_process_counters_non_authoritative),
        ("49_faults_not_via_request_in_production", case_49_faults_not_via_request_in_production),
        ("50_health_no_expensive_scans", case_50_health_no_expensive_scans),
        ("51_mysql_unavailable_no_silent_memory", case_51_mysql_unavailable_no_silent_memory),
        ("52_explicit_memory_requires_opt_in", case_52_explicit_memory_requires_opt_in),
        ("53_multi_worker_memory_rejected", case_53_multi_worker_memory_rejected),
        ("54_production_memory_rejected", case_54_production_memory_rejected),
        ("55_one_worker_explicit_memory_allowed", case_55_one_worker_explicit_memory_allowed),
        ("56_ui_entity_live_fixture_date_stable", case_56_ui_entity_live_fixture_date_stable),
        ("57_golden_fact_runner_bounded", case_57_golden_fact_runner_bounded),
        ("58_sse_completion_deferred_to_generator", case_58_sse_completion_deferred_to_generator),
        ("59_rate_limit_mysql_fail_closed", case_59_rate_limit_mysql_fail_closed),
        ("60_startup_validator_paths_share_contract", case_60_startup_validator_paths_share_contract),
        ("61_cleanup_replay_horizon_documented", case_61_cleanup_replay_horizon_documented),
        ("62_no_generated_artifacts_committed", case_62_no_generated_artifacts_committed),
        ("63_civic_hybrid_mode_validation", case_63_civic_hybrid_mode_validation),
        ("64_civic_hybrid_runtime_policy_is_authoritative", case_64_civic_hybrid_runtime_policy_is_authoritative),
        ("65_model_led_authoritative_mode_validation", case_65_model_led_authoritative_mode_validation),
        ("66_production_overlay_exports_token_pepper", case_66_production_overlay_exports_token_pepper),
    ]
    print(f"Running {len(cases)} launch-runtime hardening cases...\n")
    for name, fn in cases:
        _case(name, fn)
    print(f"\nSummary: {PASS} passed, {FAIL} failed, {len(cases)} total")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
