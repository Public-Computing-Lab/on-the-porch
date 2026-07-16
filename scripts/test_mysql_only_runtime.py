#!/usr/bin/env python3
"""Focused checks for the MySQL-only shared-state runtime contract."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))
sys.path.insert(0, str(ROOT))


def test_requirements_omit_redis() -> None:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "redis" not in text, "requirements.txt must not list redis"


def test_start_redis_script_rejects() -> None:
    script = ROOT / "start_redis.sh"
    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "not supported" in (proc.stderr + proc.stdout).lower()


def test_rate_limiter_has_no_redis_path() -> None:
    from rate_limit import RateLimiter

    text = (API / "rate_limit.py").read_text(encoding="utf-8").lower()
    assert "self._redis" not in text
    assert "import redis" not in text
    limiter = RateLimiter(mysql_store=None)
    allowed, retry = limiter.check("t", "id", 5, 60)
    assert allowed is True and retry == 0


def test_semantic_backend_rejects_redis() -> None:
    from semantic_cache import _RedisSemanticBackend, build_semantic_cache_backend

    try:
        build_semantic_cache_backend(
            backend_name="redis",
            redis_key="x",
            mysql_store=None,
        )
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "not supported" in str(exc).lower()
    try:
        _RedisSemanticBackend()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "not supported" in str(exc).lower()


def test_ingest_redis_client_rejected_and_mysql_status_works() -> None:
    from ingest_queue import _redis_client, get_ingest_job_status, record_job_start

    try:
        _redis_client()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "not supported" in str(exc).lower()

    # Ignore legacy redis_client argument.
    started = record_job_start("guest_cleanup", redis_client=object(), mysql_conn=None)
    assert "run_id" in started

    status = get_ingest_job_status()
    assert status["enabled"] is True
    assert "jobs" in status


def test_cache_contract_rejects_redis_via_subprocess() -> None:
    env = os.environ.copy()
    env["CACHE_BACKEND"] = "redis"
    env["SEMANTIC_CACHE_BACKEND"] = "mysql"
    # Avoid loading a local .env that could override; config loads PROJECT_ROOT/.env
    # which in this worktree may be absent. Subprocess still sets env vars first if
    # dotenv does not override existing keys — python-dotenv default does not override.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config import validate_cache_backend_contract; "
            "errs=validate_cache_backend_contract(); "
            "raise SystemExit(0 if errs and any('redis' in e.lower() for e in errs) else 1)",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ops_health_requires_mysql_in_production() -> None:
    from ops_health import validate_production_config

    ok, issues = validate_production_config(
        production_mode=True,
        cache_backend="redis",
        semantic_cache_enabled=True,
        gemini_model="gemini-2.5-flash-lite",
        gemini_fast_model="gemini-2.5-flash-lite",
        embed_provider="gemini",
    )
    assert ok is False
    assert any("mysql" in i.lower() and "redis" in i.lower() for i in issues)

    ok2, issues2 = validate_production_config(
        production_mode=True,
        cache_backend="mysql",
        semantic_cache_enabled=True,
        gemini_model="gemini-2.5-flash-lite",
        gemini_fast_model="gemini-2.5-flash-lite",
        embed_provider="gemini",
    )
    assert not any("CACHE_BACKEND must be" in i for i in issues2)


def main() -> int:
    tests = [
        test_requirements_omit_redis,
        test_start_redis_script_rejects,
        test_rate_limiter_has_no_redis_path,
        test_semantic_backend_rejects_redis,
        test_ingest_redis_client_rejected_and_mysql_status_works,
        test_cache_contract_rejects_redis_via_subprocess,
        test_ops_health_requires_mysql_in_production,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"MySQL-only runtime tests: {len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
