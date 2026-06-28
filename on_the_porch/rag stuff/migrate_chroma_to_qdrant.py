#!/usr/bin/env python3
"""
Migrate Chroma (BGE) documents to Qdrant and build a BM25 sidecar index (Iteration 4).

Usage:
  python migrate_chroma_to_qdrant.py
  python migrate_chroma_to_qdrant.py --source on_the_porch/vectordb_bge_v1
  python migrate_chroma_to_qdrant.py --validate
  python migrate_chroma_to_qdrant.py --promote-staging

After migration:
  VECTOR_DB_PROVIDER=qdrant
  BM25_HYBRID_ENABLED=true
  # restart API
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

_THIS_FILE = Path(__file__).resolve()
_RAG_DIR = _THIS_FILE.parent
_ROOT_DIR = _RAG_DIR.parents[1]
load_dotenv(_ROOT_DIR / ".env")

if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from bm25_index import BM25Index  # noqa: E402
from retrieval import (  # noqa: E402
    LocalBGEEmbeddings,
    VECTORDB_BGE_DIR,
    configure_vectordb_paths,
    retrieve,
)
from qdrant_store import (  # noqa: E402
    BM25_INDEX_PATH,
    QDRANT_COLLECTION,
    QDRANT_MODE,
    QDRANT_PATH,
    QDRANT_STAGING_COLLECTION,
    _point_id,
    get_qdrant_store,
    reset_qdrant_singleton,
)

DEFAULT_SAMPLE_QUERIES = [
    "What do residents say about housing?",
    "anti-displacement policy Dorchester",
    "community safety meetings",
    "upcoming events Codman Square",
    "Washington Street trash pickup",
]


def _load_chroma_rows(source_dir: Path) -> list[tuple[str, str, dict]]:
    import chromadb

    client = chromadb.PersistentClient(path=str(source_dir))
    collection = client.get_collection("langchain")
    count = collection.count()
    if count <= 0:
        return []
    rows = collection.get(limit=count, include=["documents", "metadatas"])
    documents = rows.get("documents") or []
    metadatas = rows.get("metadatas") or []
    ids = rows.get("ids") or []
    return [
        (str(doc_id), doc or "", meta or {})
        for doc_id, doc, meta in zip(ids, documents, metadatas)
    ]


def migrate(
    source_dir: Path,
    *,
    collection_name: str,
    batch_size: int = 64,
    wipe: bool = True,
    dry_run: bool = False,
) -> int:
    rows = _load_chroma_rows(source_dir)
    if not rows:
        print(f"No documents in {source_dir}")
        return 0

    print(f"Source Chroma: {source_dir} ({len(rows)} docs)")
    print(f"Target Qdrant collection: {collection_name}")

    if dry_run:
        print("DRY RUN — no writes.")
        return len(rows)

    if wipe and QDRANT_MODE == "local":
        # Local embedded client stores all collections under QDRANT_PATH.
        pass

    reset_qdrant_singleton()
    store = get_qdrant_store(collection_name=collection_name)
    client = store.client

    if wipe:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
        store = get_qdrant_store(collection_name=collection_name)
        client = store.client

    print("Loading local BGE model for re-embedding …")
    embeddings = LocalBGEEmbeddings()
    from qdrant_client.models import PointStruct

    written = 0
    started = time.perf_counter()
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        texts = [text for _, text, _ in batch]
        vectors = embeddings.embed_documents(texts)
        points = []
        for (doc_id, text, metadata), vector in zip(batch, vectors):
            payload = dict(metadata)
            payload["page_content"] = text
            payload["external_id"] = doc_id
            points.append(
                PointStruct(
                    id=_point_id(doc_id),
                    vector=vector,
                    payload=payload,
                ),
            )
        client.upsert(collection_name=collection_name, points=points)
        written += len(batch)
        print(f"  upserted {written}/{len(rows)} …")

    elapsed = time.perf_counter() - started
    print(f"Qdrant migration done: {written} points in {elapsed:.1f}s")

    print("Building BM25 index …")
    index = BM25Index()
    index.build(rows)
    index.save(BM25_INDEX_PATH)
    print(f"BM25 index saved → {BM25_INDEX_PATH} ({len(rows)} docs)")
    return written


def validate_retrieval(collection_name: str, queries: list[str]) -> float:
    import os

    os.environ["VECTOR_DB_PROVIDER"] = "qdrant"
    reset_qdrant_singleton()
    configure_vectordb_paths(bge_dir=VECTORDB_BGE_DIR)

    matches = 0
    for query in queries:
        hits = retrieve(query, k=3)
        src = (
            (hits.get("metadata") or [{}])[0].get("source")
            if hits.get("chunks")
            else None
        )
        print(f"  {query!r}: top source={src!r}")
        if src:
            matches += 1
    ratio = matches / len(queries) if queries else 0.0
    print(f"Queries with at least one hit: {matches}/{len(queries)} ({ratio:.0%})")
    return ratio


def promote_staging() -> None:
    """Replace active collection with staging (local embedded mode)."""
    if QDRANT_MODE != "local":
        print("promote-staging only supported for QDRANT_MODE=local", file=sys.stderr)
        sys.exit(1)

    reset_qdrant_singleton()
    staging = get_qdrant_store(collection_name=QDRANT_STAGING_COLLECTION)
    if staging.count() <= 0:
        print("Staging collection is empty.", file=sys.stderr)
        sys.exit(1)

    client = staging.client
    all_points = []
    scroll, offset = client.scroll(
        collection_name=QDRANT_STAGING_COLLECTION,
        limit=10_000,
        with_payload=True,
        with_vectors=True,
    )
    all_points.extend(scroll)
    while offset is not None:
        scroll, offset = client.scroll(
            collection_name=QDRANT_STAGING_COLLECTION,
            offset=offset,
            limit=10_000,
            with_payload=True,
            with_vectors=True,
        )
        all_points.extend(scroll)

    try:
        client.delete_collection(QDRANT_COLLECTION)
    except Exception:
        pass
    active = get_qdrant_store(collection_name=QDRANT_COLLECTION)
    from qdrant_client.models import PointStruct

    batch_size = 128
    for start in range(0, len(all_points), batch_size):
        batch = all_points[start : start + batch_size]
        active.client.upsert(
            collection_name=QDRANT_COLLECTION,
            points=[
                PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload=point.payload,
                )
                for point in batch
            ],
        )
    print(f"Promoted {len(all_points)} points from staging → {QDRANT_COLLECTION}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate Chroma store to Qdrant + BM25")
    parser.add_argument("--source", type=Path, default=VECTORDB_BGE_DIR)
    parser.add_argument(
        "--collection",
        default=QDRANT_COLLECTION,
        help="Target Qdrant collection (default: otp_documents)",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-wipe", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--promote-staging", action="store_true")
    args = parser.parse_args()

    if args.promote_staging:
        promote_staging()
        return 0

    source = args.source.resolve()
    if not source.exists():
        print(f"Source not found: {source}", file=sys.stderr)
        return 1

    count = migrate(
        source,
        collection_name=args.collection,
        batch_size=args.batch_size,
        wipe=not args.no_wipe,
        dry_run=args.dry_run,
    )
    if count == 0:
        return 1

    if args.validate and not args.dry_run:
        validate_retrieval(args.collection, DEFAULT_SAMPLE_QUERIES)

    if not args.dry_run:
        print(
            "\nNext: set VECTOR_DB_PROVIDER=qdrant in .env, restart API, "
            f"and verify GET /health (qdrant=connected, path={QDRANT_PATH})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
