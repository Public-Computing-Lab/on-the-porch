"""Deterministically compile semantic tasks into the strict civic tool catalog.

The compiler contains no resident-phrase recognizers and executes nothing.  It
maps validated domain, goal, evidence, geography, time, and source concepts to
registered requests, then reuses the existing argument validators.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
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
    EvidenceNeed,
    GeographyMode,
    PublicSourceType,
    SemanticDomain,
    SemanticGoal,
    SemanticPlan,
    SemanticStrategy,
    SemanticTask,
    SemanticTimeKind,
    SubjectKind,
)


COMPILER_VERSION = "semantic-compiler-v1"


class SemanticCompilationError(ValueError):
    """Raised when a valid semantic plan cannot map to a safe registered request."""


@dataclass(frozen=True)
class CompiledSemanticPlan:
    compiler_version: str
    tool_requests: Tuple[RegisteredToolRequest, ...]
    clarification_reason: Optional[str]
    requires_location_resolution: bool = False

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "compiler_version": self.compiler_version,
            "tool_requests": [item.to_dict() for item in self.tool_requests],
            "clarification_reason": self.clarification_reason,
            "requires_location_resolution": self.requires_location_resolution,
        }


def compile_semantic_plan(
    plan: SemanticPlan,
    *,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Iterable[str] = (),
) -> CompiledSemanticPlan:
    """Compile a semantic plan without executing a tool or mutating trusted state."""
    if plan.strategy is SemanticStrategy.DIRECT_RESPONSE:
        return CompiledSemanticPlan(COMPILER_VERSION, (), None)
    if plan.strategy is SemanticStrategy.ASK_CLARIFICATION:
        return CompiledSemanticPlan(
            COMPILER_VERSION, (), plan.clarification_question or "More information is required.",
        )

    tokens = tuple(trusted_reference_tokens)
    pending: List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]] = []
    source_status: List[str] = []
    requires_resolution = False
    for task in plan.tasks:
        try:
            task_requests, task_status, task_resolution = _compile_task(
                task,
                plan=plan,
                trusted_context=trusted_context,
                trusted_reference_tokens=tokens,
            )
        except SemanticCompilationError as exc:
            return CompiledSemanticPlan(COMPILER_VERSION, (), str(exc))
        pending.extend(task_requests)
        source_status.extend(task_status)
        requires_resolution = requires_resolution or task_resolution

    if source_status:
        unique_sources = list(dict.fromkeys(source_status))
        domains = tuple(_source_id_domain(item) for item in unique_sources)
        pending.append((
            ToolId.GET_SOURCE_STATUS,
            {"operation": "STATUS", "source_ids": unique_sources},
            domains,
        ))
    if not pending:
        return CompiledSemanticPlan(
            COMPILER_VERSION, (), "The semantic plan did not identify retrievable evidence.",
        )
    if len(pending) > 4:
        return CompiledSemanticPlan(
            COMPILER_VERSION, (), "The request needs more than four independent evidence operations.",
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
        requires_location_resolution=requires_resolution,
    )


def _compile_task(
    task: SemanticTask,
    *,
    plan: SemanticPlan,
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
                {
                    "operation": "RESOLVE",
                    "query": task.geography.value,
                    "scope": None,
                },
                (CivicDomain.LOCATION,),
            )
        ], [], False)

    status_sources = _status_sources(task) if EvidenceNeed.SOURCE_STATUS in task.evidence_needs else []
    non_status_needs = tuple(
        item for item in task.evidence_needs if item is not EvidenceNeed.SOURCE_STATUS
    )
    if not non_status_needs:
        return [], status_sources, False

    if task.geography.mode is GeographyMode.MISSING:
        raise SemanticCompilationError("A location is required before evidence can be gathered.")
    if task.geography.mode is GeographyMode.EXPLICIT:
        return ([
            (
                ToolId.RESOLVE_LOCATION,
                {"operation": "RESOLVE", "query": task.geography.value, "scope": None},
                (CivicDomain.LOCATION,),
            )
        ], status_sources, True)

    location_id = _location_id(task, trusted_context, trusted_reference_tokens)
    time_scope = _time_scope(task, trusted_reference_tokens)
    if task.domain is SemanticDomain.EVENTS:
        return _compile_events(task, plan, location_id, time_scope), status_sources, False
    if task.domain is SemanticDomain.SERVICE_REQUESTS:
        return _compile_service_requests(task, location_id, time_scope), status_sources, False
    if task.domain is SemanticDomain.CRIME:
        return _compile_crime(task, location_id, time_scope), status_sources, False
    if task.domain is SemanticDomain.DOCUMENTS:
        return _compile_documents(task, plan, location_id, time_scope), status_sources, False
    raise SemanticCompilationError("The semantic domain has no registered compiler mapping.")


def _compile_events(
    task: SemanticTask,
    plan: SemanticPlan,
    location_id: Optional[str],
    time_scope: Optional[Dict[str, Any]],
) -> List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]]:
    if task.subject.kind is SubjectKind.EVENT and plan.trusted_summary_reference:
        return [(
            ToolId.QUERY_EVENTS,
            {"operation": "DETAILS", "entity_token": plan.trusted_summary_reference},
            (CivicDomain.EVENTS,),
        )]
    if location_id is None:
        raise SemanticCompilationError("An event listing requires a trusted location.")
    if time_scope is None:
        raise SemanticCompilationError("An event listing requires a time period.")
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
    issue_id = _subject_id(task) if task.subject.kind is SubjectKind.ISSUE else None
    if EvidenceNeed.RECORDS in task.evidence_needs and not any(
        item in task.evidence_needs for item in (
            EvidenceNeed.COUNT,
            EvidenceNeed.STATUS_BREAKDOWN,
            EvidenceNeed.CATEGORY_DISTRIBUTION,
            EvidenceNeed.LOCATION_DISTRIBUTION,
            EvidenceNeed.PERIOD_COMPARISON,
        )
    ):
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
    operation, group_by = _aggregate_operation(task, crime=False)
    arguments: Dict[str, Any] = {
        "operation": operation,
        "issue_scope": "SPECIFIC" if issue_id else "ALL",
        "issue_family_id": issue_id,
        "location_id": location_id,
        "time_scope": time_scope,
        "group_by": group_by,
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
    operation, group_by = _aggregate_operation(task, crime=True)
    arguments: Dict[str, Any] = {
        "operation": operation,
        "offense_category_id": _subject_id(task) if task.subject.kind is SubjectKind.ISSUE else None,
        "location_id": location_id,
        "time_scope": time_scope,
        "group_by": group_by,
        "limit": 10,
    }
    if operation == "COMPARE":
        arguments["comparison_time_scope"] = _comparison_scope(time_scope)
    return [(ToolId.AGGREGATE_CRIME, arguments, (CivicDomain.CRIME_AND_SAFETY,))]


def _compile_documents(
    task: SemanticTask,
    plan: SemanticPlan,
    location_id: Optional[str],
    time_scope: Optional[Dict[str, Any]],
) -> List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]]:
    corpora, domains = _document_corpora(task)
    source_types = [
        _DOCUMENT_SOURCE_TYPES[item].value
        for item in task.source_types
        if item in _DOCUMENT_SOURCE_TYPES
    ]
    query = task.subject.text or plan.user_goal
    return [(
        ToolId.SEARCH_DOCUMENTS,
        {
            "operation": "SEARCH",
            "query": query,
            "corpora": corpora,
            "location_id": location_id,
            "time_scope": time_scope,
            "source_types": source_types,
            "limit": 8,
        },
        domains,
    )]


def _aggregate_operation(task: SemanticTask, *, crime: bool) -> Tuple[str, Optional[str]]:
    needs = set(task.evidence_needs)
    if task.goal is SemanticGoal.COMPARE or EvidenceNeed.PERIOD_COMPARISON in needs:
        return "COMPARE", None
    if EvidenceNeed.STATUS_BREAKDOWN in needs:
        if crime:
            raise SemanticCompilationError("Crime status grouping is not a registered aggregate.")
        return "GROUP", "STATUS"
    if EvidenceNeed.CATEGORY_DISTRIBUTION in needs or task.goal is SemanticGoal.GROUP:
        return "GROUP", "OFFENSE" if crime else "CATEGORY"
    if EvidenceNeed.LOCATION_DISTRIBUTION in needs:
        return "GROUP", "AREA" if crime else "STREET"
    if task.goal is SemanticGoal.STATUS and not crime:
        return "STATUS", None
    return "COUNT", None


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
    if kind is SemanticTimeKind.EXPLICIT_DATE_RANGE:
        start, end = (task.time.value or "").split("/", 1)
        return {
            "period": SemanticPeriod.EXPLICIT_DATE_RANGE.value,
            "start_date": start,
            "end_date_exclusive": end,
        }
    if kind is SemanticTimeKind.RETAIN_TRUSTED_PERIOD:
        if task.time.value not in set(trusted_reference_tokens):
            raise SemanticCompilationError("The trusted time reference is unavailable.")
        return {
            "period": SemanticPeriod.RETAIN_TRUSTED_PERIOD.value,
            "trusted_reference_token": task.time.value,
        }
    return {"period": SemanticPeriod(kind.value).value}


def _comparison_scope(current: Mapping[str, Any]) -> Dict[str, Any]:
    period = current.get("period")
    previous = {
        SemanticPeriod.CURRENT_WEEK.value: SemanticPeriod.PREVIOUS_CALENDAR_WEEK.value,
        SemanticPeriod.CURRENT_CALENDAR_MONTH.value: SemanticPeriod.PREVIOUS_CALENDAR_MONTH.value,
    }.get(period)
    if previous is None:
        raise SemanticCompilationError("The requested comparison period is underspecified.")
    return {"period": previous}


def _subject_id(task: SemanticTask) -> str:
    text = task.subject.text
    if not text:
        raise SemanticCompilationError("A specific subject requires a bounded public concept.")
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")
    if not value:
        raise SemanticCompilationError("The subject cannot be canonicalized safely.")
    return value[:80]


def _document_corpora(
    task: SemanticTask,
) -> Tuple[List[str], Tuple[CivicDomain, ...]]:
    corpus_map = {
        PublicSourceType.LAW_OR_REGULATION: ("POLICY", CivicDomain.POLICY),
        PublicSourceType.PLANNING_POLICY: ("POLICY", CivicDomain.POLICY),
        PublicSourceType.MEETING_TRANSCRIPT: ("TRANSCRIPT", CivicDomain.MEETING_TRANSCRIPTS),
        PublicSourceType.JOURNALISM: ("NEWS", CivicDomain.NEWS),
        PublicSourceType.COMMUNITY: ("COMMUNITY", CivicDomain.COMMUNITY_DOCUMENTS),
    }
    pairs = [corpus_map[item] for item in task.source_types if item in corpus_map]
    if not pairs:
        subject_defaults = {
            SubjectKind.POLICY: ("POLICY", CivicDomain.POLICY),
            SubjectKind.MEETING: ("TRANSCRIPT", CivicDomain.MEETING_TRANSCRIPTS),
            SubjectKind.NEWS: ("NEWS", CivicDomain.NEWS),
            SubjectKind.COMMUNITY_TOPIC: ("COMMUNITY", CivicDomain.COMMUNITY_DOCUMENTS),
        }
        pair = subject_defaults.get(task.subject.kind)
        if pair is None:
            raise SemanticCompilationError("Document evidence requires a public source category.")
        pairs = [pair]
    corpora = list(dict.fromkeys(pair[0] for pair in pairs))
    domains = tuple(dict.fromkeys(pair[1] for pair in pairs))
    return corpora, domains


_DOCUMENT_SOURCE_TYPES = {
    PublicSourceType.LAW_OR_REGULATION: SourceTypeFilter.BINDING_LAW,
    PublicSourceType.PLANNING_POLICY: SourceTypeFilter.PLANNING_POLICY,
    PublicSourceType.MEETING_TRANSCRIPT: SourceTypeFilter.MEETING_DISCUSSION,
    PublicSourceType.JOURNALISM: SourceTypeFilter.JOURNALISM,
    PublicSourceType.COMMUNITY: SourceTypeFilter.COMMUNITY_OPINION,
}


def _status_sources(task: SemanticTask) -> List[str]:
    if task.domain is SemanticDomain.EVENTS:
        return ["events"]
    if task.domain is SemanticDomain.SERVICE_REQUESTS:
        return ["boston_311"]
    if task.domain is SemanticDomain.CRIME:
        return ["bpd_crime"]
    if task.domain is SemanticDomain.DOCUMENTS:
        corpora, _ = _document_corpora(task)
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


__all__ = [
    "COMPILER_VERSION",
    "CompiledSemanticPlan",
    "SemanticCompilationError",
    "compile_semantic_plan",
]
