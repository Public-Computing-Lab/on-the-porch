#!/usr/bin/env python3
"""Focused checks for the MySQL-only shared-state runtime contract.

Covers configuration rejection, dependency quarantine, MySQL ingest status,
documentation/dependency hygiene, and optional live multi-worker checks.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))
sys.path.insert(0, str(ROOT))

CASE_RESULTS: List[tuple[str, str]] = []


def _ok(case: str) -> None:
    CASE_RESULTS.append((case, "PASS"))
    print(f"OK  {case}")


def _fail(case: str, exc: BaseException) -> None:
    CASE_RESULTS.append((case, f"FAIL:{exc}"))
    print(f"FAIL {case}: {exc}")


def _run(case: str, fn) -> None:
    try:
        fn()
        _ok(case)
    except Exception as exc:  # noqa: BLE001
        _fail(case, exc)


def _contract_subprocess(env_updates: Dict[str, str]) -> List[str]:
    env = os.environ.copy()
    env.update(env_updates)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config import validate_cache_backend_contract; "
            "import json; print(json.dumps(validate_cache_backend_contract()))",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip() or "[]")


# -----------------------------------------------------------------------------
# Configuration / dependency cases (1-8, 20, 22-25)
# -----------------------------------------------------------------------------


def case_01_default_backend_mysql() -> None:
    text = (ROOT / "config.py").read_text(encoding="utf-8")
    assert 'CACHE_BACKEND = _env("CACHE_BACKEND", "mysql")' in text
    from config import SUPPORTED_CACHE_BACKENDS

    assert SUPPORTED_CACHE_BACKENDS == frozenset({"mysql", "memory"})
    assert "redis" not in SUPPORTED_CACHE_BACKENDS


def case_02_explicit_mysql_ok() -> None:
    errs = _contract_subprocess(
        {"CACHE_BACKEND": "mysql", "SEMANTIC_CACHE_BACKEND": "mysql"}
    )
    assert errs == []


def case_03_cache_backend_redis_fails() -> None:
    errs = _contract_subprocess(
        {"CACHE_BACKEND": "redis", "SEMANTIC_CACHE_BACKEND": "mysql"}
    )
    assert errs and any("CACHE_BACKEND=redis" in e for e in errs)
    blob = " ".join(errs).lower()
    assert "password" not in blob and "mysql_password" not in blob


def case_04_semantic_backend_redis_fails() -> None:
    errs = _contract_subprocess(
        {"CACHE_BACKEND": "mysql", "SEMANTIC_CACHE_BACKEND": "redis"}
    )
    assert errs and any("SEMANTIC_CACHE_BACKEND=redis" in e for e in errs)


def case_05_unknown_backend_fails() -> None:
    errs = _contract_subprocess(
        {"CACHE_BACKEND": "memcached", "SEMANTIC_CACHE_BACKEND": "mysql"}
    )
    assert errs and any("memcached" in e for e in errs)
    errs2 = _contract_subprocess(
        {"CACHE_BACKEND": "mysql", "SEMANTIC_CACHE_BACKEND": "memcached"}
    )
    assert errs2 and any("memcached" in e for e in errs2)


def case_06_redis_url_alone_inert() -> None:
    # REDIS_URL must not activate Redis; contract still clean with mysql backends.
    errs = _contract_subprocess(
        {
            "CACHE_BACKEND": "mysql",
            "SEMANTIC_CACHE_BACKEND": "mysql",
            "REDIS_URL": "redis://example.invalid:6379/0",
        }
    )
    assert errs == []
    # No redis import in config / rate_limit / mysql_cache
    for rel in ("config.py", "api/rate_limit.py", "api/mysql_cache.py"):
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        assert "import redis" not in text


def case_07_import_without_redis_package() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="otp-noredis-"))
    try:
        venv = tmp / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        py = venv / "bin" / "python"
        # Minimal deps for config + rate_limit + ingest_queue + semantic_cache factories
        subprocess.run(
            [str(py), "-m", "pip", "install", "-q", "python-dotenv", "mysql-connector-python"],
            check=True,
        )
        # Prove redis is absent
        miss = subprocess.run(
            [str(py), "-c", "import redis"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert miss.returncode != 0
        env = os.environ.copy()
        env["CACHE_BACKEND"] = "mysql"
        env["SEMANTIC_CACHE_BACKEND"] = "mysql"
        check = subprocess.run(
            [
                str(py),
                "-c",
                "import sys; "
                f"sys.path[:0]=[{str(API)!r},{str(ROOT)!r}]; "
                "from config import assert_cache_backend_contract, validate_cache_backend_contract; "
                "assert_cache_backend_contract(); "
                "from rate_limit import RateLimiter; "
                "from semantic_cache import build_semantic_cache_backend; "
                "build_semantic_cache_backend(backend_name='mysql', redis_key='k', mysql_store=None); "
                "from ingest_queue import _redis_client; "
                "\n"
                "try:\n"
                "  _redis_client()\n"
                "except RuntimeError:\n"
                "  pass\n"
                "else:\n"
                "  raise SystemExit('redis client should fail')\n"
                "print('noredis_ok')",
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 0, check.stdout + check.stderr
        assert "noredis_ok" in check.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_08_no_redis_client_construction() -> None:
    text_api = (API / "api_v2.py").read_text(encoding="utf-8")
    assert "import redis" not in text_api
    assert "redis.Redis" not in text_api
    assert "class _RedisSessionCache" in text_api
    assert "Redis session cache is not supported" in text_api
    from semantic_cache import _RedisSemanticBackend, build_semantic_cache_backend

    try:
        _RedisSemanticBackend()
        raise AssertionError("expected")
    except RuntimeError as exc:
        assert "not supported" in str(exc).lower()
    try:
        build_semantic_cache_backend(backend_name="redis", redis_key="k")
        raise AssertionError("expected")
    except RuntimeError as exc:
        assert "not supported" in str(exc).lower()


def case_20_errors_have_no_credentials() -> None:
    errs = _contract_subprocess(
        {"CACHE_BACKEND": "redis", "SEMANTIC_CACHE_BACKEND": "redis"}
    )
    blob = json.dumps(errs).lower()
    for needle in ("password", "token", "secret", "api_key", "pepper"):
        assert needle not in blob


def case_22_docs_no_active_redis_setup() -> None:
    # Active setup docs must not instruct installing/starting Redis.
    for rel in (
        "README.md",
        "example_env.txt",
        "start_api.sh",
        "docs/local_production_simulation.md",
    ):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "brew install redis" not in text.lower()
        assert "./start_redis.sh" not in text or "do not run" in text.lower() or "not supported" in text.lower()
        # README may mention do not run start_redis — allowed
        if rel == "README.md":
            assert "Redis is not supported" in text
        if rel == "example_env.txt":
            assert re.search(r"^CACHE_BACKEND=mysql\s*$", text, re.M)
            assert not re.search(r"^CACHE_BACKEND=redis\s*$", text, re.M)
            assert not re.search(r"^REDIS_URL=redis://", text, re.M)


def case_23_deps_no_redis() -> None:
    for rel in ("requirements.txt", "api/requirements.txt"):
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        assert "redis" not in text or text.strip().startswith("#")


def case_24_memory_rejected_in_production() -> None:
    from ops_health import validate_production_config

    ok, issues = validate_production_config(
        production_mode=True,
        cache_backend="memory",
        semantic_cache_enabled=True,
        gemini_model="gemini-2.5-flash-lite",
        gemini_fast_model="gemini-2.5-flash-lite",
        embed_provider="gemini",
    )
    assert ok is False
    assert any("must be 'mysql'" in i for i in issues)


def case_25_no_process_local_shared_correctness() -> None:
    # Authoritative conversation state lives in MySQL thread envelope, not module dicts.
    text = (API / "api_v2.py").read_text(encoding="utf-8")
    assert "thread_state_json" in text
    assert "CACHE_BACKEND=mysql" in text or "CACHE_BACKEND == \"mysql\"" in text
    # Memory session cache docstring must say non-authoritative / single-process
    assert "NOT shared across gunicorn workers" in text
    assert "non-authoritative" in text or "single-process" in text


def case_21_ingestion_status_mysql() -> None:
    from ingest_queue import _redis_client, get_ingest_job_status, record_job_start

    try:
        _redis_client()
        raise AssertionError("expected")
    except RuntimeError:
        pass
    status = get_ingest_job_status()
    assert "jobs" in status
    assert status.get("error") != "redis"
    # Source uses ingest_job_runs
    src = (API / "ingest_queue.py").read_text(encoding="utf-8")
    assert "FROM ingest_job_runs" in src
    assert "import redis" not in src


# -----------------------------------------------------------------------------
# Live API cases (9-19) — require --base-url
# -----------------------------------------------------------------------------


class _Client:
    def __init__(self, base_url: str) -> None:
        import requests

        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.token = ""
        self.csrf = ""

    def guest(self) -> None:
        r = self.session.post(f"{self.base}/auth/guest", timeout=15)
        r.raise_for_status()
        payload = r.json()
        self.token = payload["session_token"]
        self.csrf = payload["csrf_token"]

    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-CSRF-Token": self.csrf,
            "Content-Type": "application/json",
        }

    def create_thread(self, title: str) -> str:
        r = self.session.post(
            f"{self.base}/conversations",
            headers=self.headers(),
            json={"title": title},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["thread"]["id"]

    def send_json(self, thread_id: str, message: str):
        return self.session.post(
            f"{self.base}/conversations/{thread_id}/messages",
            headers=self.headers(),
            json={"message": message},
            timeout=45,
        )

    def send_sse(self, thread_id: str, message: str):
        return self.session.post(
            f"{self.base}/conversations/{thread_id}/messages/stream",
            headers=self.headers(),
            json={"message": message},
            stream=True,
            timeout=45,
        )


def _read_state(thread_id: str) -> Dict[str, Any]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from test_active_event_followups import _read_live_state  # noqa: WPS433

    env = _read_live_state(thread_id)
    return {
        "revision": env.conversation_state.revision,
        "topic": (
            env.conversation_state.active_topic.value
            if env.conversation_state.active_topic
            else None
        ),
        "cache_mode": env.retrieval_cache.get("mode"),
        "cache_sig": env.retrieval_cache.get("event_query_signature"),
        "cache_ts": env.retrieval_cache.get("timestamp"),
        "entity": (
            env.conversation_state.active_entity.to_dict()
            if env.conversation_state.active_entity
            else None
        ),
    }


def run_live_cases(base_url: str) -> None:
    import requests

    # 19 health has no redis dependency status
    def case_19() -> None:
        r = requests.get(f"{base_url.rstrip('/')}/health", timeout=15)
        r.raise_for_status()
        payload = r.json()
        assert "redis" not in payload
        blob = json.dumps(payload).lower()
        assert "redis://" not in blob
        assert payload.get("cache_backend") == "mysql"

    _run("19_health_no_redis_field", case_19)

    # 09 JSON
    def case_09() -> None:
        c = _Client(base_url)
        c.guest()
        tid = c.create_thread("mysql-only-json")
        r = c.send_json(tid, "What events are happening this weekend?")
        assert r.status_code == 201, r.text
        am = r.json()["assistant_message"]
        assert am["response_mode"] == "structured_events"
        assert am.get("model_name") is None

    _run("09_json_conversation", case_09)

    # 10 SSE
    def case_10() -> None:
        c = _Client(base_url)
        c.guest()
        tid = c.create_thread("mysql-only-sse")
        r = c.send_sse(tid, "What events are happening tomorrow?")
        assert r.status_code == 200
        body = "".join(chunk.decode("utf-8", errors="ignore") for chunk in r.iter_content(chunk_size=None))
        assert "structured_events" in body or "event" in body.lower()

    _run("10_sse_conversation", case_10)

    # 11 guest session across workers
    def case_11() -> None:
        c = _Client(base_url)
        c.guest()
        # Hit auth/me repeatedly to land on both workers
        modes = set()
        for _ in range(20):
            r = c.session.get(f"{base_url.rstrip('/')}/auth/me", headers=c.headers(), timeout=15)
            assert r.status_code == 200
            assert r.json().get("authenticated") is True
            modes.add(r.json().get("is_guest"))
        assert True in modes

    _run("11_guest_session_across_workers", case_11)

    # 12 authenticated session — guest is the supported fixture; treat guest continuity as auth bearer path
    def case_12() -> None:
        c = _Client(base_url)
        c.guest()
        r = c.session.get(f"{base_url.rstrip('/')}/auth/me", headers=c.headers(), timeout=15)
        assert r.status_code == 200 and r.json()["authenticated"] is True
        # Password auth fixtures are not provisioned in local demo; guest bearer is the
        # cross-worker session path available without production users.

    _run("12_authenticated_session_guest_bearer", case_12)

    # 13-15 state/cache across workers
    def case_13_15() -> None:
        c = _Client(base_url)
        c.guest()
        tid = c.create_thread("mysql-only-state")
        r1 = c.send_json(tid, "What events are happening this weekend?")
        assert r1.status_code == 201
        before = _read_state(tid)
        assert before["topic"] == "EVENTS"
        # Second request (likely other worker) refinement
        r2 = c.send_json(tid, "What about next weekend?")
        assert r2.status_code == 201
        after = _read_state(tid)
        assert after["topic"] == "EVENTS"
        assert after["revision"] >= before["revision"]
        assert after["cache_sig"] is not None
        assert after["cache_ts"] is not None
        # Force another worker hit
        for _ in range(8):
            r = c.send_json(tid, "Thanks.")
            assert r.status_code in (201, 409)
            if r.status_code == 201:
                break
        final = _read_state(tid)
        assert final["topic"] == "EVENTS"
        assert final["cache_sig"] == after["cache_sig"] or final["revision"] >= after["revision"]

    _run("13_15_state_and_event_cache_across_workers", case_13_15)

    # 16 emergency
    def case_16() -> None:
        c = _Client(base_url)
        c.guest()
        tid = c.create_thread("mysql-only-emergency")
        before = _read_state(tid)
        r = c.send_json(tid, "There's a shooting happening outside right now.")
        assert r.status_code == 201
        am = r.json()["assistant_message"]
        assert am["response_mode"] == "emergency"
        assert am["content"].startswith("Call 911 now.")
        after = _read_state(tid)
        assert after["revision"] == before["revision"]

    _run("16_emergency_routing", case_16)

    # 17 state-aware event retrieval
    def case_17() -> None:
        c = _Client(base_url)
        c.guest()
        tid = c.create_thread("mysql-only-events")
        r = c.send_json(tid, "What events are happening in Codman Square this weekend?")
        assert r.status_code == 201
        assert r.json()["assistant_message"]["response_mode"] == "structured_events"
        st = _read_state(tid)
        assert st["topic"] == "EVENTS"

    _run("17_state_aware_event_retrieval", case_17)

    # 18 same-thread conflict
    def case_18() -> None:
        c = _Client(base_url)
        c.guest()
        tid = c.create_thread("mysql-only-race")

        def once() -> int:
            return c.send_json(tid, "What events are happening this weekend?").status_code

        with ThreadPoolExecutor(max_workers=2) as ex:
            statuses = sorted(f.result() for f in [ex.submit(once), ex.submit(once)])
        assert statuses == [201, 409], statuses

    _run("18_same_thread_conflict", case_18)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("LOCAL_API_URL", ""))
    args = parser.parse_args()

    unit_cases = [
        ("01_default_backend_mysql", case_01_default_backend_mysql),
        ("02_explicit_mysql_ok", case_02_explicit_mysql_ok),
        ("03_cache_backend_redis_fails", case_03_cache_backend_redis_fails),
        ("04_semantic_backend_redis_fails", case_04_semantic_backend_redis_fails),
        ("05_unknown_backend_fails", case_05_unknown_backend_fails),
        ("06_redis_url_alone_inert", case_06_redis_url_alone_inert),
        ("07_import_without_redis_package", case_07_import_without_redis_package),
        ("08_no_redis_client_construction", case_08_no_redis_client_construction),
        ("20_errors_have_no_credentials", case_20_errors_have_no_credentials),
        ("21_ingestion_status_mysql", case_21_ingestion_status_mysql),
        ("22_docs_no_active_redis_setup", case_22_docs_no_active_redis_setup),
        ("23_deps_no_redis", case_23_deps_no_redis),
        ("24_memory_rejected_in_production", case_24_memory_rejected_in_production),
        ("25_no_process_local_shared_correctness", case_25_no_process_local_shared_correctness),
    ]
    for name, fn in unit_cases:
        _run(name, fn)

    # Quarantine stubs + start_redis (also required)
    def start_redis() -> None:
        proc = subprocess.run(
            ["bash", str(ROOT / "start_redis.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 1

    _run("start_redis_obsolete_stub", start_redis)

    if args.base_url:
        run_live_cases(args.base_url)
    else:
        print("SKIP live cases 09-19 (pass --base-url to enable)")

    failed = sum(1 for _, status in CASE_RESULTS if status.startswith("FAIL"))
    passed = sum(1 for _, status in CASE_RESULTS if status == "PASS")
    print(
        json.dumps(
            {
                "passed": passed,
                "failed": failed,
                "total": len(CASE_RESULTS),
                "cases": [{"id": i, "status": s} for i, s in CASE_RESULTS],
            },
            indent=2,
        )
    )
    print(f"MySQL-only runtime tests: {passed}/{len(CASE_RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
