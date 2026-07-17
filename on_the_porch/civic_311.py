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
            "pothole", "potholes", "hole in the road", "hole in the street",
            "road crater", "broken road surface",
        ),
        ("Request for Pothole Repair", "BWSC Pothole"),
        ("Pothole",),
    ),
    FamilyDefinition(
        IssueFamily.DRAINAGE_AND_FLOODING,
        "drainage and flooding",
        (
            "flooding", "street flooding", "water collecting", "standing water",
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
            "road conditions", "pavement condition", "street damage", "sidewalk repair", "roadway repair", "sidewalk cover",
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

    issue_family: str
    geography: CivicGeography
    period_kind: str
    executed_period: CivicPeriod
    data_through_date: Optional[str]
    freshness_status: str
    coverage_status: str
    outputs: Tuple[str, ...]
    source_key: str = SOURCE_311
    semantic_revision: int = 1

    def to_dict(self) -> Dict[str, Any]:
        value = {
            "domain": "311",
            "issue_family": self.issue_family,
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
            family = str(raw.get("issue_family") or "")
            if family not in _FAMILY_BY_ID:
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
            "persistence_ms": self.telemetry.persistence_ms,
            "total_latency_ms": self.telemetry.total_latency_ms,
            "llm_call_count": self.telemetry.llm_call_count,
            "embedding_call_count": 0,
            "fallback_used": self.telemetry.fallback_used,
            "timeout_stage": self.telemetry.timeout_stage,
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
        }


PlannerLLM = Callable[[str, float], str]
SynthesisLLM = Callable[[str, float], str]
ConnectionFactory = Callable[[], Any]


def _bounded_optional_text(value: Any, maximum: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:maximum] if text else None


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
        or text in {"what about last month", "what about this month", "what about last week"}
    )


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
    if _contains_311_signal(message):
        return True
    # A bare reference is still a civic request.  Without trusted context the
    # planner returns a clarification instead of letting an unrelated agent
    # manufacture a referent.
    if _is_reference_only(message):
        return True
    context = Active311FactContext.from_mapping(active_context)
    return context is not None and _is_reference_only(message)


def _period_from_message(message: str, *, today: date) -> CivicPeriod:
    text = _normalized(message)
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
    r"\b(?:on|at|near|along|around)\s+([A-Za-z0-9 .'-]{2,72}?(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|way|place|pl|square|sq))\b",
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
    return _canonical_street(match.group(1)) if match else None


def _needs_street(message: str) -> bool:
    text = _normalized(message)
    return any(_phrase_in(text, phrase) for phrase in ("my street", "this street", "our street"))


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
    if active_context and _is_reference_only(message):
        return active_context.geography
    return CivicGeography()


def _planned_outputs(message: str, family: Optional[IssueFamily], active: Optional[Active311FactContext]) -> Tuple[Tuple[str, ...], Tuple[str, ...], str]:
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
            OutputName.TOP_STREETS.value,
            OutputName.SOURCE_FRESHNESS.value,
        ),
        (
            ToolName.ISSUE_COUNT.value,
            ToolName.STATUS_BREAKDOWN.value,
            ToolName.TOP_STREETS.value,
            ToolName.SOURCE_FRESHNESS.value,
        ),
        "recent_reports",
    )


def _default_period_from_context(message: str, active: Optional[Active311FactContext], *, today: date) -> CivicPeriod:
    text = _normalized(message)
    if active and _is_reference_only(message) and not re.search(r"\b(?:last|this|recent|\d+ days?)\b", text):
        return active.executed_period
    return _period_from_message(message, today=today)


def _clarification_plan(
    *,
    message: str,
    family: Optional[IssueFamily],
    geography: CivicGeography,
    period: CivicPeriod,
    clarification: str,
) -> StrictToolPlan:
    return StrictToolPlan(
        domain="311",
        intent="clarification",
        issue_family=family.value if family else None,
        geography=geography.to_dict(),
        period=period.to_dict(),
        outputs=(),
        tools=(),
        needs_clarification=True,
        clarification_message=clarification,
        confidence=0.98,
        planner_source=PlannerSource.RULE,
    )


def _validate_llm_plan(raw: str, *, fallback: StrictToolPlan) -> Optional[StrictToolPlan]:
    """Accept only a bounded planner selection, never arbitrary calls or SQL."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, Mapping):
        return None
    family = str(value.get("issue_family") or "")
    if family not in _FAMILY_BY_ID:
        return None
    outputs = bounded_strings(value.get("outputs") if isinstance(value.get("outputs"), list) else (), limit=7, maximum_length=48)
    tools = bounded_strings(value.get("tools") if isinstance(value.get("tools"), list) else (), limit=7, maximum_length=48)
    allowed_outputs = {item.value for item in OutputName}
    allowed_tools = set(TOOL_REGISTRY)
    if not outputs or not tools or any(item not in allowed_outputs for item in outputs) or any(item not in allowed_tools for item in tools):
        return None
    try:
        confidence = float(value.get("confidence"))
    except (TypeError, ValueError):
        return None
    if confidence < 0.75:
        return None
    return StrictToolPlan(
        domain="311",
        intent=_bounded_optional_text(value.get("intent"), 64) or fallback.intent,
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
) -> StrictToolPlan:
    """Build one typed 311 plan with deterministic rules before an optional planner."""
    today = now or date.today()
    active = Active311FactContext.from_mapping(active_context)
    family = family_for_message(message)
    if family is None and active and _is_reference_only(message):
        family = IssueFamily(active.issue_family)
    geography = _geography_from_context(message, active_context=active, state_location=state_location)
    period = _default_period_from_context(message, active, today=today)
    if _needs_street(message) and geography.street is None:
        return _clarification_plan(
            message=message,
            family=family,
            geography=geography,
            period=period,
            clarification="Which street in Dorchester are you asking about?",
        )
    if family is None and not _is_category_overview(message):
        if _is_status_followup(message):
            return _clarification_plan(
                message=message,
                family=None,
                geography=geography,
                period=period,
                clarification="Which 311 issue or set of reports are you asking about?",
            )
        if planner_llm and deadline and deadline.remaining(reserve_for_persistence=True) >= 3.0:
            fallback = StrictToolPlan(
                domain="311", intent="ambiguous_311", issue_family=None,
                geography=geography.to_dict(), period=period.to_dict(), outputs=(), tools=(),
                needs_clarification=True,
                clarification_message="Which city-service issue are you asking about?",
                confidence=0.4, planner_source=PlannerSource.RULE,
            )
            prompt = (
                "Interpret a Dorchester 311 request. Return JSON only with issue_family, intent, "
                "outputs, tools, confidence. Allowed issue_family values: "
                + ", ".join(item.value for item in IssueFamily)
                + ". Allowed outputs: " + ", ".join(item.value for item in OutputName)
                + ". Allowed tools: " + ", ".join(TOOL_REGISTRY)
                + ". Never write SQL or a resident answer. Question: " + message[:400]
            )
            try:
                planned = _validate_llm_plan(planner_llm(prompt, min(2.5, deadline.remaining(reserve_for_persistence=True))), fallback=fallback)
                if planned is not None:
                    return planned
            except Exception:
                pass
        return _clarification_plan(
            message=message,
            family=None,
            geography=geography,
            period=period,
            clarification="Which city-service issue are you asking about?",
        )
    outputs, tools, intent = _planned_outputs(message, family, active)
    return StrictToolPlan(
        domain="311",
        intent=intent,
        issue_family=family.value if family else None,
        geography=geography.to_dict(),
        period=period.to_dict(),
        outputs=outputs,
        tools=tools,
        needs_clarification=False,
        clarification_message=None,
        confidence=0.99 if family or _is_category_overview(message) else 0.8,
        planner_source=PlannerSource.RULE,
    )


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
        limitations.append(
            f"The requested period is outside local coverage through {assessment.data_through_date.isoformat()}."
        )
        return fresh, executed, EvidenceStatus.STALE_BUT_USABLE, limitations
    if assessment.coverage_status is CoverageStatus.PARTIAL:
        limitations.append(
            f"The requested period is only covered through {assessment.data_through_date.isoformat()}."
        )
        return fresh, executed, EvidenceStatus.PARTIALLY_COVERED, limitations
    if assessment.freshness_status in {FreshnessStatus.STALE, FreshnessStatus.DELAYED}:
        limitations.append(
            f"The local source contains records through {assessment.data_through_date.isoformat()}."
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
) -> Tuple[str, List[Any]]:
    clauses = [
        "(neighborhood = %s OR police_district = %s)",
        "open_date >= %s",
        "open_date < %s",
    ]
    params: List[Any] = ["Dorchester", "C11", period.start.isoformat(), period.end_exclusive.isoformat()]
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


def _tool_issue_count(connection_factory: ConnectionFactory, family: Optional[IssueFamily], geography: CivicGeography, period: CivicPeriod) -> Dict[str, Any]:
    if geography.street:
        where, params = _where_clause(
            family=family,
            geography=geography,
            period=period,
            include_street=False,
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
    where, params = _where_clause(family=family, geography=geography, period=period)
    rows = _query(connection_factory, f"SELECT COUNT(*) AS count FROM service_requests_311 WHERE {where}", params, limit=1)
    return {"count": _int_value(rows[0].get("count") if rows else 0)}


def _tool_status_breakdown(connection_factory: ConnectionFactory, family: Optional[IssueFamily], geography: CivicGeography, period: CivicPeriod) -> Dict[str, Any]:
    where, params = _where_clause(family=family, geography=geography, period=period)
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


def _tool_top_streets(connection_factory: ConnectionFactory, family: Optional[IssueFamily], geography: CivicGeography, period: CivicPeriod) -> Dict[str, Any]:
    where, params = _where_clause(family=family, geography=geography, period=period)
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


def _tool_recent_examples(connection_factory: ConnectionFactory, family: Optional[IssueFamily], geography: CivicGeography, period: CivicPeriod) -> Dict[str, Any]:
    where, params = _where_clause(family=family, geography=geography, period=period)
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


def _tool_compare_periods(connection_factory: ConnectionFactory, family: Optional[IssueFamily], geography: CivicGeography, period: CivicPeriod) -> Dict[str, Any]:
    prior_end = period.start
    prior_start = prior_end - timedelta(days=period.days)
    prior = CivicPeriod("COMPARISON_BASELINE", f"the preceding {period.days}-day period", prior_start, prior_end)
    current = _tool_issue_count(connection_factory, family, geography, period)["count"]
    previous = _tool_issue_count(connection_factory, family, geography, prior)["count"]
    difference = current - previous
    percentage = None if previous == 0 else round((difference / previous) * 100.0, 1)
    return {
        "current_count": current,
        "previous_count": previous,
        "difference": difference,
        "percentage_change": percentage,
        "previous_period": prior.to_dict(),
    }


def _tool_category_distribution(connection_factory: ConnectionFactory, geography: CivicGeography, period: CivicPeriod) -> Dict[str, Any]:
    where, params = _where_clause(family=None, geography=geography, period=period)
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
) -> Dict[str, Any]:
    if name == ToolName.SOURCE_FRESHNESS.value:
        return {"freshness": dict(freshness)}
    if name == ToolName.ISSUE_COUNT.value:
        return _tool_issue_count(connection_factory, family, geography, period)
    if name == ToolName.STATUS_BREAKDOWN.value:
        return _tool_status_breakdown(connection_factory, family, geography, period)
    if name == ToolName.TOP_STREETS.value:
        return _tool_top_streets(connection_factory, family, geography, period)
    if name == ToolName.RECENT_EXAMPLES.value:
        return _tool_recent_examples(connection_factory, family, geography, period)
    if name == ToolName.PERIOD_COMPARISON.value:
        return _tool_compare_periods(connection_factory, family, geography, period)
    if name == ToolName.CATEGORY_DISTRIBUTION.value:
        return _tool_category_distribution(connection_factory, geography, period)
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
    return EvidenceBundle(
        question_scope={"domain": "311", "intent": plan.intent},
        issue_family=family.value if family else None,
        geography=geography.to_dict(),
        requested_period=requested.to_dict(),
        executed_period=executed.to_dict(),
        freshness=dict(freshness),
        coverage={"status": freshness.get("coverage_status"), "data_through_date": freshness.get("data_through_date")},
        facts=facts,
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


def _family_label(family: Optional[str]) -> str:
    definition = _FAMILY_BY_ID.get(str(family or ""))
    return definition.label if definition else "street-service"


def _period_intro(bundle: EvidenceBundle) -> str:
    requested = bundle.requested_period.get("label", "the requested period")
    executed = bundle.executed_period.get("label", requested)
    through = _date_phrase(bundle.freshness.get("data_through_date"))
    if executed != requested:
        lead = f"The local Boston 311 dataset contains records through {through or 'the latest recorded date'}, so it does not cover {requested}. I used {executed}."
        return lead
    if bundle.evidence_status in {EvidenceStatus.PARTIALLY_COVERED, EvidenceStatus.STALE_BUT_USABLE}:
        return f"The available local Boston 311 data contains records through {through or 'the latest recorded date'}."
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
    family_label = _family_label(bundle.issue_family)
    period = bundle.executed_period.get("label", "the selected period")
    geo = bundle.geography
    street_label = str(geo.get("street") or "").title()
    place = street_label or ("Dorchester" if geo.get("filterable", True) else "Dorchester-wide records")
    prefix = _period_intro(bundle)
    lines: List[str] = [prefix] if prefix else []
    if plan.intent == "category_overview":
        categories = (bundle.facts.get(ToolName.CATEGORY_DISTRIBUTION.value) or {}).get("categories") or []
        if not categories:
            lines.append(f"I found no matching supported street-service requests in Dorchester for {period}.")
        else:
            listed = "; ".join(f"{item['label']} ({_int_value(item['count'])})" for item in categories)
            lines.append(f"For {period}, the most common supported street-service requests in Dorchester were {listed}.")
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_ENRICHED
    if plan.intent == "period_comparison":
        comparison = bundle.facts.get(ToolName.PERIOD_COMPARISON.value) or {}
        current = _int_value(comparison.get("current_count"))
        previous = _int_value(comparison.get("previous_count"))
        difference = _int_value(comparison.get("difference"))
        direction = "more" if difference > 0 else "fewer" if difference < 0 else "the same number of"
        sentence = f"There were {current} {family_label} 311 requests in Dorchester for {period}, {abs(difference)} {direction} than the preceding comparable period ({previous})."
        percentage = comparison.get("percentage_change")
        if percentage is not None:
            sentence += f" That is a {abs(float(percentage)):.1f}% {'increase' if float(percentage) > 0 else 'decrease'} from the comparable period."
        lines.append(sentence)
        lines.append(f"Source: {SOURCE_LABEL}. Comparable periods use the same number of covered days.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_ENRICHED
    if plan.intent == "status_followup":
        status = bundle.facts.get(ToolName.STATUS_BREAKDOWN.value) or {}
        open_count = _optional_nonnegative_int(status.get("open_count"))
        closed_count = _optional_nonnegative_int(status.get("closed_count"))
        if open_count is not None and closed_count is not None:
            lines.append(
                f"For the matching {family_label} 311 requests in {place} for {period}, "
                f"the recorded statuses include {open_count} open or in-progress requests and {closed_count} closed requests. "
                "A closed request does not verify that a physical problem was repaired, and an open request does not show active work."
            )
        else:
            lines.append(
                f"I found matching {family_label} 311 requests in {place} for {period}, "
                "but the available status breakdown is incomplete. A closed request does not verify that a physical problem was repaired."
            )
        if bundle.limitations:
            lines.append(bundle.limitations[0])
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_ENRICHED
    count_data = bundle.facts.get(ToolName.ISSUE_COUNT.value) or {}
    count = _int_value(count_data.get("count"))
    if count_data.get("street_coverage_unavailable"):
        lines.append(
            f"I cannot verify a street-specific count for {street_label} from the available {family_label} 311 records for {period}, because those matching records do not include street fields."
        )
        if bundle.limitations:
            lines.append(bundle.limitations[0])
        lines.append(f"Source: {SOURCE_LABEL}.")
        return "\n\n".join(lines), AnswerMode.DETERMINISTIC_FALLBACK
    if bundle.evidence_status is EvidenceStatus.NO_MATCHING_RECORDS:
        lines.append(f"I found no matching {family_label} 311 requests in {place} for {period}.")
    else:
        lines.append(f"I found {count} {family_label} 311 requests in {place} for {period}.")
    status = bundle.facts.get(ToolName.STATUS_BREAKDOWN.value) or {}
    if status:
        open_count = _int_value(status.get("open_count"))
        closed_count = _int_value(status.get("closed_count"))
        lines.append(
            f"The recorded statuses include {open_count} open or in-progress requests and {closed_count} closed requests. "
            "A closed request does not verify that a physical problem was repaired, and an open request does not show active work."
        )
    streets = (bundle.facts.get(ToolName.TOP_STREETS.value) or {}).get("streets") or []
    rendered_streets = _format_streets(streets)
    if rendered_streets:
        lines.append(f"The streets with the most matching reports were {rendered_streets}.")
    examples = (bundle.facts.get(ToolName.RECENT_EXAMPLES.value) or {}).get("examples") or []
    if examples and plan.intent == "street_status":
        rendered = "; ".join(
            " ".join(part for part in (item.get("opened"), item.get("street"), item.get("status")) if part)
            for item in examples[:3]
        )
        if rendered:
            lines.append(f"Recent matching request records include: {rendered}.")
    if bundle.limitations:
        lines.append(bundle.limitations[0])
    lines.append(f"Source: {SOURCE_LABEL}.")
    mode = AnswerMode.DETERMINISTIC_DIRECT if not status and not streets else AnswerMode.DETERMINISTIC_ENRICHED
    return "\n\n".join(lines), mode


def _synthesis_prompt(question: str, bundle: EvidenceBundle) -> str:
    return (
        "Answer a Dorchester resident using only this structured official 311 evidence. "
        "Answer the question first. Preserve counts, streets, geography, and periods exactly. "
        "Do not mention tools, SQL, RAG, tables, or internal identifiers. Do not infer repair from closed status or active work from open status. "
        "Do not add emergency guidance. If data is stale or partial, state the supplied limitation naturally. "
        "Resident question: " + question[:600] + "\nEvidence: " + json.dumps(bundle.bounded_for_model(), ensure_ascii=False, separators=(",", ":"), default=str)
    )


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


def validate_claims(answer: str, bundle: EvidenceBundle) -> Tuple[bool, Tuple[str, ...]]:
    text = str(answer or "").strip()
    issues: List[str] = []
    if not text:
        issues.append("empty_answer")
    if internal_terms(text):
        issues.append("internal_term")
    if re.search(r"\bcall 911\b", text, re.IGNORECASE):
        issues.append("unsupported_emergency_guidance")
    if re.search(r"\b(?:closed|closure)\b.{0,40}\b(?:repaired|fixed|resolved)\b", text, re.IGNORECASE):
        issues.append("closed_is_repaired")
    if re.search(r"\bopen\b.{0,40}\b(?:actively|currently)\s+(?:being )?worked", text, re.IGNORECASE):
        issues.append("open_is_active_work")
    if bundle.evidence_status in {EvidenceStatus.STALE_BUT_USABLE, EvidenceStatus.PARTIALLY_COVERED} and re.search(r"\b(?:right now|currently)\b", text, re.IGNORECASE):
        issues.append("stale_claimed_current")
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


def _context_from_bundle(bundle: EvidenceBundle, plan: StrictToolPlan) -> Optional[Dict[str, Any]]:
    if not plan.issue_family or plan.needs_clarification:
        return None
    try:
        context = Active311FactContext(
            issue_family=plan.issue_family,
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
) -> Civic311Result:
    """Run the bounded 311 path once and return no resident-internal diagnostics."""
    started = time.perf_counter()
    current_now = now or datetime.now().astimezone()
    deadline = GlobalDeadline.from_started_at(request_started_at or started)
    plan_started = time.perf_counter()
    plan = plan_311_question(
        message,
        active_context=active_context,
        state_location=state_location,
        now=current_now.date(),
        planner_llm=planner_llm,
        deadline=deadline,
    )
    planner_ms = int((time.perf_counter() - plan_started) * 1000)
    if plan.needs_clarification:
        evidence = EvidenceBundle(
            question_scope={"domain": "311", "intent": plan.intent},
            issue_family=plan.issue_family,
            geography=plan.geography,
            requested_period=plan.period,
            executed_period=plan.period,
            freshness={}, coverage={}, evidence_status=EvidenceStatus.INSUFFICIENT_EVIDENCE,
        )
        telemetry = CivicTelemetry(
            planner_source=plan.planner_source,
            planner_ms=planner_ms,
            total_latency_ms=int((time.perf_counter() - started) * 1000),
            evidence_status=evidence.evidence_status,
            answer_mode=AnswerMode.CLARIFICATION,
        )
        return Civic311Result(
            answer=plan.clarification_message or "Which city-service issue are you asking about?",
            mode=AnswerMode.CLARIFICATION, sources=[], evidence=evidence, plan=plan,
            telemetry=telemetry, next_context=None, update_context=False,
        )
    requested = _period_from_plan(plan)
    fresh, executed, evidence_status, limitations = _status_and_period(
        plan, connection_factory=connection_factory, now=current_now, deadline=deadline,
    )
    family = IssueFamily(plan.issue_family) if plan.issue_family else None
    geography = _geography_from_plan(plan)
    results, failures, tool_ms, timeout_stage = _execute_tools(
        plan,
        connection_factory=connection_factory,
        family=family,
        geography=geography,
        period=executed,
        freshness=fresh,
        deadline=deadline,
    )
    bundle = _bundle_from_execution(
        message=message, plan=plan, freshness=fresh, requested=requested, executed=executed,
        initial_status=evidence_status, limitations=limitations, results=results, failures=failures,
    )
    answer, mode = deterministic_answer(bundle, plan)
    synthesis_ms = 0
    llm_calls = 1 if plan.planner_source is PlannerSource.LLM else 0
    fallback_used = False
    complex_request = plan.intent in {"period_comparison", "street_status"} or len(plan.tools) >= 4
    if (
        synthesis_llm is not None
        and complex_request
        and bundle.evidence_status not in {EvidenceStatus.DEPENDENCY_UNAVAILABLE, EvidenceStatus.INSUFFICIENT_EVIDENCE}
        and deadline.remaining(reserve_for_persistence=True) >= 2.0
    ):
        synthesis_started = time.perf_counter()
        try:
            candidate = synthesis_llm(_synthesis_prompt(message, bundle), min(6.0, deadline.remaining(reserve_for_persistence=True)))
            llm_calls += 1
            valid, _issues = validate_claims(candidate, bundle)
            if valid:
                answer = candidate.strip()
                mode = AnswerMode.EVIDENCE_BOUNDED_SYNTHESIS
            else:
                fallback_used = True
        except Exception:
            fallback_used = True
            timeout_stage = timeout_stage or "synthesis"
        synthesis_ms = int((time.perf_counter() - synthesis_started) * 1000)
    validation_started = time.perf_counter()
    valid, _issues = validate_claims(answer, bundle)
    if not valid:
        answer, mode = deterministic_answer(bundle, plan)
        mode = AnswerMode.DETERMINISTIC_FALLBACK
        fallback_used = True
    validation_ms = int((time.perf_counter() - validation_started) * 1000)
    source = _source_record(fresh, executed)
    sources = [{key: value for key, value in source.items() if key != "source_key"}]
    next_context = _context_from_bundle(bundle, plan)
    telemetry = CivicTelemetry(
        planner_source=plan.planner_source,
        planner_ms=planner_ms,
        tool_execution_ms=tool_ms,
        tool_count=len(plan.tools),
        synthesis_ms=synthesis_ms,
        validation_ms=validation_ms,
        total_latency_ms=int((time.perf_counter() - started) * 1000),
        llm_call_count=llm_calls,
        embedding_call_count=0,
        evidence_status=bundle.evidence_status,
        answer_mode=mode,
        fallback_used=fallback_used,
        timeout_stage=timeout_stage,
    )
    return Civic311Result(
        answer=answer,
        mode=mode,
        sources=sources,
        evidence=bundle,
        plan=plan,
        telemetry=telemetry,
        next_context=next_context,
        update_context=next_context is not None,
    )
