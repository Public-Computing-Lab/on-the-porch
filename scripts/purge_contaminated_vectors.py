#!/usr/bin/env python3
"""Purge DEMO/TEST/INTERNAL and known fixture filenames from the active Chroma index."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "on_the_porch" / "rag stuff"), str(ROOT / "on_the_porch"), str(ROOT)]

BAD_MARKERS = (
    "analysis",
    "digest",
    "reporting_note",
    "template",
    "fixtures/",
    "zoning_reference_overview",
    "imagine_boston_2030_public",
)


def main() -> None:
    from langchain_community.vectorstores import Chroma  # type: ignore
    from retrieval import get_embeddings, resolve_vectordb_dir  # type: ignore

    store = Chroma(
        collection_name="langchain",
        persist_directory=str(resolve_vectordb_dir()),
        embedding_function=get_embeddings(),
    )
    data = store._collection.get(include=["metadatas", "documents"])
    ids = data.get("ids") or []
    metas = data.get("metadatas") or []
    docs = data.get("documents") or []
    to_delete = []
    for index, meta in enumerate(metas):
        meta = meta or {}
        prov = str(meta.get("provenance_class") or "").upper()
        blob = " ".join(
            [
                str(meta.get("source") or ""),
                str(meta.get("path") or ""),
                str(meta.get("title") or ""),
                prov,
                str((docs[index] or "")[:120]),
            ]
        ).casefold()
        if prov in {"DEMO_FIXTURE", "TEST_ONLY", "INTERNAL_ANALYSIS"}:
            to_delete.append(ids[index])
            continue
        if any(marker in blob for marker in BAD_MARKERS):
            to_delete.append(ids[index])
    for start in range(0, len(to_delete), 200):
        batch = to_delete[start : start + 200]
        if batch:
            store.delete(ids=batch)
    print({"total": len(ids), "deleted": len(to_delete), "remaining": store._collection.count()})


if __name__ == "__main__":
    main()
