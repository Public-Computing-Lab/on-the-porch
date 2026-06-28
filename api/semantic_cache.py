"""Semantic response cache.

Caches final answers keyed by the *meaning* of the question (its embedding), so a
near-duplicate question can be answered instantly without re-running the whole
routing/retrieval/generation pipeline.

Backends:
- In-memory (default): per-process, suitable for single-worker dev.
- Redis (Iteration 1): shared across gunicorn workers when CACHE_BACKEND=redis.
- Uses the same embedding provider as RAG (Iteration 3: local BGE when EMBED_PROVIDER=local).

Conservative by design (SEMANTIC_CACHE_ENABLED=false by default):
- Only first-turn questions are cached/served (follow-ups depend on conversation
  context, so they must never hit the cache).
- Time-sensitive questions are excluded by the caller.
- Entries expire (TTL) so answers can't go stale indefinitely.
- A Gemini embedding failure degrades to "no cache" rather than raising.
"""

from __future__ import annotations

import json
import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class SemanticCacheBackend(Protocol):
    def list_entries(self, now: float) -> List[Dict[str, Any]]:
        ...

    def save_entries(self, entries: List[Dict[str, Any]]) -> None:
        ...


class _InMemorySemanticBackend:
    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def list_entries(self, now: float) -> List[Dict[str, Any]]:
        with self._lock:
            self._entries = [e for e in self._entries if e["expires_at"] > now]
            return list(self._entries)

    def save_entries(self, entries: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._entries = entries


class _RedisSemanticBackend:
    """Store cache entries as a JSON blob in Redis (shared across workers)."""

    def __init__(self, client, key: str) -> None:
        self._client = client
        self._key = key
        self._lock = threading.Lock()

    def list_entries(self, now: float) -> List[Dict[str, Any]]:
        try:
            raw = self._client.get(self._key)
            if not raw:
                return []
            entries = json.loads(raw)
            if not isinstance(entries, list):
                return []
            alive = [e for e in entries if e.get("expires_at", 0) > now]
            if len(alive) != len(entries):
                self.save_entries(alive)
            return alive
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ Semantic cache redis read failed: {exc}")
            return []

    def save_entries(self, entries: List[Dict[str, Any]]) -> None:
        try:
            payload = json.dumps(entries, ensure_ascii=False)
            # TTL is enforced per-entry; Redis key TTL is a safety net (24h).
            self._client.set(self._key, payload, ex=86400)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ Semantic cache redis write failed: {exc}")


class SemanticResponseCache:
    def __init__(
        self,
        *,
        enabled: bool,
        threshold: float,
        ttl_seconds: int,
        max_entries: int,
        embed_fn: Callable[[str], List[float]],
        backend: Optional[SemanticCacheBackend] = None,
    ) -> None:
        self.enabled = enabled
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._embed_fn = embed_fn
        self._backend = backend or _InMemorySemanticBackend()
        self._last_embedding: Optional[Tuple[str, List[float]]] = None

    def _embed(self, question: str) -> Optional[List[float]]:
        if self._last_embedding and self._last_embedding[0] == question:
            return self._last_embedding[1]
        try:
            vec = self._embed_fn(question)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ Semantic cache embedding failed: {exc}")
            return None
        if vec:
            self._last_embedding = (question, vec)
        return vec

    def get(self, question: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        vec = self._embed(question)
        if not vec:
            return None
        now = time.time()
        entries = self._backend.list_entries(now)
        best: Optional[Dict[str, Any]] = None
        best_sim = 0.0
        for entry in entries:
            sim = _cosine(vec, entry["vec"])
            if sim > best_sim:
                best_sim = sim
                best = entry
        if best is not None and best_sim >= self.threshold:
            best["last_used"] = now
            self._backend.save_entries(entries)
            payload = dict(best["payload"])
            payload["_similarity"] = round(best_sim, 4)
            return payload
        return None

    def put(self, question: str, payload: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        vec = self._embed(question)
        if not vec:
            return
        now = time.time()
        entries = self._backend.list_entries(now)
        entries.append(
            {
                "vec": vec,
                "payload": dict(payload),
                "question": question,
                "expires_at": now + self.ttl_seconds,
                "last_used": now,
            }
        )
        if len(entries) > self.max_entries:
            entries.sort(key=lambda e: e["last_used"])
            entries = entries[-self.max_entries:]
        self._backend.save_entries(entries)


def build_semantic_cache_backend(
    *,
    backend_name: str,
    redis_client=None,
    redis_key: str,
) -> SemanticCacheBackend:
    if backend_name == "redis" and redis_client is not None:
        return _RedisSemanticBackend(redis_client, redis_key)
    return _InMemorySemanticBackend()
