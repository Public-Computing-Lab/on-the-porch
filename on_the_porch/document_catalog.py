"""MySQL-backed civic document/chunk catalog (vector store remains rebuildable)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_id_for(source_id: str, content_sha: str) -> str:
    return f"doc:{source_id}:{content_sha[:24]}"


def chunk_id_for(document_id: str, ordinal: int, content_sha: str) -> str:
    return f"chunk:{document_id}:{ordinal}:{content_sha[:16]}"


def upsert_document(
    conn,
    *,
    source_id: str,
    title: str,
    doc_type: str,
    body_text: str,
    publisher: Optional[str] = None,
    official_url: Optional[str] = None,
    geography: Optional[str] = None,
    publication_date: Optional[date] = None,
    index_version: Optional[str] = None,
    limitations: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    chunks: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Tuple[str, int]:
    """Upsert one document and replace its chunk rows. Returns (document_id, chunk_count)."""
    content_sha = _sha256_text(body_text)
    doc_id = document_id_for(source_id, content_sha)
    chunk_rows = list(chunks or [])
    if not chunk_rows and body_text.strip():
        chunk_rows = [{"text": body_text.strip(), "section_path": None, "page_number": None}]
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO civic_documents (
                document_id, source_id, title, publisher, official_url, geography,
                publication_date, doc_type, content_sha256, chunk_count, index_version,
                status, limitations, metadata_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s)
            ON DUPLICATE KEY UPDATE
                title = VALUES(title),
                publisher = VALUES(publisher),
                official_url = VALUES(official_url),
                geography = VALUES(geography),
                publication_date = VALUES(publication_date),
                doc_type = VALUES(doc_type),
                chunk_count = VALUES(chunk_count),
                index_version = VALUES(index_version),
                status = 'active',
                limitations = VALUES(limitations),
                metadata_json = VALUES(metadata_json),
                updated_at = UTC_TIMESTAMP()
            """,
            (
                doc_id,
                source_id,
                title[:512],
                publisher,
                official_url,
                geography,
                publication_date,
                doc_type[:64],
                content_sha,
                len(chunk_rows),
                index_version,
                limitations,
                json.dumps(dict(metadata or {}), ensure_ascii=True)[:8000],
            ),
        )
        cursor.execute(
            "UPDATE civic_document_chunks SET status = 'replaced' WHERE document_id = %s",
            (doc_id,),
        )
        for ordinal, chunk in enumerate(chunk_rows):
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            c_sha = _sha256_text(text)
            c_id = chunk_id_for(doc_id, ordinal, c_sha)
            cursor.execute(
                """
                INSERT INTO civic_document_chunks (
                    chunk_id, document_id, source_id, ordinal, content_sha256,
                    vector_id, section_path, page_number, char_count, index_version, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')
                ON DUPLICATE KEY UPDATE
                    content_sha256 = VALUES(content_sha256),
                    vector_id = VALUES(vector_id),
                    section_path = VALUES(section_path),
                    page_number = VALUES(page_number),
                    char_count = VALUES(char_count),
                    index_version = VALUES(index_version),
                    status = 'active'
                """,
                (
                    c_id,
                    doc_id,
                    source_id,
                    ordinal,
                    c_sha,
                    chunk.get("vector_id"),
                    chunk.get("section_path"),
                    chunk.get("page_number"),
                    len(text),
                    index_version,
                ),
            )
        conn.commit()
        return doc_id, len(chunk_rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def catalog_stats(conn) -> Dict[str, Any]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM civic_documents WHERE status = 'active') AS documents,
              (SELECT COUNT(*) FROM civic_document_chunks WHERE status = 'active') AS chunks,
              (SELECT COUNT(DISTINCT source_id) FROM civic_documents WHERE status = 'active') AS sources_with_docs
            """
        )
        row = cursor.fetchone() or {}
        return {key: int(row.get(key) or 0) for key in row}
    finally:
        cursor.close()


def list_recent_documents(conn, *, limit: int = 50) -> List[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT document_id, source_id, title, publisher, official_url, doc_type,
                   publication_date, chunk_count, index_version, status, ingested_at
            FROM civic_documents
            ORDER BY ingested_at DESC
            LIMIT %s
            """,
            (max(1, min(int(limit), 200)),),
        )
        rows = cursor.fetchall() or []
        for row in rows:
            for key in ("publication_date", "ingested_at"):
                value = row.get(key)
                if isinstance(value, (datetime, date)):
                    row[key] = value.isoformat()
        return rows
    finally:
        cursor.close()


def verify_index_parity(conn, *, expected_chunks: Optional[int] = None) -> Dict[str, Any]:
    stats = catalog_stats(conn)
    result = {
        "mysql_documents": stats.get("documents", 0),
        "mysql_chunks": stats.get("chunks", 0),
        "expected_chunks": expected_chunks,
        "parity_ok": True,
        "issues": [],
    }
    if expected_chunks is not None and int(expected_chunks) != int(stats.get("chunks") or 0):
        result["parity_ok"] = False
        result["issues"].append("mysql_chunk_count_mismatch")
    return result
