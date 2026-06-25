"""Unified vector DB access for ingestion scripts (Iteration 4)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma

from langchain_core.documents import Document

from retrieval import get_embeddings, resolve_vectordb_dir


def vector_db_provider() -> str:
    return os.getenv("VECTOR_DB_PROVIDER", "chroma").strip().lower()


def ingest_collection_name() -> str:
    """Collection targeted by cron ingest (staging when blue-green is enabled)."""
    if vector_db_provider() != "qdrant":
        return "langchain"
    if os.getenv("QDRANT_INGEST_STAGING", "false").strip().lower() in ("1", "true", "yes"):
        from qdrant_store import QDRANT_STAGING_COLLECTION

        return QDRANT_STAGING_COLLECTION
    from qdrant_store import QDRANT_COLLECTION

    return QDRANT_COLLECTION


def open_ingest_vectordb(vectordb_dir: Path | None = None):
    """Return a vector store handle for ingestion (Chroma or Qdrant)."""
    if vector_db_provider() == "qdrant":
        from qdrant_store import QdrantVectorStore, get_qdrant_store

        collection = ingest_collection_name()
        return get_qdrant_store(collection_name=collection)

    directory = vectordb_dir or resolve_vectordb_dir()
    embeddings = get_embeddings()
    if directory.exists():
        return Chroma(
            persist_directory=str(directory),
            embedding_function=embeddings,
        )
    directory.mkdir(parents=True, exist_ok=True)
    return Chroma(
        persist_directory=str(directory),
        embedding_function=embeddings,
    )


def vectordb_get(store, where: Optional[dict] = None) -> dict:
    if hasattr(store, "get"):
        return store.get(where=where)
    return {"ids": [], "documents": [], "metadatas": []}


def vectordb_delete(store, ids: List[str]) -> None:
    if ids and hasattr(store, "delete"):
        store.delete(ids)


def vectordb_add_documents(
    store,
    documents: List[Document],
    ids: List[str],
) -> None:
    if vector_db_provider() == "qdrant":
        from qdrant_store import VectorDocument

        qdrant_docs = [
            VectorDocument(page_content=doc.page_content, metadata=dict(doc.metadata or {}))
            for doc in documents
        ]
        store.add_documents(qdrant_docs, ids=ids)
        return
    store.add_documents(documents=documents, ids=ids)


def vectordb_get_all_ids(store) -> set[str]:
    data = vectordb_get(store)
    return set(data.get("ids") or [])


def rebuild_bm25_sidecar(rows: list[tuple[str, str, dict]], index_path: Path | None = None) -> int:
    """Rebuild BM25 index after incremental ingest (best-effort)."""
    try:
        from bm25_index import BM25Index, clear_bm25_cache

        if index_path is None:
            if vector_db_provider() == "qdrant":
                from qdrant_store import BM25_INDEX_PATH

                index_path = BM25_INDEX_PATH
            else:
                index_path = resolve_vectordb_dir() / "bm25_index.pkl"
        index = BM25Index()
        count = index.build(rows)
        if count > 0:
            index.save(index_path)
            clear_bm25_cache()
        return count
    except Exception as exc:
        print(f"  ⚠️ BM25 rebuild skipped: {exc}")
        return 0


def vectordb_location(store, vectordb_dir: Path | None = None) -> str:
    if vector_db_provider() == "qdrant":
        from qdrant_store import QDRANT_COLLECTION, QDRANT_MODE, QDRANT_PATH, QDRANT_URL

        if QDRANT_MODE == "server":
            return f"{QDRANT_URL}/{QDRANT_COLLECTION}"
        return str(QDRANT_PATH)
    directory = vectordb_dir or resolve_vectordb_dir()
    return str(directory.resolve())

