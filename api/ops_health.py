"""Operational health helpers: ingestion lag and production config checks."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _parse_iso_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _read_last_jsonl_record(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _read_last_cron_finish(path: Path) -> Optional[datetime]:
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    marker = "Cron ingestion job finished successfully"
    for line in reversed(lines):
        if marker not in line:
            continue
        # Log format: [2026-06-22 12:56:57] Cron ingestion job finished
        if line.startswith("[") and "]" in line:
            ts_text = line[1:line.index("]")]
            try:
                dt = datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S")
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return datetime.now(timezone.utc)
    return None


def check_ingestion_lag(
    *,
    repo_root: Path,
    stale_hours: int,
) -> Dict[str, Any]:
    """Report how fresh civic data and the daily cron job are."""
    now = datetime.now(timezone.utc)
    boston_log = (
        repo_root
        / "on_the_porch/data_ingestion/boston_data_sync/boston_sync_log.jsonl"
    )
    cron_log = repo_root / "logs/cron_ingest.log"

    boston_record = _read_last_jsonl_record(boston_log)
    boston_end: Optional[datetime] = None
    boston_errors: list[str] = []
    if boston_record:
        boston_end = _parse_iso_timestamp(
            str(boston_record.get("end_time") or "")
        )
        for ds in boston_record.get("datasets") or []:
            for err in ds.get("errors") or []:
                if err:
                    boston_errors.append(str(err))

    cron_end = _read_last_cron_finish(cron_log)

    def _hours_ago(dt: Optional[datetime]) -> Optional[float]:
        if dt is None:
            return None
        return max(0.0, (now - dt).total_seconds() / 3600.0)

    boston_hours = _hours_ago(boston_end)
    cron_hours = _hours_ago(cron_end)

    status = "ok"
    if boston_hours is None or boston_hours > stale_hours:
        status = "stale"
    if cron_hours is None or cron_hours > stale_hours:
        status = "stale"
    if boston_errors:
        status = "degraded"

    return {
        "status": status,
        "stale_threshold_hours": stale_hours,
        "boston_sync": {
            "last_success_at": boston_end.isoformat() if boston_end else None,
            "hours_ago": (
                round(boston_hours, 2) if boston_hours is not None else None
            ),
            "log_path": str(boston_log),
            "recent_errors": boston_errors[:3],
        },
        "cron_ingest": {
            "last_success_at": cron_end.isoformat() if cron_end else None,
            "hours_ago": (
                round(cron_hours, 2) if cron_hours is not None else None
            ),
            "log_path": str(cron_log),
        },
    }


def validate_production_config(
    *,
    production_mode: bool,
    cache_backend: str,
    semantic_cache_enabled: bool,
    gemini_model: str,
    gemini_fast_model: str,
    embed_provider: str = "gemini",
    vectordb_dir: Optional[Path] = None,
    vector_db_provider: str = "chroma",
) -> Tuple[bool, list[str]]:
    """Return (ok, warnings/errors) for production deployment settings."""
    issues: list[str] = []
    if not production_mode:
        return True, issues

    default_secrets = {
        "FLASK_SECRET_KEY": "agent-api-secret-2025",
        "TOKEN_PEPPER": "agent-api-secret-2025",
    }
    for env_key, default_val in default_secrets.items():
        if os.getenv(env_key, default_val) == default_val:
            issues.append(
                f"{env_key} is still the default value; set a strong secret in production"
            )

    if cache_backend not in {"redis", "mysql"}:
        issues.append(
            "CACHE_BACKEND must be 'redis' or 'mysql' when PRODUCTION_MODE=true "
            "(use mysql when Redis is unavailable)"
        )

    if not semantic_cache_enabled:
        issues.append(
            "SEMANTIC_CACHE_ENABLED is false; enable in production "
            "to cut repeat LLM cost"
        )

    if embed_provider == "gemini":
        issues.append(
            "EMBED_PROVIDER=gemini uses paid embedding API; "
            "set EMBED_PROVIDER=local after BGE reindex (Iteration 3)"
        )
    elif embed_provider == "local":
        if vector_db_provider == "qdrant":
            try:
                from qdrant_store import check_qdrant_health

                q_status = check_qdrant_health()
                if q_status != "connected":
                    issues.append(
                        f"VECTOR_DB_PROVIDER=qdrant but Qdrant is {q_status}; "
                        "run migrate_chroma_to_qdrant.py"
                    )
            except Exception:
                issues.append(
                    "VECTOR_DB_PROVIDER=qdrant but qdrant-client is unavailable"
                )
        elif vectordb_dir is None or not vectordb_dir.exists():
            issues.append(
                "EMBED_PROVIDER=local but BGE vector store is missing; "
                "run on_the_porch/rag stuff/reindex_bge_vectordb.py"
            )

    model_pairs = (
        ("GEMINI_MODEL", gemini_model),
        ("GEMINI_FAST_MODEL", gemini_fast_model),
    )
    for label, model in model_pairs:
        name = (model or "").lower()
        if "pro" in name and "flash" not in name:
            issues.append(
                f"{label}={model!r} is expensive for production; "
                "prefer gemini-2.5-flash-lite"
            )

    ok = not any("must be" in msg or "missing" in msg for msg in issues)
    return ok, issues


def check_311_feed_balance(get_db_connection) -> Dict[str, Any]:
    """Warn when only the Creatio 311 feed is present (prod undercount ~85%)."""
    dorchester = (
        "(`neighborhood` = 'Dorchester' OR `police_district` = 'C11')"
    )
    month_filter = (
        "open_date >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01') "
        "AND open_date < DATE_FORMAT(CURDATE(), '%Y-%m-01')"
    )
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS c FROM service_requests_311")
        total = int(cur.fetchone()["c"])
        cur.execute(
            """
            SELECT
              CASE WHEN case_id REGEXP '^[0-9]+$' THEN 'legacy_2026' ELSE 'creatio_bcs' END AS feed,
              COUNT(*) AS n
            FROM service_requests_311
            GROUP BY feed
            """
        )
        split = {row["feed"]: int(row["n"]) for row in cur.fetchall()}
        cur.execute(
            f"SELECT COUNT(*) AS c FROM service_requests_311 "
            f"WHERE {dorchester} AND {month_filter}"
        )
        last_month = int(cur.fetchone()["c"])
        cur.execute(
            f"""
            SELECT
              CASE WHEN case_id REGEXP '^[0-9]+$' THEN 'legacy_2026' ELSE 'creatio_bcs' END AS feed,
              COUNT(*) AS n
            FROM service_requests_311
            WHERE {dorchester} AND {month_filter}
            GROUP BY feed
            """
        )
        last_month_split = {row["feed"]: int(row["n"]) for row in cur.fetchall()}
        cur.close()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "error": str(exc)}

    legacy = split.get("legacy_2026", 0)
    creatio = split.get("creatio_bcs", 0)
    lm_legacy = last_month_split.get("legacy_2026", 0)
    lm_creatio = last_month_split.get("creatio_bcs", 0)

    status = "ok"
    warnings: list[str] = []
    if total > 500 and legacy == 0:
        status = "degraded"
        warnings.append(
            "service_requests_311 has Creatio rows but zero legacy_2026 rows; "
            "add 311_service_requests_2026_legacy to boston_datasets_config.json "
            "and run: boston_data_sync.py --dataset 311_service_requests_2026_legacy --full"
        )
    elif last_month > 0 and lm_legacy == 0 and lm_creatio > 0:
        status = "degraded"
        warnings.append(
            f"Last month's Dorchester 311 count is Creatio-only ({lm_creatio}); "
            "expect ~3,100/month when legacy_2026 is synced."
        )

    return {
        "status": status,
        "total_rows": total,
        "by_feed": split,
        "dorchester_last_month": last_month,
        "dorchester_last_month_by_feed": last_month_split,
        "warnings": warnings,
    }
