"""Compile high-level semantic actions into strict registered civic requests.

The compiler sees validated semantic fields, not the resident message. It derives
evidence requirements, source-status checks, argument shapes, and non-persistent
state effects without executing tools or using resident-phrase recognizers.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from civic_tool_catalog import (
    CivicDomain,
    RegisteredToolRequest,
    SemanticPeriod,
    SourceTypeFilter,
    ToolContractError,
    ToolId,
)
from semantic_planner import (
    GeographyMode,
    PlannerAction,
    SemanticDomain,
    SemanticOperation,
    SemanticPlan,
    SemanticTask,
    SemanticTimeKind,
    SourceScope,
)


COMPILER_VERSION = "semantic-compiler-v2"


class SemanticCompilationError(ValueError):
    """Raised when a valid semantic action cannot map to a safe request."""


class DerivedStateEffect(str, Enum):
    PRESERVE = "PRESERVE"
    REFINE = "REFINE"
    REPLACE_TOPIC = "REPLACE_TOPIC"


@dataclass(frozen=True)
class CompiledSemanticPlan:
    compiler_version: str
    tool_requests: Tuple[RegisteredToolRequest, ...]
    clarification_reason: Optional[str]
    derived_state_effect: DerivedStateEffect
    requires_location_resolution: bool = False

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "compiler_version": self.compiler_version,
            "tool_requests": [item.to_dict() for item in self.tool_requests],
            "clarification_reason": self.clarification_reason,
            "derived_state_effect": self.derived_state_effect.value,
            "requires_location_resolution": self.requires_location_resolution,
        }


def compile_semantic_plan(
    plan: SemanticPlan,
    *,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Iterable[str] = (),
) -> CompiledSemanticPlan:
    """Compile a validated semantic action without execution or state mutation."""
    state_effect = _derive_state_effect(plan, trusted_context)
    if plan.action is PlannerAction.RESPOND_DIRECTLY:
        return CompiledSemanticPlan(COMPILER_VERSION, (), None, state_effect)
    if plan.action is PlannerAction.ASK_CLARIFICATION:
        return CompiledSemanticPlan(
            COMPILER_VERSION,
            (),
            plan.clarification_question or "More information is required.",
            state_effect,
        )

    tokens = tuple(trusted_reference_tokens)
    pending: List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]] = []
    status_sources: List[str] = []
    requires_resolution = False
    for task in plan.tasks:
        try:
            task_requests, task_sources, task_resolution = _compile_task(
                task,
                trusted_context=trusted_context,
                trusted_reference_tokens=tokens,
            )
        except SemanticCompilationError as exc:
            return CompiledSemanticPlan(COMPILER_VERSION, (), str(exc), state_effect)
        pending.extend(task_requests)
        status_sources.extend(task_sources)
        requires_resolution = requires_resolution or task_resolution

    if status_sources:
        unique_sources = list(dict.fromkeys(status_sources))
        pending.append((
            ToolId.GET_SOURCE_STATUS,
            {"operation": "STATUS", "source_ids": unique_sources},
            tuple(_source_id_domain(item) for item in unique_sources),
        ))
    if not pending:
        return CompiledSemanticPlan(
            COMPILER_VERSION,
            (),
            "The semantic action did not identify retrievable evidence.",
            state_effect,
        )
    if len(pending) > 4:
        return CompiledSemanticPlan(
            COMPILER_VERSION,
            (),
            "The request needs more than four independent evidence operations.",
            state_effect,
        )

    compiled: List[RegisteredToolRequest] = []
    for index, (tool_id, arguments, domains) in enumerate(pending, start=1):
        try:
            compiled.append(RegisteredToolRequest.from_mapping(
                {
                    "request_id": f"semantic_{index}",
                    "tool_id": tool_id.value,
                    "arguments": arguments,
                },
                selected_domains=domains,
                trusted_reference_tokens=tokens,
            ))
        except ToolContractError as exc:
            raise SemanticCompilationError(
                f"Compiler produced an invalid registered request for {tool_id.value}."
            ) from exc
    return CompiledSemanticPlan(
        COMPILER_VERSION,
        tuple(compiled),
        None,
        state_effect,
        requires_location_resolution=requires_resolution,
    )


def _compile_task(
    task: SemanticTask,
    *,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Tuple[str, ...],
) -> Tuple[
    List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]],
    List[str],
    bool,
]:
    if task.domain is SemanticDomain.LOCATION:
        return ([
            (
                ToolId.RESOLVE_LOCATION,
                {"operation": "RESOLVE", "query": task.geography.value, "scope": None},
                (CivicDomain.LOCATION,),
            )
        ], [], False)
    if task.operation is SemanticOperation.STATUS \
            and task.geography.mode is GeographyMode.NONE \
            and task.time.kind is SemanticTimeKind.NONE:
        return [], _status_sources(task), False
    if task.geography.mode is GeographyMode.MISSING:
        raise SemanticCompilationError("A location is required before evidence can be gathered.")
    if task.time.kind is SemanticTimeKind.MISSING:
        raise SemanticCompilationError("A time period is required before evidence can be gathered.")
    if task.geography.mode is GeographyMode.EXPLICIT:
        return ([
            (
                ToolId.RESOLVE_LOCATION,
                {"operation": "RESOLVE", "query": task.geography.value, "scope": None},
                (CivicDomain.LOCATION,),
            )
        ], [], True)

    location_id = _location_id(task, trusted_context, trusted_reference_tokens)
    time_scope = _time_scope(task, trusted_reference_tokens)
    status_sources = _status_sources(task)
    if task.domain is SemanticDomain.EVENTS:
        return _compile_events(task, trusted_context, location_id, time_scope), status_sources, False
    if task.domain is SemanticDomain.SERVICE_REQUESTS:
        return _compile_service_requests(task, location_id, time_scope), status_sources, False
    if task.domain is SemanticDomain.CRIME:
        return _compile_crime(task, location_id, time_scope), status_sources, False
    if task.domain is SemanticDomain.DOCUMENTS:
        return _compile_documents(task, location_id, time_scope), status_sources, False
    raise SemanticCompilationError("The semantic domain has no registered compiler mapping.")


def _compile_events(
    task: SemanticTask,
    trusted_context: Mapping[str, Any],
    location_id: Optional[str],
    time_scope: Optional[Dict[str, Any]],
) -> List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]]:
    active_entity = trusted_context.get("active_entity")
    if task.operation is SemanticOperation.FIND \
            and task.geography.mode is GeographyMode.NONE \
            and task.time.kind is SemanticTimeKind.NONE \
            and isinstance(active_entity, Mapping) \
            and isinstance(active_entity.get("token"), str):
        return [(
            ToolId.QUERY_EVENTS,
            {"operation": "DETAILS", "entity_token": active_entity["token"]},
            (CivicDomain.EVENTS,),
        )]
    if location_id is None:
        raise SemanticCompilationError("An event search requires a trusted location.")
    if time_scope is None:
        raise SemanticCompilationError("An event search requires a time period.")
    return [(
        ToolId.QUERY_EVENTS,
        {
            "operation": "LIST",
            "location_id": location_id,
            "time_scope": time_scope,
            "limit": 10,
        },
        (CivicDomain.EVENTS,),
    )]


def _compile_service_requests(
    task: SemanticTask,
    location_id: Optional[str],
    time_scope: Optional[Dict[str, Any]],
) -> List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]]:
    if location_id is None:
        raise SemanticCompilationError("Service-request evidence requires a trusted location.")
    if time_scope is None:
        raise SemanticCompilationError("Service-request evidence requires a time period.")
    issue_id = _subject_id(task.subject)
    if task.operation in {
        SemanticOperation.FIND, SemanticOperation.CHECK_EXISTENCE,
        SemanticOperation.SUMMARIZE, SemanticOperation.EXPLAIN,
    }:
        return [(
            ToolId.SEARCH_311_RECORDS,
            {
                "operation": "SEARCH",
                "issue_family_id": issue_id,
                "location_id": location_id,
                "time_scope": time_scope,
                "limit": 8,
            },
            (CivicDomain.BOSTON_311,),
        )]
    operation = {
        SemanticOperation.COUNT: "COUNT",
        SemanticOperation.GROUP: "GROUP",
        SemanticOperation.COMPARE: "COMPARE",
        SemanticOperation.STATUS: "STATUS",
    }.get(task.operation)
    if operation is None:
        raise SemanticCompilationError("Service-request operation is unsupported.")
    arguments: Dict[str, Any] = {
        "operation": operation,
        "issue_scope": "SPECIFIC" if issue_id else "ALL",
        "issue_family_id": issue_id,
        "location_id": location_id,
        "time_scope": time_scope,
        "group_by": "CATEGORY" if operation == "GROUP" else None,
        "limit": 10,
    }
    if operation == "COMPARE":
        arguments["comparison_time_scope"] = _comparison_scope(time_scope)
    return [(ToolId.AGGREGATE_311, arguments, (CivicDomain.BOSTON_311,))]


def _compile_crime(
    task: SemanticTask,
    location_id: Optional[str],
    time_scope: Optional[Dict[str, Any]],
) -> List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]]:
    if location_id is None:
        raise SemanticCompilationError("Crime evidence requires a trusted location.")
    if time_scope is None:
        raise SemanticCompilationError("Crime evidence requires a time period.")
    operation = {
        SemanticOperation.FIND: "COUNT",
        SemanticOperation.CHECK_EXISTENCE: "COUNT",
        SemanticOperation.COUNT: "COUNT",
        SemanticOperation.GROUP: "GROUP",
        SemanticOperation.COMPARE: "COMPARE",
        SemanticOperation.SUMMARIZE: "GROUP",
        SemanticOperation.EXPLAIN: "GROUP",
    }.get(task.operation)
    if operation is None:
        raise SemanticCompilationError("Crime operation is unsupported.")
    arguments: Dict[str, Any] = {
        "operation": operation,
        "offense_category_id": _subject_id(task.subject),
        "location_id": location_id,
        "time_scope": time_scope,
        "group_by": "OFFENSE" if operation == "GROUP" else None,
        "limit": 10,
    }
    if operation == "COMPARE":
        arguments["comparison_time_scope"] = _comparison_scope(time_scope)
    return [(ToolId.AGGREGATE_CRIME, arguments, (CivicDomain.CRIME_AND_SAFETY,))]


def _compile_documents(
    task: SemanticTask,
    location_id: Optional[str],
    time_scope: Optional[Dict[str, Any]],
) -> List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]]:
    corpora, domains = _document_corpora(task.source_scope)
    source_types = []
    if task.source_scope in _DOCUMENT_SOURCE_TYPES:
        source_types = [_DOCUMENT_SOURCE_TYPES[task.source_scope].value]
    return [(
        ToolId.SEARCH_DOCUMENTS,
        {
            "operation": "SEARCH",
            "query": task.subject or "Dorchester civic information",
            "corpora": corpora,
            "location_id": location_id,
            "time_scope": time_scope,
            "source_types": source_types,
            "limit": 8,
        },
        domains,
    )]


def _location_id(
    task: SemanticTask,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Sequence[str],
) -> Optional[str]:
    if task.geography.mode is GeographyMode.NONE:
        return None
    if task.geography.mode is GeographyMode.TRUSTED_REFERENCE:
        token = task.geography.value
        references = trusted_context.get("location_references")
        if token in set(trusted_reference_tokens) and isinstance(references, Mapping):
            value = references.get(token)
            if isinstance(value, str):
                return value
        raise SemanticCompilationError("The trusted location reference cannot be resolved.")
    geography = trusted_context.get("default_geography")
    if isinstance(geography, Mapping) and isinstance(geography.get("location_id"), str):
        return geography["location_id"]
    return None


def _time_scope(
    task: SemanticTask,
    trusted_reference_tokens: Sequence[str],
) -> Optional[Dict[str, Any]]:
    kind = task.time.kind
    if kind is SemanticTimeKind.NONE:
        return None
    if kind is SemanticTimeKind.LAST_N_DAYS:
        return {"period": SemanticPeriod.LAST_N_DAYS.value, "n_days": int(task.time.value or "0")}
    if kind is SemanticTimeKind.TRUSTED_REFERENCE:
        if task.time.value not in set(trusted_reference_tokens):
            raise SemanticCompilationError("The trusted time reference is unavailable.")
        return {
            "period": SemanticPeriod.RETAIN_TRUSTED_PERIOD.value,
            "trusted_reference_token": task.time.value,
        }
    if kind is SemanticTimeKind.EXPLICIT_DATE_RANGE:
        period = _semantic_range_period(task.time.value or "")
        if period is None:
            raise SemanticCompilationError("The semantic time range needs deterministic resolution.")
        return {"period": period.value}
    period = {
        SemanticTimeKind.CURRENT_WEEK: SemanticPeriod.CURRENT_WEEK,
        SemanticTimeKind.NEXT_CALENDAR_WEEK: SemanticPeriod.NEXT_CALENDAR_WEEK,
        SemanticTimeKind.PREVIOUS_CALENDAR_MONTH: SemanticPeriod.PREVIOUS_CALENDAR_MONTH,
    }.get(kind)
    if period is None:
        raise SemanticCompilationError("The semantic time period is unsupported.")
    return {"period": period.value}


def _semantic_range_period(value: str) -> Optional[SemanticPeriod]:
    normalized = " ".join(value.casefold().replace("_", " ").split())
    return {
        "today": SemanticPeriod.CURRENT_DAY,
        "this weekend": SemanticPeriod.CURRENT_WEEKEND,
        "next weekend": SemanticPeriod.NEXT_WEEKEND,
        "previous calendar week": SemanticPeriod.PREVIOUS_CALENDAR_WEEK,
        "current calendar month": SemanticPeriod.CURRENT_CALENDAR_MONTH,
    }.get(normalized)


def _comparison_scope(current: Mapping[str, Any]) -> Dict[str, Any]:
    previous = {
        SemanticPeriod.CURRENT_WEEK.value: SemanticPeriod.PREVIOUS_CALENDAR_WEEK.value,
        SemanticPeriod.CURRENT_CALENDAR_MONTH.value: SemanticPeriod.PREVIOUS_CALENDAR_MONTH.value,
    }.get(current.get("period"))
    if previous is None:
        raise SemanticCompilationError("The requested comparison period is underspecified.")
    return {"period": previous}


def _subject_id(subject: Optional[str]) -> Optional[str]:
    if not subject:
        return None
    normalized = unicodedata.normalize("NFKD", subject).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")
    if not value:
        raise SemanticCompilationError("The subject cannot be canonicalized safely.")
    return value[:80]


def _document_corpora(scope: SourceScope) -> Tuple[List[str], Tuple[CivicDomain, ...]]:
    mapping = {
        SourceScope.LAW_OR_REGULATION: ("POLICY", CivicDomain.POLICY),
        SourceScope.PLANNING_POLICY: ("POLICY", CivicDomain.POLICY),
        SourceScope.MEETING_TRANSCRIPT: ("TRANSCRIPT", CivicDomain.MEETING_TRANSCRIPTS),
        SourceScope.NEWS: ("NEWS", CivicDomain.NEWS),
        SourceScope.COMMUNITY: ("COMMUNITY", CivicDomain.COMMUNITY_DOCUMENTS),
    }
    if scope in mapping:
        corpus, domain = mapping[scope]
        return [corpus], (domain,)
    return (
        ["POLICY", "TRANSCRIPT", "NEWS", "COMMUNITY"],
        (
            CivicDomain.POLICY,
            CivicDomain.MEETING_TRANSCRIPTS,
            CivicDomain.NEWS,
            CivicDomain.COMMUNITY_DOCUMENTS,
        ),
    )


_DOCUMENT_SOURCE_TYPES = {
    SourceScope.LAW_OR_REGULATION: SourceTypeFilter.BINDING_LAW,
    SourceScope.PLANNING_POLICY: SourceTypeFilter.PLANNING_POLICY,
    SourceScope.MEETING_TRANSCRIPT: SourceTypeFilter.MEETING_DISCUSSION,
    SourceScope.NEWS: SourceTypeFilter.JOURNALISM,
    SourceScope.COMMUNITY: SourceTypeFilter.COMMUNITY_OPINION,
}


def _status_sources(task: SemanticTask) -> List[str]:
    if task.domain is SemanticDomain.EVENTS:
        return ["events"]
    if task.domain is SemanticDomain.SERVICE_REQUESTS:
        return ["boston_311"]
    if task.domain is SemanticDomain.CRIME:
        return ["bpd_crime"]
    if task.domain is SemanticDomain.DOCUMENTS:
        corpora, _ = _document_corpora(task.source_scope)
        return [{
            "POLICY": "policy_documents",
            "TRANSCRIPT": "meeting_transcripts",
            "NEWS": "news",
            "COMMUNITY": "community_documents",
        }[item] for item in corpora]
    return []


def _source_id_domain(source_id: str) -> CivicDomain:
    return {
        "events": CivicDomain.EVENTS,
        "boston_311": CivicDomain.BOSTON_311,
        "bpd_crime": CivicDomain.CRIME_AND_SAFETY,
        "policy_documents": CivicDomain.POLICY,
        "meeting_transcripts": CivicDomain.MEETING_TRANSCRIPTS,
        "news": CivicDomain.NEWS,
        "community_documents": CivicDomain.COMMUNITY_DOCUMENTS,
    }[source_id]


def _derive_state_effect(
    plan: SemanticPlan,
    trusted_context: Mapping[str, Any],
) -> DerivedStateEffect:
    if plan.action is not PlannerAction.GATHER_EVIDENCE:
        return DerivedStateEffect.PRESERVE
    if any(
        task.geography.mode is GeographyMode.TRUSTED_REFERENCE
        or task.time.kind is SemanticTimeKind.TRUSTED_REFERENCE
        for task in plan.tasks
    ):
        return DerivedStateEffect.REFINE
    active_topic = trusted_context.get("active_topic")
    topic_domain = {
        "EVENTS": SemanticDomain.EVENTS,
        "BOSTON_311": SemanticDomain.SERVICE_REQUESTS,
        "SERVICE_REQUESTS": SemanticDomain.SERVICE_REQUESTS,
        "CRIME_AND_SAFETY": SemanticDomain.CRIME,
        "CRIME": SemanticDomain.CRIME,
    }.get(active_topic)
    domains = {task.domain for task in plan.tasks}
    if topic_domain is not None and domains == {topic_domain}:
        return DerivedStateEffect.REFINE
    return DerivedStateEffect.REPLACE_TOPIC


__all__ = [
    "COMPILER_VERSION",
    "CompiledSemanticPlan",
    "DerivedStateEffect",
    "SemanticCompilationError",
    "compile_semantic_plan",
]
