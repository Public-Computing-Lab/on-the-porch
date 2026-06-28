import os
import re
import sqlite3
import threading
import time
from pathlib import Path

import chromadb
import google.generativeai as genai  # type: ignore

try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma

# Resolve to an absolute path anchored at this file (on_the_porch/vectordb_new)
# so the location does not depend on the process working directory. The live
# server still overrides this via _fix_retrieval_vectordb_path(), but resolving
# here keeps standalone scripts pointed at the same store the server reads.
_RAG_DIR = Path(__file__).resolve().parent
VECTORDB_GEMINI_DIR = (_RAG_DIR / "../vectordb_new").resolve()
VECTORDB_BGE_DIR = (_RAG_DIR / "../vectordb_bge_v1").resolve()

GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "models/gemini-embedding-001")
# Iteration 3: local BGE embeddings (default gemini until BGE index is built).
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "gemini").strip().lower()
LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
LOCAL_EMBED_DEVICE = os.getenv("LOCAL_EMBED_DEVICE", "cpu")
# Iteration 4: Qdrant vector store + BM25 hybrid retrieval.
VECTOR_DB_PROVIDER = os.getenv("VECTOR_DB_PROVIDER", "chroma").strip().lower()
BM25_HYBRID_ENABLED = os.getenv("BM25_HYBRID_ENABLED", "true").strip().lower() in (
    "1", "true", "yes",
)
# Active store path (updated by configure_vectordb_paths at API startup).
VECTORDB_DIR = VECTORDB_BGE_DIR if EMBED_PROVIDER == "local" else VECTORDB_GEMINI_DIR
_embeddings_provider: str | None = None
# How many times to retry a vector-store read when SQLite is momentarily locked
# (e.g. the nightly ingestion cron is writing to the same store).
_CHROMA_MAX_ATTEMPTS = max(1, int(os.getenv("CHROMA_MAX_ATTEMPTS", "5")))
# Tracks which vector-store paths have already had WAL mode enabled.
_WAL_ENABLED_PATHS: set[str] = set()

# Reuse a single Chroma client + embeddings object instead of rebuilding them on
# every retrieval call (the old behavior rebuilt them 4-6x per question). The
# client is safe to share across threads for reads. Disable with
# VECTORDB_SINGLETON=false to fall back to per-call construction.
_VECTORDB_SINGLETON_ENABLED = os.getenv("VECTORDB_SINGLETON", "true").strip().lower() in ("1", "true", "yes")
_vectordb_singleton = None
_vectordb_singleton_path: str | None = None
_embeddings_singleton = None
_vectordb_lock = threading.RLock()
# Per-request embedding reuse (Iteration 1): one embed per question across sources.
_query_embed_local = threading.local()
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "me", "of", "on", "or", "that", "the", "this",
    "to", "was", "what", "when", "where", "which", "who", "why", "with",
}


def _configure_gemini() -> None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    genai.configure(api_key=api_key)


class GeminiEmbeddings:
    """
    Minimal embeddings wrapper using Gemini's embedding API, compatible with LangChain's interface.
    """

    def __init__(self, model: str | None = None) -> None:
        _configure_gemini()
        self.model = model or GEMINI_EMBED_MODEL

    def _embed(self, text: str):
        res = genai.embed_content(model=self.model, content=text)
        return res["embedding"]

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


class LocalBGEEmbeddings:
    """Local sentence-transformers embeddings (Iteration 3)."""

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name or LOCAL_EMBED_MODEL
        self.device = (device or LOCAL_EMBED_DEVICE).strip() or "cpu"
        self._model = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def embed_documents(self, texts):
        if not texts:
            return []
        model = self._load_model()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def resolve_vectordb_dir() -> Path:
    """Return the vector store path for the active embedding provider."""
    if EMBED_PROVIDER == "local":
        return VECTORDB_BGE_DIR
    return VECTORDB_GEMINI_DIR


def configure_vectordb_paths(
    *,
    gemini_dir: Path | str | None = None,
    bge_dir: Path | str | None = None,
) -> Path:
    """Set gemini/BGE store paths and point VECTORDB_DIR at the active store."""
    global VECTORDB_DIR, VECTORDB_GEMINI_DIR, VECTORDB_BGE_DIR
    global _vectordb_singleton, _vectordb_singleton_path, _embeddings_singleton, _embeddings_provider

    if gemini_dir is not None:
        VECTORDB_GEMINI_DIR = Path(gemini_dir).resolve()
    if bge_dir is not None:
        VECTORDB_BGE_DIR = Path(bge_dir).resolve()
    VECTORDB_DIR = resolve_vectordb_dir()
    _vectordb_singleton = None
    _vectordb_singleton_path = None
    _embeddings_singleton = None
    _embeddings_provider = None
    return VECTORDB_DIR


def get_embeddings():
    """Shared embedding function for retrieval, cache, and ingestion."""
    global _embeddings_singleton, _embeddings_provider
    provider = EMBED_PROVIDER if EMBED_PROVIDER in ("local", "gemini") else "gemini"
    if _embeddings_singleton is None or _embeddings_provider != provider:
        _embeddings_singleton = (
            LocalBGEEmbeddings() if provider == "local" else GeminiEmbeddings()
        )
        _embeddings_provider = provider
    return _embeddings_singleton


def embed_text(text: str) -> list:
    """Embed a single string (query or cache key)."""
    return get_embeddings().embed_query(text)


def clear_query_embedding_cache() -> None:
    """Clear the per-request query embedding (call at start of each RAG fetch)."""
    _query_embed_local.vec = None
    _query_embed_local.key = None


def get_query_embedding(query: str) -> list:
    """Embed a query once per request; reused by parallel retrieval tasks."""
    key = (query or "").strip()
    if getattr(_query_embed_local, "key", None) == key and getattr(
        _query_embed_local, "vec", None
    ):
        return _query_embed_local.vec
    vec = get_embeddings().embed_query(key)
    _query_embed_local.key = key
    _query_embed_local.vec = vec
    return vec


def _is_locked_error(exc: Exception) -> bool:
    """True when an exception looks like a transient SQLite lock/contention."""
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database is busy" in message
        or "disk i/o error" in message
    )


def _is_transient_chroma_error(exc: Exception) -> bool:
    """True when Chroma/SQLite may recover on retry (lock, segment, corrupt handle)."""
    message = str(exc).lower()
    return _is_locked_error(exc) or any(
        token in message
        for token in (
            "failed to get segments",
            "file is not a database",
            "database disk image is malformed",
            "notadb",
            "code: 26",
        )
    )


def _reset_vectordb_singleton() -> None:
    """Drop cached Chroma client so the next read opens a fresh handle."""
    global _vectordb_singleton, _vectordb_singleton_path
    with _vectordb_lock:
        _vectordb_singleton = None
        _vectordb_singleton_path = None


def _ensure_sqlite_wal() -> None:
    """Best-effort: put the Chroma SQLite store into WAL mode.

    WAL (Write-Ahead Logging) lets the live server keep *reading* while the
    nightly ingestion cron *writes* to the same store, instead of failing with
    "database is locked". journal_mode=WAL is persisted in the database header,
    so this only needs to succeed once; we track it per path and retry until it
    does. This is intentionally non-fatal — if it can't be set we simply rely on
    the read-retry logic below.
    """
    try:
        path_str = str(Path(VECTORDB_DIR).resolve())
    except Exception:
        return
    if path_str in _WAL_ENABLED_PATHS:
        return
    db_path = Path(VECTORDB_DIR) / "chroma.sqlite3"
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
        finally:
            conn.close()
        _WAL_ENABLED_PATHS.add(path_str)
    except Exception as exc:
        print(f"  ⚠️ Could not enable SQLite WAL on vector store: {exc}")


def _bm25_index_path() -> Path:
    if VECTOR_DB_PROVIDER == "qdrant":
        from qdrant_store import BM25_INDEX_PATH

        return BM25_INDEX_PATH
    return Path(VECTORDB_DIR) / "bm25_index.pkl"


def _load_bm25_index():
    if not BM25_HYBRID_ENABLED:
        return None
    try:
        from bm25_index import get_bm25_index

        return get_bm25_index(_bm25_index_path())
    except Exception as exc:
        print(f"  ⚠️ BM25 index unavailable: {exc}")
        return None


def _merge_vector_and_bm25(
    query: str,
    vector_result: dict,
    *,
    k: int,
    doc_type=None,
    tags=None,
    source=None,
) -> dict:
    """Reciprocal-rank fusion of dense vector hits and BM25 keyword hits."""
    index = _load_bm25_index()
    if index is None:
        return vector_result

    from bm25_index import reciprocal_rank_fusion

    vector_ids: list[str] = []
    id_to_chunk: dict[str, str] = {}
    id_to_meta: dict[str, dict] = {}
    for chunk, meta in zip(
        vector_result.get("chunks") or [],
        vector_result.get("metadata") or [],
    ):
        doc_id = str(
            (meta or {}).get("external_id")
            or (meta or {}).get("note_id")
            or (meta or {}).get("source")
            or chunk[:80]
        )
        vector_ids.append(doc_id)
        id_to_chunk[doc_id] = chunk
        id_to_meta[doc_id] = meta or {}

    bm25_rows = index.search(
        query, k=k * 2, doc_type=doc_type, tags=tags, source=source,
    )
    bm25_ids = [row[1] for row in bm25_rows]
    for _, doc_id, chunk, meta in bm25_rows:
        id_to_chunk.setdefault(doc_id, chunk)
        id_to_meta.setdefault(doc_id, meta)

    fused_ids = reciprocal_rank_fusion([vector_ids, bm25_ids], limit=k * 3)
    chunks: list[str] = []
    metadata: list[dict] = []
    for doc_id in fused_ids:
        chunk = id_to_chunk.get(doc_id)
        if not chunk:
            continue
        meta = id_to_meta.get(doc_id, {})
        if tags:
            raw_tags = meta.get("tags")
            if raw_tags:
                doc_tags = [t.strip() for t in str(raw_tags).split(",")]
                if not any(tag in doc_tags for tag in tags):
                    continue
        chunks.append(chunk)
        metadata.append(meta)
        if len(chunks) >= k:
            break

    if not chunks:
        return vector_result
    return {
        "chunks": chunks,
        "metadata": metadata,
        "scores": None,
        "query": query,
    }


def load_vectordb():
    """Load the active vector database (Chroma or Qdrant).

    Returns a cached singleton keyed by the current store. If the path
    changes (the live server overrides it at startup), the client is rebuilt.
    """
    if VECTOR_DB_PROVIDER == "qdrant":
        from qdrant_store import get_qdrant_store

        return get_qdrant_store()

    _ensure_sqlite_wal()

    if not _VECTORDB_SINGLETON_ENABLED:
        return Chroma(
            persist_directory=str(VECTORDB_DIR),
            embedding_function=get_embeddings(),
        )

    global _vectordb_singleton, _vectordb_singleton_path
    current_path = str(VECTORDB_DIR)
    with _vectordb_lock:
        if _vectordb_singleton is None or _vectordb_singleton_path != current_path:
            _vectordb_singleton = Chroma(
                persist_directory=current_path,
                embedding_function=get_embeddings(),
            )
            _vectordb_singleton_path = current_path
        return _vectordb_singleton


def _normalize_query_terms(text: str) -> list[str]:
    terms = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return [term for term in terms if term not in _STOPWORDS]


def _matches_doc_type(metadata, doc_type) -> bool:
    if not doc_type:
        return True
    current = metadata.get("doc_type")
    if isinstance(doc_type, (list, tuple)):
        allowed = {value for value in doc_type if value}
        return current in allowed if allowed else True
    return current == doc_type


def _matches_source(metadata, source) -> bool:
    if not source:
        return True
    return metadata.get("source") == source


def _matches_tags(metadata, tags) -> bool:
    if not tags:
        return True
    raw_tags = metadata.get("tags")
    if not raw_tags:
        return False
    doc_tags = {tag.strip().lower() for tag in raw_tags.split(",") if tag.strip()}
    requested = {tag.strip().lower() for tag in tags if tag and tag.strip()}
    return bool(doc_tags & requested) if requested else True


def _score_keyword_match(query: str, document: str, metadata) -> float:
    query_lower = (query or "").lower().strip()
    if not query_lower:
        return 0.0

    content_lower = (document or "").lower()
    source_lower = str(metadata.get("source", "")).lower()
    folder_lower = str(metadata.get("folder_category", "")).lower()
    terms = _normalize_query_terms(query_lower)

    score = 0.0

    if query_lower in content_lower:
        score += 10.0
    if query_lower in source_lower:
        score += 8.0

    for term in terms:
        score += min(content_lower.count(term), 5)
        if term in source_lower:
            score += 2.0
        if term in folder_lower:
            score += 1.0

    return score


def _load_raw_documents():
    _ensure_sqlite_wal()
    with _vectordb_lock:
        client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
        collection = client.get_collection("langchain")
        count = collection.count()
        if count <= 0:
            return []
        rows = collection.get(limit=count, include=["documents", "metadatas"])
    documents = rows.get("documents") or []
    metadatas = rows.get("metadatas") or []
    return list(zip(documents, metadatas))


def _keyword_retrieve(query, k=5, doc_type=None, tags=None, source=None):
    rows = []
    tag_matched_rows = []
    for document, metadata in _load_raw_documents():
        metadata = metadata or {}
        if not _matches_doc_type(metadata, doc_type):
            continue
        if not _matches_source(metadata, source):
            continue
        score = _score_keyword_match(query, document, metadata)
        if score <= 0:
            continue
        row = (score, document, metadata)
        rows.append(row)
        if _matches_tags(metadata, tags):
            tag_matched_rows.append(row)

    ranked_rows = tag_matched_rows if tag_matched_rows else rows
    ranked_rows.sort(key=lambda item: item[0], reverse=True)
    top_rows = ranked_rows[:k]
    return {
        "chunks": [document for _, document, _ in top_rows],
        "metadata": [metadata for _, _, metadata in top_rows],
        "scores": [score for score, _, _ in top_rows],
        "query": query,
    }


def retrieve(
    query,
    k=5,
    doc_type=None,
    tags=None,
    source=None,
    min_score=None,
    vectordb=None,
    query_embedding=None,
):
    """
    Universal retrieval with flexible metadata filtering.
    
    Args:
        query: Search query string
        k: Number of results to return
        doc_type: Filter by document type ('transcript', 'policy', or 'client_upload')
        tags: Filter by tags (list of tags, e.g., ['media', 'community'])
               For transcripts only - uses OR logic (chunk must have ANY tag)
        source: Filter by specific source filename
        min_score: Optional minimum similarity score threshold (lower is more similar)
    
    Returns:
        dict with chunks, metadata, and optional scores
    
    Examples:
        # Only transcripts
        retrieve(query, doc_type='transcript')
        
        # Transcripts with specific tags
        retrieve(query, doc_type='transcript', tags=['media', 'community'])
        
        # Only policy docs
        retrieve(query, doc_type='policy')
        
        # Specific source file
        retrieve(query, source='Boston Anti-Displacement Plan Analysis.txt')
        
        # Everything
        retrieve(query)
    """
    # Defensive clamp: Chroma requires k >= 1
    try:
        k = int(k)
    except (TypeError, ValueError):
        k = 5
    if k <= 0:
        k = 5

    # Build filter dictionary (Chroma requires $and / $or for combinations)
    filter_dict = None

    # Allow doc_type to be a single value or a list of values
    doc_filter = None
    if isinstance(doc_type, (list, tuple)):
        doc_types = [dt for dt in doc_type if dt]
        if len(doc_types) == 1:
            doc_filter = {"doc_type": doc_types[0]}
        elif len(doc_types) > 1:
            doc_filter = {"$or": [{"doc_type": dt} for dt in doc_types]}
    elif doc_type:
        doc_filter = {"doc_type": doc_type}

    if doc_filter and source:
        filter_dict = {
            "$and": [
                doc_filter,
                {"source": source},
            ]
        }
    elif doc_filter:
        filter_dict = doc_filter
    elif source:
        filter_dict = {"source": source}

    def _do_semantic_search(vectordb):
        embedding = query_embedding if query_embedding is not None else get_query_embedding(query)
        # Retrieve with or without score threshold
        if min_score is not None:
            score_fn = getattr(vectordb, "similarity_search_with_score_by_vector", None)
            if score_fn is not None:
                results_with_scores = score_fn(
                    embedding,
                    k=k * 3 if tags else k,
                    filter=filter_dict if filter_dict else None,
                )
            else:
                results_with_scores = vectordb.similarity_search_with_score(
                    query,
                    k=k * 3 if tags else k,
                    filter=filter_dict if filter_dict else None,
                )

            # Post-process tag filtering if needed (soft filter: fall back to unfiltered if no matches)
            if tags:
                filtered_results = []
                for doc, score in results_with_scores:
                    if "tags" in doc.metadata:
                        doc_tags = [t.strip() for t in doc.metadata["tags"].split(",")]
                        if any(tag in doc_tags for tag in tags):
                            filtered_results.append((doc, score))
                            if len(filtered_results) >= k:
                                break
                if filtered_results:
                    results_with_scores = filtered_results

            filtered_results = [(doc, score) for doc, score in results_with_scores if score <= min_score]

            return {
                "chunks": [doc.page_content for doc, _ in filtered_results[:k]],
                "metadata": [doc.metadata for doc, _ in filtered_results[:k]],
                "scores": [score for _, score in filtered_results[:k]],
                "query": query,
            }

        results = vectordb.similarity_search_by_vector(
            embedding,
            k=k * 3 if tags else k,
            filter=filter_dict if filter_dict else None,
        )

        if tags:
            filtered_results = []
            for doc in results:
                if "tags" in doc.metadata:
                    doc_tags = [t.strip() for t in doc.metadata["tags"].split(",")]
                    if any(tag in doc_tags for tag in tags):
                        filtered_results.append(doc)
                        if len(filtered_results) >= k:
                            break
            if filtered_results:
                results = filtered_results

        return {
            "chunks": [doc.page_content for doc in results[:k]],
            "metadata": [doc.metadata for doc in results[:k]],
            "scores": None,
            "query": query,
        }

    # The nightly ingestion cron writes to the same SQLite-backed store. A
    # concurrent write can momentarily lock the DB; rather than immediately
    # degrading to keyword search, wait out a transient lock and retry.
    for attempt in range(1, _CHROMA_MAX_ATTEMPTS + 1):
        try:
            with _vectordb_lock:
                db = vectordb if vectordb is not None else load_vectordb()
                result = _do_semantic_search(db)
            if BM25_HYBRID_ENABLED:
                result = _merge_vector_and_bm25(
                    query,
                    result,
                    k=k,
                    doc_type=doc_type,
                    tags=tags,
                    source=source,
                )
            return result
        except Exception as exc:
            if _is_transient_chroma_error(exc) and attempt < _CHROMA_MAX_ATTEMPTS:
                print(
                    f"  ⏳ Vector store transient error "
                    f"(attempt {attempt}/{_CHROMA_MAX_ATTEMPTS}): {exc}; retrying"
                )
                _reset_vectordb_singleton()
                time.sleep(min(2 ** (attempt - 1), 4) * 0.25)
                continue
            print(f"  ⚠️ Semantic retrieval failed, falling back to keyword search: {exc}")
            try:
                return _keyword_retrieve(query, k=k, doc_type=doc_type, tags=tags, source=source)
            except Exception as kw_exc:
                print(f"  ⚠️ Keyword retrieval failed: {kw_exc}")
                return {"chunks": [], "metadata": [], "scores": None, "query": query}


def retrieve_transcripts(query, tags=None, k=5, query_embedding=None):
    """
    Convenience function for transcript-only search.
    
    Args:
        query: Search query
        tags: Optional list of tags to filter by (e.g., ['media', 'community'])
        k: Number of results
    
    Example:
        retrieve_transcripts("What do people say about safety?", tags=['safety'])
    """
    return retrieve(
        query, k=k, doc_type="transcript", tags=tags, query_embedding=query_embedding,
    )


def retrieve_policies(query, k=5, source=None, query_embedding=None):
    """
    Convenience function for policy-only search.
    
    Args:
        query: Search query
        k: Number of results
        source: Optional specific policy document to search
    
    Example:
        retrieve_policies("anti-displacement strategies")
    """
    return retrieve(
        query, k=k, doc_type="policy", source=source, query_embedding=query_embedding,
    )



def format_results(result_dict):
    """Format retrieval results for display."""
    formatted = []
    formatted.append(f"Query: {result_dict['query']}")
    formatted.append(f"Total results: {len(result_dict['chunks'])}\n")
    
    for i, (chunk, meta) in enumerate(zip(result_dict['chunks'], result_dict['metadata']), 1):
        formatted.append(f"\n{'='*80}")
        formatted.append(f"Result {i}")
        formatted.append(f"Source: {meta.get('source', 'Unknown')}")
        formatted.append(f"Type: {meta.get('doc_type', 'Unknown')}")
        
        if meta.get('tags'):
            formatted.append(f"Tags: {meta['tags']}")
        if meta.get('Heading'):
            formatted.append(f"Section: {meta['Heading']}")
        
        if result_dict.get('scores'):
            formatted.append(f"Score: {result_dict['scores'][i-1]:.4f}")
        
        formatted.append(f"\nContent:\n{chunk[:300]}...")
    
    return '\n'.join(formatted)


def retrieve_rss(query, k=5, source=None, query_embedding=None):
    """
    Search RSS-ingested content in ChromaDB (doc_type='calendar_event' with feed_url set).
    
    Args:
        query: Search query
        k: Number of results
        source: Optional specific feed source name (e.g., 'Dorchester Reporter')
    
    Example:
        retrieve_rss("upcoming events Codman Square")
    """
    # RSS docs have doc_type='calendar_event' AND a 'feed_url' metadata field.
    # We filter by calendar_event and post-filter to only RSS-sourced docs
    # (newsletter PDFs also use calendar_event but don't have feed_url).
    results = retrieve(
        query, k=k * 2, doc_type="calendar_event", source=source,
        query_embedding=query_embedding,
    )

    # Post-filter: keep only RSS docs (they have feed_url in metadata)
    rss_chunks = []
    rss_meta = []
    for chunk, meta in zip(results["chunks"], results["metadata"]):
        if meta.get("feed_url"):
            rss_chunks.append(chunk)
            rss_meta.append(meta)
            if len(rss_chunks) >= k:
                break

    # If source filter returned nothing, fall back to unfiltered RSS docs
    if not rss_chunks:
        fallback = retrieve(
            query, k=k * 2, doc_type="calendar_event", query_embedding=query_embedding,
        )
        for chunk, meta in zip(fallback["chunks"], fallback["metadata"]):
            if meta.get("feed_url"):
                rss_chunks.append(chunk)
                rss_meta.append(meta)
                if len(rss_chunks) >= k:
                    break

    return {
        "chunks": rss_chunks,
        "metadata": rss_meta,
        "scores": None,
        "query": query,
    }

if __name__ == "__main__":
    # Example usage
    print("=== Example 1: Search transcripts only ===")
    results = retrieve_transcripts("What do people say about media representation?", k=3)
    print(format_results(results))
    
    print("\n\n=== Example 2: Search with specific tags ===")
    results = retrieve_transcripts("community safety", tags=['safety'], k=3)
    print(format_results(results))
    
    print("\n\n=== Example 3: Search policy docs ===")
    results = retrieve_policies("housing affordability", k=3)
    print(format_results(results))
