"""Authoritative civic source registry for On the Porch (Phase 5).

MySQL ``source_registry`` is the single product inventory. Structured freshness
for 311/crime continues to use ``civic_source_status``; this module links those
keys and document corpora into one operator-facing contract.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

REGISTRY_SEED_PATH = Path(__file__).resolve().parent / "source_registry_seed.json"


@dataclass(frozen=True)
class SourceFamilySpec:
    source_id: str
    family: str
    title: str
    authority: str
    source_type: str
    publisher: str
    official_url: Optional[str]
    geography: str
    update_frequency: str
    ingestion_method: str
    content_format: str
    freshness_policy: str
    retention_policy: str
    dedupe_key_template: str
    parsing_strategy: str
    indexing_eligible: bool
    citation_policy: str
    limitations: str
    connector_id: str


def load_seed_inventory(path: Optional[Path] = None) -> List[SourceFamilySpec]:
    seed_path = path or REGISTRY_SEED_PATH
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    items: List[SourceFamilySpec] = []
    for row in raw.get("sources") or []:
        items.append(
            SourceFamilySpec(
                source_id=str(row["source_id"]),
                family=str(row["family"]),
                title=str(row["title"]),
                authority=str(row["authority"]),
                source_type=str(row["source_type"]),
                publisher=str(row.get("publisher") or row["authority"]),
                official_url=row.get("official_url"),
                geography=str(row.get("geography") or "Dorchester"),
                update_frequency=str(row.get("update_frequency") or "unknown"),
                ingestion_method=str(row["ingestion_method"]),
                content_format=str(row.get("content_format") or "mixed"),
                freshness_policy=str(row.get("freshness_policy") or ""),
                retention_policy=str(row.get("retention_policy") or ""),
                dedupe_key_template=str(row.get("dedupe_key_template") or "{source_id}:{content_sha256}"),
                parsing_strategy=str(row.get("parsing_strategy") or "default"),
                indexing_eligible=bool(row.get("indexing_eligible", False)),
                citation_policy=str(row.get("citation_policy") or ""),
                limitations=str(row.get("limitations") or ""),
                connector_id=str(row.get("connector_id") or "none"),
            )
        )
    return items


def ensure_registry_seeded(conn, *, seed_path: Optional[Path] = None) -> int:
    """Insert missing seed rows. Never overwrite operator-disabled state."""
    specs = load_seed_inventory(seed_path)
    cursor = conn.cursor()
    inserted = 0
    try:
        for spec in specs:
            cursor.execute(
                "SELECT source_id FROM source_registry WHERE source_id = %s",
                (spec.source_id,),
            )
            if cursor.fetchone():
                continue
            cursor.execute(
                """
                INSERT INTO source_registry (
                    source_id, family, title, authority, source_type, publisher,
                    official_url, geography, update_frequency, ingestion_method,
                    content_format, freshness_policy, retention_policy,
                    dedupe_key_template, parsing_strategy, indexing_eligible,
                    citation_policy, limitations, connector_id, enabled, status
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,'registered'
                )
                """,
                (
                    spec.source_id,
                    spec.family,
                    spec.title,
                    spec.authority,
                    spec.source_type,
                    spec.publisher,
                    spec.official_url,
                    spec.geography,
                    spec.update_frequency,
                    spec.ingestion_method,
                    spec.content_format,
                    spec.freshness_policy,
                    spec.retention_policy,
                    spec.dedupe_key_template,
                    spec.parsing_strategy,
                    1 if spec.indexing_eligible else 0,
                    spec.citation_policy,
                    spec.limitations,
                    spec.connector_id,
                ),
            )
            inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
    return inserted


def list_sources(
    conn,
    *,
    enabled_only: bool = False,
    family: Optional[str] = None,
) -> List[Dict[str, Any]]:
    clauses = []
    params: List[Any] = []
    if enabled_only:
        clauses.append("enabled = 1")
    if family:
        clauses.append("family = %s")
        params.append(family)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT source_id, family, title, authority, source_type, publisher,
                   official_url, geography, update_frequency, ingestion_method,
                   content_format, freshness_policy, retention_policy,
                   dedupe_key_template, parsing_strategy, indexing_eligible,
                   citation_policy, limitations, connector_id, enabled, status,
                   last_successful_ingest_at, last_attempt_at, last_error,
                   index_version, chunk_count, created_at, updated_at
            FROM source_registry
            {where}
            ORDER BY family, title
            """,
            params,
        )
        rows = cursor.fetchall() or []
        for row in rows:
            for key in (
                "last_successful_ingest_at",
                "last_attempt_at",
                "created_at",
                "updated_at",
            ):
                value = row.get(key)
                if isinstance(value, datetime):
                    row[key] = value.isoformat()
            row["indexing_eligible"] = bool(row.get("indexing_eligible"))
            row["enabled"] = bool(row.get("enabled"))
        return rows
    finally:
        cursor.close()


def get_source(conn, source_id: str) -> Optional[Dict[str, Any]]:
    rows = list_sources(conn)
    for row in rows:
        if row["source_id"] == source_id:
            return row
    return None


def set_source_enabled(conn, source_id: str, enabled: bool) -> bool:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE source_registry
            SET enabled = %s, status = CASE WHEN %s = 1 THEN 'registered' ELSE 'disabled' END
            WHERE source_id = %s
            """,
            (1 if enabled else 0, 1 if enabled else 0, source_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def mark_ingest_result(
    conn,
    source_id: str,
    *,
    success: bool,
    error: Optional[str] = None,
    index_version: Optional[str] = None,
    chunk_count: Optional[int] = None,
) -> None:
    cursor = conn.cursor()
    try:
        if success:
            cursor.execute(
                """
                UPDATE source_registry
                SET last_successful_ingest_at = UTC_TIMESTAMP(),
                    last_attempt_at = UTC_TIMESTAMP(),
                    last_error = NULL,
                    status = 'healthy',
                    index_version = COALESCE(%s, index_version),
                    chunk_count = COALESCE(%s, chunk_count)
                WHERE source_id = %s
                """,
                (index_version, chunk_count, source_id),
            )
        else:
            cursor.execute(
                """
                UPDATE source_registry
                SET last_attempt_at = UTC_TIMESTAMP(),
                    last_error = %s,
                    status = 'failed'
                WHERE source_id = %s
                """,
                ((error or "ingestion failed")[:1024], source_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def record_operator_action(
    conn,
    *,
    action: str,
    target_type: str,
    target_id: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    actor_email: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO operator_audit_log (
                actor_user_id, actor_email, action, target_type, target_id, details_json
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                actor_user_id,
                actor_email,
                action[:64],
                target_type[:64],
                (target_id or "")[:160] or None,
                json.dumps(details or {}, ensure_ascii=True)[:8000],
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def registry_health_summary(conn) -> Dict[str, Any]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(enabled = 1) AS enabled,
              SUM(status = 'healthy') AS healthy,
              SUM(status = 'failed') AS failed,
              SUM(status = 'disabled') AS disabled,
              SUM(indexing_eligible = 1) AS indexing_eligible
            FROM source_registry
            """
        )
        row = cursor.fetchone() or {}
        return {key: int(row.get(key) or 0) for key in row}
    finally:
        cursor.close()


def seed_as_dicts() -> List[Dict[str, Any]]:
    return [asdict(item) for item in load_seed_inventory()]
