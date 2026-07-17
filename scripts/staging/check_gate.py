#!/usr/bin/env python3
"""Report staging gate readiness from the local workstation (no remote mutation)."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    print("Staging gate status (local preparation only)")
    print(f"repo={ROOT}")
    docs = ROOT / "docs" / "staging"
    for name in (
        "README.md",
        "environment_contract.md",
        "hosting_audit.md",
        "runbook.md",
        "connection_budget.md",
        "fixtures.md",
        "operator_prerequisites.md",
    ):
        path = docs / name
        print(f"doc_{'ok' if path.is_file() else 'MISSING'} {name}")

    # No staging host in common env
    staging_url = ""
    try:
        from dotenv import load_dotenv
        import os

        load_dotenv(ROOT / ".env", override=False)
        staging_url = (os.getenv("STAGING_BASE_URL") or "").strip()
    except Exception:
        pass

    if not staging_url:
        print("remote_staging_url=MISSING")
        print("gate=BLOCKED_NO_STAGING_HOST")
        print(
            "Next: provision staging FQDN/MySQL/SSH per docs/staging/README.md, "
            "then re-run smoke_https.py --base-url https://..."
        )
        return 2

    if not staging_url.lower().startswith("https://"):
        print("remote_staging_url=INVALID_NOT_HTTPS")
        return 1

    host = staging_url.split("//", 1)[1].split("/", 1)[0].split(":")[0]
    try:
        socket.getaddrinfo(host, 443)
        print(f"dns_ok host={host}")
    except OSError as exc:
        print(f"dns_FAILED host={host} err={exc.__class__.__name__}")
        return 1

    print("gate=REMOTE_URL_PRESENT_CONTINUE_SMOKE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
