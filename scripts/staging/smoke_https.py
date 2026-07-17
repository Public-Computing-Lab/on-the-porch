#!/usr/bin/env python3
"""HTTPS staging smoke checks. Refuses to run without an explicit https base URL.

Does not mutate databases. Safe to point at staging when it exists.
Never prints response bodies that may contain resident text at length —
assertions use status codes and small allowlisted fields only.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict
from urllib.parse import urlparse


FORBIDDEN_DB_HINTS = {
    # Refuse accidental use of known local demo identity as "staging HTTPS".
    "sentiment_demo",
}


def _require_https(base: str) -> str:
    base = base.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https":
        raise SystemExit("Refusing: --base-url must be https:// (staging TLS required)")
    if not parsed.netloc:
        raise SystemExit("Refusing: invalid --base-url")
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="https://staging-host")
    args = parser.parse_args()
    base = _require_https(args.base_url)

    try:
        import requests
    except ImportError as exc:
        raise SystemExit(f"requests required: {exc}") from exc

    failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        if ok:
            print(f"PASS  {name}")
        else:
            failed += 1
            print(f"FAIL  {name}: {detail}")

    # TLS + public health
    try:
        health = requests.get(f"{base}/health", timeout=20)
        body = health.json()
        check("health_status_200", health.status_code == 200, str(health.status_code))
        check("health_minimal_status", body.get("status") == "ok", str(body.keys()))
        banned = {
            "model", "vectordb", "mysql", "redis", "pool", "path", "ingestion",
            "traceback", "password", "secret", "CACHE_BACKEND",
        }
        blob = json.dumps(body).lower()
        check(
            "health_no_internals",
            not any(b.lower() in blob for b in banned if b.lower() not in ("status",)),
            blob[:200],
        )
        # Only allow status + optional version
        allowed = {"status", "version"}
        check("health_keys_allowlist", set(body.keys()) <= allowed, str(body.keys()))
    except Exception as exc:  # noqa: BLE001
        check("health_reachable", False, f"{exc.__class__.__name__}")

    # Admin readiness unauthenticated
    try:
        ready = requests.get(f"{base}/admin/health/ready", timeout=20)
        check(
            "admin_ready_rejects_anonymous",
            ready.status_code in (401, 403),
            str(ready.status_code),
        )
    except Exception as exc:  # noqa: BLE001
        check("admin_ready_reachable", False, f"{exc.__class__.__name__}")

    # Guest bootstrap + CSRF over HTTPS
    try:
        session = requests.Session()
        guest = session.post(f"{base}/auth/guest", timeout=20)
        check("guest_create", guest.status_code in (200, 201), str(guest.status_code))
        payload: Dict[str, Any] = guest.json() if guest.ok else {}
        token = payload.get("session_token")
        csrf = payload.get("csrf_token")
        check("guest_tokens_present", bool(token and csrf))
        me = session.get(
            f"{base}/auth/me",
            headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
            timeout=20,
        )
        check("guest_me", me.status_code == 200, str(me.status_code))
        # Admin readiness as guest must fail
        ready_g = session.get(
            f"{base}/admin/health/ready",
            headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
            timeout=20,
        )
        check(
            "admin_ready_rejects_guest",
            ready_g.status_code in (401, 403),
            str(ready_g.status_code),
        )
    except Exception as exc:  # noqa: BLE001
        check("guest_flow", False, f"{exc.__class__.__name__}")

    print(f"\nbase={base}")
    print(f"failed={failed}")
    if failed:
        print("Staging smoke incomplete — do not promote.")
        return 1
    print("Staging HTTPS smoke passed (subset). Continue with SSE/idempotency suites.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
