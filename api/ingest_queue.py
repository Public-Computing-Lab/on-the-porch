"""Ingestion job tracking via Redis + MySQL (Iteration 5)."""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

INGEST_QUEUE_KEY = os.getenv("INGEST_QUEUE_KEY", "rethinkai:ingest:jobs")
INGEST_STATUS_PREFIX = os.getenv("INGEST_STATUS_PREFIX", "rethinkai:ingest:status:")
INGEST_STATUS_TTL_SECONDS = int(os.getenv("INGEST_STATUS_TTL_SECONDS", "604800"))


def is_ingest_queue_enabled() -> bool:
    return os.getenv("INGEST_QUEUE_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _redis_client():
    import redis

    url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def record_job_start(
    job_name: str,
    *,
    redis_client=None,
    mysql_conn=None,
) -> Dict[str, Any]:
    """Mark a job as running. Returns run_id and optional mysql row id."""
    run_id = f"{job_name}:{int(time.time() * 1000)}"
    payload = {
        "job_name": job_name,
        "status": "running",
        "started_at": _utcnow().isoformat(),
        "finished_at": None,
        "duration_seconds": None,
        "error": None,
        "run_id": run_id,
    }
    if is_ingest_queue_enabled() and redis_client is not None:
        key = f"{INGEST_STATUS_PREFIX}{job_name}"
        redis_client.setex(key, INGEST_STATUS_TTL_SECONDS, json.dumps(payload))
        redis_client.lpush(INGEST_QUEUE_KEY, json.dumps({"run_id": run_id, **payload}))
    if mysql_conn is not None and _ingest_runs_table_exists(mysql_conn):
        cursor = mysql_conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO ingest_job_runs (job_name, status, started_at)
                VALUES (%s, 'running', UTC_TIMESTAMP())
                """,
                (job_name,),
            )
            mysql_conn.commit()
            payload["mysql_id"] = cursor.lastrowid
        except Exception:
            mysql_conn.rollback()
        finally:
            cursor.close()
    return {"run_id": run_id, "mysql_id": payload.get("mysql_id")}


def record_job_finish(
    job_name: str,
    *,
    status: str = "success",
    error: Optional[str] = None,
    started_at: Optional[float] = None,
    redis_client=None,
    mysql_conn=None,
    mysql_row_id: Optional[int] = None,
) -> Dict[str, Any]:
    finished = _utcnow()
    duration = None
    if started_at is not None:
        duration = round(time.time() - started_at, 2)
    payload = {
        "job_name": job_name,
        "status": status,
        "finished_at": finished.isoformat(),
        "duration_seconds": duration,
        "error": (error or "")[:2000] or None,
    }
    if is_ingest_queue_enabled() and redis_client is not None:
        key = f"{INGEST_STATUS_PREFIX}{job_name}"
        existing_raw = redis_client.get(key)
        merged = json.loads(existing_raw) if existing_raw else {"job_name": job_name}
        merged.update(payload)
        redis_client.setex(key, INGEST_STATUS_TTL_SECONDS, json.dumps(merged))
    if mysql_conn is not None and mysql_row_id and _ingest_runs_table_exists(mysql_conn):
        cursor = mysql_conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE ingest_job_runs
                SET status = %s, finished_at = UTC_TIMESTAMP(),
                    duration_seconds = %s, error_message = %s
                WHERE id = %s
                """,
                (status, duration, payload["error"], mysql_row_id),
            )
            mysql_conn.commit()
        except Exception:
            mysql_conn.rollback()
        finally:
            cursor.close()
    return payload


def _ingest_runs_table_exists(conn) -> bool:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = 'ingest_job_runs'
            LIMIT 1
            """
        )
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        cursor.close()


def get_ingest_job_status(redis_client=None) -> Dict[str, Any]:
    """Return latest status per known ingest job (for /health)."""
    jobs = [
        "boston_sync",
        "dotnews",
        "tribe_events",
        "rss",
        "google_drive",
        "guest_cleanup",
        "materialized_refresh",
    ]
    out: Dict[str, Any] = {"enabled": is_ingest_queue_enabled(), "jobs": {}}
    if not is_ingest_queue_enabled():
        return out
    client = redis_client
    if client is None:
        try:
            client = _redis_client()
        except Exception as exc:
            out["error"] = str(exc)
            return out
    for job in jobs:
        raw = client.get(f"{INGEST_STATUS_PREFIX}{job}")
        if raw:
            try:
                out["jobs"][job] = json.loads(raw)
            except json.JSONDecodeError:
                out["jobs"][job] = {"status": "unknown"}
    return out


def _coerce_utc_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text[:26], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_311_freshness(
    connection_factory: Callable[[], Any],
    *,
    stale_hours: float = 2.0,
) -> Dict[str, Any]:
    """Alert when the newest Dorchester 311 row is older than threshold."""
    conn = connection_factory()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT MAX(`open_date`) AS latest_open
            FROM service_requests_311
            WHERE neighborhood = 'Dorchester' OR police_district = 'C11'
            """
        )
        row = cursor.fetchone()
        latest = row[0] if row else None
        latest_dt = _coerce_utc_datetime(latest)
        if latest_dt is None:
            return {"status": "unknown", "latest_open": None, "hours_ago": None}
        now = _utcnow()
        hours = (now - latest_dt).total_seconds() / 3600.0
        status = "ok" if hours <= stale_hours else "stale"
        return {
            "status": status,
            "latest_open": str(latest),
            "hours_ago": round(hours, 2),
            "stale_threshold_hours": stale_hours,
        }
    except Exception as exc:
        return {"status": "error", "error": exc.__class__.__name__}
    finally:
        cursor.close()
        conn.close()
