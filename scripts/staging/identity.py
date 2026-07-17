"""Shared staging-target identity guards (no secrets)."""

from __future__ import annotations

import os
import re

# Explicit allowlist patterns for mutable staging / disposable prep DBs.
_ALLOWED_DB_RE = re.compile(
    r"^(otp_staging([_][a-z0-9]+)*)|(staging([_][a-z0-9]+)*)$",
    re.IGNORECASE,
)

FORBIDDEN_DB_NAMES = {
    "",
    "mysql",
    "information_schema",
    "performance_schema",
    "sys",
    "sentiment_demo",
    "rethink_ai_boston",
}


def assert_staging_mutation_allowed(
    *,
    db_name: str | None = None,
    require_mutation_ok: bool = True,
) -> str:
    """Fail closed unless the operator affirmed staging mutation and DB identity.

    Affirmative evidence required (all of):
    1. OTP_STAGING_MUTATION_OK=1 (or true/yes/on)
    2. MYSQL_DB (or override) matches staging allowlist pattern, OR equals
       OTP_STAGING_MYSQL_DB when that env is set
    3. Name is not in the forbidden list / production-looking without staging

    Hostname substring alone is never sufficient.

    Local prep exception (explicit, dual-gated): when
    OTP_STAGING_ALLOW_FORBIDDEN_DB_FOR_LOCAL_PREP=1 and OTP_STAGING_MYSQL_DB is set,
    the contracted DB name may be used even if it appears in FORBIDDEN_DB_NAMES.
    Use only for disposable fixture-prefix tests; never for remote staging claims.
    """
    if require_mutation_ok:
        flag = (os.getenv("OTP_STAGING_MUTATION_OK") or "").strip().lower()
        if flag not in {"1", "true", "yes", "on"}:
            raise RuntimeError(
                "Refusing mutation: set OTP_STAGING_MUTATION_OK=1 only after verifying "
                "the target MySQL schema is staging-only (never production)."
            )

    name = (db_name if db_name is not None else os.getenv("MYSQL_DB") or "").strip()
    lowered = name.lower()
    expected = (os.getenv("OTP_STAGING_MYSQL_DB") or "").strip()
    local_prep = (os.getenv("OTP_STAGING_ALLOW_FORBIDDEN_DB_FOR_LOCAL_PREP") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if lowered in FORBIDDEN_DB_NAMES:
        if not (local_prep and expected and name == expected):
            raise RuntimeError(f"Refusing database name {name!r} (forbidden / non-staging)")

    if expected:
        if name != expected:
            raise RuntimeError(
                "Refusing: MYSQL_DB does not match OTP_STAGING_MYSQL_DB contract"
            )
        return name

    if "prod" in lowered and "staging" not in lowered:
        raise RuntimeError(
            f"Refusing database name {name!r}: looks like production without staging marker"
        )

    if not _ALLOWED_DB_RE.match(name):
        raise RuntimeError(
            f"Refusing database name {name!r}: must match staging allowlist "
            "(e.g. staging_*, otp_staging_*) or OTP_STAGING_MYSQL_DB contract"
        )
    return name
