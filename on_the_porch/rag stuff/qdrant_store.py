"""Qdrant vector store backend (Iteration 4).

Replaces Chroma for the hot read path so ingestion jobs no longer contend on
SQLite. Supports local embedded mode (no Docker) or a remote Qdrant server.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_RAG_DIR = Path(__file__).resolve().parent

VECTOR_DB_PROVIDER = os.getenv("VECTOR_DB_PROVIDER", "chroma").strip().lower()
QDRANT_MODE = os.getenv("QDRANT_MODE", "local").strip().lower()
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").strip()
QDRANT_PATH_RAW = os.getenv("QDRANT_PATH", "../qdrant_data")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "otp_documents")
QDRANT_STAGING_COLLECTION = os.getenv(
    "QDRANT_STAGING_COLLECTION", "otp_documents_staging",
)
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "768"))

if Path(QDRANT_PATH_RAW).is_absolute():
    QDRANT_PATH = Path(QDRANT_PATH_RAW)
else:
    QDRANT_PATH = (_RAG_DIR / QDRANT_PATH_RAW).resolve()

BM25_INDEX_PATH = QDRANT_PATH / "bm25_index.pkl"

_client_lock = threading.Lock()
_client_singleton = None
_store_singleton = None
_store_collection: Optional[str] = None


@dataclass
class VectorDocument:
    page_content: str
    metadata: Dict[str, Any]


def _get_client():
    global _client_singleton
    with _client_lock:
        if _client_singleton is not None:
            return _client_singleton
        from qdrant_client import QdrantClient

        if QDRANT_MODE == "server":
            _client_singleton = QdrantClient(url=QDRANT_URL)
        else:
            QDRANT_PATH.mkdir(parents=True, exist_ok=True)
            _client_singleton = QdrantClient(path=str(QDRANT_PATH))
        return _client_singleton


def _metadata_filter(
    doc_type: Any = None,
    source: Optional[str] = None,
):
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    must = []
    if doc_type:
        if isinstance(doc_type, (list, tuple)):
            values = [dt for dt in doc_type if dt]
            if len(values) == 1:
                must.append(
                    FieldCondition(key="doc_type", match=MatchValue(value=values[0])),
                )
            elif values:
                must.append(
                    FieldCondition(key="doc_type", match=MatchAny(any=values)),
                )
        else:
            must.append(
                FieldCondition(key="doc_type", match=MatchValue(value=doc_type)),
            )
    if source:
        must.append(FieldCondition(key="source", match=MatchValue(value=source)))
    if not must:
        return None
    return Filter(must=must)


def _payload_to_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = dict(payload or {})
    meta.pop("page_content", None)
    return meta


def _point_id(doc_id: str) -> str:
    try:
        uuid.UUID(str(doc_id))
        return str(doc_id)
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(doc_id)))


class QdrantVectorStore:
  """Minimal LangChain-compatible store used by retrieval.py."""

  def __init__(self, collection_name: str | None = None) -> None:
      self.collection_name = collection_name or QDRANT_COLLECTION
      self.client = _get_client()
      self._ensure_collection()

  def _ensure_collection(self) -> None:
      from qdrant_client.models import Distance, VectorParams

      collections = {c.name for c in self.client.get_collections().collections}
      if self.collection_name not in collections:
          self.client.create_collection(
              collection_name=self.collection_name,
              vectors_config=VectorParams(
                  size=VECTOR_SIZE,
                  distance=Distance.COSINE,
              ),
          )

  def count(self) -> int:
      try:
          info = self.client.get_collection(self.collection_name)
          return int(getattr(info, "points_count", 0) or 0)
      except Exception:
          return 0

  def get(self, where: Optional[Dict[str, Any]] = None) -> Dict[str, List]:
      from qdrant_client.models import FieldCondition, Filter, MatchValue

      q_filter = None
      if where:
          must = []
          for key, value in where.items():
              must.append(FieldCondition(key=key, match=MatchValue(value=value)))
          if must:
              q_filter = Filter(must=must)

      rows, _offset = self.client.scroll(
          collection_name=self.collection_name,
          scroll_filter=q_filter,
          limit=10_000,
          with_payload=True,
          with_vectors=False,
      )
      ids: List[str] = []
      documents: List[str] = []
      metadatas: List[Dict[str, Any]] = []
      for point in rows:
          payload = point.payload or {}
          external_id = str(payload.get("external_id") or point.id)
          ids.append(external_id)
          documents.append(str(payload.get("page_content") or ""))
          metadatas.append(_payload_to_metadata(payload))
      return {"ids": ids, "documents": documents, "metadatas": metadatas}

  def add_documents(
      self,
      documents: List[VectorDocument],
      ids: Optional[List[str]] = None,
  ) -> List[str]:
      from qdrant_client.models import PointStruct

      assigned = list(ids or [])
      points = []
      for idx, doc in enumerate(documents):
          external_id = assigned[idx] if idx < len(assigned) else str(uuid.uuid4())
          if idx >= len(assigned):
              assigned.append(external_id)
          payload = dict(doc.metadata or {})
          payload["page_content"] = doc.page_content
          payload["external_id"] = external_id
          points.append(
              PointStruct(
                  id=_point_id(external_id),
                  vector=self._embed(doc.page_content),
                  payload=payload,
              ),
          )
      self.client.upsert(collection_name=self.collection_name, points=points)
      return assigned

  def delete(self, ids: List[str]) -> None:
      if not ids:
          return
      self.client.delete(
          collection_name=self.collection_name,
          points_selector=[_point_id(doc_id) for doc_id in ids],
      )

  def similarity_search_by_vector(
      self,
      embedding: List[float],
      k: int = 5,
      filter: Optional[Dict[str, Any]] = None,
  ) -> List[VectorDocument]:
      q_filter = None
      if filter:
          q_filter = _metadata_filter(
              doc_type=filter.get("doc_type"),
              source=filter.get("source"),
          )
          if "$and" in filter:
              for clause in filter["$and"]:
                  if "doc_type" in clause:
                      q_filter = _metadata_filter(doc_type=clause["doc_type"])
                  if "source" in clause:
                      q_filter = _metadata_filter(
                          doc_type=filter.get("doc_type"),
                          source=clause.get("source"),
                      )
          if "$or" in filter:
              doc_types = [c.get("doc_type") for c in filter["$or"] if c.get("doc_type")]
              q_filter = _metadata_filter(doc_type=doc_types)

      hits = self.client.search(
          collection_name=self.collection_name,
          query_vector=embedding,
          limit=k,
          query_filter=q_filter,
          with_payload=True,
      )
      docs: List[VectorDocument] = []
      for hit in hits:
          payload = hit.payload or {}
          text = str(payload.get("page_content") or "")
          docs.append(VectorDocument(page_content=text, metadata=_payload_to_metadata(payload)))
      return docs

  def similarity_search_with_score_by_vector(
      self,
      embedding: List[float],
      k: int = 5,
      filter: Optional[Dict[str, Any]] = None,
  ) -> List[tuple[VectorDocument, float]]:
      from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

      q_filter = None
      if filter:
          if "$and" in filter:
              must = []
              for clause in filter["$and"]:
                  for key, value in clause.items():
                      if key == "doc_type" and isinstance(value, list):
                          must.append(
                              FieldCondition(key=key, match=MatchAny(any=value)),
                          )
                      else:
                          must.append(
                              FieldCondition(key=key, match=MatchValue(value=value)),
                          )
              q_filter = Filter(must=must) if must else None
          elif "$or" in filter:
              doc_types = [c.get("doc_type") for c in filter["$or"] if c.get("doc_type")]
              q_filter = _metadata_filter(doc_type=doc_types)
          else:
              q_filter = _metadata_filter(
                  doc_type=filter.get("doc_type"),
                  source=filter.get("source"),
              )

      hits = self.client.search(
          collection_name=self.collection_name,
          query_vector=embedding,
          limit=k,
          query_filter=q_filter,
          with_payload=True,
      )
      results: List[tuple[VectorDocument, float]] = []
      for hit in hits:
          payload = hit.payload or {}
          text = str(payload.get("page_content") or "")
          doc = VectorDocument(page_content=text, metadata=_payload_to_metadata(payload))
          results.append((doc, float(hit.score or 0.0)))
      return results

  def _embed(self, text: str) -> List[float]:
      from retrieval import get_embeddings

      return get_embeddings().embed_query(text)


def get_qdrant_store(collection_name: str | None = None) -> QdrantVectorStore:
    global _store_singleton, _store_collection
    name = collection_name or QDRANT_COLLECTION
    with _client_lock:
        if _store_singleton is None or _store_collection != name:
            _store_singleton = QdrantVectorStore(collection_name=name)
            _store_collection = name
        return _store_singleton


def reset_qdrant_singleton() -> None:
    global _client_singleton, _store_singleton, _store_collection
    with _client_lock:
        _client_singleton = None
        _store_singleton = None
        _store_collection = None


def check_qdrant_health() -> str:
    if VECTOR_DB_PROVIDER != "qdrant":
        return "not_configured"
    try:
        store = get_qdrant_store()
        count = store.count()
        if count <= 0:
            return "empty"
        return "connected"
    except Exception:
        return "unavailable"


def list_all_documents() -> List[tuple[str, str, Dict[str, Any]]]:
    """Return (id, text, metadata) for BM25 rebuild / migration."""
    store = get_qdrant_store()
    rows, offset = store.client.scroll(
        collection_name=store.collection_name,
        limit=10_000,
        with_payload=True,
        with_vectors=False,
    )
    out: List[tuple[str, str, Dict[str, Any]]] = []
    while rows:
        for point in rows:
            payload = point.payload or {}
            text = str(payload.get("page_content") or "")
            external_id = str(payload.get("external_id") or point.id)
            out.append((external_id, text, _payload_to_metadata(payload)))
        if offset is None:
            break
        rows, offset = store.client.scroll(
            collection_name=store.collection_name,
            offset=offset,
            limit=10_000,
            with_payload=True,
            with_vectors=False,
        )
    return out
