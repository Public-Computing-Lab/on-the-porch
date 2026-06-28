#!/usr/bin/env python3
"""
Blue-green re-index: copy documents from the Gemini-embedded Chroma store into a
new store embedded with local BGE (Iteration 3).

Usage:
  python reindex_bge_vectordb.py
  python reindex_bge_vectordb.py --validate   # compare top-1 overlap on sample queries
  python reindex_bge_vectordb.py --dry-run

After a successful run, set in .env:
  EMBED_PROVIDER=local
  # VECTORDB_BGE_DIR defaults to on_the_porch/vectordb_bge_v1
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

_THIS_FILE = Path(__file__).resolve()
_RAG_DIR = _THIS_FILE.parent
_ROOT_DIR = _RAG_DIR.parents[1]
load_dotenv(_ROOT_DIR / ".env")

if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from retrieval import (  # noqa: E402
    GeminiEmbeddings,
    LocalBGEEmbeddings,
    VECTORDB_BGE_DIR,
    VECTORDB_GEMINI_DIR,
    configure_vectordb_paths,
    retrieve,
)

DEFAULT_SAMPLE_QUERIES = [
    "What do residents say about housing?",
    "anti-displacement policy Dorchester",
    "community safety meetings",
    "upcoming events Codman Square",
    "311 requests trash pickup",
]


def _load_all_documents(source_dir: Path) -> list[tuple[str, str, dict]]:
    """Return (id, text, metadata) rows from the source Chroma collection."""
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


def reindex(
    source_dir: Path,
    target_dir: Path,
    *,
    batch_size: int = 64,
    wipe_target: bool = True,
    dry_run: bool = False,
) -> int:
    rows = _load_all_documents(source_dir)
    if not rows:
        print(f"No documents found in {source_dir}")
        return 0

    print(f"Source: {source_dir} ({len(rows)} documents)")
    print(f"Target: {target_dir}")

    if dry_run:
        print("DRY RUN — would re-embed and write documents only.")
        return len(rows)

    if wipe_target and target_dir.exists():
        print(f"Removing existing target store at {target_dir}")
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    embeddings = LocalBGEEmbeddings()
    vectordb = Chroma(
        persist_directory=str(target_dir),
        embedding_function=embeddings,
    )

    docs: list[Document] = []
    ids: list[str] = []
    for doc_id, text, metadata in rows:
        meta = dict(metadata)
        ids.append(doc_id)
        docs.append(Document(page_content=text, metadata=meta))

    written = 0
    started = time.perf_counter()
    for start in range(0, len(docs), batch_size):
        batch_docs = docs[start : start + batch_size]
        batch_ids = ids[start : start + batch_size]
        vectordb.add_documents(batch_docs, ids=batch_ids)
        written += len(batch_docs)
        print(f"  embedded {written}/{len(docs)} …")

    elapsed = time.perf_counter() - started
    print(f"Done: {written} documents in {elapsed:.1f}s → {target_dir}")
    return written


def validate_overlap(
    source_dir: Path,
    target_dir: Path,
    queries: list[str],
    k: int = 5,
) -> float:
    """Return fraction of queries where gemini and BGE top-1 sources match."""
    import retrieval as retrieval_mod

    configure_vectordb_paths(gemini_dir=source_dir, bge_dir=target_dir)
    gemini_db = Chroma(
        persist_directory=str(source_dir),
        embedding_function=GeminiEmbeddings(),
    )
    bge_db = Chroma(
        persist_directory=str(target_dir),
        embedding_function=LocalBGEEmbeddings(),
    )

    matches = 0
    for query in queries:
        retrieval_mod.VECTORDB_DIR = source_dir.resolve()
        gemini_hits = retrieve(query, k=k, vectordb=gemini_db)
        retrieval_mod.VECTORDB_DIR = target_dir.resolve()
        bge_hits = retrieve(query, k=k, vectordb=bge_db)

        g_src = (
            (gemini_hits.get("metadata") or [{}])[0].get("source")
            if gemini_hits.get("chunks")
            else None
        )
        b_src = (
            (bge_hits.get("metadata") or [{}])[0].get("source")
            if bge_hits.get("chunks")
            else None
        )
        if g_src and g_src == b_src:
            matches += 1
        print(f"  {query!r}: gemini={g_src!r} bge={b_src!r}")

    ratio = matches / len(queries) if queries else 0.0
    print(f"Top-1 source overlap: {matches}/{len(queries)} ({ratio:.0%})")
    return ratio


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-index Chroma store with local BGE embeddings")
    parser.add_argument(
        "--source",
        type=Path,
        default=VECTORDB_GEMINI_DIR,
        help="Gemini-embedded Chroma directory (default: vectordb_new)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=VECTORDB_BGE_DIR,
        help="Output BGE Chroma directory (default: vectordb_bge_v1)",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="After reindex, compare top-1 retrieval overlap on sample queries",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Skip reindex; only run validation (both stores must exist)",
    )
    parser.add_argument("--no-wipe", action="store_true", help="Append to target instead of wiping")
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()

    if not source.exists():
        print(f"Source vector store not found: {source}", file=sys.stderr)
        return 1

    if not args.validate_only:
        count = reindex(
            source,
            target,
            batch_size=args.batch_size,
            wipe_target=not args.no_wipe,
            dry_run=args.dry_run,
        )
        if count == 0:
            return 1

    if args.validate or args.validate_only:
        if not target.exists():
            print(f"Target store not found: {target}", file=sys.stderr)
            return 1
        validate_overlap(source, target, DEFAULT_SAMPLE_QUERIES)

    if not args.dry_run and not args.validate_only:
        print(
            "\nNext: set EMBED_PROVIDER=local in .env and restart the API "
            f"(active store: {target})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
