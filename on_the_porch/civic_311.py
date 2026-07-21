"""Bounded, evidence-grounded 311 answering for the five supported civic families.

The module is deliberately self-contained: it maps reviewed resident language to
real Boston 311 categories, runs a fixed registry of parameterized MySQL queries,
and returns structured evidence plus a resident-safe answer.  It does not use
vectors, model-generated SQL, or public vector writes.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from calendar import month_name
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from answering_kernel import (
    AnswerMode,
    CivicTelemetry,
    EvidenceBundle,
    EvidenceStatus,
    GlobalDeadline,
    PlannerSource,
    StrictToolPlan,
    ToolSpec,
    bounded_strings,
)
from resident_response import internal_terms
from semantic_dialogue_controller import (
    CapabilityIntent,
    ClarificationReason,
    DialogueDomain,
    DialogueRelationship,
    DomainHint,
    ResponseStrategy,
    TrustedReferenceTarget,
    TurnKind,
)
from source_freshness import (
    CoverageStatus,
    FreshnessStatus,
    SOURCE_311,
    assess_freshness,
    load_source_status,
    slim_freshness_metadata,
)


MAX_CONTEXT_BYTES = 2048
MAX_TOOL_WORKERS = 4
DEFAULT_LOOKBACK_DAYS = 7
SOURCE_LABEL = "Boston 311 service-request data"
SOURCE_AUTHORITY = "Official City of Boston service-request data"


class IssueFamily(str, Enum):
    ROAD_SURFACE_POTHOLE = "ROAD_SURFACE_POTHOLE"
    DRAINAGE_AND_FLOODING = "DRAINAGE_AND_FLOODING"
    STREET_AND_PAVEMENT_CONDITION = "STREET_AND_PAVEMENT_CONDITION"
    TRASH_AND_ILLEGAL_DUMPING = "TRASH_AND_ILLEGAL_DUMPING"
    STREETLIGHT = "STREETLIGHT"


class IssueScope(str, Enum):
    SPECIFIC_FAMILY = "SPECIFIC_FAMILY"
    ALL_311 = "ALL_311"
    UNSPECIFIED = "UNSPECIFIED"


class ToolName(str, Enum):
    ISSUE_COUNT = "get_311_issue_count"
    STATUS_BREAKDOWN = "get_311_status_breakdown"
    TOP_STREETS = "get_311_top_streets"
    RECENT_EXAMPLES = "get_311_recent_examples"
    PERIOD_COMPARISON = "compare_311_periods"
    CATEGORY_DISTRIBUTION = "get_311_category_distribution"
    SOURCE_FRESHNESS = "get_311_source_freshness"


class OutputName(str, Enum):
    ISSUE_COUNT = "ISSUE_COUNT"
    STATUS_BREAKDOWN = "STATUS_BREAKDOWN"
    TOP_STREETS = "TOP_STREETS"
    RECENT_EXAMPLES = "RECENT_EXAMPLES"
    PERIOD_COMPARISON = "PERIOD_COMPARISON"
    CATEGORY_DISTRIBUTION = "CATEGORY_DISTRIBUTION"
    SOURCE_FRESHNESS = "SOURCE_FRESHNESS"
    MATCHING_REQUESTS = "MATCHING_REQUESTS"


@dataclass(frozen=True)
class FamilyDefinition:
    family: IssueFamily
    label: str
    aliases: Tuple[str, ...]
    service_names: Tuple[str, ...]
    case_topics: Tuple[str, ...] = ()
    exclusions: Tuple[str, ...] = ()


# These values are reviewed against the local source schema rather than inferred
# from resident vocabulary.  Keep the source mapping deliberately narrow.
FAMILY_DEFINITIONS: Tuple[FamilyDefinition, ...] = (
    FamilyDefinition(
        IssueFamily.ROAD_SURFACE_POTHOLE,
        "pothole",
        (
            "pothole", "potholes", "road hole", "hole in the road", "hole in the street",
            "road crater", "broken road surface",
        ),
        ("Request for Pothole Repair", "BWSC Pothole"),
        ("Pothole",),
    ),
    FamilyDefinition(
        IssueFamily.DRAINAGE_AND_FLOODING,
        "drainage and flooding",
        (
            "drainage", "flooding", "street flooding", "water collecting", "standing water",
            "drain", "drains", "storm drain", "storm drains", "blocked drain", "clogged storm drain", "water not draining",
            "water backing up", "catch basin", "catchbasin",
        ),
        ("Catchbasin",),
        ("Catchbasin",),
        ("basement flooding",),
    ),
    FamilyDefinition(
        IssueFamily.STREET_AND_PAVEMENT_CONDITION,
        "street and pavement condition",
        (
            "cracked road", "broken pavement", "damaged pavement", "road condition",
            "road conditions", "damaged road surface", "damaged road surfaces",
            "pavement condition", "street damage", "sidewalk repair", "roadway repair", "sidewalk cover",
        ),
        (
            "Roadway Repair", "Sidewalk Repair", "Sidewalk Repair (Make Safe)",
            "Sidewalk Cover / Manhole",
        ),
    ),
    FamilyDefinition(
        IssueFamily.TRASH_AND_ILLEGAL_DUMPING,
        "trash and illegal dumping",
        (
            "trash", "garbage", "dumping", "illegal dumping", "trash dumped", "garbage left on the street",
            "abandoned trash", "overflowing public trash", "dumped furniture",
            "missed trash", "missed recycling", "overflowing dumpster",
        ),
        (
            "Illegal Dumping", "Improper Storage of Trash (Barrels)",
            "Missed Trash/Recycling/Yard Waste/Bulk Item",
            "Overflowing or Un-kept Dumpster", "Trash on Vacant Lot",
            "Empty Litter Basket", "Litter Basket Maintenance",
            "Park Litter & Debris", "Park Overflowing Trash Can",
        ),
    ),
    FamilyDefinition(
        IssueFamily.STREETLIGHT,
        "streetlight",
        (
            "streetlight", "streetlights", "streetlight out", "street light out", "broken streetlight",
            "broken street light", "dark street", "lamp post not working",
            "street lighting issue",
        ),
        ("Street Light Outage", "Street Light Other", "Street Light Knockdown"),
    ),
)
_FAMILY_BY_ID = {definition.family.value: definition for definition in FAMILY_DEFINITIONS}

TOOL_REGISTRY: Dict[str, ToolSpec] = {
    ToolName.ISSUE_COUNT.value: ToolSpec(ToolName.ISSUE_COUNT.value, True, 2.0, 1),
    ToolName.STATUS_BREAKDOWN.value: ToolSpec(ToolName.STATUS_BREAKDOWN.value, False, 2.0, 8),
    ToolName.TOP_STREETS.value: ToolSpec(ToolName.TOP_STREETS.value, False, 2.0, 5),
    ToolName.RECENT_EXAMPLES.value: ToolSpec(ToolName.RECENT_EXAMPLES.value, False, 2.0, 4),
    ToolName.PERIOD_COMPARISON.value: ToolSpec(ToolName.PERIOD_COMPARISON.value, True, 2.0, 2),
    ToolName.CATEGORY_DISTRIBUTION.value: ToolSpec(ToolName.CATEGORY_DISTRIBUTION.value, True, 2.0, 6),
    ToolName.SOURCE_FRESHNESS.value: ToolSpec(ToolName.SOURCE_FRESHNESS.value, True, 1.0, 1),
}


@dataclass(frozen=True)
class CivicPeriod:
    kind: str
    label: str
    start: date
    end_exclusive: date

    def __post_init__(self) -> None:
        if self.end_exclusive <= self.start:
            raise ValueError("311 periods must be half-open and non-empty")
        if (self.end_exclusive - self.start).days > 366:
            raise ValueError("311 periods must be bounded to one year")

    @property
    def days(self) -> int:
        return (self.end_exclusive - self.start).days

    def to_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "label": self.label,
            "start_date": self.start.isoformat(),
            "end_date_exclusive": self.end_exclusive.isoformat(),
        }


@dataclass(frozen=True)
class CivicGeography:
    label: str = "Dorchester"
    kind: str = "DORCHESTER"
    street: Optional[str] = None
    subarea: Optional[str] = None
    filterable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "street": self.street,
            "subarea": self.subarea,
            "filterable": self.filterable,
        }


@dataclass(frozen=True)
class Active311FactContext:
    """Small, trusted context stored in the MySQL thread-state envelope."""

    issue_family: Optional[str]
    geography: CivicGeography
    period_kind: str
    executed_period: CivicPeriod
    data_through_date: Optional[str]
    freshness_status: str
    coverage_status: str
    outputs: Tuple[str, ...]
    issue_scope: str = IssueScope.SPECIFIC_FAMILY.value
    source_key: str = SOURCE_311
    semantic_revision: int = 1

    def to_dict(self) -> Dict[str, Any]:
        value = {
            "domain": "311",
            "issue_family": self.issue_family,
            "issue_scope": self.issue_scope,
            "geography": self.geography.to_dict(),
            "period_kind": self.period_kind,
            "executed_period": self.executed_period.to_dict(),
            "data_through_date": self.data_through_date,
            "freshness_status": self.freshness_status,
            "coverage_status": self.coverage_status,
            "outputs": list(self.outputs),
            "source_key": self.source_key,
            "semantic_revision": self.semantic_revision,
        }
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_CONTEXT_BYTES:
            raise ValueError("active 311 context exceeds the size limit")
        return value

    @classmethod
    def from_mapping(cls, raw: Any) -> Optional["Active311FactContext"]:
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or raw.get("domain") != "311":
            return None
        try:
            family = str(raw.get("issue_family") or "") or None
            scope = str(raw.get("issue_scope") or IssueScope.SPECIFIC_FAMILY.value)
            if scope not in {item.value for item in IssueScope}:
                return None
            if scope == IssueScope.SPECIFIC_FAMILY.value and family not in _FAMILY_BY_ID:
                return None
            if scope != IssueScope.SPECIFIC_FAMILY.value and family is not None:
                return None
            geo_raw = raw.get("geography")
            period_raw = raw.get("executed_period")
            if not isinstance(geo_raw, Mapping) or not isinstance(period_raw, Mapping):
                return None
            street = _bounded_optional_text(geo_raw.get("street"), 96)
            subarea = _bounded_optional_text(geo_raw.get("subarea"), 64)
            geo = CivicGeography(
                label=_bounded_optional_text(geo_raw.get("label"), 64) or "Dorchester",
                kind=_bounded_optional_text(geo_raw.get("kind"), 32) or "DORCHESTER",
                street=street,
                subarea=subarea,
                filterable=bool(geo_raw.get("filterable", True)),
            )
            period = CivicPeriod(
                kind=_bounded_optional_text(period_raw.get("kind"), 32) or "RECENT",
                label=_bounded_optional_text(period_raw.get("label"), 96) or "the selected period",
                start=date.fromisoformat(str(period_raw.get("start_date"))),
                end_exclusive=date.fromisoformat(str(period_raw.get("end_date_exclusive"))),
            )
            outputs_raw = raw.get("outputs")
            if not isinstance(outputs_raw, list):
                return None
            outputs = bounded_strings(outputs_raw, limit=7, maximum_length=48)
            return cls(
                issue_family=family,
                geography=geo,
                period_kind=_bounded_optional_text(raw.get("period_kind"), 32) or period.kind,
                executed_period=period,
                data_through_date=_bounded_optional_text(raw.get("data_through_date"), 10),
                freshness_status=_bounded_optional_text(raw.get("freshness_status"), 32) or "UNKNOWN",
                coverage_status=_bounded_optional_text(raw.get("coverage_status"), 32) or "UNKNOWN",
                outputs=outputs,
                issue_scope=scope,
                source_key=SOURCE_311,
                semantic_revision=int(raw.get("semantic_revision") or 1),
            )
        except (TypeError, ValueError):
            return None


@dataclass
class Civic311Result:
    answer: str
    mode: AnswerMode
    sources: List[Dict[str, Any]]
    evidence: EvidenceBundle
    plan: StrictToolPlan
    telemetry: CivicTelemetry
    next_context: Optional[Dict[str, Any]]
    update_context: bool

    def to_agent_result(self) -> Dict[str, Any]:
        diagnostics = {
            "planner_source": self.telemetry.planner_source.value,
            "planner_ms": self.telemetry.planner_ms,
            "tool_execution_ms": self.telemetry.tool_execution_ms,
            "evidence_status": self.evidence.evidence_status.value,
            "answer_mode": self.mode.value,
            "tool_count": self.telemetry.tool_count,
            "synthesis_ms": self.telemetry.synthesis_ms,
            "validation_ms": self.telemetry.validation_ms,
            "repair_ms": self.telemetry.repair_ms,
            "persistence_ms": self.telemetry.persistence_ms,
            "total_latency_ms": self.telemetry.total_latency_ms,
            "llm_call_count": self.telemetry.llm_call_count,
            "planner_call_count": self.telemetry.planner_call_count,
            "synthesis_call_count": self.telemetry.synthesis_call_count,
            "repair_call_count": self.telemetry.repair_call_count,
            "embedding_call_count": 0,
            "fallback_used": self.telemetry.fallback_used,
            "timeout_stage": self.telemetry.timeout_stage,
        }
        trusted_result_summary: Optional[Dict[str, Any]] = None
        count_result = self.evidence.tool_results.get(ToolName.ISSUE_COUNT.value)
        count = count_result.get("count") if isinstance(count_result, Mapping) else None
        if self.plan.intent == "exact_count" and isinstance(count, int) and count >= 0:
            trusted_result_summary = {
                "kind": "CIVIC_311_COUNT",
                "status": self.evidence.evidence_status.value,
                "count": count,
                "request_label": _request_label(
                    self.evidence.issue_family,
                    all_311=self.plan.issue_scope == IssueScope.ALL_311.value,
                ),
                "geography_label": str(self.evidence.geography.get("label") or "Dorchester")[:96],
                "executed_start_date": self.evidence.executed_period.get("start_date"),
                "executed_end_date_exclusive": self.evidence.executed_period.get("end_date_exclusive"),
                "requested_end_date_exclusive": self.evidence.requested_period.get("end_date_exclusive"),
                "data_through_date": self.evidence.freshness.get("data_through_date"),
            }
        return {
            "answer": self.answer,
            "mode": "structured_311",
            "sources": self.sources,
            "result": {"civic_311": diagnostics},
            "retrieval_cache": {},
            "model_name": None,
            "llm_calls": self.telemetry.llm_call_count,
            "execution_degraded": self.evidence.evidence_status in {
                EvidenceStatus.DEPENDENCY_UNAVAILABLE,
                EvidenceStatus.INSUFFICIENT_EVIDENCE,
            },
            "freshness_completion": (
                "PARTIAL"
                if self.evidence.evidence_status
                in {EvidenceStatus.PARTIAL, EvidenceStatus.STALE_BUT_USABLE, EvidenceStatus.PARTIALLY_COVERED}
                else "ACCEPTABLE"
            ),
            "freshness_blocks_current_condition": False,
            "source_freshness": dict(self.evidence.freshness),
            "civic_311_context": self.next_context,
            "civic_311_context_update": self.update_context,
            "civic_311_diagnostics": diagnostics,
            "trusted_result_summary": trusted_result_summary,
        }


PlannerLLM = Callable[[str, float], str]
SynthesisLLM = Callable[[str, float], str]
RepairLLM = Callable[[str, float], str]
ConnectionFactory = Callable[[], Any]


def _bounded_optional_text(value: Any, maximum: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:maximum] if text else None


def canonical_issue_family_id(value: Any) -> Optional[str]:
    """Map one compiler-owned issue concept to the reviewed 311 registry."""
    text = _normalized(str(value or "").replace("_", " "))
    if not text:
        return None
    matches = []
    for definition in FAMILY_DEFINITIONS:
        candidates = {
            _normalized(definition.family.value.replace("_", " ")),
            _normalized(definition.label),
            *(_normalized(alias) for alias in definition.aliases),
        }
        if text in candidates:
            matches.append(definition.family.value)
    return matches[0] if len(matches) == 1 else None


def build_compiled_311_plan(
    *,
    tool_id: str,
    arguments: Mapping[str, Any],
    resolved_period: Mapping[str, Any],
) -> StrictToolPlan:
    """Translate one validated registered request into the existing 311 plan."""
    operation = str(arguments.get("operation") or "").upper()
    raw_family = arguments.get("issue_family_id")
    family = canonical_issue_family_id(raw_family)
    issue_scope = (
        IssueScope.ALL_311.value
        if raw_family is None
        else IssueScope.SPECIFIC_FAMILY.value
    )
    if raw_family is not None and family is None:
        raise ValueError("compiled 311 issue family is not registered")
    location_id = str(arguments.get("location_id") or "")
    geography_by_id = {
        "dorchester": CivicGeography().to_dict(),
        "codman_square": CivicGeography(
            label="Codman Square",
            kind="SUBAREA",
            subarea="Codman Square",
            filterable=False,
        ).to_dict(),
        "fields_corner": CivicGeography(
            label="Fields Corner",
            kind="SUBAREA",
            subarea="Fields Corner",
            filterable=False,
        ).to_dict(),
    }
    if location_id not in geography_by_id:
        raise ValueError("compiled 311 location is not registered")
    try:
        period = CivicPeriod(
            kind=str(resolved_period["period"]),
            label=str(resolved_period["label"]),
            start=date.fromisoformat(str(resolved_period["start_date"])),
            end_exclusive=date.fromisoformat(str(resolved_period["end_date_exclusive"])),
        ).to_dict()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("compiled 311 period is invalid") from exc

    if tool_id == "aggregate_311":
        mapping = {
            "COUNT": (
                "exact_count",
                (OutputName.ISSUE_COUNT.value,),
                (ToolName.ISSUE_COUNT.value, ToolName.SOURCE_FRESHNESS.value),
            ),
            "GROUP": (
                "category_overview",
                (OutputName.CATEGORY_DISTRIBUTION.value,),
                (ToolName.CATEGORY_DISTRIBUTION.value, ToolName.SOURCE_FRESHNESS.value),
            ),
            "COMPARE": (
                "period_comparison",
                (OutputName.PERIOD_COMPARISON.value,),
                (ToolName.PERIOD_COMPARISON.value, ToolName.SOURCE_FRESHNESS.value),
            ),
            "STATUS": (
                "status_followup",
                (OutputName.STATUS_BREAKDOWN.value,),
                (ToolName.STATUS_BREAKDOWN.value, ToolName.SOURCE_FRESHNESS.value),
            ),
        }
    elif tool_id == "search_311_records":
        mapping = {
            "SEARCH": (
                "recent_reports",
                (
                    OutputName.ISSUE_COUNT.value,
                    OutputName.STATUS_BREAKDOWN.value,
                    OutputName.RECENT_EXAMPLES.value,
                ),
                (
                    ToolName.ISSUE_COUNT.value,
                    ToolName.STATUS_BREAKDOWN.value,
                    ToolName.RECENT_EXAMPLES.value,
                    ToolName.SOURCE_FRESHNESS.value,
                ),
            ),
        }
    else:
        raise ValueError("compiled request is not a 311 capability")
    if operation not in mapping:
        raise ValueError("compiled 311 operation is unsupported")
    intent, outputs, tools = mapping[operation]
    return StrictToolPlan(
        domain="311",
        intent=intent,
        issue_family=family,
        issue_scope=issue_scope,
        geography=geography_by_id[location_id],
        period=period,
        outputs=outputs,
        tools=tools,
        needs_clarification=False,
        clarification_message=None,
        confidence=1.0,
        planner_source=PlannerSource.LLM,
    )


def _normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()


def _phrase_in(normalized: str, phrase: str) -> bool:
    return f" {_normalized(phrase)} " in f" {normalized} "


def family_for_message(message: str) -> Optional[IssueFamily]:
    text = _normalized(message)
    matches: List[IssueFamily] = []
    for definition in FAMILY_DEFINITIONS:
        if any(_phrase_in(text, alias) for alias in definition.aliases):
            if not any(_phrase_in(text, exclusion) for exclusion in definition.exclusions):
                matches.append(definition.family)
    return matches[0] if len(matches) == 1 else None


def _family_definition(family: IssueFamily) -> FamilyDefinition:
    return _FAMILY_BY_ID[family.value]


def _is_category_overview(message: str) -> bool:
    text = _normalized(message)
    return bool(
        re.search(r"\b(?:what|which) street problems?\b", text)
        or _phrase_in(text, "street problems")
        or _phrase_in(text, "road problems")
    )


def _is_status_followup(message: str) -> bool:
    text = _normalized(message)
    return bool(
        re.search(r"\b(?:are|were) (?:these|those|they|the reports|the complaints).{0,24}\bopen\b", text)
        or _phrase_in(text, "still open")
        or _phrase_in(text, "were any of those closed")
    )


def _is_top_streets_followup(message: str) -> bool:
    text = _normalized(message)
    return bool(re.search(r"\bwhich streets?\b", text) and re.search(r"\bmost|top|highest\b", text))


def _is_comparison(message: str) -> bool:
    text = _normalized(message)
    return bool(
        _phrase_in(text, "getting worse")
        or _phrase_in(text, "getting better")
        or _phrase_in(text, "compared with")
        or _phrase_in(text, "compared to")
        or _phrase_in(text, "trend")
    )


def _is_implementation_question(message: str) -> bool:
    text = _normalized(message)
    return _phrase_in(text, "has anything been done") or _phrase_in(text, "has it been fixed")


def _is_reference_only(message: str) -> bool:
    text = _normalized(message)
    return bool(
        _is_status_followup(message)
        or _is_top_streets_followup(message)
        or re.search(r"\b(?:these|those|the)\s+(?:311\s+)?(?:reports|requests|complaints)\b", text)
        or text in {
            "what about last month",
            "what about this month",
            "what about last week",
            "what about the month before",
            "the month before",
            "what types were most common",
            "which types were most common",
            "what about overall",
            "what about there",
        }
    )


_ALL_311_RESPONSES = frozenset(
    {
        "overall",
        "all",
        "all of them",
        "every issue",
        "all 311 requests",
        "total",
        "regardless of issue",
        "any issue",
        "the overall count",
    }
)


def _is_all_311_response(message: str) -> bool:
    return _normalized(message).strip(" .,!?:;") in _ALL_311_RESPONSES


def _is_overall_refinement(message: str) -> bool:
    text = _normalized(message).strip(" .,!?:;")
    return bool(
        text in {"overall", "what about overall"}
        or re.search(r"\b(?:everything|all) overall\b", text)
        or (re.search(r"\boverall\b", text) and re.search(r"\bnot only\b", text))
    )


def _is_explicit_all_311(message: str) -> bool:
    text = _normalized(message)
    has_311_noun = bool(
        re.search(r"\b311\s+(?:requests?|cases?|reports?)\b", text)
        or re.search(r"\b(?:city|service)\s+requests?\b", text)
        or (re.search(r"\brequests?\b", text) and re.search(r"\b(?:regardless of issue|any issue)\b", text))
    )
    aggregate_language = bool(
        re.search(r"\b(?:how many|count|number|total|overall|all|regardless of issue|any issue)\b", text)
    )
    return bool(has_311_noun and aggregate_language and family_for_message(message) is None)


def _is_month_before_followup(message: str) -> bool:
    return bool(re.search(r"\b(?:what about )?(?:the )?month before\b", _normalized(message)))


def _is_category_distribution_followup(message: str) -> bool:
    text = _normalized(message)
    return bool(re.search(r"\b(?:what|which) (?:types|categories) (?:were|are) most common\b", text))


def _is_community_opinion_request(message: str) -> bool:
    text = _normalized(message)
    return bool(
        re.search(r"\b(?:residents?|community|people)\b", text)
        and re.search(r"\b(?:say|saying|concerned|concerns|feel|think|opinion)\b", text)
    )


def build_311_dialogue_hint(
    message: str,
    active_context: Optional[Mapping[str, Any]],
) -> Optional[DomainHint]:
    """Return a reviewed semantic hint without exposing a tool implementation."""
    # Community voice still belongs to the established RAG/hybrid route. A
    # matching 311 issue family is evidence context, not a request for a count.
    if _is_community_opinion_request(message):
        return None
    active = Active311FactContext.from_mapping(active_context)
    pending_scope = bool(active and active.coverage_status == "PENDING_CLARIFICATION")
    if _is_explicit_all_311(message) and re.search(
        r"\bwithout (?:looking|checking|searching) (?:anything )?up\b",
        _normalized(message),
    ):
        return DomainHint(
            turn_kind=TurnKind.AMBIGUOUS,
            domain=DialogueDomain.CIVIC_311,
            relationship=DialogueRelationship.STANDALONE,
            response_strategy=ResponseStrategy.ASK_CLARIFICATION,
            tool_required=False,
            capability_intent=CapabilityIntent.NONE,
            clarification_reason=ClarificationReason.EVIDENCE_REQUIRED,
            trusted_reference_target=TrustedReferenceTarget.NONE,
            direct_response=(
                "I need to check the current 311 data to answer that accurately. "
                "Would you like me to look it up?"
            ),
            priority=120,
        )
    if pending_scope and _is_all_311_response(message):
        return DomainHint(
            turn_kind=TurnKind.CLARIFICATION_RESPONSE,
            domain=DialogueDomain.CIVIC_311,
            relationship=DialogueRelationship.FOLLOW_UP,
            response_strategy=ResponseStrategy.EXECUTE_CAPABILITY,
            tool_required=True,
            capability_intent=CapabilityIntent.COUNT_311,
            trusted_reference_target=TrustedReferenceTarget.PENDING_311_ISSUE_SCOPE,
            priority=100,
        )
    if _is_explicit_all_311(message) or (active and _is_overall_refinement(message)):
        return DomainHint(
            turn_kind=(TurnKind.CONTEXTUAL_TOOL_FOLLOWUP if active else TurnKind.FACTUAL_TOOL_QUERY),
            domain=DialogueDomain.CIVIC_311,
            relationship=(DialogueRelationship.REFINEMENT if active else DialogueRelationship.STANDALONE),
            response_strategy=ResponseStrategy.EXECUTE_CAPABILITY,
            tool_required=True,
            capability_intent=CapabilityIntent.COUNT_311,
            trusted_reference_target=(TrustedReferenceTarget.ACTIVE_311_CONTEXT if active else TrustedReferenceTarget.NONE),
            priority=95,
        )
    if active and active.issue_scope == IssueScope.ALL_311.value and _is_month_before_followup(message):
        return DomainHint(
            TurnKind.CONTEXTUAL_TOOL_FOLLOWUP,
            DialogueDomain.CIVIC_311,
            DialogueRelationship.FOLLOW_UP,
            ResponseStrategy.EXECUTE_CAPABILITY,
            True,
            CapabilityIntent.COUNT_311,
            trusted_reference_target=TrustedReferenceTarget.ACTIVE_311_CONTEXT,
            priority=95,
        )
    if active and active.issue_scope == IssueScope.ALL_311.value and _is_category_distribution_followup(message):
        return DomainHint(
            TurnKind.CONTEXTUAL_TOOL_FOLLOWUP,
            DialogueDomain.CIVIC_311,
            DialogueRelationship.FOLLOW_UP,
            ResponseStrategy.EXECUTE_CAPABILITY,
            True,
            CapabilityIntent.DISTRIBUTE_311_CATEGORIES,
            trusted_reference_target=TrustedReferenceTarget.ACTIVE_311_CONTEXT,
            priority=95,
        )
    if is_311_candidate(message, active_context):
        return DomainHint(
            TurnKind.FACTUAL_TOOL_QUERY if active is None else TurnKind.CONTEXTUAL_TOOL_FOLLOWUP,
            DialogueDomain.CIVIC_311,
            DialogueRelationship.STANDALONE if active is None else DialogueRelationship.FOLLOW_UP,
            ResponseStrategy.EXECUTE_CAPABILITY,
            True,
            CapabilityIntent.QUERY_311,
            trusted_reference_target=(TrustedReferenceTarget.ACTIVE_311_CONTEXT if active else TrustedReferenceTarget.NONE),
            priority=80,
        )
    return None


def _contains_311_signal(message: str) -> bool:
    text = _normalized(message)
    if _is_category_overview(message) or family_for_message(message) is not None:
        return True
    return any(
        _phrase_in(text, value)
        for value in ("311", "service request", "service requests", "city complaint", "city complaints")
    )


def is_311_candidate(message: str, active_context: Optional[Mapping[str, Any]]) -> bool:
    """Return true only for the reviewed civic family or a trusted reference."""
    if _contains_311_signal(message) or _is_explicit_all_311(message):
        return True
    # A bare reference is still a civic request.  Without trusted context the
    # planner returns a clarification instead of letting an unrelated agent
    # manufacture a referent.
    if _is_reference_only(message):
        return True
    context = Active311FactContext.from_mapping(active_context)
    return context is not None and _is_contextual_followup(message)


def _period_from_message(message: str, *, today: date) -> CivicPeriod:
    text = _normalized(message)
    month_lookup = {name.lower(): index for index, name in enumerate(month_name) if name}
    month_match = re.search(
        r"\b(?:in|during|for)\s+(january|february|march|april|may|june|july|august|september|october|november|december)(?:\s+(20\d{2}))?\b",
        text,
    )
    if month_match:
        month = month_lookup[month_match.group(1)]
        year = int(month_match.group(2) or today.year)
        if month_match.group(2) is None and month > today.month:
            year -= 1
        start = date(year, month, 1)
        end = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
        return CivicPeriod("CALENDAR_MONTH", f"{month_name[month]} {year}", start, end)
    if "last month" in text:
        first_this_month = today.replace(day=1)
        end = first_this_month
        start = (end - timedelta(days=1)).replace(day=1)
        return CivicPeriod("LAST_MONTH", f"last month ({start.strftime('%B %Y')})", start, end)
    if "this month" in text:
        start = today.replace(day=1)
        return CivicPeriod("THIS_MONTH", "this month", start, today + timedelta(days=1))
    if "last week" in text:
        end = today - timedelta(days=today.weekday())
        return CivicPeriod("LAST_WEEK", "last week", end - timedelta(days=7), end)
    match = re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", text)
    if match:
        days = max(1, min(int(match.group(1)), 90))
        return CivicPeriod("ROLLING_DAYS", f"the last {days} days", today - timedelta(days=days - 1), today + timedelta(days=1))
    if "30 day" in text or "last 30" in text:
        return CivicPeriod("ROLLING_30_DAYS", "the last 30 days", today - timedelta(days=29), today + timedelta(days=1))
    if "week" in text or "recent" in text or "recently" in text:
        return CivicPeriod("RECENT", "the last 7 days", today - timedelta(days=6), today + timedelta(days=1))
    return CivicPeriod("RECENT", "the last 7 days", today - timedelta(days=6), today + timedelta(days=1))


_STREET_CAPTURE = re.compile(
    r"\b(?:on|at|near|along|around)\s+([A-Za-z0-9 .'-]{2,72}?\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|way|place|pl|square|sq))\b",
    re.IGNORECASE,
)


def _canonical_street(value: str) -> Optional[str]:
    text = _normalized(value)
    if not text or text in {"my street", "this street", "the street", "our street"}:
        return None
    replacements = {
        "street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd",
        "drive": "dr", "place": "pl", "square": "sq",
    }
    parts = [replacements.get(part, part) for part in text.split()]
    normalized = " ".join(parts)
    return normalized[:96] if len(normalized) >= 3 else None


def _street_from_message(message: str) -> Optional[str]:
    match = _STREET_CAPTURE.search(message or "")
    if match:
        return _canonical_street(match.group(1))
    stripped = str(message or "").strip(" .,!?:;\"'")
    if re.fullmatch(
        r"[A-Za-z0-9 .'-]{2,72}\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|way|place|pl)",
        stripped,
        re.IGNORECASE,
    ):
        return _canonical_street(stripped)
    return None


def _needs_street(message: str) -> bool:
    text = _normalized(message)
    return any(_phrase_in(text, phrase) for phrase in ("my street", "this street", "our street"))


def _is_contextual_followup(message: str) -> bool:
    return (
        _is_reference_only(message)
        or _is_all_311_response(message)
        or _is_overall_refinement(message)
        or _street_from_message(message) is not None
    )


def _requires_semantic_planner(message: str) -> bool:
    text = _normalized(message)
    return bool(
        _is_implementation_question(message)
        and re.search(r"\b(?:and|also)\b", text)
        and re.search(r"\b(?:complain|complained|complaints?|reports?|requests?|other people)\b", text)
    )


def _is_exact_count_request(message: str) -> bool:
    text = _normalized(message)
    return bool(
        (text.startswith("how many ") or re.search(r"\b(?:count|number) of\b", text))
        and not _is_comparison(message)
        and not _is_implementation_question(message)
        and not re.search(r"\b(?:status|open|closed|which streets?|top streets?|and what|and how)\b", text)
    )


def _geography_from_context(
    message: str,
    *,
    active_context: Optional[Active311FactContext],
    state_location: Optional[Mapping[str, Any]],
) -> CivicGeography:
    street = _street_from_message(message)
    if street:
        return CivicGeography(label="Dorchester", street=street)
    if _needs_street(message) and active_context and active_context.geography.street:
        return active_context.geography
    normalized = _normalized(message)
    explicit_subareas = {
        "codman square": "Codman Square",
        "fields corner": "Fields Corner",
    }
    for phrase, label in explicit_subareas.items():
        if _phrase_in(normalized, phrase):
            # The reviewed 311 source has no verified subarea boundary field.
            # Keep the named place as scope metadata and state the Dorchester-
            # wide evidence limitation instead of inventing a fuzzy filter.
            return CivicGeography(label=label, kind="SUBAREA", subarea=label, filterable=False)
    if _phrase_in(normalized, "there") and state_location:
        location_id = str(state_location.get("id") or "")
        if location_id in {"codman_square", "fields_corner"}:
            label = "Codman Square" if location_id == "codman_square" else "Fields Corner"
            # The supplied source has no authoritative subarea boundary field.
            return CivicGeography(label=label, kind="SUBAREA", subarea=label, filterable=False)
    if active_context and _is_contextual_followup(message):
        return active_context.geography
    return CivicGeography()


def _planned_outputs(message: str, family: Optional[IssueFamily], active: Optional[Active311FactContext]) -> Tuple[Tuple[str, ...], Tuple[str, ...], str]:
    if active and (
        _is_month_before_followup(message)
        or _is_all_311_response(message)
        or _is_overall_refinement(message)
    ):
        outputs = active.outputs or (
            OutputName.ISSUE_COUNT.value,
            OutputName.SOURCE_FRESHNESS.value,
        )
        output_set = set(outputs)
        if OutputName.CATEGORY_DISTRIBUTION.value in output_set:
            intent = "category_overview"
        elif OutputName.PERIOD_COMPARISON.value in output_set:
            intent = "period_comparison"
        elif OutputName.STATUS_BREAKDOWN.value in output_set and OutputName.ISSUE_COUNT.value not in output_set:
            intent = "status_followup"
        elif OutputName.TOP_STREETS.value in output_set:
            intent = "top_streets"
        elif OutputName.RECENT_EXAMPLES.value in output_set:
            intent = "street_status"
        else:
            intent = "exact_count"
        return outputs, _tools_for_outputs(outputs), intent
    if _is_category_distribution_followup(message):
        return (
            (OutputName.CATEGORY_DISTRIBUTION.value, OutputName.SOURCE_FRESHNESS.value),
            (ToolName.CATEGORY_DISTRIBUTION.value, ToolName.SOURCE_FRESHNESS.value),
            "category_overview",
        )
    if _is_category_overview(message):
        return (
            (OutputName.CATEGORY_DISTRIBUTION.value, OutputName.SOURCE_FRESHNESS.value),
            (ToolName.CATEGORY_DISTRIBUTION.value, ToolName.SOURCE_FRESHNESS.value),
            "category_overview",
        )
    if _is_comparison(message):
        return (
            (OutputName.PERIOD_COMPARISON.value, OutputName.SOURCE_FRESHNESS.value),
            (ToolName.PERIOD_COMPARISON.value, ToolName.SOURCE_FRESHNESS.value),
            "period_comparison",
        )
    if _is_exact_count_request(message):
        return (
            (OutputName.ISSUE_COUNT.value, OutputName.SOURCE_FRESHNESS.value),
            (ToolName.ISSUE_COUNT.value, ToolName.SOURCE_FRESHNESS.value),
            "exact_count",
        )
    if _is_status_followup(message):
        return (
            (OutputName.STATUS_BREAKDOWN.value, OutputName.SOURCE_FRESHNESS.value),
            (ToolName.STATUS_BREAKDOWN.value, ToolName.SOURCE_FRESHNESS.value),
            "status_followup",
        )
    if _is_top_streets_followup(message):
        return (
            (OutputName.TOP_STREETS.value, OutputName.ISSUE_COUNT.value, OutputName.SOURCE_FRESHNESS.value),
            (ToolName.TOP_STREETS.value, ToolName.ISSUE_COUNT.value, ToolName.SOURCE_FRESHNESS.value),
            "top_streets",
        )
    if _street_from_message(message) and active and OutputName.MATCHING_REQUESTS.value in active.outputs:
        return (
            (
                OutputName.MATCHING_REQUESTS.value,
                OutputName.STATUS_BREAKDOWN.value,
                OutputName.RECENT_EXAMPLES.value,
                OutputName.SOURCE_FRESHNESS.value,
            ),
            (
                ToolName.ISSUE_COUNT.value,
                ToolName.STATUS_BREAKDOWN.value,
                ToolName.RECENT_EXAMPLES.value,
                ToolName.SOURCE_FRESHNESS.value,
            ),
            "street_status",
        )
    if _is_implementation_question(message):
        return (
            (
                OutputName.MATCHING_REQUESTS.value,
                OutputName.STATUS_BREAKDOWN.value,
                OutputName.RECENT_EXAMPLES.value,
                OutputName.SOURCE_FRESHNESS.value,
            ),
            (
                ToolName.ISSUE_COUNT.value,
                ToolName.STATUS_BREAKDOWN.value,
                ToolName.RECENT_EXAMPLES.value,
                ToolName.SOURCE_FRESHNESS.value,
            ),
            "street_status",
        )
    return (
        (
            OutputName.ISSUE_COUNT.value,
            OutputName.STATUS_BREAKDOWN.value,
            OutputName.SOURCE_FRESHNESS.value,
        ),
        (
            ToolName.ISSUE_COUNT.value,
            ToolName.STATUS_BREAKDOWN.value,
            ToolName.SOURCE_FRESHNESS.value,
        ),
        "recent_reports",
    )


def _default_period_from_context(message: str, active: Optional[Active311FactContext], *, today: date) -> CivicPeriod:
    if active and _is_month_before_followup(message):
        end = active.executed_period.start.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        return CivicPeriod(
            "CALENDAR_MONTH",
            f"the month before ({start.strftime('%B %Y')})",
            start,
            end,
        )
    text = _normalized(message)
    if active and _is_contextual_followup(message) and not re.search(r"\b(?:last|this|recent|\d+ days?)\b", text):
        return active.executed_period
    return _period_from_message(message, today=today)


def _clarification_plan(
    *,
    message: str,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    clarification: str,
    outputs: Sequence[str] = (),
) -> StrictToolPlan:
    return StrictToolPlan(
        domain="311",
        intent="clarification",
        issue_family=family.value if family else None,
        geography=geography.to_dict(),
        period=period.to_dict(),
        outputs=bounded_strings(outputs, limit=7, maximum_length=48),
        tools=(),
        needs_clarification=True,
        clarification_message=clarification,
        confidence=0.98,
        planner_source=PlannerSource.RULE,
        issue_scope=(
            IssueScope.SPECIFIC_FAMILY.value
            if family is not None
            else IssueScope.UNSPECIFIED.value
        ),
    )


_PLANNER_KEYS = frozenset({
    "domain",
    "intent",
    "issue_family",
    "geography_scope",
    "street_reference",
    "time_intent",
    "requested_outputs",
    "needs_clarification",
    "confidence",
})
_PLANNER_INTENTS = frozenset({
    "recent_reports",
    "exact_count",
    "status_followup",
    "top_streets",
    "street_status",
    "period_comparison",
    "category_overview",
})
_OUTPUT_TO_TOOL = {
    OutputName.ISSUE_COUNT.value: ToolName.ISSUE_COUNT.value,
    OutputName.MATCHING_REQUESTS.value: ToolName.ISSUE_COUNT.value,
    OutputName.STATUS_BREAKDOWN.value: ToolName.STATUS_BREAKDOWN.value,
    OutputName.TOP_STREETS.value: ToolName.TOP_STREETS.value,
    OutputName.RECENT_EXAMPLES.value: ToolName.RECENT_EXAMPLES.value,
    OutputName.PERIOD_COMPARISON.value: ToolName.PERIOD_COMPARISON.value,
    OutputName.CATEGORY_DISTRIBUTION.value: ToolName.CATEGORY_DISTRIBUTION.value,
    OutputName.SOURCE_FRESHNESS.value: ToolName.SOURCE_FRESHNESS.value,
}
_INTENT_REQUIRED_OUTPUTS = {
    "recent_reports": frozenset({OutputName.ISSUE_COUNT.value}),
    "exact_count": frozenset({OutputName.ISSUE_COUNT.value}),
    "status_followup": frozenset({OutputName.STATUS_BREAKDOWN.value}),
    "top_streets": frozenset({OutputName.TOP_STREETS.value}),
    "street_status": frozenset({OutputName.MATCHING_REQUESTS.value, OutputName.STATUS_BREAKDOWN.value}),
    "period_comparison": frozenset({OutputName.PERIOD_COMPARISON.value}),
    "category_overview": frozenset({OutputName.CATEGORY_DISTRIBUTION.value}),
}
_GEOGRAPHY_SCOPES = frozenset({"DORCHESTER", "STREET", "SUBAREA"})
_STREET_REFERENCE_STATES = frozenset({"NONE", "EXPLICIT", "INHERITED", "MISSING"})
_TIME_INTENTS = frozenset({
    "RECENT", "LAST_MONTH", "THIS_MONTH", "LAST_WEEK", "ROLLING_30_DAYS", "INHERIT",
})


def _tools_for_outputs(outputs: Sequence[str]) -> Tuple[str, ...]:
    tools: List[str] = []
    for output in outputs:
        tool = _OUTPUT_TO_TOOL.get(output)
        if tool and tool not in tools:
            tools.append(tool)
    if ToolName.SOURCE_FRESHNESS.value not in tools:
        tools.append(ToolName.SOURCE_FRESHNESS.value)
    return tuple(tools)


def _requires_street_status_outputs(outputs: Sequence[str]) -> bool:
    required = {
        OutputName.MATCHING_REQUESTS.value,
        OutputName.STATUS_BREAKDOWN.value,
    }
    return required.issubset(set(outputs))


def _validate_llm_plan(raw: str, *, fallback: StrictToolPlan) -> Optional[StrictToolPlan]:
    """Accept one semantic selection; derive tool calls deterministically."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, Mapping) or set(value) != _PLANNER_KEYS:
        return None
    if value.get("domain") != "311":
        return None
    family = str(value.get("issue_family") or "")
    if family not in _FAMILY_BY_ID:
        return None
    intent = str(value.get("intent") or "")
    if intent not in _PLANNER_INTENTS:
        return None
    outputs = bounded_strings(
        value.get("requested_outputs") if isinstance(value.get("requested_outputs"), list) else (),
        limit=7,
        maximum_length=48,
    )
    allowed_outputs = {item.value for item in OutputName}
    if not outputs or any(item not in allowed_outputs for item in outputs):
        return None
    if not _INTENT_REQUIRED_OUTPUTS[intent].issubset(outputs):
        return None
    if _requires_street_status_outputs(fallback.outputs) and (
        intent != "street_status" or not set(fallback.outputs).issubset(outputs)
    ):
        return None
    geography_scope = str(value.get("geography_scope") or "")
    street_reference = str(value.get("street_reference") or "")
    time_intent = str(value.get("time_intent") or "")
    if geography_scope not in _GEOGRAPHY_SCOPES:
        return None
    if street_reference not in _STREET_REFERENCE_STATES or time_intent not in _TIME_INTENTS:
        return None
    fallback_street = fallback.geography.get("street")
    if street_reference == "EXPLICIT" and not fallback_street:
        return None
    if geography_scope == "SUBAREA" and not fallback.geography.get("subarea"):
        return None
    needs_clarification = value.get("needs_clarification")
    if not isinstance(needs_clarification, bool):
        return None
    if street_reference == "MISSING" and not needs_clarification:
        return None
    try:
        confidence = float(value.get("confidence"))
    except (TypeError, ValueError):
        return None
    if confidence < 0.75:
        return None
    if needs_clarification:
        clarification = (
            "Which street in Dorchester are you asking about?"
            if street_reference == "MISSING"
            else "Which city-service issue are you asking about?"
        )
        return StrictToolPlan(
            domain="311",
            intent="clarification",
            issue_family=family,
            geography=fallback.geography,
            period=fallback.period,
            outputs=outputs,
            tools=(),
            needs_clarification=True,
            clarification_message=clarification,
            confidence=confidence,
            planner_source=PlannerSource.LLM,
        )
    tools = _tools_for_outputs(outputs)
    if any(tool not in TOOL_REGISTRY for tool in tools):
        return None
    return StrictToolPlan(
        domain="311",
        intent=intent,
        issue_family=family,
        geography=fallback.geography,
        period=fallback.period,
        outputs=outputs,
        tools=tools,
        needs_clarification=False,
        clarification_message=None,
        confidence=confidence,
        planner_source=PlannerSource.LLM,
    )


def plan_311_question(
    message: str,
    *,
    active_context: Optional[Mapping[str, Any]] = None,
    state_location: Optional[Mapping[str, Any]] = None,
    now: Optional[date] = None,
    planner_llm: Optional[PlannerLLM] = None,
    deadline: Optional[GlobalDeadline] = None,
    planner_stats: Optional[Dict[str, Any]] = None,
) -> StrictToolPlan:
    """Build one typed 311 plan with deterministic rules before an optional planner."""
    today = now or date.today()
    active = Active311FactContext.from_mapping(active_context)
    family = family_for_message(message)
    aggregate_refinement = bool(active and _is_overall_refinement(message))
    if aggregate_refinement:
        family = None
    if (
        family is None
        and active
        and active.issue_scope == IssueScope.SPECIFIC_FAMILY.value
        and _is_contextual_followup(message)
        and not aggregate_refinement
    ):
        family = IssueFamily(str(active.issue_family))
    issue_scope = (
        IssueScope.ALL_311
        if (
            _is_explicit_all_311(message)
            or aggregate_refinement
            or (
                active
                and active.issue_scope == IssueScope.ALL_311.value
                and _is_contextual_followup(message)
            )
            or (
                active
                and active.coverage_status == "PENDING_CLARIFICATION"
                and _is_all_311_response(message)
            )
        )
        else (IssueScope.SPECIFIC_FAMILY if family is not None else IssueScope.UNSPECIFIED)
    )
    geography = _geography_from_context(message, active_context=active, state_location=state_location)
    period = _default_period_from_context(message, active, today=today)
    outputs, tools, intent = _planned_outputs(message, family, active)
    missing_street = _needs_street(message) and geography.street is None
    fallback = (
        _clarification_plan(
            message=message,
            family=family,
            geography=geography,
            period=period,
            clarification="Which street in Dorchester are you asking about?",
            outputs=outputs,
        )
        if missing_street
        else StrictToolPlan(
            domain="311",
            intent=intent,
            issue_family=family.value if family else None,
            geography=geography.to_dict(),
            period=period.to_dict(),
            outputs=outputs,
            tools=tools,
            needs_clarification=False,
            clarification_message=None,
            confidence=0.99 if family or _is_category_overview(message) else 0.4,
            planner_source=PlannerSource.RULE,
            issue_scope=issue_scope.value,
        )
    )
    semantic_value = issue_scope is not IssueScope.ALL_311 and not (family is None and _is_reference_only(message)) and (
        (family is None and not _is_category_overview(message))
        or _requires_semantic_planner(message)
    )
    if planner_llm and deadline and semantic_value and deadline.remaining(reserve_for_persistence=True) >= 3.0:
        if planner_stats is not None:
            planner_stats["call_count"] = 1
        compound_constraint = (
            " This reviewed compound request requires intent=street_status and must preserve "
            "these requested_outputs: " + ", ".join(fallback.outputs) + "."
            if _requires_street_status_outputs(fallback.outputs)
            else ""
        )
        prompt = (
            "Classify one Dorchester 311 question. Return one JSON object with exactly these keys: "
            "domain, intent, issue_family, geography_scope, street_reference, time_intent, "
            "requested_outputs, needs_clarification, confidence. "
            "domain must be 311. Allowed intent: " + ", ".join(sorted(_PLANNER_INTENTS))
            + ". Allowed issue_family: " + ", ".join(item.value for item in IssueFamily)
            + ". Allowed geography_scope: DORCHESTER, STREET, SUBAREA. "
            "Allowed street_reference: NONE, EXPLICIT, INHERITED, MISSING. "
            "Allowed time_intent: " + ", ".join(sorted(_TIME_INTENTS))
            + ". Allowed requested_outputs: " + ", ".join(item.value for item in OutputName)
            + ". Do not return tools, SQL, URLs, dates, filters, or prose. "
            "If the question says my street without a trusted street, set street_reference=MISSING "
            "and needs_clarification=true."
            + compound_constraint
            + " Question: " + message[:400]
        )
        try:
            raw = planner_llm(prompt, min(2.5, deadline.remaining(reserve_for_persistence=True)))
            planned = _validate_llm_plan(raw, fallback=fallback)
            if planned is not None:
                if planner_stats is not None:
                    planner_stats["accepted"] = True
                return planned
            if planner_stats is not None:
                planner_stats["failure"] = "invalid_output"
        except TimeoutError:
            if planner_stats is not None:
                planner_stats["failure"] = "timeout"
        except Exception:
            if planner_stats is not None:
                planner_stats["failure"] = "provider_failure"
    if missing_street:
        return fallback
    if issue_scope is IssueScope.UNSPECIFIED and not _is_category_overview(message):
        if _is_status_followup(message):
            return _clarification_plan(
                message=message,
                family=None,
                geography=geography,
                period=period,
                clarification="Which 311 issue or set of reports are you asking about?",
            )
        return _clarification_plan(
            message=message,
            family=None,
            geography=geography,
            period=period,
            clarification="Which city-service issue are you asking about?",
        )
    return fallback


def _period_from_plan(plan: StrictToolPlan) -> CivicPeriod:
    value = plan.period
    return CivicPeriod(
        kind=str(value["kind"]),
        label=str(value["label"]),
        start=date.fromisoformat(str(value["start_date"])),
        end_exclusive=date.fromisoformat(str(value["end_date_exclusive"])),
    )


def _geography_from_plan(plan: StrictToolPlan) -> CivicGeography:
    value = plan.geography
    return CivicGeography(
        label=str(value.get("label") or "Dorchester"),
        kind=str(value.get("kind") or "DORCHESTER"),
        street=_bounded_optional_text(value.get("street"), 96),
        subarea=_bounded_optional_text(value.get("subarea"), 64),
        filterable=bool(value.get("filterable", True)),
    )


def _latest_usable_period(requested: CivicPeriod, data_through: Optional[date]) -> Optional[CivicPeriod]:
    if data_through is None:
        return None
    covered_end = data_through + timedelta(days=1)
    if requested.end_exclusive <= covered_end:
        return requested
    if requested.start < covered_end:
        return CivicPeriod(
            requested.kind,
            f"the covered portion of {requested.label}",
            requested.start,
            covered_end,
        )
    days = requested.days
    start = covered_end - timedelta(days=days)
    return CivicPeriod(
        requested.kind,
        f"the latest fully covered {days}-day period ({start.strftime('%B')} {start.day} through {data_through.strftime('%B')} {data_through.day}, {data_through.year})",
        start,
        covered_end,
    )


def _status_and_period(
    plan: StrictToolPlan,
    *,
    connection_factory: ConnectionFactory,
    now: datetime,
    deadline: GlobalDeadline,
) -> Tuple[Dict[str, Any], CivicPeriod, EvidenceStatus, List[str]]:
    requested = _period_from_plan(plan)
    # Freshness is evidence, not a best-effort prelude.  Bound it by the same
    # request deadline so a slow source-status read cannot make the remaining
    # tool plan irrelevant.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="civic311source")
    future = executor.submit(load_source_status, connection_factory, SOURCE_311)
    try:
        wait_seconds = min(1.5, deadline.remaining(reserve_for_persistence=True))
        done, pending = wait((future,), timeout=max(0.0, wait_seconds))
        if not done:
            future.cancel()
            return (
                {
                    "source_key": SOURCE_311,
                    "source_label": SOURCE_LABEL,
                    "source_authority": SOURCE_AUTHORITY,
                    "freshness_status": FreshnessStatus.UNKNOWN.value,
                    "coverage_status": CoverageStatus.UNKNOWN.value,
                    "data_through_date": None,
                    "requested_period": requested.to_dict(),
                },
                requested,
                EvidenceStatus.DEPENDENCY_UNAVAILABLE,
                ["The source coverage could not be loaded within the request time limit."],
            )
        status = future.result()
    except Exception:
        return (
            {
                "source_key": SOURCE_311,
                "source_label": SOURCE_LABEL,
                "source_authority": SOURCE_AUTHORITY,
                "freshness_status": FreshnessStatus.UNKNOWN.value,
                "coverage_status": CoverageStatus.UNKNOWN.value,
                "data_through_date": None,
                "requested_period": requested.to_dict(),
            },
            requested,
            EvidenceStatus.DEPENDENCY_UNAVAILABLE,
            ["The source coverage could not be verified."],
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    assessment = assess_freshness(
        status,
        requested_start=requested.start,
        requested_end_exclusive=requested.end_exclusive,
        current_condition=False,
        now=now,
    )
    fresh = slim_freshness_metadata(assessment)
    fresh["source_label"] = SOURCE_LABEL
    fresh["source_authority"] = SOURCE_AUTHORITY
    fresh["requested_period"] = requested.to_dict()
    limitations: List[str] = []
    if assessment.data_through_date is None:
        return fresh, requested, EvidenceStatus.DEPENDENCY_UNAVAILABLE, ["The source coverage could not be verified."]
    executed = _latest_usable_period(requested, assessment.data_through_date)
    if executed is None:
        return fresh, requested, EvidenceStatus.DEPENDENCY_UNAVAILABLE, ["The source coverage could not be verified."]
    if assessment.coverage_status is CoverageStatus.OUTSIDE:
        through = _date_phrase(assessment.data_through_date.isoformat()) or assessment.data_through_date.isoformat()
        if plan.intent == "exact_count":
            limitations.append(
                f"The local data only runs through {through}, so it does not cover {requested.label}."
            )
            return fresh, requested, EvidenceStatus.OUTSIDE_COVERAGE, limitations
        limitations.append(
            f"The local data only runs through {through}, so it does not describe current conditions."
        )
        return fresh, executed, EvidenceStatus.STALE_BUT_USABLE, limitations
    if assessment.coverage_status is CoverageStatus.PARTIAL:
        through = _date_phrase(assessment.data_through_date.isoformat()) or assessment.data_through_date.isoformat()
        limitations.append(
            f"The local data only runs through {through}, so it covers only part of {requested.label}."
        )
        return fresh, executed, EvidenceStatus.PARTIALLY_COVERED, limitations
    if assessment.freshness_status in {FreshnessStatus.STALE, FreshnessStatus.DELAYED}:
        through = _date_phrase(assessment.data_through_date.isoformat()) or assessment.data_through_date.isoformat()
        limitations.append(
            f"The local data only runs through {through}, so it does not describe current conditions."
        )
        return fresh, executed, EvidenceStatus.STALE_BUT_USABLE, limitations
    return fresh, executed, EvidenceStatus.COMPLETE, limitations


def _family_filter(family: Optional[IssueFamily]) -> Tuple[str, List[Any]]:
    if family is None:
        clauses = []
        values: List[Any] = []
        for definition in FAMILY_DEFINITIONS:
            clause, clause_values = _family_filter(definition.family)
            clauses.append(f"({clause})")
            values.extend(clause_values)
        return " OR ".join(clauses), values
    definition = _family_definition(family)
    clauses: List[str] = []
    values: List[Any] = []
    if definition.service_names:
        placeholders = ", ".join(["%s"] * len(definition.service_names))
        clauses.append(f"LOWER(COALESCE(service_name, '')) IN ({placeholders})")
        values.extend(name.lower() for name in definition.service_names)
    if definition.case_topics:
        placeholders = ", ".join(["%s"] * len(definition.case_topics))
        clauses.append(f"LOWER(COALESCE(case_topic, '')) IN ({placeholders})")
        values.extend(name.lower() for name in definition.case_topics)
    return " OR ".join(clauses), values


def _where_clause(
    *,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    include_street: bool = True,
    all_311: bool = False,
) -> Tuple[str, List[Any]]:
    clauses = [
        "(neighborhood = %s OR police_district = %s)",
        "open_date >= %s",
        "open_date < %s",
    ]
    params: List[Any] = ["Dorchester", "C11", period.start.isoformat(), period.end_exclusive.isoformat()]
    if not all_311:
        family_clause, family_params = _family_filter(family)
        if family_clause:
            clauses.append(f"({family_clause})")
            params.extend(family_params)
    if geography.street and include_street:
        clauses.append("LOWER(REPLACE(TRIM(COALESCE(street_name, '')), '.', '')) = %s")
        params.append(geography.street)
    return " AND ".join(clauses), params


def _query(connection_factory: ConnectionFactory, sql: str, params: Sequence[Any], *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = None
    cursor = None
    try:
        conn = connection_factory()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, tuple(params))
        rows = list(cursor.fetchall() or [])
        return rows[:limit] if limit else rows
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_nonnegative_int(value: Any) -> Optional[int]:
    """Return a reported count without treating a missing value as zero."""
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _tool_issue_count(
    connection_factory: ConnectionFactory,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    *,
    all_311: bool = False,
) -> Dict[str, Any]:
    if geography.street:
        where, params = _where_clause(
            family=family,
            geography=geography,
            period=period,
            include_street=False,
            all_311=all_311,
        )
        rows = _query(
            connection_factory,
            "SELECT COUNT(*) AS total_matching, "
            "SUM(CASE WHEN street_name IS NOT NULL AND TRIM(street_name) <> '' THEN 1 ELSE 0 END) AS addressable_count, "
            "SUM(CASE WHEN LOWER(REPLACE(TRIM(COALESCE(street_name, '')), '.', '')) = %s THEN 1 ELSE 0 END) AS count "
            f"FROM service_requests_311 WHERE {where}",
            [geography.street, *params],
            limit=1,
        )
        row = rows[0] if rows else {}
        total_matching = _int_value(row.get("total_matching"))
        addressable_count = _int_value(row.get("addressable_count"))
        return {
            "count": _int_value(row.get("count")),
            "total_matching": total_matching,
            "addressable_count": addressable_count,
            "street_coverage_unavailable": total_matching > 0 and addressable_count == 0,
        }
    where, params = _where_clause(
        family=family, geography=geography, period=period, all_311=all_311,
    )
    rows = _query(connection_factory, f"SELECT COUNT(*) AS count FROM service_requests_311 WHERE {where}", params, limit=1)
    return {"count": _int_value(rows[0].get("count") if rows else 0)}


def _tool_status_breakdown(
    connection_factory: ConnectionFactory,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    *,
    all_311: bool = False,
) -> Dict[str, Any]:
    where, params = _where_clause(
        family=family, geography=geography, period=period, all_311=all_311,
    )
    rows = _query(
        connection_factory,
        f"SELECT COALESCE(NULLIF(case_status, ''), 'Unknown') AS status, COUNT(*) AS count "
        f"FROM service_requests_311 WHERE {where} GROUP BY COALESCE(NULLIF(case_status, ''), 'Unknown') ORDER BY count DESC LIMIT 8",
        params,
        limit=8,
    )
    normalized = [{"status": str(row.get("status") or "Unknown"), "count": _int_value(row.get("count"))} for row in rows]
    open_count = sum(item["count"] for item in normalized if item["status"].lower() not in {"closed", "unknown"})
    closed_count = sum(item["count"] for item in normalized if item["status"].lower() == "closed")
    return {"statuses": normalized, "open_count": open_count, "closed_count": closed_count}


def _tool_top_streets(
    connection_factory: ConnectionFactory,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    *,
    all_311: bool = False,
) -> Dict[str, Any]:
    where, params = _where_clause(
        family=family, geography=geography, period=period, all_311=all_311,
    )
    rows = _query(
        connection_factory,
        f"SELECT street_name, COUNT(*) AS count FROM service_requests_311 WHERE {where} "
        "AND street_name IS NOT NULL AND TRIM(street_name) <> '' "
        "GROUP BY street_name ORDER BY count DESC, street_name ASC LIMIT 5",
        params,
        limit=5,
    )
    return {
        "streets": [
            {"street": str(row.get("street_name") or "").strip(), "count": _int_value(row.get("count"))}
            for row in rows if str(row.get("street_name") or "").strip()
        ]
    }


def _tool_recent_examples(
    connection_factory: ConnectionFactory,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    *,
    all_311: bool = False,
) -> Dict[str, Any]:
    where, params = _where_clause(
        family=family, geography=geography, period=period, all_311=all_311,
    )
    rows = _query(
        connection_factory,
        f"SELECT case_id, open_date, service_name, case_topic, street_name, case_status "
        f"FROM service_requests_311 WHERE {where} ORDER BY open_date DESC LIMIT 4",
        params,
        limit=4,
    )
    return {
        "examples": [
            {
                "opened": str(row.get("open_date") or "")[:10],
                "street": str(row.get("street_name") or "").strip() or None,
                "status": str(row.get("case_status") or "").strip() or None,
                "category": str(row.get("service_name") or row.get("case_topic") or "").strip() or None,
            }
            for row in rows
        ]
    }


def _tool_compare_periods(
    connection_factory: ConnectionFactory,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    *,
    all_311: bool = False,
) -> Dict[str, Any]:
    prior_end = period.start
    prior_start = prior_end - timedelta(days=period.days)
    prior = CivicPeriod("COMPARISON_BASELINE", f"the preceding {period.days}-day period", prior_start, prior_end)
    current = _tool_issue_count(
        connection_factory, family, geography, period, all_311=all_311,
    )["count"]
    previous = _tool_issue_count(
        connection_factory, family, geography, prior, all_311=all_311,
    )["count"]
    difference = current - previous
    percentage = None if previous == 0 else round((difference / previous) * 100.0, 1)
    return {
        "current_count": current,
        "previous_count": previous,
        "difference": difference,
        "percentage_change": percentage,
        "previous_period": prior.to_dict(),
    }


def _tool_category_distribution(
    connection_factory: ConnectionFactory,
    geography: CivicGeography,
    period: CivicPeriod,
    *,
    all_311: bool = False,
) -> Dict[str, Any]:
    where, params = _where_clause(
        family=None, geography=geography, period=period, all_311=all_311,
    )
    if all_311:
        rows = _query(
            connection_factory,
            "SELECT COALESCE(NULLIF(TRIM(service_name), ''), NULLIF(TRIM(case_topic), ''), 'Other') AS category, "
            f"COUNT(*) AS count FROM service_requests_311 WHERE {where} "
            "GROUP BY COALESCE(NULLIF(TRIM(service_name), ''), NULLIF(TRIM(case_topic), ''), 'Other') "
            "ORDER BY count DESC, category ASC LIMIT 8",
            params,
            limit=8,
        )
        return {
            "categories": [
                {
                    "family": None,
                    "label": str(row.get("category") or "Other").strip(),
                    "count": _int_value(row.get("count")),
                }
                for row in rows
                if str(row.get("category") or "").strip()
            ]
        }
    rows = _query(
        connection_factory,
        f"SELECT service_name, case_topic, COUNT(*) AS count FROM service_requests_311 WHERE {where} "
        "GROUP BY service_name, case_topic ORDER BY count DESC LIMIT 80",
        params,
        limit=80,
    )
    totals: Dict[str, int] = {definition.family.value: 0 for definition in FAMILY_DEFINITIONS}
    for row in rows:
        service = str(row.get("service_name") or "").lower()
        topic = str(row.get("case_topic") or "").lower()
        for definition in FAMILY_DEFINITIONS:
            if service in {value.lower() for value in definition.service_names} or topic in {value.lower() for value in definition.case_topics}:
                totals[definition.family.value] += _int_value(row.get("count"))
                break
    entries = [
        {"family": family, "label": _FAMILY_BY_ID[family].label, "count": count}
        for family, count in totals.items() if count > 0
    ]
    entries.sort(key=lambda item: (-item["count"], item["label"]))
    return {"categories": entries[:5]}


def _execute_tool(
    name: str,
    *,
    connection_factory: ConnectionFactory,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    freshness: Mapping[str, Any],
    all_311: bool,
) -> Dict[str, Any]:
    if name == ToolName.SOURCE_FRESHNESS.value:
        return {"freshness": dict(freshness)}
    if name == ToolName.ISSUE_COUNT.value:
        return _tool_issue_count(
            connection_factory, family, geography, period, all_311=all_311,
        )
    if name == ToolName.STATUS_BREAKDOWN.value:
        return _tool_status_breakdown(
            connection_factory, family, geography, period, all_311=all_311,
        )
    if name == ToolName.TOP_STREETS.value:
        return _tool_top_streets(
            connection_factory, family, geography, period, all_311=all_311,
        )
    if name == ToolName.RECENT_EXAMPLES.value:
        return _tool_recent_examples(
            connection_factory, family, geography, period, all_311=all_311,
        )
    if name == ToolName.PERIOD_COMPARISON.value:
        return _tool_compare_periods(
            connection_factory, family, geography, period, all_311=all_311,
        )
    if name == ToolName.CATEGORY_DISTRIBUTION.value:
        return _tool_category_distribution(
            connection_factory, geography, period, all_311=all_311,
        )
    raise ValueError("unregistered 311 tool")


def _execute_tools(
    plan: StrictToolPlan,
    *,
    connection_factory: ConnectionFactory,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    freshness: Mapping[str, Any],
    deadline: GlobalDeadline,
) -> Tuple[Dict[str, Any], List[str], int, Optional[str]]:
    started = time.perf_counter()
    if not plan.tools:
        return {}, [], 0, None
    results: Dict[str, Any] = {}
    failures: List[str] = []
    timeout_stage: Optional[str] = None
    all_311 = plan.issue_scope == IssueScope.ALL_311.value
    executor = ThreadPoolExecutor(max_workers=min(MAX_TOOL_WORKERS, len(plan.tools)), thread_name_prefix="civic311")
    futures: Dict[Future[Dict[str, Any]], str] = {}
    try:
        for name in plan.tools:
            if name not in TOOL_REGISTRY:
                failures.append("unregistered_tool")
                continue
            futures[executor.submit(
                _execute_tool,
                name,
                connection_factory=connection_factory,
                family=family,
                geography=geography,
                period=period,
                freshness=freshness,
                all_311=all_311,
            )] = name
        wait_seconds = min(3.0, deadline.remaining(reserve_for_persistence=True))
        if wait_seconds > 0 and futures:
            done, pending = wait(tuple(futures), timeout=wait_seconds)
        else:
            done, pending = set(), set(futures)
        for future in done:
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:
                failures.append(name)
        if pending:
            timeout_stage = "tools"
            for future in pending:
                failures.append(futures[future])
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return results, list(dict.fromkeys(failures)), int((time.perf_counter() - started) * 1000), timeout_stage


def _source_record(freshness: Mapping[str, Any], period: CivicPeriod) -> Dict[str, Any]:
    return {
        "type": "official",
        "source": SOURCE_LABEL,
        "authority": SOURCE_AUTHORITY,
        "data_through_date": freshness.get("data_through_date"),
        "covered_period": period.to_dict(),
        "source_key": SOURCE_311,
    }


def _bundle_from_execution(
    *,
    message: str,
    plan: StrictToolPlan,
    freshness: Mapping[str, Any],
    requested: CivicPeriod,
    executed: CivicPeriod,
    initial_status: EvidenceStatus,
    limitations: List[str],
    results: Dict[str, Any],
    failures: List[str],
) -> EvidenceBundle:
    geography = _geography_from_plan(plan)
    family = IssueFamily(plan.issue_family) if plan.issue_family else None
    facts: Dict[str, Any] = {}
    for name, value in results.items():
        facts[name] = value
    status = initial_status
    count_result = results.get(ToolName.ISSUE_COUNT.value)
    if count_result is not None and _int_value(count_result.get("count")) == 0 and status is EvidenceStatus.COMPLETE:
        status = EvidenceStatus.NO_MATCHING_RECORDS
    if failures:
        status = EvidenceStatus.PARTIAL if results else EvidenceStatus.DEPENDENCY_UNAVAILABLE
    if count_result and count_result.get("street_coverage_unavailable"):
        limitations.append(
            "The matching local 311 records do not include street fields, so a street-specific count cannot be verified from this dataset."
        )
        if status not in {EvidenceStatus.DEPENDENCY_UNAVAILABLE, EvidenceStatus.INSUFFICIENT_EVIDENCE}:
            status = EvidenceStatus.PARTIALLY_COVERED
    if geography.subarea and not geography.filterable:
        limitations.append(
            f"The local 311 source does not include a verified {geography.subarea} boundary, so the available count is Dorchester-wide.")
        if status is EvidenceStatus.COMPLETE:
            status = EvidenceStatus.PARTIALLY_COVERED
    model_fact_labels = {
        ToolName.ISSUE_COUNT.value: "issue_count",
        ToolName.STATUS_BREAKDOWN.value: "status_breakdown",
        ToolName.TOP_STREETS.value: "top_streets",
        ToolName.RECENT_EXAMPLES.value: "recent_examples",
        ToolName.PERIOD_COMPARISON.value: "period_comparison",
        ToolName.CATEGORY_DISTRIBUTION.value: "category_distribution",
    }
    model_facts = {
        model_fact_labels[name]: value
        for name, value in results.items()
        if name in model_fact_labels
    }
    return EvidenceBundle(
        question_scope={
            "domain": "311",
            "intent": plan.intent,
            "issue_scope": plan.issue_scope,
            "issue_label": _family_label(
                plan.issue_family,
                all_311=plan.issue_scope == IssueScope.ALL_311.value,
            ),
            "requested_outputs": list(plan.outputs),
        },
        issue_family=family.value if family else None,
        geography=geography.to_dict(),
        requested_period=requested.to_dict(),
        executed_period=executed.to_dict(),
        freshness=dict(freshness),
        coverage={"status": freshness.get("coverage_status"), "data_through_date": freshness.get("data_through_date")},
        facts=facts,
        model_facts=model_facts,
        tool_results=results,
        limitations=list(dict.fromkeys(limitations))[:5],
        missing_evidence=failures[:7],
        source_records=[_source_record(freshness, executed)],
        evidence_status=status,
        partial_failures=failures[:7],
    )


def _date_phrase(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(str(value)[:10])
        return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"
    except ValueError:
        return None


def _family_label(family: Optional[str], *, all_311: bool = False) -> str:
    if all_311:
        return "all 311 service requests"
    definition = _FAMILY_BY_ID.get(str(family or ""))
    return definition.label if definition else "street-service"


def _request_label(family: Optional[str], *, all_311: bool = False) -> str:
    if all_311:
        return "311 service requests"
    labels = {
        IssueFamily.ROAD_SURFACE_POTHOLE.value: "pothole-related 311 service requests",
        IssueFamily.DRAINAGE_AND_FLOODING.value: "drainage- or flooding-related 311 service requests",
        IssueFamily.STREET_AND_PAVEMENT_CONDITION.value: "street- or pavement-related 311 service requests",
        IssueFamily.TRASH_AND_ILLEGAL_DUMPING.value: "trash- or illegal-dumping-related 311 service requests",
        IssueFamily.STREETLIGHT.value: "streetlight-related 311 service requests",
    }
    return labels.get(str(family or ""), "street-related 311 service requests")


def _counted_request_label(count: int, family: Optional[str], *, all_311: bool = False) -> str:
    plural = _request_label(family, all_311=all_311)
    return plural[:-1] if count == 1 else plural


def _period_range(period: Mapping[str, Any]) -> Optional[str]:
    try:
        start = date.fromisoformat(str(period["start_date"]))
        end = date.fromisoformat(str(period["end_date_exclusive"])) - timedelta(days=1)
    except (KeyError, TypeError, ValueError):
        return None
    start_text = f"{start.strftime('%B')} {start.day}"
    if start.year != end.year:
        start_text += f", {start.year}"
    end_text = f"{end.strftime('%B')} {end.day}, {end.year}"
    return f"{start_text} through {end_text}"


def _status_count_phrase(count: int, label: str, *, include_request: bool) -> str:
    noun = "request" if count == 1 else "requests"
    verb = "is" if count == 1 else "are"
    if include_request:
        return f"{count} {noun} {verb} recorded as {label}"
    return f"{count} {verb} recorded as {label}"


def _status_sentence(open_count: int, closed_count: int) -> str:
    return (
        _status_count_phrase(open_count, "open or in progress", include_request=True)
        + ", and "
        + _status_count_phrase(closed_count, "closed", include_request=False)
        + "."
    )


def _append_limitations(lines: List[str], bundle: EvidenceBundle) -> None:
    rendered = _normalized(" ".join(lines))
    for limitation in bundle.limitations[:3]:
        normalized = _normalized(limitation)
        if normalized and normalized not in rendered:
            lines.append(limitation)
            rendered = _normalized(" ".join(lines))


def _append_period_context(lines: List[str], prefix: str, bundle: EvidenceBundle) -> None:
    if prefix:
        lines.append(prefix)
    _append_limitations(lines, bundle)


def _period_intro(bundle: EvidenceBundle) -> str:
    requested = bundle.requested_period.get("label", "the requested period")
    executed = bundle.executed_period.get("label", requested)
    through = _date_phrase(bundle.freshness.get("data_through_date"))
    range_text = _period_range(bundle.executed_period)
    coverage_status = str(bundle.coverage.get("status") or "").upper()
    if coverage_status == CoverageStatus.PARTIAL.value:
        rendered_period = executed
        if range_text and _normalized(range_text) not in _normalized(executed):
            rendered_period = f"{executed} ({range_text})"
        used = f" I used {rendered_period}." if executed != requested else ""
        missing = ""
        try:
            requested_end = date.fromisoformat(str(bundle.requested_period["end_date_exclusive"]))
            executed_end = date.fromisoformat(str(bundle.executed_period["end_date_exclusive"]))
            if executed_end < requested_end:
                missing_end = requested_end - timedelta(days=1)
                missing = (
                    f" {executed_end.strftime('%B')} {executed_end.day} through "
                    f"{missing_end.strftime('%B')} {missing_end.day} are not covered and are not treated as zero."
                )
        except (KeyError, TypeError, ValueError):
            missing = ""
        return (
            f"The local data only runs through {through or 'the latest recorded date'}, so it covers only part of {requested}."
            + used
            + missing
        )
    if executed != requested:
        return f"The local data only runs through {through or 'the latest recorded date'}, so it does not describe current conditions."
    if bundle.evidence_status in {EvidenceStatus.PARTIALLY_COVERED, EvidenceStatus.STALE_BUT_USABLE}:
        return f"The local data only runs through {through or 'the latest recorded date'}, so it does not describe current conditions."
    return ""


def _format_streets(streets: Sequence[Mapping[str, Any]]) -> str:
    parts = [f"{item.get('street')} ({_int_value(item.get('count'))})" for item in streets if item.get("street")]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def deterministic_answer(bundle: EvidenceBundle, plan: StrictToolPlan) -> Tuple[str, AnswerMode]:
    if plan.needs_clarification:
        return plan.clarification_message or "Which city-service issue are you asking about?", AnswerMode.CLARIFICATION
    if bundle.evidence_status is EvidenceStatus.DEPENDENCY_UNAVAILABLE:
        return (
            "I could not load the available Boston 311 service-request data right now, so I cannot confirm a count.",
            AnswerMode.DETERMINISTIC_FALLBACK,
        )
    if bundle.evidence_status is EvidenceStatus.OUTSIDE_COVERAGE:
        requested = str(bundle.requested_period.get("label") or "that period")
        through = _date_phrase(bundle.freshness.get("data_through_date"))
        return (
            f"I can't provide a reliable count for {requested} from the current local dataset. "
            f"The available 311 service-request data contains records only through "
            f"{through or 'the latest recorded date'}.",
            AnswerMode.DETERMINISTIC_FALLBACK,
        )
    all_311 = plan.issue_scope == IssueScope.ALL_311.value
    family_label = _family_label(bundle.issue_family, all_311=all_311)
    request_label = _request_label(bundle.issue_family, all_311=all_311)
    period = bundle.executed_period.get("label", "the selected period")
    geo = bundle.geography
    street_label = str(geo.get("street") or "").title()
    place = street_label or ("Dorchester" if geo.get("filterable", True) else "Dorchester-wide records")
    if street_label:
        place_scope = f"for {street_label}"
    elif geo.get("filterable", True):
        place_scope = "in Dorchester"
    else:
        place_scope = "in the available Dorchester-wide records"
    prefix = _period_intro(bundle)
    lines: List[str] = []
    required_fact = {
        "category_overview": ToolName.CATEGORY_DISTRIBUTION.value,
        "period_comparison": ToolName.PERIOD_COMPARISON.value,
        "status_followup": ToolName.STATUS_BREAKDOWN.value,
        "top_streets": ToolName.TOP_STREETS.value,
    }.get(plan.intent, ToolName.ISSUE_COUNT.value)
    if bundle.evidence_status is EvidenceStatus.PARTIAL and required_fact not in bundle.facts:
        lines.append(
            f"Some Boston 311 results for {family_label} {place_scope} for {period} were temporarily unavailable, "
            "so I cannot confirm the requested result from this response."
        )
        _append_period_context(lines, prefix, bundle)
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_FALLBACK
    if plan.intent == "category_overview":
        categories = (bundle.facts.get(ToolName.CATEGORY_DISTRIBUTION.value) or {}).get("categories") or []
        if not categories:
            scope_label = "311 service requests" if all_311 else "street-related 311 service requests in the available categories"
            lines.append(f"I did not find matching {scope_label} in Dorchester for {period}.")
        else:
            listed = "; ".join(f"{item['label']} ({_int_value(item['count'])})" for item in categories)
            scope_label = "311 service-request categories" if all_311 else "street-related 311 service requests in the available categories"
            lines.append(f"For {period}, the most common {scope_label} in Dorchester were {listed}.")
        _append_period_context(lines, prefix, bundle)
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_ENRICHED
    if plan.intent == "period_comparison":
        comparison = bundle.facts.get(ToolName.PERIOD_COMPARISON.value) or {}
        current = _int_value(comparison.get("current_count"))
        previous = _int_value(comparison.get("previous_count"))
        difference = _int_value(comparison.get("difference"))
        try:
            percentage = abs(float(comparison["percentage_change"]))
        except (KeyError, TypeError, ValueError):
            percentage = None
        days = max(1, (date.fromisoformat(bundle.executed_period["end_date_exclusive"]) - date.fromisoformat(bundle.executed_period["start_date"])).days)
        if difference > 0:
            direction = "increase"
            sentence = f"In the two latest comparable {days}-day periods available, {request_label} increased from {previous} to {current}"
        elif difference < 0:
            direction = "decrease"
            sentence = f"In the two latest comparable {days}-day periods available, {request_label} decreased from {previous} to {current}"
        else:
            direction = None
            sentence = f"In the two latest comparable {days}-day periods available, {request_label} remained at {current}"
        if direction:
            request_word = "request" if abs(difference) == 1 else "requests"
            article = "an" if direction == "increase" else "a"
            sentence += f", {article} {direction} of {abs(difference)} {request_word}"
            if percentage is not None:
                sentence += f", or {percentage:g}%"
        sentence += "."
        lines.append(sentence)
        _append_period_context(lines, prefix, bundle)
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_ENRICHED
    if plan.intent == "status_followup":
        status = bundle.facts.get(ToolName.STATUS_BREAKDOWN.value) or {}
        open_count = _optional_nonnegative_int(status.get("open_count"))
        closed_count = _optional_nonnegative_int(status.get("closed_count"))
        if open_count is not None and closed_count is not None:
            lines.append(
                f"For the matching {request_label} {place_scope} for {period}, "
                f"{_status_sentence(open_count, closed_count)} "
                "A closed request does not verify that a physical problem was repaired, and an open request does not show active work."
            )
        else:
            lines.append(
                f"I found matching {family_label} 311 requests {place_scope} for {period}, "
                "but the available status breakdown is incomplete. A closed request does not verify that a physical problem was repaired."
            )
        _append_period_context(lines, prefix, bundle)
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_ENRICHED
    if plan.intent == "top_streets":
        count_data = bundle.facts.get(ToolName.ISSUE_COUNT.value) or {}
        count = _int_value(count_data.get("count"))
        streets = (bundle.facts.get(ToolName.TOP_STREETS.value) or {}).get("streets") or []
        rendered_streets = _format_streets(streets)
        if rendered_streets:
            lines.append(
                f"For {period}, the streets with the most matching {family_label} 311 requests were {rendered_streets}."
            )
        elif count > 0:
            lines.append(
                f"I found {count} matching {request_label} for {period}, but the source data did not provide a usable street breakdown for those matching records."
            )
        else:
            lines.append(f"I did not find matching {request_label} in the available records for {period}.")
        _append_period_context(lines, prefix, bundle)
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_ENRICHED
    count_data = bundle.facts.get(ToolName.ISSUE_COUNT.value) or {}
    count = _int_value(count_data.get("count"))
    if count_data.get("street_coverage_unavailable"):
        lines.append(
            f"I cannot verify a street-specific count for {street_label} from the available {family_label} 311 records for {period}, because those matching records do not include street fields."
        )
        _append_period_context(lines, prefix, bundle)
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_FALLBACK
    if plan.intent == "recent_reports" and count > 0:
        request_word = _counted_request_label(count, bundle.issue_family, all_311=all_311)
        lines.append(
            f"During {period}, there {'was' if count == 1 else 'were'} {count} {request_word} {place_scope}."
        )
        status = bundle.facts.get(ToolName.STATUS_BREAKDOWN.value) or {}
        open_count = _optional_nonnegative_int(status.get("open_count"))
        closed_count = _optional_nonnegative_int(status.get("closed_count"))
        if open_count is not None and closed_count is not None:
            closed_verb = "is" if closed_count == 1 else "are"
            open_verb = "is" if open_count == 1 else "are"
            lines.append(
                f"{closed_count} {closed_verb} recorded as closed and {open_count} {open_verb} recorded as open or in progress."
            )
        lines.append("Service requests are not necessarily distinct verified physical conditions.")
        _append_period_context(lines, prefix, bundle)
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_ENRICHED
    if count == 0:
        lines.append(f"I did not find matching {request_label} {place_scope} for {period}.")
        if street_label:
            lines.append(
                "Street names in 311 records may be missing or formatted differently, so this is not definitive proof that no request was made for that location."
            )
        lines.append("Request records or their absence do not determine whether the physical condition was addressed.")
    else:
        lines.append(
            f"I found {count} {_counted_request_label(count, bundle.issue_family, all_311=all_311)} "
            f"{place_scope} for {period}."
        )
        lines.append(
            "These are service requests, not a verified count of distinct physical conditions."
        )
    status = bundle.facts.get(ToolName.STATUS_BREAKDOWN.value) or {}
    if status and count > 0:
        open_count = _int_value(status.get("open_count"))
        closed_count = _int_value(status.get("closed_count"))
        lines.append(
            f"{_status_sentence(open_count, closed_count)} "
            "A closed request does not verify that a physical problem was repaired, and an open request does not show active work."
        )
    streets = (bundle.facts.get(ToolName.TOP_STREETS.value) or {}).get("streets") or []
    rendered_streets = _format_streets(streets)
    if rendered_streets:
        lines.append(f"The streets with the most matching requests were {rendered_streets}.")
    examples = (bundle.facts.get(ToolName.RECENT_EXAMPLES.value) or {}).get("examples") or []
    if examples and plan.intent == "street_status":
        rendered = "; ".join(
            " ".join(part for part in (item.get("opened"), item.get("street"), item.get("status")) if part)
            for item in examples[:3]
        )
        if rendered:
            lines.append(f"Recent matching request records include: {rendered}.")
    _append_period_context(lines, prefix, bundle)
    lines.append(f"Source: {SOURCE_LABEL}.")
    mode = AnswerMode.DETERMINISTIC_DIRECT if not status and not streets else AnswerMode.DETERMINISTIC_ENRICHED
    return "\n\n".join(lines), mode


_MODEL_FACT_BY_OUTPUT = {
    OutputName.ISSUE_COUNT.value: "issue_count",
    OutputName.MATCHING_REQUESTS.value: "issue_count",
    OutputName.STATUS_BREAKDOWN.value: "status_breakdown",
    OutputName.TOP_STREETS.value: "top_streets",
    OutputName.RECENT_EXAMPLES.value: "recent_examples",
    OutputName.PERIOD_COMPARISON.value: "period_comparison",
    OutputName.CATEGORY_DISTRIBUTION.value: "category_distribution",
}


def _answer_task_projection(question: str, plan: StrictToolPlan, bundle: EvidenceBundle) -> Dict[str, Any]:
    """Project only evidence that can answer this plan's requested information need."""
    payload = bundle.bounded_for_model()
    requested = set(plan.outputs)
    allowed_by_intent = {
        "exact_count": {OutputName.ISSUE_COUNT.value},
        "recent_reports": {OutputName.ISSUE_COUNT.value, OutputName.STATUS_BREAKDOWN.value},
        "status_followup": {OutputName.STATUS_BREAKDOWN.value},
        "top_streets": {OutputName.TOP_STREETS.value, OutputName.ISSUE_COUNT.value},
        "street_status": {
            OutputName.MATCHING_REQUESTS.value,
            OutputName.ISSUE_COUNT.value,
            OutputName.STATUS_BREAKDOWN.value,
            OutputName.RECENT_EXAMPLES.value,
        },
        "period_comparison": {OutputName.PERIOD_COMPARISON.value},
        "category_overview": {OutputName.CATEGORY_DISTRIBUTION.value},
    }.get(plan.intent, requested)
    relevant_outputs = requested.intersection(allowed_by_intent)
    fact_keys = {
        _MODEL_FACT_BY_OUTPUT[output]
        for output in relevant_outputs
        if output in _MODEL_FACT_BY_OUTPUT
    }
    payload["scope"] = {
        "intent": plan.intent,
        "issue_label": bundle.question_scope.get("issue_label") or _family_label(
            bundle.issue_family,
            all_311=plan.issue_scope == IssueScope.ALL_311.value,
        ),
    }
    payload["task"] = {
        "requested_information": [key.replace("_", " ") for key in sorted(fact_keys)],
        "resident_question": question[:600],
    }
    payload["facts"] = {
        key: value
        for key, value in dict(payload.get("facts") or {}).items()
        if key in fact_keys
    }
    location_relevant = bool(bundle.geography.get("street") or bundle.geography.get("subarea"))
    street_relevant = OutputName.TOP_STREETS.value in relevant_outputs or location_relevant
    payload["limitations"] = [
        limitation
        for limitation in payload.get("limitations") or []
        if street_relevant or "street" not in str(limitation).lower()
    ]
    return payload


def _synthesis_prompt(question: str, bundle: EvidenceBundle, plan: StrictToolPlan) -> str:
    executed_label = str(bundle.executed_period.get("label") or "the executed period")
    intent_instruction = {
        "street_status": (
            "Answer the matching-request and status questions together. Include recent examples only when supplied. "
            "State that request records or their absence do not determine whether the physical condition was addressed. "
        ),
        "period_comparison": (
            "Report only the change between the two supplied equivalent periods; do not make a current or general trend claim. "
        ),
        "category_overview": (
            "Rank only the supplied street-related categories and do not discuss missing street fields. "
        ),
    }.get(plan.intent, "Answer only the requested information represented in the task evidence. ")
    return (
        "Answer a Dorchester resident using only this structured official 311 evidence. "
        "Answer the question first. Preserve supplied counts, streets, geography, and periods exactly. "
        "Do not mention an evidence type, missing field, ranking, or limitation unless it appears in the answer task below. "
        + intent_instruction
        + "For a percentage change, use the absolute magnitude with the direction word increase or decrease; do not write a signed percentage. "
        f"Call the records '{_request_label(bundle.issue_family, all_311=plan.issue_scope == IssueScope.ALL_311.value)}', not complaints or reports. "
        "Say that service requests are not necessarily distinct verified physical conditions. "
        "Use count-aware grammar: one request is recorded as open; multiple requests are recorded as open. "
        "Use the resident-friendly issue_family label from the evidence; never emit an all-caps enum or underscore identifier. "
        "Do not mention tools, SQL, RAG, tables, or internal identifiers. Do not infer repair from closed status or active work from open status. "
        "For a street_status question, explicitly state that request records or their absence do not determine whether the physical condition was addressed. "
        "Do not add emergency guidance. If data is stale or partial, use the supplied resident-facing limitation; never say stale but usable, outside local coverage, or coverage status. "
        "For a zero count, say that you did not find matching records; never claim definitively that there were no requests. "
        f"State the executed period using this exact label or its exact inclusive date range: {executed_label}. "
        f"End with exactly: Source: {SOURCE_LABEL}. "
        "Resident question: " + question[:600] + "\nAnswer task evidence: " + json.dumps(_answer_task_projection(question, plan, bundle), ensure_ascii=False, separators=(",", ":"), default=str)
    )


def _repair_prompt(question: str, bundle: EvidenceBundle, plan: StrictToolPlan, failures: Sequence[str]) -> str:
    return (
        _synthesis_prompt(question, bundle, plan)
        + "\nThe previous draft failed deterministic validation for only these reasons: "
        + ", ".join(bounded_strings(failures, limit=8, maximum_length=48))
        + f". Include the source line exactly as 'Source: {SOURCE_LABEL}.' "
        + "Return one corrected answer only. Do not discuss the validation process."
    )


def _should_synthesize(plan: StrictToolPlan, bundle: EvidenceBundle) -> bool:
    if plan.needs_clarification:
        return False
    if bundle.evidence_status in {
        EvidenceStatus.DEPENDENCY_UNAVAILABLE,
        EvidenceStatus.INSUFFICIENT_EVIDENCE,
        EvidenceStatus.NO_MATCHING_RECORDS,
    }:
        return False
    issue_count = _optional_nonnegative_int(
        (bundle.facts.get(ToolName.ISSUE_COUNT.value) or {}).get("count")
    )
    if issue_count == 0:
        return False
    # Use synthesis only when it adds semantic or communicative value beyond
    # the deterministic evidence composer. Reviewed count/status, ranking, and
    # two-period shapes are clearer and cheaper when composed deterministically.
    return plan.intent == "street_status" and {
        OutputName.MATCHING_REQUESTS.value,
        OutputName.STATUS_BREAKDOWN.value,
        OutputName.RECENT_EXAMPLES.value,
    }.issubset(set(plan.outputs))


@dataclass(frozen=True)
class CompositionOutcome:
    answer: str
    mode: AnswerMode
    synthesis_ms: int = 0
    validation_ms: int = 0
    repair_ms: int = 0
    synthesis_call_count: int = 0
    repair_call_count: int = 0
    fallback_used: bool = False
    timeout_stage: Optional[str] = None


def _allowed_numbers(bundle: EvidenceBundle) -> set[str]:
    encoded = json.dumps(bundle.bounded_for_model(), default=str)
    return set(re.findall(r"\b\d+(?:\.\d+)?\b", encoded))


def _known_streets(bundle: EvidenceBundle) -> set[str]:
    values: set[str] = set()
    if bundle.geography.get("street"):
        values.add(str(bundle.geography["street"]).lower())
    if bundle.geography.get("subarea"):
        # A named subarea may include a suffix such as "Square". It is a
        # scope limitation explicitly supplied by the planner, not an invented
        # street claim.
        values.add(str(bundle.geography["subarea"]).lower())
    for item in (bundle.facts.get(ToolName.TOP_STREETS.value) or {}).get("streets") or []:
        if item.get("street"):
            values.add(str(item["street"]).lower())
    for item in (bundle.facts.get(ToolName.RECENT_EXAMPLES.value) or {}).get("examples") or []:
        if item.get("street"):
            values.add(str(item["street"]).lower())
    return values


_ANSWER_DATE_RANGE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{1,2})(?:,\s*(20\d{2}))?\s*(?:through|to|[-\u2013])\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)?\s*"
    r"(\d{1,2}),\s*(20\d{2})\b",
    re.IGNORECASE,
)
_MONTH_NUMBER = {name.lower(): index for index, name in enumerate(month_name) if name}


def _answer_date_ranges(text: str) -> set[Tuple[date, date]]:
    ranges: set[Tuple[date, date]] = set()
    for match in _ANSWER_DATE_RANGE.finditer(text):
        start_month = _MONTH_NUMBER[match.group(1).lower()]
        end_month = _MONTH_NUMBER[(match.group(4) or match.group(1)).lower()]
        end_year = int(match.group(6))
        start_year = int(match.group(3) or end_year)
        try:
            ranges.add(
                (
                    date(start_year, start_month, int(match.group(2))),
                    date(end_year, end_month, int(match.group(5))),
                )
            )
        except ValueError:
            continue
    return ranges


def _bundle_date_ranges(bundle: EvidenceBundle) -> set[Tuple[date, date]]:
    ranges: set[Tuple[date, date]] = set()
    for raw in (bundle.requested_period, bundle.executed_period):
        try:
            ranges.add(
                (
                    date.fromisoformat(str(raw["start_date"])),
                    date.fromisoformat(str(raw["end_date_exclusive"])) - timedelta(days=1),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    try:
        requested_end = date.fromisoformat(str(bundle.requested_period["end_date_exclusive"]))
        executed_end = date.fromisoformat(str(bundle.executed_period["end_date_exclusive"]))
        if executed_end < requested_end:
            ranges.add((executed_end, requested_end - timedelta(days=1)))
    except (KeyError, TypeError, ValueError):
        pass
    return ranges


def _answer_has_labeled_count(text: str, count: int, label: str) -> bool:
    escaped = re.escape(str(count))
    return bool(
        re.search(rf"\b{escaped}\b.{{0,28}}\b{label}\b", text, re.IGNORECASE)
        or re.search(rf"\b{label}\b.{{0,28}}\b{escaped}\b", text, re.IGNORECASE)
    )


def _relevance_issues(
    text: str,
    bundle: EvidenceBundle,
    plan: Optional[StrictToolPlan],
) -> List[str]:
    """Reject supported-but-off-task claim classes for a known answer plan."""
    if plan is not None:
        intent = plan.intent
        requested = set(plan.outputs)
    else:
        intent = str(bundle.question_scope.get("intent") or "")
        requested = {
            str(value)
            for value in bundle.question_scope.get("requested_outputs") or ()
        }
    if not requested:
        return []

    issues: List[str] = []
    category_ranking = re.search(
        r"\b(?:most common|leading|highest(?:-ranked)?|top categor(?:y|ies))\b",
        text,
        re.IGNORECASE,
    )
    if category_ranking and not (
        intent == "category_overview"
        and OutputName.CATEGORY_DISTRIBUTION.value in requested
    ):
        issues.append("irrelevant_category_ranking")

    street_commentary = re.search(
        r"\b(?:street (?:breakdown|fields?|data|ranking)|streets? (?:were|was) not specified|usable street breakdown|streets? with the most)\b",
        text,
        re.IGNORECASE,
    )
    location_relevant = bool(bundle.geography.get("street") or bundle.geography.get("subarea"))
    if street_commentary and not (
        OutputName.TOP_STREETS.value in requested or location_relevant
    ):
        issues.append("irrelevant_street_commentary")

    status_commentary = re.search(r"\b(?:open|closed|in progress)\b", text, re.IGNORECASE)
    if status_commentary and not (
        OutputName.STATUS_BREAKDOWN.value in requested
        or intent in {"status_followup", "street_status"}
    ):
        issues.append("irrelevant_status_commentary")

    comparison_claim = re.search(
        r"\b(?:increased|decreased|increase of|decrease of|percentage change|comparable(?:\s+\d+-day)?\s+periods?|compared with|compared to)\b",
        text,
        re.IGNORECASE,
    )
    if comparison_claim and OutputName.PERIOD_COMPARISON.value not in requested:
        issues.append("irrelevant_comparison_claim")

    work_completion = re.search(
        r"\b(?:repaired|fixed|active work|physical condition was addressed|work was completed)\b",
        text,
        re.IGNORECASE,
    )
    if work_completion and intent not in {"street_status", "status_followup"}:
        issues.append("irrelevant_work_completion_caveat")
    return issues


def validate_claims(
    answer: str,
    bundle: EvidenceBundle,
    plan: Optional[StrictToolPlan] = None,
) -> Tuple[bool, Tuple[str, ...]]:
    text = str(answer or "").strip()
    normalized_text = _normalized(text)
    issues: List[str] = []
    if not text:
        issues.append("empty_answer")
    if internal_terms(text):
        issues.append("internal_term")
    if re.search(r"\bcall 911\b", text, re.IGNORECASE):
        issues.append("unsupported_emergency_guidance")
    if re.search(r"-\s*\d+(?:\.\d+)?\s*%", text):
        issues.append("signed_directional_percentage")
    if re.search(r"\b1\s+(?:open(?:\s+or\s+in[- ]progress)?|closed)\s+requests\b", text, re.IGNORECASE):
        issues.append("singular_request_grammar")
    if re.search(r"\b(?:stale but usable|outside local coverage|coverage status)\b", text, re.IGNORECASE):
        issues.append("internal_freshness_wording")
    if re.search(r"\bcomplaints?\b", text, re.IGNORECASE):
        issues.append("complaint_terminology")
    issues.extend(_relevance_issues(text, bundle, plan))
    if bundle.question_scope.get("intent") == "period_comparison" and re.search(
        r"\b(?:getting (?:better|worse)|not getting worse|improving|worsening)\b", text, re.IGNORECASE
    ):
        issues.append("unsupported_current_trend")
    if bundle.question_scope.get("intent") == "category_overview" and re.search(
        r"^\s*in\s+dorchester,?\s+the\b.{0,160}\bthere were\b", text, re.IGNORECASE | re.DOTALL
    ):
        issues.append("awkward_category_sentence")
    if re.search(r"\b(?:closed|closure)\b.{0,40}\b(?:repaired|fixed|resolved)\b", text, re.IGNORECASE):
        issues.append("closed_is_repaired")
    claims_open_active_work = re.search(
        r"\bopen\b.{0,40}\b(?:shows?|means?|indicates?)\s+(?:that\s+)?active work\b"
        r"|\bopen\b.{0,40}\b(?:actively|currently)\s+(?:being )?worked\b",
        text,
        re.IGNORECASE,
    )
    negates_open_active_work = re.search(
        r"\bopen\b.{0,40}\b(?:does not|doesn't|do not|don't|not)\s+"
        r"(?:show|mean|indicate)\s+(?:that\s+)?active work\b",
        text,
        re.IGNORECASE,
    )
    if claims_open_active_work and not negates_open_active_work:
        issues.append("open_is_active_work")
    if bundle.question_scope.get("intent") == "street_status" and not (
        re.search(
            r"\b(?:does not|do not|don't|cannot|can't)\b.{0,90}\b(?:determine|show|verify|prove)\b.{0,90}\b(?:physical|condition|addressed|repaired|completed)\b",
            normalized_text,
        )
        or re.search(
            r"\b(?:physical|condition)\b.{0,90}\b(?:does not|do not|don't|cannot|can't)\b.{0,90}\b(?:addressed|repaired|completed)\b",
            normalized_text,
        )
    ):
        issues.append("missing_physical_completion_caveat")
    if bundle.evidence_status in {EvidenceStatus.STALE_BUT_USABLE, EvidenceStatus.PARTIALLY_COVERED} and re.search(
        r"\b(?:right now|currently|(?:data|records?|coverage)\s+(?:is|are)\s+current)\b",
        text,
        re.IGNORECASE,
    ):
        issues.append("stale_claimed_current")
    coverage_status = str(bundle.coverage.get("status") or "").upper()
    requested_label = str(bundle.requested_period.get("label") or "").strip()
    executed_label = str(bundle.executed_period.get("label") or "").strip()
    if coverage_status == "OUTSIDE" and requested_label != executed_label and "does not describe current conditions" not in normalized_text:
        issues.append("missing_current_conditions_limitation")
    if coverage_status == "PARTIAL" and "covers only part" not in normalized_text:
        issues.append("missing_partial_coverage_limitation")
    if bundle.evidence_status not in {
        EvidenceStatus.DEPENDENCY_UNAVAILABLE,
        EvidenceStatus.INSUFFICIENT_EVIDENCE,
    } and _normalized(SOURCE_LABEL) not in normalized_text:
        issues.append("missing_source_label")
    family_label = _family_label(bundle.issue_family)
    if bundle.issue_family and _normalized(family_label) not in normalized_text:
        issues.append("missing_issue_family")
    executed_label = str(bundle.executed_period.get("label") or "").strip()
    answer_ranges = _answer_date_ranges(text)
    allowed_ranges = _bundle_date_ranges(bundle)
    executed_range = None
    try:
        executed_range = (
            date.fromisoformat(str(bundle.executed_period["start_date"])),
            date.fromisoformat(str(bundle.executed_period["end_date_exclusive"])) - timedelta(days=1),
        )
    except (KeyError, TypeError, ValueError):
        pass
    comparison_period_scope = False
    if bundle.question_scope.get("intent") == "period_comparison" and executed_range is not None:
        period_days = (executed_range[1] - executed_range[0]).days + 1
        comparison_period_scope = bool(re.search(
            rf"\btwo latest comparable {period_days} day periods\b",
            normalized_text,
        ))
    if (
        executed_label
        and _normalized(executed_label) not in normalized_text
        and (executed_range is None or executed_range not in answer_ranges)
        and not comparison_period_scope
    ):
        issues.append("missing_executed_period")
    if any(candidate not in allowed_ranges for candidate in answer_ranges):
        issues.append("unsupported_date_range")
    through = str(bundle.freshness.get("data_through_date") or "").strip()
    if requested_label and executed_label and requested_label != executed_label:
        human_through = _date_phrase(through)
        if through not in text and (not human_through or _normalized(human_through) not in normalized_text):
            issues.append("missing_coverage_date")
    geography = bundle.geography
    street = str(geography.get("street") or "").strip()
    if street and _normalized(street) not in normalized_text:
        issues.append("missing_requested_street")
    subarea = str(geography.get("subarea") or "").strip()
    if subarea and _normalized(subarea) not in normalized_text:
        issues.append("missing_requested_subarea")
    if subarea and not geography.get("filterable", True):
        if not re.search(r"\b(?:dorchester wide|boundary|cannot verify|not available)\b", normalized_text):
            issues.append("missing_subarea_limitation")
    if bundle.question_scope.get("intent") in {"recent_reports", "street_status"}:
        issue_count = _optional_nonnegative_int(
            (bundle.facts.get(ToolName.ISSUE_COUNT.value) or {}).get("count")
        )
        if issue_count and not re.search(r"\b(?:not (?:a )?verified count of distinct|may not represent distinct|not necessarily distinct)\b", normalized_text):
            issues.append("missing_distinct_request_caveat")
        status = bundle.facts.get(ToolName.STATUS_BREAKDOWN.value) or {}
        open_count = _optional_nonnegative_int(status.get("open_count"))
        closed_count = _optional_nonnegative_int(status.get("closed_count"))
        if issue_count and open_count is not None and closed_count is not None and not (
            _answer_has_labeled_count(text, open_count, "open")
            and _answer_has_labeled_count(text, closed_count, "closed")
        ):
            issues.append("missing_status_breakdown")
        if issue_count == 0 and re.search(
            r"\b(?:there (?:were|are) (?:no|0)|(?:i )?found no)\b", text, re.IGNORECASE
        ):
            issues.append("categorical_zero_claim")
    allowed_numbers = _allowed_numbers(bundle)
    for number in re.findall(r"\b\d+(?:\.\d+)?\b", text):
        if number not in allowed_numbers:
            issues.append("unsupported_number")
            break
    known_streets = _known_streets(bundle)
    for candidate in re.findall(
        r"\b([A-Z][A-Za-z0-9.'-]*(?:\s+[A-Z][A-Za-z0-9.'-]*){0,6}\s(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Way|Place|Pl|Square|Sq))\b",
        text,
    ):
        if candidate.strip().lower() not in known_streets:
            issues.append("unsupported_street")
            break
    return not issues, tuple(dict.fromkeys(issues))


def compose_311_answer(
    message: str,
    *,
    bundle: EvidenceBundle,
    plan: StrictToolPlan,
    deadline: GlobalDeadline,
    synthesis_llm: Optional[SynthesisLLM] = None,
    repair_llm: Optional[RepairLLM] = None,
    prior_timeout_stage: Optional[str] = None,
    prior_fallback_used: bool = False,
) -> CompositionOutcome:
    """Select one bounded answer mode and validate every resident-facing claim."""
    answer, mode = deterministic_answer(bundle, plan)
    synthesis_ms = 0
    validation_ms = 0
    repair_ms = 0
    synthesis_call_count = 0
    repair_call_count = 0
    synthesis_accepted = False
    fallback_used = prior_fallback_used
    timeout_stage = prior_timeout_stage
    synthesis_warranted = _should_synthesize(plan, bundle)
    if synthesis_warranted and synthesis_llm is None:
        fallback_used = True
    if synthesis_llm is not None and synthesis_warranted and deadline.remaining(reserve_for_persistence=True) >= 2.0:
        synthesis_started = time.perf_counter()
        synthesis_call_count = 1
        candidate: Optional[str] = None
        try:
            candidate = synthesis_llm(
                _synthesis_prompt(message, bundle, plan),
                min(6.0, deadline.remaining(reserve_for_persistence=True)),
            )
        except TimeoutError:
            fallback_used = True
            timeout_stage = timeout_stage or "synthesis"
        except Exception:
            fallback_used = True
            timeout_stage = timeout_stage or "synthesis_provider"
        synthesis_ms = int((time.perf_counter() - synthesis_started) * 1000)
        if candidate is not None:
            validation_started = time.perf_counter()
            valid, issues = validate_claims(candidate, bundle, plan)
            validation_ms += int((time.perf_counter() - validation_started) * 1000)
            if valid:
                answer = candidate.strip()
                mode = AnswerMode.EVIDENCE_BOUNDED_SYNTHESIS
                synthesis_accepted = True
            else:
                fallback_used = True
                if repair_llm is not None and deadline.remaining(reserve_for_persistence=True) >= 2.0:
                    repair_started = time.perf_counter()
                    repair_call_count = 1
                    repaired: Optional[str] = None
                    try:
                        repaired = repair_llm(
                            _repair_prompt(message, bundle, plan, issues),
                            min(3.0, deadline.remaining(reserve_for_persistence=True)),
                        )
                    except TimeoutError:
                        timeout_stage = timeout_stage or "repair"
                    except Exception:
                        timeout_stage = timeout_stage or "repair_provider"
                    repair_ms = int((time.perf_counter() - repair_started) * 1000)
                    if repaired is not None:
                        validation_started = time.perf_counter()
                        repaired_valid, _repair_issues = validate_claims(repaired, bundle, plan)
                        validation_ms += int((time.perf_counter() - validation_started) * 1000)
                        if repaired_valid:
                            answer = repaired.strip()
                            mode = AnswerMode.EVIDENCE_BOUNDED_SYNTHESIS
                            synthesis_accepted = True
    elif synthesis_warranted and synthesis_llm is not None:
        fallback_used = True
        timeout_stage = timeout_stage or "synthesis_budget"
    if synthesis_call_count and not synthesis_accepted:
        answer, _deterministic_mode = deterministic_answer(bundle, plan)
        mode = AnswerMode.DETERMINISTIC_FALLBACK
    validation_started = time.perf_counter()
    valid, _issues = validate_claims(answer, bundle, plan)
    if not valid:
        answer, mode = deterministic_answer(bundle, plan)
        mode = AnswerMode.DETERMINISTIC_FALLBACK
        fallback_used = True
    validation_ms += int((time.perf_counter() - validation_started) * 1000)
    return CompositionOutcome(
        answer=answer,
        mode=mode,
        synthesis_ms=synthesis_ms,
        validation_ms=validation_ms,
        repair_ms=repair_ms,
        synthesis_call_count=synthesis_call_count,
        repair_call_count=repair_call_count,
        fallback_used=fallback_used,
        timeout_stage=timeout_stage,
    )


def _context_from_bundle(bundle: EvidenceBundle, plan: StrictToolPlan) -> Optional[Dict[str, Any]]:
    if plan.needs_clarification or plan.issue_scope == IssueScope.UNSPECIFIED.value:
        return None
    try:
        context = Active311FactContext(
            issue_family=plan.issue_family,
            issue_scope=plan.issue_scope,
            geography=_geography_from_plan(plan),
            period_kind=str(bundle.executed_period.get("kind") or "RECENT"),
            executed_period=CivicPeriod(
                kind=str(bundle.executed_period["kind"]),
                label=str(bundle.executed_period["label"]),
                start=date.fromisoformat(str(bundle.executed_period["start_date"])),
                end_exclusive=date.fromisoformat(str(bundle.executed_period["end_date_exclusive"])),
            ),
            data_through_date=_bounded_optional_text(bundle.freshness.get("data_through_date"), 10),
            freshness_status=str(bundle.freshness.get("freshness_status") or "UNKNOWN"),
            coverage_status=str(bundle.evidence_status.value),
            outputs=bounded_strings(plan.outputs, limit=7, maximum_length=48),
        )
        return context.to_dict()
    except (KeyError, TypeError, ValueError):
        return None


def _pending_context_from_plan(plan: StrictToolPlan) -> Optional[Dict[str, Any]]:
    """Persist only validated canonical scope needed to answer a clarification."""
    if not plan.needs_clarification:
        return None
    try:
        period = _period_from_plan(plan)
        return Active311FactContext(
            issue_family=plan.issue_family,
            issue_scope=plan.issue_scope,
            geography=_geography_from_plan(plan),
            period_kind=period.kind,
            executed_period=period,
            data_through_date=None,
            freshness_status="UNKNOWN",
            coverage_status="PENDING_CLARIFICATION",
            outputs=bounded_strings(plan.outputs, limit=7, maximum_length=48),
        ).to_dict()
    except (KeyError, TypeError, ValueError):
        return None


def execute_311_plan(
    message: str,
    *,
    plan: StrictToolPlan,
    connection_factory: ConnectionFactory,
    deadline: GlobalDeadline,
    now: Optional[datetime] = None,
    started_at: Optional[float] = None,
    planner_ms: int = 0,
    planner_call_count: int = 0,
    planner_failure: str = "",
    synthesis_llm: Optional[SynthesisLLM] = None,
    repair_llm: Optional[RepairLLM] = None,
) -> Civic311Result:
    """Execute one already-validated plan without interpreting the resident turn."""
    started = started_at or time.perf_counter()
    current_now = now or datetime.now().astimezone()
    if plan.needs_clarification:
        evidence = EvidenceBundle(
            question_scope={"domain": "311", "intent": plan.intent},
            issue_family=plan.issue_family,
            geography=plan.geography,
            requested_period=plan.period,
            executed_period=plan.period,
            freshness={},
            coverage={},
            evidence_status=EvidenceStatus.INSUFFICIENT_EVIDENCE,
        )
        telemetry = CivicTelemetry(
            planner_source=plan.planner_source,
            planner_ms=planner_ms,
            total_latency_ms=int((time.perf_counter() - started) * 1000),
            llm_call_count=planner_call_count,
            planner_call_count=planner_call_count,
            evidence_status=evidence.evidence_status,
            answer_mode=AnswerMode.CLARIFICATION,
            fallback_used=bool(planner_failure),
            timeout_stage="planner" if planner_failure == "timeout" else None,
        )
        pending_context = _pending_context_from_plan(plan)
        return Civic311Result(
            answer=plan.clarification_message or "Which city-service issue are you asking about?",
            mode=AnswerMode.CLARIFICATION,
            sources=[],
            evidence=evidence,
            plan=plan,
            telemetry=telemetry,
            next_context=pending_context,
            update_context=pending_context is not None,
        )
    requested = _period_from_plan(plan)
    fresh, executed, evidence_status, limitations = _status_and_period(
        plan,
        connection_factory=connection_factory,
        now=current_now,
        deadline=deadline,
    )
    family = IssueFamily(plan.issue_family) if plan.issue_family else None
    geography = _geography_from_plan(plan)
    if evidence_status is EvidenceStatus.OUTSIDE_COVERAGE:
        results = {ToolName.SOURCE_FRESHNESS.value: {"freshness": dict(fresh)}}
        failures: List[str] = []
        tool_ms = 0
        timeout_stage = None
        executed_tool_count = 1
    else:
        results, failures, tool_ms, timeout_stage = _execute_tools(
            plan,
            connection_factory=connection_factory,
            family=family,
            geography=geography,
            period=executed,
            freshness=fresh,
            deadline=deadline,
        )
        executed_tool_count = len(plan.tools)
    bundle = _bundle_from_execution(
        message=message,
        plan=plan,
        freshness=fresh,
        requested=requested,
        executed=executed,
        initial_status=evidence_status,
        limitations=limitations,
        results=results,
        failures=failures,
    )
    composition = compose_311_answer(
        message,
        bundle=bundle,
        plan=plan,
        deadline=deadline,
        synthesis_llm=synthesis_llm,
        repair_llm=repair_llm,
        prior_timeout_stage=timeout_stage or ("planner" if planner_failure == "timeout" else None),
        prior_fallback_used=bool(planner_failure),
    )
    source = _source_record(fresh, executed)
    sources = [{key: value for key, value in source.items() if key != "source_key"}]
    next_context = _context_from_bundle(bundle, plan)
    telemetry = CivicTelemetry(
        planner_source=plan.planner_source,
        planner_ms=planner_ms,
        tool_execution_ms=tool_ms,
        tool_count=executed_tool_count,
        synthesis_ms=composition.synthesis_ms,
        validation_ms=composition.validation_ms,
        repair_ms=composition.repair_ms,
        total_latency_ms=int((time.perf_counter() - started) * 1000),
        llm_call_count=(
            planner_call_count
            + composition.synthesis_call_count
            + composition.repair_call_count
        ),
        planner_call_count=planner_call_count,
        synthesis_call_count=composition.synthesis_call_count,
        repair_call_count=composition.repair_call_count,
        embedding_call_count=0,
        evidence_status=bundle.evidence_status,
        answer_mode=composition.mode,
        fallback_used=composition.fallback_used,
        timeout_stage=composition.timeout_stage,
    )
    return Civic311Result(
        answer=composition.answer,
        mode=composition.mode,
        sources=sources,
        evidence=bundle,
        plan=plan,
        telemetry=telemetry,
        next_context=next_context,
        update_context=next_context is not None,
    )


def process_311_question(
    message: str,
    *,
    connection_factory: ConnectionFactory,
    active_context: Optional[Mapping[str, Any]] = None,
    state_location: Optional[Mapping[str, Any]] = None,
    request_started_at: Optional[float] = None,
    now: Optional[datetime] = None,
    planner_llm: Optional[PlannerLLM] = None,
    synthesis_llm: Optional[SynthesisLLM] = None,
    repair_llm: Optional[RepairLLM] = None,
) -> Civic311Result:
    """Run the bounded 311 path once and return no resident-internal diagnostics."""
    started = time.perf_counter()
    current_now = now or datetime.now().astimezone()
    deadline = GlobalDeadline.from_started_at(request_started_at or started)
    plan_started = time.perf_counter()
    planner_stats: Dict[str, Any] = {"call_count": 0}
    plan = plan_311_question(
        message,
        active_context=active_context,
        state_location=state_location,
        now=current_now.date(),
        planner_llm=planner_llm,
        deadline=deadline,
        planner_stats=planner_stats,
    )
    planner_ms = int((time.perf_counter() - plan_started) * 1000)
    planner_call_count = int(planner_stats.get("call_count") or 0)
    planner_failure = str(planner_stats.get("failure") or "")
    return execute_311_plan(
        message,
        plan=plan,
        connection_factory=connection_factory,
        deadline=deadline,
        now=current_now,
        started_at=started,
        planner_ms=planner_ms,
        planner_call_count=planner_call_count,
        planner_failure=planner_failure,
        synthesis_llm=synthesis_llm,
        repair_llm=repair_llm,
    )
