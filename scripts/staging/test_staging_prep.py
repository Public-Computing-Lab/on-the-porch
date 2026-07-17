#!/usr/bin/env python3
"""Local staging-preparation tests (no remote host required)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "staging"))
sys.path.insert(0, str(ROOT / "api"))

PASS = 0
FAIL = 0


def _case(name: str, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"PASS  {name}")
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"FAIL  {name}: {exc.__class__.__name__}: {exc}")


def case_docs_present() -> None:
    required = [
        "README.md",
        "environment_contract.md",
        "hosting_audit.md",
        "runbook.md",
        "connection_budget.md",
        "fixtures.md",
        "operator_prerequisites.md",
    ]
    for name in required:
        path = ROOT / "docs" / "staging" / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert "staging" in text.lower()
        # Must not claim remote validation succeeded (avoid matching negated phrases).
        lowered = text.lower()
        for claim in (
            "staging validation is complete",
            "staging has been successfully validated",
            "remote staging is validated",
        ):
            assert claim not in lowered, claim


def case_hosting_distinguishes_paths() -> None:
    text = (ROOT / "docs" / "staging" / "hosting_audit.md").read_text(encoding="utf-8")
    assert "SUPPORTED" in text
    assert "LEGACY" in text
    assert "start_api.sh" in text
    assert "Passenger" in text
    assert "must not treat any other path as preferred" in text.lower() or "must not" in text.lower()


def case_runbook_steps() -> None:
    text = (ROOT / "docs" / "staging" / "runbook.md").read_text(encoding="utf-8")
    for needle in (
        "Resource identity",
        "Staging database",
        "Backup before migration",
        "Migration",
        "Environment validation",
        "Gunicorn startup",
        "HTTPS health",
        "Admin readiness",
        "SSE validation",
        "Session and CSRF",
        "Smoke dialogues",
        "Structured log",
        "Connection-budget",
        "Cleanup dry run",
        "Backup restore",
        "Rollback rehearsal",
        "Promotion decision",
        "Process shutdown",
    ):
        assert needle.lower() in text.lower(), needle


def case_identity_guards() -> None:
    from identity import assert_staging_mutation_allowed

    os.environ.pop("OTP_STAGING_MUTATION_OK", None)
    os.environ.pop("OTP_STAGING_MYSQL_DB", None)
    try:
        assert_staging_mutation_allowed(db_name="otp_staging_prep_test")
        raise AssertionError("expected refusal without mutation ok")
    except RuntimeError:
        pass

    os.environ["OTP_STAGING_MUTATION_OK"] = "1"
    try:
        assert_staging_mutation_allowed(db_name="sentiment_demo")
        raise AssertionError("expected refuse sentiment_demo")
    except RuntimeError:
        pass
    try:
        assert_staging_mutation_allowed(db_name="rethink_ai_boston")
        raise AssertionError("expected refuse rethink_ai_boston")
    except RuntimeError:
        pass
    try:
        assert_staging_mutation_allowed(db_name="production_app")
        raise AssertionError("expected refuse production_app")
    except RuntimeError:
        pass

    assert assert_staging_mutation_allowed(db_name="otp_staging_prep_test") == "otp_staging_prep_test"
    assert assert_staging_mutation_allowed(db_name="staging_demo") == "staging_demo"

    os.environ["OTP_STAGING_MYSQL_DB"] = "otp_staging_contract"
    try:
        assert_staging_mutation_allowed(db_name="otp_staging_other")
        raise AssertionError("expected contract mismatch")
    except RuntimeError:
        pass
    assert assert_staging_mutation_allowed(db_name="otp_staging_contract") == "otp_staging_contract"
    os.environ.pop("OTP_STAGING_MYSQL_DB", None)

    # Forbidden DB still refused without local-prep dual gate.
    os.environ["OTP_STAGING_MYSQL_DB"] = "sentiment_demo"
    try:
        assert_staging_mutation_allowed(db_name="sentiment_demo")
        raise AssertionError("expected refuse sentiment_demo without local prep flag")
    except RuntimeError:
        pass
    os.environ["OTP_STAGING_ALLOW_FORBIDDEN_DB_FOR_LOCAL_PREP"] = "1"
    assert assert_staging_mutation_allowed(db_name="sentiment_demo") == "sentiment_demo"
    os.environ.pop("OTP_STAGING_ALLOW_FORBIDDEN_DB_FOR_LOCAL_PREP", None)
    os.environ.pop("OTP_STAGING_MYSQL_DB", None)
    os.environ.pop("OTP_STAGING_MUTATION_OK", None)


def case_dreamhost_deploy_refuses() -> None:
    env = os.environ.copy()
    env.pop("OTP_ALLOW_LEGACY_PASSENGER_DEPLOY", None)
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "dreamhost" / "deploy.sh")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert proc.returncode == 2, proc.stderr
    assert "LEGACY" in proc.stderr or "REFUSED" in proc.stderr


def case_no_secrets_in_staging_tree() -> None:
    banned = ("PASSWORD=", "BEGIN RSA", "sk-proj-", "mysql+pymysql://")
    for path in (ROOT / "docs" / "staging").rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in banned:
            assert token not in text, f"{path}: {token}"
    for path in (ROOT / "scripts" / "staging").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "MYSQL_PASSWORD=" not in text or 'os.getenv("MYSQL_PASSWORD"' in text


def case_environment_contract_categories() -> None:
    text = (ROOT / "docs" / "staging" / "environment_contract.md").read_text(encoding="utf-8")
    for needle in (
        "PRODUCTION_MODE=true",
        "CACHE_BACKEND=mysql",
        "SEMANTIC_CACHE_BACKEND=mysql",
        "DYNAMIC_SQL_ENABLED=false",
        "CHAT_VECTOR_WRITES_ENABLED=false",
        "OTP_TURN_IDEMPOTENCY_FAULTS",
        "LEGACY_CHAT_ENABLED=false",
        "REQUIRE_CLIENT_TURN_ID",
        "FLASK_SESSION_COOKIE_SECURE=true",
        "HTTPS",
    ):
        assert needle in text, needle


def case_shell_syntax_deploy() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "dreamhost" / "deploy.sh")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr


def main() -> int:
    cases = [
        ("docs_present", case_docs_present),
        ("hosting_distinguishes_paths", case_hosting_distinguishes_paths),
        ("runbook_steps", case_runbook_steps),
        ("identity_guards", case_identity_guards),
        ("dreamhost_deploy_refuses", case_dreamhost_deploy_refuses),
        ("no_secrets_in_staging_tree", case_no_secrets_in_staging_tree),
        ("environment_contract_categories", case_environment_contract_categories),
        ("shell_syntax_deploy", case_shell_syntax_deploy),
    ]
    print(f"Running {len(cases)} staging-prep local cases...\n")
    for name, fn in cases:
        _case(name, fn)
    print(f"\nSummary: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
