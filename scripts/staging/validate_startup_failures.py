#!/usr/bin/env python3
"""Synthetic production-mode startup failure cases (no remote host required).

Verifies launch_runtime fail-closed categories without printing secret values.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from launch_runtime import (  # noqa: E402
    legacy_chat_enabled,
    require_client_turn_id,
    validate_launch_runtime,
)


def _strong(seed: str) -> str:
    return f"otp-staging-{seed}-9f3c2e1b7a4d6e8f0c1b2a3d4e5f6789"


def _base(**overrides: str) -> Dict[str, str]:
    env = {
        "PRODUCTION_MODE": "true",
        "FLASK_SECRET_KEY": _strong("flask"),
        "TOKEN_PEPPER": _strong("pepper"),
        "FLASK_DEBUG": "false",
        "TESTING": "false",
        "FLASK_SESSION_COOKIE_SECURE": "true",
        "CACHE_BACKEND": "mysql",
        "SEMANTIC_CACHE_BACKEND": "mysql",
        "ALLOW_SINGLE_PROCESS_MEMORY_BACKEND": "false",
        "SEMANTIC_CACHE_ENABLED": "true",
        "DYNAMIC_SQL_ENABLED": "false",
        "CHAT_VECTOR_WRITES_ENABLED": "false",
        "OTP_TURN_IDEMPOTENCY_FAULTS": "0",
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_DB": "staging_placeholder",
        "MYSQL_USER": "staging_user",
        "RATE_LIMIT_ENABLED": "true",
        "ALLOWED_ORIGINS": "https://staging.example.test",
        "LEGACY_CHAT_ENABLED": "false",
        "REQUIRE_CLIENT_TURN_ID": "true",
    }
    env.update(overrides)
    return env


CASES = [
    ("missing_secret", {"FLASK_SECRET_KEY": ""}),
    ("weak_default_secret", {"FLASK_SECRET_KEY": "change-me-in-production"}),
    ("redis_backend", {"CACHE_BACKEND": "redis"}),
    ("memory_backend", {"CACHE_BACKEND": "memory", "ALLOW_SINGLE_PROCESS_MEMORY_BACKEND": "true"}),
    ("debug_enabled", {"FLASK_DEBUG": "true"}),
    ("testing_enabled", {"TESTING": "true"}),
    ("insecure_cookies", {"FLASK_SESSION_COOKIE_SECURE": "false"}),
    ("dynamic_sql", {"DYNAMIC_SQL_ENABLED": "true"}),
    ("vector_writes", {"CHAT_VECTOR_WRITES_ENABLED": "true"}),
    ("fault_injection", {"OTP_TURN_IDEMPOTENCY_FAULTS": "1"}),
    ("missing_rate_limit_policy", {"RATE_LIMIT_ENABLED": ""}),
    ("wildcard_cors", {"ALLOWED_ORIGINS": "*"}),
    ("missing_cors", {"ALLOWED_ORIGINS": ""}),
    ("legacy_chat_enabled", {"LEGACY_CHAT_ENABLED": "true"}),
    ("missing_mysql", {"MYSQL_HOST": "", "MYSQL_DB": "", "MYSQL_USER": ""}),
]


def _assert_no_secret_leak(blob: str, *secrets: str) -> None:
    for secret in secrets:
        if secret and len(secret) > 8 and secret in blob:
            raise AssertionError("secret value leaked in validator output")


def main() -> int:
    print("Staging startup-failure categories (synthetic env)")
    failed = 0
    ok = validate_launch_runtime(production_mode=True, env=_base())
    if not ok.ok:
        print("FAIL baseline valid config:", "; ".join(ok.errors))
        return 1
    print("PASS baseline_valid_config")

    for name, overrides in CASES:
        result = validate_launch_runtime(production_mode=True, env=_base(**overrides))
        blob = " ".join(result.errors + list(result.warnings or []))
        try:
            _assert_no_secret_leak(
                blob,
                overrides.get("FLASK_SECRET_KEY", ""),
                _strong("flask"),
                _strong("pepper"),
            )
        except AssertionError as exc:
            print(f"FAIL {name}: {exc}")
            failed += 1
            continue

        if name == "legacy_chat_enabled":
            warns = " ".join(result.warnings or [])
            if "LEGACY_CHAT" in warns.upper() or "legacy" in warns.lower():
                print(f"PASS {name}: warn-only ({len(result.warnings)} warning(s))")
            else:
                print(f"FAIL {name}: expected legacy warning")
                failed += 1
            continue

        if result.ok:
            print(f"FAIL {name}: expected failure")
            failed += 1
        else:
            print(f"PASS {name}: fail-closed ({len(result.errors)} category error(s))")

    if not require_client_turn_id(production_mode=True, env=_base()):
        print("FAIL require_client_turn_id production default/true")
        failed += 1
    else:
        print("PASS require_client_turn_id production")

    if require_client_turn_id(
        production_mode=True, env=_base(REQUIRE_CLIENT_TURN_ID="false")
    ):
        print("FAIL require_client_turn_id=false should disable requirement")
        failed += 1
    else:
        print("PASS require_client_turn_id can be explicitly false (policy surface)")

    if legacy_chat_enabled(production_mode=True, env=_base(LEGACY_CHAT_ENABLED="false")):
        print("FAIL legacy chat should be off")
        failed += 1
    else:
        print("PASS legacy_chat_default_off")

    total = len(CASES) + 4
    print(f"\nSummary: {total - failed}/{total} checks, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
