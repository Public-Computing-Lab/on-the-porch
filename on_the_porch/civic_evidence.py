"""Domain-neutral evidence contracts for deterministic civic tools.

Phase 3 wires these contracts into crime, documents, entity, and grounded
synthesis paths used by the authoritative model-led turn service.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from answering_kernel import EvidenceBundle as LegacyEvidenceBundle
from civic_tool_catalog import ToolId


MAX_FACTS = 32
MAX_RECORDS = 25
MAX_ATOMS = 48
MAX_SOURCES = 16
MAX_LIMITATIONS = 12
MAX_RECORD_BYTES = 4_000
MAX_TOOL_RESULT_BYTES = 48_000
MAX_EVIDENCE_BUNDLE_BYTES = 64_000
_STABLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_CITATION_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/?=&%#-]{0,511}$")
_PARTIAL_FAILURE_CODES = {
    "SOURCE_TEMPORARILY_UNAVAILABLE",
    "SOURCE_TIMEOUT",
    "SOURCE_RESPONSE_INVALID",
    "TOOL_DEADLINE_EXCEEDED",
    "TOOL_RESULT_INCOMPLETE",
    "LEGACY_PARTIAL_FAILURE",
}


class CivicEvidenceError(ValueError):
    """Raised when tool evidence violates the bounded public contract."""


class ToolResultStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CONFLICTING = "CONFLICTING"
    ABSENT = "ABSENT"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    STALE_BUT_USABLE = "STALE_BUT_USABLE"
    OUTSIDE_COVERAGE = "OUTSIDE_COVERAGE"


class CivicSourceType(str, Enum):
    BINDING_LAW = "BINDING_LAW"
    PLANNING_POLICY = "PLANNING_POLICY"
    OFFICIAL_OPERATIONAL_DATA = "OFFICIAL_OPERATIONAL_DATA"
    MEETING_DISCUSSION = "MEETING_DISCUSSION"
    JOURNALISM = "JOURNALISM"
    COMMUNITY_OPINION = "COMMUNITY_OPINION"


@dataclass(frozen=True)
class CivicSourceRecord:
    internal_source_id: str
    resident_source_name: str
    source_type: CivicSourceType
    authority: str
    publication_or_data_date: Optional[str]
    geography: Optional[Mapping[str, Any]]
    approved_citation_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, CivicSourceType):
            raise CivicEvidenceError("source type is invalid")
        _stable_id(self.internal_source_id, "internal source ID")
        _bounded_text(self.resident_source_name, 160, "resident source name")
        _bounded_text(self.authority, 160, "source authority")
        if not _CITATION_REF.fullmatch(self.approved_citation_reference):
            raise CivicEvidenceError("approved citation reference is invalid")
        if self.publication_or_data_date is not None:
            _bounded_text(self.publication_or_data_date, 32, "publication or data date")
        if self.geography is not None:
            _bounded_json(self.geography, "source geography", maximum_bytes=4_000)

    def bounded_for_model(self) -> Dict[str, Any]:
        return {
            "source_id": self.internal_source_id,
            "source_name": self.resident_source_name,
            "source_type": self.source_type.value,
            "authority": self.authority,
            "publication_or_data_date": self.publication_or_data_date,
            "geography": _json_copy(self.geography) if self.geography else None,
            "citation_reference": self.approved_citation_reference,
        }


@dataclass(frozen=True)
class CivicEvidenceAtom:
    evidence_id: str
    claim_type: str
    value: Any
    source_id: str
    citation_reference: str
    observed_at: Optional[str] = None

    def __post_init__(self) -> None:
        _stable_id(self.evidence_id, "evidence ID")
        _stable_id(self.source_id, "evidence source ID")
        _stable_id(self.claim_type, "claim type")
        if not _CITATION_REF.fullmatch(self.citation_reference):
            raise CivicEvidenceError("evidence citation reference is invalid")
        _bounded_json(self.value, "evidence value", maximum_bytes=8_000)
        if self.observed_at is not None:
            _bounded_text(self.observed_at, 40, "evidence observed time")

    def bounded_for_model(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "claim_type": self.claim_type,
            "value": _json_copy(self.value),
            "source_id": self.source_id,
            "citation_reference": self.citation_reference,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class CivicToolResult:
    request_id: str
    tool_id: str
    status: ToolResultStatus
    requested_scope: Mapping[str, Any]
    executed_scope: Mapping[str, Any]
    typed_facts: Mapping[str, Any] = field(default_factory=dict)
    records: Tuple[Mapping[str, Any], ...] = ()
    evidence_atoms: Tuple[CivicEvidenceAtom, ...] = ()
    source_records: Tuple[CivicSourceRecord, ...] = ()
    freshness: Mapping[str, Any] = field(default_factory=dict)
    coverage: Mapping[str, Any] = field(default_factory=dict)
    limitations: Tuple[str, ...] = ()
    missing_evidence: Tuple[str, ...] = ()
    partial_failures: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ToolResultStatus):
            raise CivicEvidenceError("tool result status is invalid")
        _stable_id(self.request_id, "tool request ID")
        _stable_id(self.tool_id, "tool ID")
        try:
            ToolId(self.tool_id)
        except ValueError as exc:
            raise CivicEvidenceError("tool result references an unregistered tool") from exc
        if len(self.typed_facts) > MAX_FACTS or len(self.records) > MAX_RECORDS:
            raise CivicEvidenceError("tool result facts or records exceed their bounds")
        if len(self.evidence_atoms) > MAX_ATOMS or len(self.source_records) > MAX_SOURCES:
            raise CivicEvidenceError("tool result evidence exceeds its bounds")
        if any(len(items) > MAX_LIMITATIONS for items in (
            self.limitations, self.missing_evidence, self.partial_failures,
        )):
            raise CivicEvidenceError("tool result diagnostics exceed their bounds")
        source_by_id = {source.internal_source_id: source for source in self.source_records}
        if len(source_by_id) != len(self.source_records):
            raise CivicEvidenceError("tool result source IDs must be unique")
        atom_ids = {atom.evidence_id for atom in self.evidence_atoms}
        if len(atom_ids) != len(self.evidence_atoms):
            raise CivicEvidenceError("tool result evidence IDs must be unique")
        for atom in self.evidence_atoms:
            source = source_by_id.get(atom.source_id)
            if source is None:
                raise CivicEvidenceError("evidence atom references an unavailable source")
            if atom.citation_reference != source.approved_citation_reference:
                raise CivicEvidenceError("evidence atom does not use its source's approved citation")
        for name, value in (
            ("requested scope", self.requested_scope),
            ("executed scope", self.executed_scope),
            ("typed facts", self.typed_facts),
            ("freshness", self.freshness),
            ("coverage", self.coverage),
        ):
            _bounded_json(value, name)
        for record in self.records:
            _bounded_json(record, "record", maximum_bytes=MAX_RECORD_BYTES)
        _bounded_messages(self.limitations, "limitations")
        _bounded_messages(self.missing_evidence, "missing evidence")
        for failure in self.partial_failures:
            if failure not in _PARTIAL_FAILURE_CODES:
                raise CivicEvidenceError("partial failure is not a sanitized code")

    def bounded_for_model(self) -> Dict[str, Any]:
        payload = {
            "request_id": self.request_id,
            "tool_id": self.tool_id,
            "status": self.status.value,
            "requested_scope": _json_copy(self.requested_scope),
            "executed_scope": _json_copy(self.executed_scope),
            "typed_facts": _json_copy(self.typed_facts),
            "records": [_json_copy(item) for item in self.records],
            "evidence_atoms": [item.bounded_for_model() for item in self.evidence_atoms],
            "source_records": [item.bounded_for_model() for item in self.source_records],
            "freshness": _json_copy(self.freshness),
            "coverage": _json_copy(self.coverage),
            "limitations": list(self.limitations),
            "missing_evidence": list(self.missing_evidence),
            "partial_failures": list(self.partial_failures),
        }
        _bounded_json(payload, "tool result model payload", maximum_bytes=MAX_TOOL_RESULT_BYTES)
        return payload


@dataclass(frozen=True)
class CivicEvidenceBundle:
    results: Tuple[CivicToolResult, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.results) <= 4:
            raise CivicEvidenceError("evidence bundle requires one through four tool results")
        request_ids = [result.request_id for result in self.results]
        if len(request_ids) != len(set(request_ids)):
            raise CivicEvidenceError("tool result request IDs must be unique")

    def bounded_for_model(self) -> Dict[str, Any]:
        payload = {"tool_results": [result.bounded_for_model() for result in self.results]}
        _bounded_json(payload, "evidence bundle model payload", maximum_bytes=MAX_EVIDENCE_BUNDLE_BYTES)
        return payload


_LEGACY_STATUS = {
    "COMPLETE": ToolResultStatus.COMPLETE,
    "PARTIAL": ToolResultStatus.PARTIAL,
    "STALE_BUT_USABLE": ToolResultStatus.STALE_BUT_USABLE,
    "PARTIALLY_COVERED": ToolResultStatus.PARTIAL,
    "OUTSIDE_COVERAGE": ToolResultStatus.OUTSIDE_COVERAGE,
    "NO_MATCHING_RECORDS": ToolResultStatus.ABSENT,
    "INSUFFICIENT_EVIDENCE": ToolResultStatus.ABSENT,
    "DEPENDENCY_UNAVAILABLE": ToolResultStatus.TEMPORARILY_UNAVAILABLE,
    "CONFLICTING_EVIDENCE": ToolResultStatus.CONFLICTING,
}


def from_legacy_311_bundle(
    bundle: LegacyEvidenceBundle,
    *,
    request_id: str = "legacy_311",
) -> CivicToolResult:
    """Adapt current 311 evidence without changing its producer or consumers."""
    if not isinstance(bundle, LegacyEvidenceBundle):
        raise CivicEvidenceError("legacy adapter requires an EvidenceBundle")
    sources: List[CivicSourceRecord] = []
    for index, raw in enumerate(bundle.source_records[:MAX_SOURCES]):
        source = dict(raw)
        internal_id = _source_id(source, index)
        citation = _citation_reference(source, internal_id)
        sources.append(CivicSourceRecord(
            internal_source_id=internal_id,
            resident_source_name=str(source.get("source") or source.get("name") or "Official civic data")[:160],
            source_type=CivicSourceType.OFFICIAL_OPERATIONAL_DATA,
            authority=str(source.get("authority") or "City of Boston")[:160],
            publication_or_data_date=_optional_text(source.get("data_through_date") or source.get("date"), 32),
            geography=_mapping_or_none(source.get("geography")),
            approved_citation_reference=citation,
        ))
    source_ids = {item.internal_source_id for item in sources}
    atoms: List[CivicEvidenceAtom] = []
    if sources:
        source_id = sources[0].internal_source_id
        citation = sources[0].approved_citation_reference
        for index, (name, value) in enumerate(list(bundle.facts.items())[:MAX_ATOMS]):
            atoms.append(CivicEvidenceAtom(
                evidence_id=f"legacy_311.fact_{index + 1}",
                claim_type=_claim_type(name, index),
                value=value,
                source_id=source_id,
                citation_reference=citation,
            ))
    if atoms and not source_ids:
        raise CivicEvidenceError("legacy facts require source metadata")
    status_name = getattr(bundle.evidence_status, "value", str(bundle.evidence_status))
    return CivicToolResult(
        request_id=request_id,
        tool_id="aggregate_311",
        status=_LEGACY_STATUS.get(status_name, ToolResultStatus.PARTIAL),
        requested_scope={
            "question_scope": _json_copy(bundle.question_scope),
            "geography": _json_copy(bundle.geography),
            "period": _json_copy(bundle.requested_period),
        },
        executed_scope={
            "geography": _json_copy(bundle.geography),
            "period": _json_copy(bundle.executed_period),
        },
        typed_facts=_json_copy(bundle.facts),
        records=tuple(_legacy_records(bundle.tool_results)),
        evidence_atoms=tuple(atoms),
        source_records=tuple(sources),
        freshness=_json_copy(bundle.freshness),
        coverage=_json_copy(bundle.coverage),
        limitations=tuple(str(item)[:300] for item in bundle.limitations[:MAX_LIMITATIONS]),
        missing_evidence=tuple(str(item)[:300] for item in bundle.missing_evidence[:MAX_LIMITATIONS]),
        partial_failures=tuple(
            "LEGACY_PARTIAL_FAILURE" for _ in bundle.partial_failures[:MAX_LIMITATIONS]
        ),
    )


def _legacy_records(tool_results: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    records: List[Mapping[str, Any]] = []
    for value in tool_results.values():
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, Mapping))
        elif isinstance(value, Mapping) and isinstance(value.get("records"), list):
            records.extend(item for item in value["records"] if isinstance(item, Mapping))
        if len(records) >= MAX_RECORDS:
            break
    return records[:MAX_RECORDS]


def _source_id(source: Mapping[str, Any], index: int) -> str:
    candidate = str(source.get("source_id") or source.get("source_key") or f"legacy_311_source_{index + 1}")
    candidate = re.sub(r"[^A-Za-z0-9_.:-]+", "_", candidate).strip("_")[:128]
    if not candidate or not candidate[0].isalpha():
        candidate = f"legacy_311_source_{index + 1}"
    return candidate


def _citation_reference(source: Mapping[str, Any], fallback: str) -> str:
    candidate = str(source.get("citation_reference") or source.get("url") or fallback)[:256]
    if _CITATION_REF.fullmatch(candidate):
        return candidate
    return fallback


def _claim_type(name: Any, index: int) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(name)).strip("_")[:128]
    return candidate if candidate and candidate[0].isalpha() else f"fact_{index + 1}"


def _stable_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise CivicEvidenceError(f"{label} is invalid")
    return value


def _bounded_text(value: Any, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CivicEvidenceError(f"{label} is invalid")
    return value


def _optional_text(value: Any, maximum: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:maximum] if text else None


def _mapping_or_none(value: Any) -> Optional[Mapping[str, Any]]:
    return value if isinstance(value, Mapping) else None


def _bounded_messages(values: Iterable[str], label: str) -> None:
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 300:
            raise CivicEvidenceError(f"{label} contain invalid text")


def _bounded_json(value: Any, label: str, *, maximum_bytes: int = 16_000) -> None:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise CivicEvidenceError(f"{label} exceeds its JSON bound")


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise CivicEvidenceError("evidence is not JSON-safe") from exc


__all__ = [
    "CivicEvidenceAtom",
    "CivicEvidenceBundle",
    "CivicEvidenceError",
    "CivicSourceRecord",
    "CivicSourceType",
    "CivicToolResult",
    "ToolResultStatus",
    "from_legacy_311_bundle",
]
