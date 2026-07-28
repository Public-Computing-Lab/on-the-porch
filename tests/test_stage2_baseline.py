from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage2_baseline",
    ROOT / "scripts" / "run_stage2_baseline.py",
)
assert SPEC is not None and SPEC.loader is not None
stage2_baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage2_baseline)


class Stage2BaselineTests(unittest.TestCase):
    def test_structured_sources_use_corpus_categories(self) -> None:
        events = stage2_baseline._normalize_sources(
            [{"type": "sql", "table": "events_materialized"}]
        )
        civic = stage2_baseline._normalize_sources(
            [{"type": "sql", "table": "service_requests_311"}]
        )
        self.assertEqual(events[0]["category"], "event_listing")
        self.assertEqual(civic[0]["category"], "official_data")

    def test_document_sources_use_corpus_categories(self) -> None:
        sources = stage2_baseline._normalize_sources(
            [
                {"type": "rag", "doc_type": "policy"},
                {"type": "rag", "doc_type": "calendar_event"},
            ]
        )
        self.assertEqual(
            [source["category"] for source in sources],
            ["official_document", "event_listing"],
        )

    def test_transcripts_represent_testimony_and_community_material(self) -> None:
        sources = stage2_baseline._normalize_sources(
            [{"type": "rag", "doc_type": "transcript"}]
        )
        self.assertEqual(
            [source["category"] for source in sources],
            ["meeting_transcript", "community_material"],
        )


if __name__ == "__main__":
    unittest.main()
