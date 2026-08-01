# Local Data and Vector Restoration

The checked-in fixture at `tests/fixtures/stage4_5/council_minutes.json` is synthetic,
officially shaped, and safe for deterministic tests. It contains a dated stable ID and a
competing unrelated housing item. It is not production data and is not automatically added
to the resident-serving vector store.

For a real local corpus:

1. Acquire only public Boston City Council minutes or transcripts through the existing
   repository-supported ingestion path.
2. Preserve stable source ID, title, publication date, publisher, official source type,
   source URL, and meeting body.
3. Chunk with the existing document processor.
4. Embed all chunks with the configured local model; never mix dimensions in one
   collection.
5. Build into a new local/staging collection, then verify it before switching the active
   configured path.
6. Run `scripts/stage4_5_parity_check.py` and retain its source-inventory manifest.
7. Run `tests/test_stage4_5_parity.py` before using the collection for local chat.

The current demo snapshot remains incomplete: 268 civic rows are tracked in MySQL, while
the active Chroma collection has 35 chunks and no identifiable City Council minutes.
