"""Coherent Phase 5 ingestion lifecycle over existing connectors.

Lifecycle: register → fetch/parse → validate → normalize → dedupe → MySQL
metadata → optional vector index update → freshness → success/failure record.

The vector store is never authoritative. Public chat vector writes stay disabled.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from document_catalog import catalog_stats, upsert_document, verify_index_parity
from source_registry import (
    ensure_registry_seeded,
    list_sources,
    mark_ingest_result,
    record_operator_action,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_DIRS = (
    ROOT / "data" / "phase5_corpus" / "policy",
    ROOT / "data" / "phase5_corpus" / "zoning",
    ROOT / "data" / "phase5_corpus" / "meetings",
    ROOT / "data" / "phase5_corpus" / "news",
    ROOT / "data" / "phase5_corpus" / "community",
    ROOT / "public" / "Policies",
)

INDEX_VERSION = "phase5-chunk-v1"
MAX_FILE_BYTES = 2_000_000
MAX_PASSAGE_CHARS = 1800
ALLOWED_SUFFIXES = {".txt", ".md", ".html", ".htm", ".pdf", ".csv", ".json"}

# Operator-registered fetch allowlist only (no arbitrary resident URLs).
HTTP_ALLOWLIST_HOSTS = frozenset(
    {
        "www.boston.gov",
        "boston.gov",
        "data.boston.gov",
        "www.bostonplans.org",
        "bostonplans.org",
    }
)


@dataclass(frozen=True)
class IngestionResult:
    source_id: str
    status: str
    run_id: str
    documents_seen: int = 0
    documents_upserted: int = 0
    chunks_upserted: int = 0
    error: Optional[str] = None
    index_version: Optional[str] = None
    details: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "run_id": self.run_id,
            "documents_seen": self.documents_seen,
            "documents_upserted": self.documents_upserted,
            "chunks_upserted": self.chunks_upserted,
            "error": self.error,
            "index_version": self.index_version,
            "details": dict(self.details or {}),
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_id(source_id: str) -> str:
    return f"{source_id}:{int(time.time() * 1000)}:{uuid.uuid4().hex[:8]}"


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk_text(text: str, *, max_chars: int = MAX_PASSAGE_CHARS) -> List[str]:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]
    passages: List[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + max_chars)
        if end < len(cleaned):
            split_at = cleaned.rfind(". ", start, end)
            if split_at > start + max_chars // 3:
                end = split_at + 1
        passages.append(cleaned[start:end].strip())
        start = end
    return [p for p in passages if p]


def _read_file(path: Path) -> Tuple[str, str]:
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise ValueError("PDF support unavailable") from exc
        reader = PdfReader(str(path))
        pages = []
        for index, page in enumerate(reader.pages):
            page_text = (page.extract_text() or "").strip()
            if page_text:
                pages.append(f"[page {index + 1}]\n{page_text}")
        return "\n\n".join(pages), "pdf"
    text = raw.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        return _strip_html(text), "html"
    return text, suffix.lstrip(".") or "text"


def _record_run(
    conn,
    *,
    result: IngestionResult,
    job_name: str,
    operator_user_id: Optional[int] = None,
) -> None:
    import json

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO source_ingestion_runs (
                source_id, run_id, job_name, status, started_at, finished_at,
                documents_seen, documents_upserted, chunks_upserted, error_message,
                index_version, operator_user_id, details_json
            ) VALUES (
                %s,%s,%s,%s,UTC_TIMESTAMP(),UTC_TIMESTAMP(),%s,%s,%s,%s,%s,%s,%s
            )
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                finished_at = UTC_TIMESTAMP(),
                documents_seen = VALUES(documents_seen),
                documents_upserted = VALUES(documents_upserted),
                chunks_upserted = VALUES(chunks_upserted),
                error_message = VALUES(error_message),
                index_version = VALUES(index_version),
                details_json = VALUES(details_json)
            """,
            (
                result.source_id,
                result.run_id,
                job_name,
                result.status,
                result.documents_seen,
                result.documents_upserted,
                result.chunks_upserted,
                (result.error or None),
                result.index_version,
                operator_user_id,
                json.dumps(dict(result.details or {}), ensure_ascii=True)[:8000],
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def ingest_local_directory(
    conn,
    *,
    source_id: str,
    directories: Sequence[Path],
    doc_type: str,
    publisher: Optional[str] = None,
    operator_user_id: Optional[int] = None,
    actor_email: Optional[str] = None,
    write_vectors: bool = False,
) -> IngestionResult:
    """Ingest allowlisted local files into MySQL catalog (and optionally Chroma)."""
    run_id = _run_id(source_id)
    seen = 0
    upserted = 0
    chunks_total = 0
    files: List[Path] = []
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES:
                files.append(path)
    try:
        vector_docs = []
        for path in files:
            seen += 1
            body, fmt = _read_file(path)
            if not body.strip():
                continue
            passages = _chunk_text(body)
            if not passages:
                continue
            title = path.stem.replace("_", " ").replace("-", " ").strip() or path.name
            doc_id, chunk_count = upsert_document(
                conn,
                source_id=source_id,
                title=title,
                doc_type=doc_type,
                body_text=body,
                publisher=publisher,
                official_url=None,
                geography="Dorchester",
                index_version=INDEX_VERSION,
                metadata={"path": str(path), "format": fmt},
                chunks=[
                    {"text": passage, "section_path": f"passage-{index + 1}"}
                    for index, passage in enumerate(passages)
                ],
            )
            upserted += 1
            chunks_total += chunk_count
            if write_vectors:
                from langchain_core.documents import Document  # type: ignore

                for index, passage in enumerate(passages):
                    vector_docs.append(
                        Document(
                            page_content=passage,
                            metadata={
                                "source": path.name,
                                "doc_type": doc_type,
                                "source_id": source_id,
                                "document_id": doc_id,
                                "chunk_ordinal": index,
                                "index_version": INDEX_VERSION,
                            },
                        )
                    )
        if write_vectors and vector_docs:
            _upsert_chroma_documents(vector_docs)
        result = IngestionResult(
            source_id=source_id,
            status="success",
            run_id=run_id,
            documents_seen=seen,
            documents_upserted=upserted,
            chunks_upserted=chunks_total,
            index_version=INDEX_VERSION,
            details={"directories": [str(p) for p in directories if p.exists()]},
        )
        mark_ingest_result(
            conn,
            source_id,
            success=True,
            index_version=INDEX_VERSION,
            chunk_count=chunks_total,
        )
    except Exception as exc:  # noqa: BLE001
        result = IngestionResult(
            source_id=source_id,
            status="failed",
            run_id=run_id,
            documents_seen=seen,
            documents_upserted=upserted,
            chunks_upserted=chunks_total,
            error=str(exc)[:500],
            index_version=INDEX_VERSION,
        )
        mark_ingest_result(conn, source_id, success=False, error=str(exc)[:500])
    _record_run(conn, result=result, job_name="local_directory", operator_user_id=operator_user_id)
    record_operator_action(
        conn,
        action="ingest_local_directory",
        target_type="source",
        target_id=source_id,
        actor_user_id=operator_user_id,
        actor_email=actor_email,
        details=result.to_dict(),
    )
    return result


def _upsert_chroma_documents(documents: Sequence[Any]) -> None:
    """Best-effort Chroma upsert using existing retrieval helpers."""
    import sys

    rag_dir = ROOT / "on_the_porch" / "rag stuff"
    if str(rag_dir) not in sys.path:
        sys.path.insert(0, str(rag_dir))
    from langchain_community.vectorstores import Chroma  # type: ignore
    from retrieval import get_embeddings, resolve_vectordb_dir  # type: ignore

    vectordb_dir = resolve_vectordb_dir()
    embeddings = get_embeddings()
    store = Chroma(
        collection_name="langchain",
        persist_directory=str(vectordb_dir),
        embedding_function=embeddings,
    )
    store.add_documents(list(documents))
    if hasattr(store, "persist"):
        store.persist()


def sync_structured_source_freshness(conn, source_id: str) -> IngestionResult:
    """Mark structured MySQL sources healthy from civic_source_status / table counts."""
    run_id = _run_id(source_id)
    cursor = conn.cursor()
    try:
        if source_id.endswith("service_requests_311"):
            cursor.execute("SELECT COUNT(*) FROM service_requests_311")
            count = int(cursor.fetchone()[0])
            key = "service_requests_311"
        elif source_id.endswith("crime_incident_reports"):
            cursor.execute("SELECT COUNT(*) FROM crime_incident_reports")
            count = int(cursor.fetchone()[0])
            key = "crime_incident_reports"
        elif source_id.endswith("shootings"):
            try:
                cursor.execute("SELECT COUNT(*) FROM shootings")
                count = int(cursor.fetchone()[0])
            except Exception:
                count = 0
            key = "shootings"
        elif source_id.endswith("weekly_events"):
            cursor.execute("SELECT COUNT(*) FROM weekly_events")
            count = int(cursor.fetchone()[0])
            key = "weekly_events"
        else:
            raise ValueError(f"unsupported structured source {source_id}")
        if count <= 0:
            raise ValueError(f"no rows for {key}")
        result = IngestionResult(
            source_id=source_id,
            status="success",
            run_id=run_id,
            documents_seen=count,
            documents_upserted=0,
            chunks_upserted=0,
            index_version=None,
            details={"mysql_table": key, "row_count": count},
        )
        mark_ingest_result(conn, source_id, success=True, chunk_count=count)
        _record_run(conn, result=result, job_name="structured_sync")
        return result
    except Exception as exc:  # noqa: BLE001
        result = IngestionResult(
            source_id=source_id,
            status="failed",
            run_id=run_id,
            error=str(exc)[:500],
        )
        mark_ingest_result(conn, source_id, success=False, error=str(exc)[:500])
        _record_run(conn, result=result, job_name="structured_sync")
        return result
    finally:
        cursor.close()


def catalog_existing_chroma_into_mysql(conn) -> IngestionResult:
    """Import Chroma metadata into MySQL catalog without rewriting vectors."""
    import sys

    run_id = _run_id("chroma.catalog_sync")
    rag_dir = ROOT / "on_the_porch" / "rag stuff"
    if str(rag_dir) not in sys.path:
        sys.path.insert(0, str(rag_dir))
    try:
        import chromadb
        from retrieval import resolve_vectordb_dir  # type: ignore

        candidates = [
            Path(resolve_vectordb_dir()),
            ROOT.parent / "ml-misi-community-sentiment" / "vectordb_new",
            ROOT / "vectordb_new",
            Path("/Users/jeet/github/ml-misi-community-sentiment/vectordb_new"),
        ]
        vectordb_path = None
        collection = None
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                client = chromadb.PersistentClient(path=str(candidate))
                col = client.get_or_create_collection("langchain")
                if col.count() > 0:
                    vectordb_path = candidate
                    collection = col
                    break
                if vectordb_path is None:
                    vectordb_path = candidate
                    collection = col
            except Exception:
                continue
        if collection is None or vectordb_path is None:
            raise RuntimeError("no chroma collection available")
        offset = 0
        seen = 0
        upserted = 0
        chunks_total = 0
        while True:
            batch = collection.get(
                limit=100,
                offset=offset,
                include=["documents", "metadatas"],
            )
            docs = batch.get("documents") or []
            metas = batch.get("metadatas") or []
            ids = batch.get("ids") or []
            if not docs:
                break
            for index, body in enumerate(docs):
                seen += 1
                meta = metas[index] if index < len(metas) else {}
                text = (body or "").strip()
                if not text:
                    continue
                doc_type = str(meta.get("doc_type") or "policy")
                source_name = str(meta.get("source") or "chroma")
                family_source = {
                    "policy": "boston.policy_corpus",
                    "transcript": "dorchester.meeting_transcripts",
                    "news": "dorchester.local_news",
                    "calendar_event": "dorchester.local_news",
                    "community_note": "dorchester.community_documents",
                }.get(doc_type, "boston.policy_corpus")
                _, chunk_count = upsert_document(
                    conn,
                    source_id=family_source,
                    title=source_name,
                    doc_type=doc_type,
                    body_text=text,
                    publisher=str(meta.get("publisher") or "Indexed corpus"),
                    index_version=INDEX_VERSION,
                    metadata={"chroma_id": ids[index] if index < len(ids) else None, **dict(meta)},
                    chunks=[{
                        "text": text,
                        "vector_id": ids[index] if index < len(ids) else None,
                        "section_path": meta.get("section"),
                    }],
                )
                upserted += 1
                chunks_total += chunk_count
            offset += len(docs)
            if len(docs) < 100:
                break
        for source_id in {
            "boston.policy_corpus",
            "dorchester.meeting_transcripts",
            "dorchester.local_news",
            "dorchester.community_documents",
        }:
            mark_ingest_result(
                conn,
                source_id,
                success=True,
                index_version=INDEX_VERSION,
                chunk_count=chunks_total,
            )
        result = IngestionResult(
            source_id="chroma.catalog_sync",
            status="success",
            run_id=run_id,
            documents_seen=seen,
            documents_upserted=upserted,
            chunks_upserted=chunks_total,
            index_version=INDEX_VERSION,
            details={"vectordb": str(vectordb_path)},
        )
    except Exception as exc:  # noqa: BLE001
        result = IngestionResult(
            source_id="chroma.catalog_sync",
            status="failed",
            run_id=run_id,
            error=str(exc)[:500],
        )
    _record_run(conn, result=result, job_name="chroma_catalog_sync")
    return result


def bootstrap_phase5(
    conn,
    *,
    include_local_corpus: bool = True,
    write_vectors: bool = False,
) -> Dict[str, Any]:
    """Seed registry, sync structured sources, catalog Chroma, ingest local corpus."""
    seeded = ensure_registry_seeded(conn)
    results: List[Dict[str, Any]] = []
    for source_id in (
        "boston.crime_incident_reports",
        "boston.service_requests_311",
        "boston.shootings",
        "dorchester.weekly_events",
    ):
        results.append(sync_structured_source_freshness(conn, source_id).to_dict())
    results.append(catalog_existing_chroma_into_mysql(conn).to_dict())
    if include_local_corpus:
        mapping = {
            "boston.policy_corpus": (
                "policy",
                [ROOT / "data" / "phase5_corpus" / "policy", ROOT / "public" / "Policies"],
            ),
            "boston.zoning_references": (
                "policy",
                [ROOT / "data" / "phase5_corpus" / "zoning"],
            ),
            "dorchester.meeting_transcripts": (
                "transcript",
                [ROOT / "data" / "phase5_corpus" / "meetings"],
            ),
            "dorchester.local_news": (
                "news",
                [ROOT / "data" / "phase5_corpus" / "news"],
            ),
            "dorchester.community_documents": (
                "transcript",
                [ROOT / "data" / "phase5_corpus" / "community"],
            ),
        }
        for source_id, (doc_type, directories) in mapping.items():
            results.append(
                ingest_local_directory(
                    conn,
                    source_id=source_id,
                    directories=directories,
                    doc_type=doc_type,
                    write_vectors=write_vectors,
                ).to_dict()
            )
    parity = verify_index_parity(conn)
    return {
        "seeded_rows": seeded,
        "sources": list_sources(conn),
        "runs": results,
        "catalog": catalog_stats(conn),
        "parity": parity,
    }


def list_ingestion_runs(conn, *, limit: int = 50) -> List[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, source_id, run_id, job_name, status, started_at, finished_at,
                   documents_seen, documents_upserted, chunks_upserted, error_message,
                   index_version
            FROM source_ingestion_runs
            ORDER BY id DESC
            LIMIT %s
            """,
            (max(1, min(int(limit), 200)),),
        )
        rows = cursor.fetchall() or []
        for row in rows:
            for key in ("started_at", "finished_at"):
                value = row.get(key)
                if isinstance(value, datetime):
                    row[key] = value.isoformat()
        return rows
    finally:
        cursor.close()
