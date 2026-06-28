"""BM25 keyword index for hybrid retrieval (Iteration 4).

Built offline during Chroma→Qdrant migration and loaded at API startup.
Improves exact-term and street-name recall alongside dense vectors.
"""

from __future__ import annotations

import pickle
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "me", "of", "on", "or", "that", "the", "this",
    "to", "was", "what", "when", "where", "which", "who", "why", "with",
}

_index_lock = threading.Lock()
_index_singleton: Optional["BM25Index"] = None
_index_path_loaded: Optional[str] = None


def _tokenize(text: str) -> List[str]:
    return [
        tok
        for tok in re.findall(r"[a-z0-9]{2,}", (text or "").lower())
        if tok not in _STOPWORDS
    ]


class BM25Index:
    """In-memory BM25 over document text + source metadata."""

    def __init__(self) -> None:
        self.ids: List[str] = []
        self.documents: List[str] = []
        self.metadatas: List[Dict[str, Any]] = []
        self._bm25 = None

    def build(self, rows: List[Tuple[str, str, Dict[str, Any]]]) -> int:
        self.ids = []
        self.documents = []
        self.metadatas = []
        corpus: List[List[str]] = []

        for doc_id, text, metadata in rows:
            if not (text or "").strip():
                continue
            meta = dict(metadata or {})
            source = str(meta.get("source") or "")
            combined = f"{source}\n{text}".strip()
            tokens = _tokenize(combined)
            if not tokens:
                continue
            self.ids.append(str(doc_id))
            self.documents.append(text)
            self.metadatas.append(meta)
            corpus.append(tokens)

        if not corpus:
            self._bm25 = None
            return 0

        from rank_bm25 import BM25Okapi

        self._bm25 = BM25Okapi(corpus)
        return len(self.ids)

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        doc_type: Any = None,
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
    ) -> List[Tuple[float, str, str, Dict[str, Any]]]:
        if self._bm25 is None or not self.ids:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        ranked: List[Tuple[float, str, str, Dict[str, Any]]] = []
        for score, doc_id, document, metadata in zip(
            scores, self.ids, self.documents, self.metadatas,
        ):
            if score <= 0:
                continue
            if not _matches_filters(metadata, doc_type=doc_type, tags=tags, source=source):
                continue
            ranked.append((float(score), doc_id, document, metadata))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[:k]

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ids": self.ids,
            "documents": self.documents,
            "metadatas": self.metadatas,
        }
        with path.open("wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)

    def load(self, path: Path) -> int:
        path = Path(path)
        if not path.exists():
            return 0
        with path.open("rb") as fh:
            payload = pickle.load(fh)
        self.ids = list(payload.get("ids") or [])
        self.documents = list(payload.get("documents") or [])
        self.metadatas = list(payload.get("metadatas") or [])
        rows = list(zip(self.ids, self.documents, self.metadatas))
        return self.build(rows)


def _matches_filters(
    metadata: Dict[str, Any],
    *,
    doc_type: Any = None,
    tags: Optional[List[str]] = None,
    source: Optional[str] = None,
) -> bool:
    if doc_type:
        current = metadata.get("doc_type")
        if isinstance(doc_type, (list, tuple)):
            allowed = {value for value in doc_type if value}
            if allowed and current not in allowed:
                return False
        elif current != doc_type:
            return False
    if source and metadata.get("source") != source:
        return False
    if tags:
        raw_tags = metadata.get("tags")
        if not raw_tags:
            return False
        doc_tags = {tag.strip().lower() for tag in str(raw_tags).split(",") if tag.strip()}
        requested = {tag.strip().lower() for tag in tags if tag and tag.strip()}
        if requested and not (doc_tags & requested):
            return False
    return True


def reciprocal_rank_fusion(
    rankings: List[List[str]],
    *,
    k: int = 60,
    limit: int = 5,
) -> List[str]:
    """Merge ranked doc-id lists with RRF (no score normalization needed)."""
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [doc_id for doc_id, _ in ordered[:limit]]


def get_bm25_index(index_path: Path) -> Optional[BM25Index]:
    """Load a cached BM25 index from disk (thread-safe singleton per path)."""
    global _index_singleton, _index_path_loaded
    path_str = str(Path(index_path).resolve())
    with _index_lock:
        if _index_singleton is not None and _index_path_loaded == path_str:
            return _index_singleton
        index = BM25Index()
        count = index.load(Path(path_str))
        if count <= 0:
            return None
        _index_singleton = index
        _index_path_loaded = path_str
        return _index_singleton


def clear_bm25_cache() -> None:
    global _index_singleton, _index_path_loaded
    with _index_lock:
        _index_singleton = None
        _index_path_loaded = None
