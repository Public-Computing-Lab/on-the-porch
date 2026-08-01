"""Current-turn evidence-domain checks and resident-safe limitations."""

from __future__ import annotations

from itertools import zip_longest
from typing import Any, Dict, Iterable, List, Optional, Tuple


OFFICIAL_COUNCIL_MINUTES = "official_council_minutes"

_COUNCIL_MARKERS = (
    "boston city council",
    "city council",
    "council meeting",
    "council hearing",
    "council committee",
)
_MEETING_RECORD_MARKERS = ("minutes", "transcript", "hearing", "meeting", "committee")
_TRANSPORTATION_MARKERS = (
    "transportation",
    "transit",
    "traffic",
    "mobility",
    "mbta",
    "bus",
    "rail",
    "street",
    "road",
    "sidewalk",
    "pedestrian",
    "bicycle",
    "bike",
)


def _searchable_text(chunk: str, metadata: Dict[str, Any]) -> str:
    values: List[str] = [chunk or ""]
    for key in (
        "title",
        "source",
        "publisher",
        "source_type",
        "source_category",
        "doc_type",
        "folder_category",
        "url",
        "source_url",
    ):
        value = metadata.get(key)
        if value is not None:
            values.append(str(value))
    return " ".join(values).lower()


def _is_official_source(metadata: Dict[str, Any]) -> bool:
    source_type = str(metadata.get("source_type") or metadata.get("source_category") or "").lower()
    trust_tier = str(metadata.get("trust_tier") or "").strip()
    provenance = " ".join(
        str(metadata.get(key) or "")
        for key in ("publisher", "source", "url", "source_url")
    ).lower()
    return (
        source_type in {"official", "official_primary", "official_secondary"}
        or trust_tier == "1"
        or "boston.gov" in provenance
        or "boston city council" in provenance
    )


def _matches_required_concepts(text: str, concepts: Iterable[str]) -> bool:
    for concept in concepts:
        normalized = str(concept or "").strip().lower()
        if not normalized:
            continue
        if normalized == "transportation":
            if not any(marker in text for marker in _TRANSPORTATION_MARKERS):
                return False
        elif normalized not in text:
            return False
    return True


def filter_evidence_for_domain(
    chunks: List[str],
    metadata: List[Dict[str, Any]],
    *,
    evidence_domain: Optional[str],
    required_concepts: Optional[List[str]] = None,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Keep only evidence that satisfies a plan's explicit source-domain contract."""
    if evidence_domain != OFFICIAL_COUNCIL_MINUTES:
        return chunks, metadata

    kept_chunks: List[str] = []
    kept_metadata: List[Dict[str, Any]] = []
    for chunk, raw_meta in zip_longest(chunks, metadata, fillvalue={}):
        if not isinstance(chunk, str) or not chunk.strip():
            continue
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        text = _searchable_text(chunk, meta)
        if not any(marker in text for marker in _COUNCIL_MARKERS):
            continue
        if not any(marker in text for marker in _MEETING_RECORD_MARKERS):
            continue
        if not _is_official_source(meta):
            continue
        if not _matches_required_concepts(text, required_concepts or []):
            continue
        kept_chunks.append(chunk)
        kept_metadata.append(meta)
    return kept_chunks, kept_metadata


def resident_evidence_limitation(
    question: str,
    *,
    evidence_domain: Optional[str] = None,
    unavailable: bool,
) -> str:
    """Describe missing evidence without exposing plan or adapter identifiers."""
    q = (question or "").lower()
    if evidence_domain == OFFICIAL_COUNCIL_MINUTES or (
        "council" in q and any(term in q for term in ("minutes", "transcript", "hearing"))
    ):
        if "dorchester" in q and any(term in q for term in _TRANSPORTATION_MARKERS[:4]):
            subject = " about Dorchester transportation projects"
        elif "dorchester" in q:
            subject = " related to Dorchester"
        elif any(term in q for term in _TRANSPORTATION_MARKERS[:4]):
            subject = " about transportation projects"
        else:
            subject = " relevant to your question"
        if unavailable:
            return f"I couldn't retrieve relevant Boston City Council minutes{subject} right now."
        return (
            f"I couldn't find relevant official Boston City Council minutes{subject} "
            "in the available sources."
        )
    if "311" in q or "service request" in q:
        if unavailable:
            return "I couldn't retrieve the relevant Boston 311 information right now."
        return "I couldn't find relevant Boston 311 information in the available sources."
    if any(term in q for term in ("crime", "shooting", "homicide", "police incident")):
        if unavailable:
            return "I couldn't retrieve the relevant public-safety records right now."
        return "I couldn't find relevant public-safety records in the available sources."
    if any(term in q for term in ("event", "calendar", "happening", "weekend")):
        if unavailable:
            return "I couldn't retrieve the relevant event information right now."
        return "I couldn't find relevant event information in the available sources."
    if unavailable:
        return "I couldn't retrieve enough relevant information to answer that reliably right now."
    return "I couldn't find enough relevant information to answer that reliably."
