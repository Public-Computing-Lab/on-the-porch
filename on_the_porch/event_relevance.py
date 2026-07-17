"""Bounded, deterministic Dorchester event-relevance classification.

Authority comes only from:
- labeled physical Location/Where fields;
- a small vetted exact-address registry;
- a small vetted Dorchester-serving virtual program allowlist.

Title text, organizer address alone, source name alone, URL, and newsletter origin
never establish Dorchester relevance by themselves.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


class EventRelevanceKind(str, Enum):
    DORCHESTER_PHYSICAL = "DORCHESTER_PHYSICAL"
    DORCHESTER_SUBAREA = "DORCHESTER_SUBAREA"
    DORCHESTER_SERVING_VIRTUAL = "DORCHESTER_SERVING_VIRTUAL"
    OUTSIDE_DORCHESTER_PHYSICAL = "OUTSIDE_DORCHESTER_PHYSICAL"
    OUTSIDE_DORCHESTER_VIRTUAL = "OUTSIDE_DORCHESTER_VIRTUAL"
    UNKNOWN_LOCATION = "UNKNOWN_LOCATION"
    UNKNOWN_RELEVANCE = "UNKNOWN_RELEVANCE"


class EventRelevanceReason(str, Enum):
    EXACT_ADDRESS_REGISTRY = "exact_address_registry"
    LABELED_DORCHESTER_ADDRESS = "labeled_dorchester_address"
    LABELED_SUBAREA_ADDRESS = "labeled_subarea_address"
    VETTED_VIRTUAL_PROGRAM = "vetted_virtual_program"
    LABELED_OUTSIDE_CITY = "labeled_outside_city"
    LABELED_VIRTUAL_UNVETTED = "labeled_virtual_unvetted"
    MISSING_LOCATION_LABEL = "missing_location_label"
    UNMAPPED_LOCATION = "unmapped_location"


# Legacy status values used by existing call sites / tests.
class EventLocationStatus(str, Enum):
    DORCHESTER = "dorchester"
    CODMAN_SQUARE = "codman_square"
    FIELDS_CORNER = "fields_corner"
    VIRTUAL = "virtual"
    UNKNOWN = "unknown"
    OUTSIDE = "outside"


_LABEL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(DATE|TIME|LOCATION|WHERE|CONTACT|URL|COST|REGISTRATION|RSVP)\s*:\s*",
    flags=re.IGNORECASE,
)

_OUTSIDE_CITY_MARKERS = (
    "randolph",
    "quincy",
    "cambridge",
    "newton",
    "brookline",
    "jamaica plain",
    "ros lindale",
    "roslindale",
    "allston",
    "brighton",
    "somerville",
    "medford",
    "malden",
    "chelsea",
    "everett",
    "dedham",
    "milton",
    "hyde park",
    "mattapan",
    "roxbury",
    "south boston",
    "east boston",
    "charlestown",
    "back bay",
    "downtown boston",
)

_VIRTUAL_MARKERS = frozenset(
    {
        "virtual",
        "online",
        "online only",
        "virtual event",
        "remote",
        "zoom",
        "webinar",
    }
)

# Exact labeled Location strings → Dorchester / subarea.


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_EXACT_LOCATION_REGISTRY = {
    _normalize(
        "The Great Hall at Codman Square Health Center, 6 Norfolk St., Dorchester, MA"
    ): (EventLocationStatus.CODMAN_SQUARE, EventRelevanceKind.DORCHESTER_SUBAREA),
    _normalize(
        "Great Hall at Codman Square Health Center, 6 Norfolk St., Dorchester, MA"
    ): (EventLocationStatus.CODMAN_SQUARE, EventRelevanceKind.DORCHESTER_SUBAREA),
    _normalize(
        "Second Church of Dorchester, 44 Moultrie St., Dorchester"
    ): (EventLocationStatus.DORCHESTER, EventRelevanceKind.DORCHESTER_PHYSICAL),
    _normalize(
        "Second Church of Dorchester, 44 Moultrie Street, Dorchester Center, MA"
    ): (EventLocationStatus.DORCHESTER, EventRelevanceKind.DORCHESTER_PHYSICAL),
}

@dataclass(frozen=True)
class VettedVirtualProgram:
    """Reviewed data exception for a Dorchester-serving virtual program."""

    canonical_id: str
    exact_titles: Tuple[str, ...]
    allowed_sources: Tuple[str, ...]
    service_area: EventLocationStatus
    evidence_basis: str


# Stable event-series IDs are not available in weekly_events. Until ingestion
# provides them, keep exceptions in one exact-match registry with a documented
# evidence basis. Source alone never qualifies an event.
VETTED_VIRTUAL_PROGRAMS: Tuple[VettedVirtualProgram, ...] = (
    VettedVirtualProgram(
        canonical_id="codman_virtual_baby_cafe",
        exact_titles=("virtual baby cafe",),
        allowed_sources=("codman",),
        service_area=EventLocationStatus.CODMAN_SQUARE,
        evidence_basis=(
            "Codman Square Health Center program listing; recurring rows use the "
            "exact title and source but do not expose a stable series ID."
        ),
    ),
)


@dataclass(frozen=True)
class EventRelevance:
    kind: EventRelevanceKind
    location_status: EventLocationStatus
    reason: EventRelevanceReason
    display_location: str
    is_virtual: bool
    subarea_id: Optional[str] = None


def extract_labeled_event_location(raw_text: Any) -> str:
    raw = str(raw_text or "")
    matches = list(_LABEL_PATTERN.finditer(raw))
    selected = ""
    for index, match in enumerate(matches):
        if match.group(1).lower() not in {"location", "where"}:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        value = raw[match.end():end].strip().splitlines()
        if not value:
            continue
        selected = value[0].strip(" ,;|")
        if selected:
            selected = selected[:300]
        else:
            selected = ""
    return selected


def _outside_city(normalized_location: str) -> bool:
    padded = f" {normalized_location} "
    return any(f" {marker} " in padded for marker in _OUTSIDE_CITY_MARKERS)


def _vetted_virtual_program(
    row: Mapping[str, Any],
) -> Optional[EventLocationStatus]:
    name = _normalize(row.get("event_name"))
    source = _normalize(row.get("source_pdf"))
    for program in VETTED_VIRTUAL_PROGRAMS:
        titles = {_normalize(value) for value in program.exact_titles}
        sources = {_normalize(value) for value in program.allowed_sources}
        if name not in titles or source not in sources:
            continue
        return program.service_area
    return None


def classify_event_relevance(row: Mapping[str, Any]) -> EventRelevance:
    labeled = extract_labeled_event_location(row.get("raw_text"))
    normalized = _normalize(labeled)
    if not normalized:
        return EventRelevance(
            EventRelevanceKind.UNKNOWN_LOCATION,
            EventLocationStatus.UNKNOWN,
            EventRelevanceReason.MISSING_LOCATION_LABEL,
            "",
            False,
        )

    if normalized in _VIRTUAL_MARKERS or normalized.startswith("virtual "):
        program_status = _vetted_virtual_program(row)
        if program_status is not None:
            subarea = (
                program_status.value
                if program_status
                in {EventLocationStatus.CODMAN_SQUARE, EventLocationStatus.FIELDS_CORNER}
                else None
            )
            return EventRelevance(
                EventRelevanceKind.DORCHESTER_SERVING_VIRTUAL,
                EventLocationStatus.VIRTUAL,
                EventRelevanceReason.VETTED_VIRTUAL_PROGRAM,
                "Virtual",
                True,
                subarea_id=subarea,
            )
        return EventRelevance(
            EventRelevanceKind.UNKNOWN_RELEVANCE,
            EventLocationStatus.VIRTUAL,
            EventRelevanceReason.LABELED_VIRTUAL_UNVETTED,
            "Virtual",
            True,
        )

    registry_hit = _EXACT_LOCATION_REGISTRY.get(normalized)
    if registry_hit is not None:
        status, kind = registry_hit
        subarea = (
            status.value
            if status in {EventLocationStatus.CODMAN_SQUARE, EventLocationStatus.FIELDS_CORNER}
            else None
        )
        return EventRelevance(
            kind,
            status,
            EventRelevanceReason.EXACT_ADDRESS_REGISTRY,
            labeled,
            False,
            subarea_id=subarea,
        )

    if _outside_city(normalized):
        return EventRelevance(
            EventRelevanceKind.OUTSIDE_DORCHESTER_PHYSICAL,
            EventLocationStatus.OUTSIDE,
            EventRelevanceReason.LABELED_OUTSIDE_CITY,
            labeled,
            False,
        )

    if "codman square" in normalized:
        return EventRelevance(
            EventRelevanceKind.DORCHESTER_SUBAREA,
            EventLocationStatus.CODMAN_SQUARE,
            EventRelevanceReason.LABELED_SUBAREA_ADDRESS,
            labeled,
            False,
            subarea_id="codman_square",
        )
    if "fields corner" in normalized:
        return EventRelevance(
            EventRelevanceKind.DORCHESTER_SUBAREA,
            EventLocationStatus.FIELDS_CORNER,
            EventRelevanceReason.LABELED_SUBAREA_ADDRESS,
            labeled,
            False,
            subarea_id="fields_corner",
        )
    if re.search(r"\bdorchester\b", normalized) and not _outside_city(normalized):
        return EventRelevance(
            EventRelevanceKind.DORCHESTER_PHYSICAL,
            EventLocationStatus.DORCHESTER,
            EventRelevanceReason.LABELED_DORCHESTER_ADDRESS,
            labeled,
            False,
        )

    return EventRelevance(
        EventRelevanceKind.UNKNOWN_RELEVANCE,
        EventLocationStatus.UNKNOWN,
        EventRelevanceReason.UNMAPPED_LOCATION,
        labeled,
        False,
    )


def classify_event_location(row: Mapping[str, Any]) -> EventLocationStatus:
    """Backward-compatible location status derived from relevance classification."""
    return classify_event_relevance(row).location_status


_DEFAULT_DORCHESTER_KINDS = frozenset(
    {
        EventRelevanceKind.DORCHESTER_PHYSICAL,
        EventRelevanceKind.DORCHESTER_SUBAREA,
        EventRelevanceKind.DORCHESTER_SERVING_VIRTUAL,
    }
)


def matches_requested_scope(
    *,
    requested_location_id: str,
    relevance: EventRelevance,
    modality: Optional[str] = None,
) -> bool:
    if modality == "virtual" and not relevance.is_virtual:
        return False
    if modality == "in_person" and relevance.is_virtual:
        return False

    if requested_location_id == "dorchester":
        return relevance.kind in _DEFAULT_DORCHESTER_KINDS
    if requested_location_id == "codman_square":
        if relevance.location_status is EventLocationStatus.CODMAN_SQUARE:
            return True
        return (
            relevance.kind is EventRelevanceKind.DORCHESTER_SERVING_VIRTUAL
            and relevance.subarea_id == "codman_square"
        )
    if requested_location_id == "fields_corner":
        if relevance.location_status is EventLocationStatus.FIELDS_CORNER:
            return True
        return (
            relevance.kind is EventRelevanceKind.DORCHESTER_SERVING_VIRTUAL
            and relevance.subarea_id == "fields_corner"
        )
    return False


def is_default_sidebar_event(row: Mapping[str, Any]) -> bool:
    return classify_event_relevance(row).kind in _DEFAULT_DORCHESTER_KINDS
