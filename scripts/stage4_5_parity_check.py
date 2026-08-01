#!/usr/bin/env python3
"""Report a redacted local runtime fingerprint and civic/vector source coverage."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
for value in (ROOT, ROOT / "api", ROOT / "on_the_porch", ROOT / "on_the_porch" / "rag stuff"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

import config  # noqa: E402


SAFE_LOCAL_DATABASES = {"sentiment_demo", "stage45_main", "stage45_dev"}
CATEGORY_MARKERS = {
    "city_council_minutes": ("city council", "minutes"),
    "city_council_transcripts": ("city council", "transcript"),
    "transportation": ("transportation", "transit", "traffic", "mobility", "mbta"),
    "planning_development": ("planning", "development", "zoning", "housing"),
    "newsletters": ("newsletter",),
    "news": ("news", "reporter"),
    "boston_gov": ("boston.gov", "boston gov"),
    "other_meeting_transcripts": ("transcript", "meeting"),
    "community_transcripts": ("community", "transcript"),
}


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _sha256(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory_fingerprint(path: Path) -> Dict[str, Any]:
    files = sorted(item for item in path.rglob("*") if item.is_file()) if path.exists() else []
    digest = hashlib.sha256()
    total_bytes = 0
    for item in files:
        relative = item.relative_to(path).as_posix()
        stat = item.stat()
        digest.update(relative.encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        total_bytes += stat.st_size
    return {
        "exists": path.exists(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "structure_sha256": digest.hexdigest(),
    }


def _database_connection():
    import mysql.connector

    host = str(config.MYSQL_HOST).lower()
    database = str(config.MYSQL_DB)
    if host not in {"localhost", "127.0.0.1", "::1"} or database not in SAFE_LOCAL_DATABASES:
        raise RuntimeError(
            "Stage 4.5 parity checks are restricted to the local demo databases; "
            f"got host={host!r}, database={database!r}."
        )
    return mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DB,
    )


def _table_count(cursor, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) AS count FROM `{table}`")
    row = cursor.fetchone() or {}
    return int(row.get("count") or 0)


def _database_inventory() -> Dict[str, Any]:
    connection = _database_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SHOW TABLES")
        tables = sorted(next(iter(row.values())) for row in cursor.fetchall())
        counts = {
            table: _table_count(cursor, table)
            for table in (
                "service_requests_311",
                "crime_incident_reports",
                "weekly_events",
                "civic_documents",
                "civic_document_chunks",
                "admin_knowledge",
            )
            if table in tables
        }

        civic_rows: List[Dict[str, Any]] = []
        if "civic_documents" in tables:
            cursor.execute("SELECT * FROM civic_documents")
            civic_rows = cursor.fetchall()

        categories: Dict[str, Dict[str, Any]] = {}
        for category, markers in CATEGORY_MARKERS.items():
            matches = []
            for row in civic_rows:
                text = " ".join(str(value or "") for value in row.values()).lower()
                if category in {
                    "city_council_minutes",
                    "city_council_transcripts",
                    "other_meeting_transcripts",
                    "community_transcripts",
                }:
                    matched = all(marker in text for marker in markers)
                else:
                    matched = any(marker in text for marker in markers)
                if matched:
                    matches.append(row)
            dates = sorted(
                str(row.get("published_at") or row.get("source_date") or "")
                for row in matches
                if row.get("published_at") or row.get("source_date")
            )
            categories[category] = {
                "database_record_count": len(matches),
                "date_range": [dates[0], dates[-1]] if dates else None,
                "latest_source_date": dates[-1] if dates else None,
                "stable_ids_present": bool(matches) and all(row.get("id") is not None for row in matches),
                "source_labels": sorted({str(row.get("source_type") or "") for row in matches if row.get("source_type")})[:10],
                "publisher_labels": sorted({str(row.get("publisher") or "") for row in matches if row.get("publisher")})[:10],
                "sample_titles": [str(row.get("title") or "") for row in matches[:3]],
            }
        categories["311"] = {"database_record_count": counts.get("service_requests_311", 0)}
        categories["crime"] = {"database_record_count": counts.get("crime_incident_reports", 0)}
        categories["events"] = {"database_record_count": counts.get("weekly_events", 0)}
        return {
            "database_category": "local_demo",
            "database_name": config.MYSQL_DB,
            "schema_table_count": len(tables),
            "tables": counts,
            "categories": categories,
        }
    finally:
        cursor.close()
        connection.close()


def _vector_category(text: str) -> List[str]:
    categories = []
    for category, markers in CATEGORY_MARKERS.items():
        if category in {
            "city_council_minutes",
            "city_council_transcripts",
            "other_meeting_transcripts",
            "community_transcripts",
        }:
            matched = all(marker in text for marker in markers)
        else:
            matched = any(marker in text for marker in markers)
        if matched:
            categories.append(category)
    return categories


def _vector_inventory() -> Dict[str, Any]:
    path = Path(config.ACTIVE_VECTORDB_DIR).resolve()
    output: Dict[str, Any] = {
        "provider": config.VECTOR_DB_PROVIDER,
        "path_fingerprint": _directory_fingerprint(path),
        "collection": None,
        "chunk_count": 0,
        "document_count": 0,
        "categories": {},
        "doc_type_counts": {},
        "metadata_fields": [],
    }
    if config.VECTOR_DB_PROVIDER != "chroma" or not path.exists():
        return output

    import chromadb

    client = chromadb.PersistentClient(path=str(path))
    collections = client.list_collections()
    if not collections:
        return output
    collection = collections[0]
    data = collection.get(include=["documents", "metadatas"])
    ids = list(data.get("ids") or [])
    documents = list(data.get("documents") or [])
    metadatas = list(data.get("metadatas") or [])
    output["collection"] = collection.name
    output["chunk_count"] = len(ids)
    document_keys = set()
    category_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    metadata_fields = set()
    doc_type_counts: Dict[str, int] = defaultdict(int)
    for item_id, document, raw_meta in zip(ids, documents, metadatas):
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        metadata_fields.update(meta.keys())
        doc_type_counts[str(meta.get("doc_type") or "unknown")] += 1
        key = meta.get("document_id") or meta.get("doc_id") or meta.get("source") or item_id
        document_keys.add(str(key))
        text = " ".join([str(document or ""), *(str(value or "") for value in meta.values())]).lower()
        for category in _vector_category(text):
            category_rows[category].append({"id": item_id, "metadata": meta})
    output["document_count"] = len(document_keys)
    output["metadata_fields"] = sorted(metadata_fields)
    output["doc_type_counts"] = dict(sorted(doc_type_counts.items()))
    output["categories"] = {
        category: {
            "vector_chunk_count": len(rows),
            "vector_document_count": len({str(row["metadata"].get("document_id") or row["metadata"].get("source") or row["id"]) for row in rows}),
            "source_labels": sorted({str(row["metadata"].get("source_type") or row["metadata"].get("doc_type") or "") for row in rows})[:10],
            "publisher_labels": sorted({str(row["metadata"].get("publisher") or "") for row in rows if row["metadata"].get("publisher")})[:10],
            "sample_titles": [str(row["metadata"].get("title") or row["metadata"].get("source") or "") for row in rows[:3]],
        }
        for category, rows in sorted(category_rows.items())
    }
    return output


def build_report() -> Dict[str, Any]:
    database = _database_inventory()
    vector = _vector_inventory()
    council_db = database["categories"]["city_council_minutes"]["database_record_count"]
    council_vectors = vector.get("categories", {}).get("city_council_minutes", {}).get("vector_chunk_count", 0)
    warnings = []
    if council_db == 0:
        warnings.append("Local civic data contains no identifiable Boston City Council minutes.")
    if council_vectors == 0:
        warnings.append("The active vector collection contains no identifiable Boston City Council minute chunks.")
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "runtime": {
            "python": sys.version.split()[0],
            "model": config.GEMINI_MODEL,
            "summary_model": config.GEMINI_SUMMARY_MODEL,
            "fallback_model": config.GEMINI_FALLBACK_MODEL,
            "embedding_provider": config.EMBED_PROVIDER,
            "embedding_model": config.LOCAL_EMBED_MODEL if config.EMBED_PROVIDER == "local" else config.GEMINI_EMBED_MODEL,
            "vector_provider": config.VECTOR_DB_PROVIDER,
            "feature_flags": {
                "bm25_hybrid_enabled": config.BM25_HYBRID_ENABLED,
                "parallel_retrieval": config.PARALLEL_RETRIEVAL,
            },
            "module_hashes": {
                name: _sha256(ROOT / path)
                for name, path in {
                    "intent_router": "on_the_porch/intent_router.py",
                    "plan_builder": "on_the_porch/plan_builder.py",
                    "plan_executor": "on_the_porch/plan_executor.py",
                    "retrieval": "on_the_porch/rag stuff/retrieval.py",
                    "answer_synthesizer": "on_the_porch/answer_synthesizer.py",
                    "config": "config.py",
                    "dependency_lock": "requirements.txt",
                }.items()
            },
        },
        "database": database,
        "vector": vector,
        "warnings": warnings,
        "council_minutes_ready": not warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    report = build_report()
    rendered = json.dumps(report, indent=2, default=_json_value, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["council_minutes_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
