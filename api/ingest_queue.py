"""Ingestion job tracking via MySQL (Iteration 5).

Redis is not supported. Job start/finish and /health status use the
`ingest_job_runs` table when present.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Optional

INGEST_QUEUE_KEY = os.getenv("INGEST_QUEUE_KEY", "rethinkai:ingest:jobs")
INGEST_STATUS_PREFIX = os.getenv("INGEST_STATUS_PREFIX", "rethinkai:ingest:status:")
INGEST_STATUS_TTL_SECONDS = int(os.getenv("INGEST_STATUS_TTL_SECONDS", "604800"))

KNOWN_INGEST_JOBS = (
    "boston_sync",
    "dotnews",
    "tribe_events",
    "rss",
    "google_drive",
    "guest_cleanup",
    "materialized_refresh",
)


def is_ingest_queue_enabled() -> bool:
    return os.getenv("INGEST_QUEUE_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _redis_client():
    """Obsolete. Redis is not supported for ingest tracking."""
    raise RuntimeError(
        "Redis ingest tracking is not supported. "
        "Use MySQL ingest_job_runs (pass mysql_conn)."
    )


def record_job_start(
    job_name: str,
    *,
    redis_client=None,
    mysql_conn=None,
) -> Dict[str, Any]:
    """Mark a job as running. Returns run_id and optional mysql row id."""
    if redis_client is not None:
        # Explicitly ignore legacy callers that still pass a Redis client.
        redis_client = None
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
    if redis_client is not None:
        redis_client = None
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


def get_ingest_job_status(
    redis_client=None,
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    """Return latest status per known ingest job (for /health) from MySQL."""
    out: Dict[str, Any] = {"enabled": is_ingest_queue_enabled(), "jobs": {}}
    if not is_ingest_queue_enabled():
        return out
    if connection_factory is None:
        try:
            import mysql.connector

            def connection_factory() -> Any:
                return mysql.connector.connect(
                    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                    port=int(os.getenv("MYSQL_PORT", "3306")),
                    user=os.getenv("MYSQL_USER", "root"),
                    password=os.getenv("MYSQL_PASSWORD", ""),
                    database=os.getenv("MYSQL_DB", "sentiment_demo"),
                )
        except Exception as exc:
            out["error"] = str(exc)
            return out
    conn = None
    try:
        conn = connection_factory()
        if not _ingest_runs_table_exists(conn):
            out["error"] = "ingest_job_runs_missing"
            return out
        cursor = conn.cursor(dictionary=True)
        try:
            for job in KNOWN_INGEST_JOBS:
                cursor.execute(
                    """
                    SELECT job_name, status, started_at, finished_at,
                           duration_seconds, error_message
                    FROM ingest_job_runs
                    WHERE job_name = %s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (job,),
                )
                row = cursor.fetchone()
                if not row:
                    continue
                out["jobs"][job] = {
                    "job_name": row["job_name"],
                    "status": row["status"],
                    "started_at": (
                        row["started_at"].isoformat()
                        if hasattr(row["started_at"], "isoformat")
                        else row["started_at"]
                    ),
                    "finished_at": (
                        row["finished_at"].isoformat()
                        if row["finished_at"] is not None
                        and hasattr(row["finished_at"], "isoformat")
                        else row["finished_at"]
                    ),
                    "duration_seconds": row["duration_seconds"],
                    "error": row["error_message"],
                }
        finally:
            cursor.close()
    except Exception as exc:
        out["error"] = exc.__class__.__name__
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
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
