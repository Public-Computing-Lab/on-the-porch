"""Source and document provenance classes for resident-safe retrieval."""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet, Optional


class ProvenanceClass(str, Enum):
    OFFICIAL_PRIMARY = "OFFICIAL_PRIMARY"
    OFFICIAL_SECONDARY = "OFFICIAL_SECONDARY"
    JOURNALISM = "JOURNALISM"
    COMMUNITY_PUBLISHED = "COMMUNITY_PUBLISHED"
    CURATED_ENTITY_PROFILE = "CURATED_ENTITY_PROFILE"
    INTERNAL_ANALYSIS = "INTERNAL_ANALYSIS"
    DEMO_FIXTURE = "DEMO_FIXTURE"
    TEST_ONLY = "TEST_ONLY"


# Excluded from resident retrieval by default.
RESIDENT_EXCLUDED: FrozenSet[str] = frozenset({
    ProvenanceClass.DEMO_FIXTURE.value,
    ProvenanceClass.TEST_ONLY.value,
    ProvenanceClass.INTERNAL_ANALYSIS.value,
})

# Thin local corpora need at least this many active documents to be "healthy".
MIN_USEFUL_DOCUMENT_COUNTS = {
    "boston.policy_corpus": 1,
    "boston.zoning_references": 1,
    "dorchester.meeting_transcripts": 1,
    "dorchester.local_news": 3,
    "dorchester.community_documents": 2,
    "boston.city_announcements": 1,
}

# Map registry source_id → document corpora used by civic_documents.
SOURCE_ID_TO_CORPORA = {
    "boston.policy_corpus": ("POLICY",),
    "boston.zoning_references": ("POLICY",),
    "dorchester.meeting_transcripts": ("TRANSCRIPT",),
    "dorchester.local_news": ("NEWS",),
    "dorchester.community_documents": ("COMMUNITY",),
    "boston.city_announcements": ("POLICY",),
}

CORPUS_TO_SOURCE_IDS = {
    "POLICY": ("boston.policy_corpus", "boston.zoning_references", "boston.city_announcements"),
    "TRANSCRIPT": ("dorchester.meeting_transcripts",),
    "NEWS": ("dorchester.local_news",),
    "COMMUNITY": ("dorchester.community_documents",),
}


def normalize_provenance(value: Optional[str]) -> str:
    raw = str(value or "").strip().upper()
    try:
        return ProvenanceClass(raw).value
    except ValueError:
        return ProvenanceClass.OFFICIAL_SECONDARY.value


def is_resident_eligible(provenance: Optional[str]) -> bool:
    return normalize_provenance(provenance) not in RESIDENT_EXCLUDED


def classify_local_path(path: str, *, source_id: str) -> ProvenanceClass:
    """Classify staged local files without inventing official authority."""
    lower = path.replace("\\", "/").casefold()
    name = lower.rsplit("/", 1)[-1]
    if "/fixtures/" in lower or name.startswith("fixture_"):
        return ProvenanceClass.TEST_ONLY
    if "analysis" in name or name.endswith("_digest.txt") or "public_digest" in name:
        return ProvenanceClass.INTERNAL_ANALYSIS
    if "overview" in name and ("zoning" in name or "policy" in name):
        return ProvenanceClass.INTERNAL_ANALYSIS
    if source_id == "dorchester.local_news":
        if "editorial" in name or "policy_note" in name or "reporting_note" in name:
            return ProvenanceClass.INTERNAL_ANALYSIS
        return ProvenanceClass.JOURNALISM
    if source_id == "dorchester.community_documents":
        return ProvenanceClass.COMMUNITY_PUBLISHED
    if source_id == "dorchester.meeting_transcripts":
        return ProvenanceClass.COMMUNITY_PUBLISHED
    if source_id in {"boston.policy_corpus", "boston.zoning_references"}:
        # Local staging copies are secondary unless an official fetch is configured.
        return ProvenanceClass.OFFICIAL_SECONDARY
    if source_id == "otp.civic_entity_profiles":
        return ProvenanceClass.CURATED_ENTITY_PROFILE
    return ProvenanceClass.OFFICIAL_SECONDARY


def resident_label_for(provenance: Optional[str], source_type: Optional[str] = None) -> str:
    cls = normalize_provenance(provenance)
    return {
        ProvenanceClass.OFFICIAL_PRIMARY.value: "Official source",
        ProvenanceClass.OFFICIAL_SECONDARY.value: "Secondary official / curated reference",
        ProvenanceClass.JOURNALISM.value: "Journalism",
        ProvenanceClass.COMMUNITY_PUBLISHED.value: "Community-published",
        ProvenanceClass.CURATED_ENTITY_PROFILE.value: "Curated organization profile",
        ProvenanceClass.INTERNAL_ANALYSIS.value: "Internal analysis (not resident evidence)",
        ProvenanceClass.DEMO_FIXTURE.value: "Demo fixture (not resident evidence)",
        ProvenanceClass.TEST_ONLY.value: "Test-only (not resident evidence)",
    }.get(cls, source_type or "Source")


def classify_ingest_health(
    *,
    source_id: str,
    success: bool,
    document_count: int,
    chunk_count: int,
    enabled: bool = True,
    indexing_eligible: bool = True,
    configured: bool = True,
) -> str:
    if not configured:
        return "NOT_CONFIGURED"
    if not enabled:
        return "disabled"
    if not success:
        return "failed"
    if indexing_eligible and document_count <= 0 and chunk_count <= 0:
        return "EMPTY"
    minimum = MIN_USEFUL_DOCUMENT_COUNTS.get(source_id)
    if minimum is not None and document_count < minimum:
        return "INCOMPLETE"
    if indexing_eligible and chunk_count <= 0:
        return "INCOMPLETE"
    return "healthy"


__all__ = [
    "CORPUS_TO_SOURCE_IDS",
    "MIN_USEFUL_DOCUMENT_COUNTS",
    "ProvenanceClass",
    "RESIDENT_EXCLUDED",
    "SOURCE_ID_TO_CORPORA",
    "classify_ingest_health",
    "classify_local_path",
    "is_resident_eligible",
    "normalize_provenance",
    "resident_label_for",
]
