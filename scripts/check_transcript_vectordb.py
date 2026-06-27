#!/usr/bin/env python3
"""Verify transcript chunks exist in the vector DB (Phase 2 monitoring)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "on_the_porch"))
sys.path.insert(0, str(ROOT / "on_the_porch" / "rag stuff"))

import retrieval  # noqa: E402


def main() -> int:
    min_chunks = int(os.getenv("TRANSCRIPT_MIN_CHUNKS", "50"))
    retrieval.clear_query_embedding_cache()
    db = retrieval.load_vectordb()
    if db is None:
        print("FAIL: vector DB not loaded")
        return 1

    try:
        data = db.get(where={"doc_type": "transcript"}, include=[])
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not query vectordb: {exc}")
        return 1

    count = len(data.get("ids") or [])
    print(f"Transcript chunks in vectordb: {count} (minimum {min_chunks})")
    if count < min_chunks:
        print("FAIL: transcript count below threshold — re-run Google Drive ingest")
        return 1
    print("PASS: transcript vectordb healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
