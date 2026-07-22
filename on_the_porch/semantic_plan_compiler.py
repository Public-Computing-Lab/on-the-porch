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
from civic_311 import canonical_issue_family_id
from conversation_dimensions import resolve_app_now, resolve_explicit_date_expression
from model_led_orchestrator import OrchestratorContractError
from semantic_planner import (
    GeographyMode,
    MissingInformation,
    PlannerAction,
    SemanticSafetyError,
    SemanticDomain,
    SemanticGoalClarity,
    SemanticGeography,
    SemanticOperation,
    SemanticPlan,
    SemanticTask,
    SemanticTime,
    SemanticTimeKind,
    SourceScope,
    validate_direct_response,
    validate_semantic_task,
)


COMPILER_VERSION = "semantic-compiler-v4"
CANONICALIZATION_VERSION = "semantic-canonicalization-v3"


class SemanticCompilationError(ValueError):
    """Raised when a valid semantic action cannot map to a safe request."""


class DerivedStateEffect(str, Enum):
    PRESERVE = "PRESERVE"
    REFINE = "REFINE"
    REPLACE_TOPIC = "REPLACE_TOPIC"


class GuardedDisposition(str, Enum):
    EXECUTABLE = "EXECUTABLE"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    INVALID_UNSAFE = "INVALID_UNSAFE"


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


@dataclass(frozen=True)
class GuardedSemanticPlan:
    disposition: GuardedDisposition
    compiled_plan: CompiledSemanticPlan
    resident_response: Optional[str]
    reason_code: str

    @property
    def tool_requests(self) -> Tuple[RegisteredToolRequest, ...]:
        return self.compiled_plan.tool_requests

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "compiled_plan": self.compiled_plan.to_safe_dict(),
            "resident_response": self.resident_response,
            "reason_code": self.reason_code,
        }


def guard_semantic_plan(
    plan: SemanticPlan,
    *,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Iterable[str] = (),
) -> GuardedSemanticPlan:
    """Fail closed before any semantic plan can reach a civic capability."""
    tokens = tuple(trusted_reference_tokens)
    plan = _normalize_semantic_plan(plan, trusted_context, tokens)
    state_effect = _derive_state_effect(plan, trusted_context)
    empty = CompiledSemanticPlan(COMPILER_VERSION, (), None, state_effect)

    if plan.action is PlannerAction.RESPOND_DIRECTLY:
        try:
            if plan.response_kind is None or plan.response_text is None:
                raise SemanticSafetyError("direct response is incomplete")
            validate_direct_response(
                plan.response_kind,
                plan.response_text,
                plan.trusted_summary_token,
                trusted_context,
                tokens,
            )
        except SemanticSafetyError:
            return GuardedSemanticPlan(
                GuardedDisposition.INVALID_UNSAFE,
                empty,
                "I need to verify that with current sources before I can answer.",
                "UNSUPPORTED_DIRECT_RESPONSE",
            )
        return GuardedSemanticPlan(
            GuardedDisposition.EXECUTABLE,
            empty,
            plan.response_text,
            "VALID_DIRECT_RESPONSE",
        )

    if plan.action is PlannerAction.ASK_CLARIFICATION:
        missing = plan.missing_information or MissingInformation.USER_MEANING
        return GuardedSemanticPlan(
            GuardedDisposition.NEEDS_CLARIFICATION,
            CompiledSemanticPlan(
                COMPILER_VERSION,
                (),
                plan.clarification_question,
                state_effect,
            ),
            plan.clarification_question or _clarification_for_missing(missing),
            f"MISSING_{missing.value}",
        )

    for task in plan.tasks:
        if task.goal_clarity is SemanticGoalClarity.DOMAIN_ONLY:
            return GuardedSemanticPlan(
                GuardedDisposition.NEEDS_CLARIFICATION,
                empty,
                _clarification_for_missing(MissingInformation.USER_MEANING),
                "MISSING_USER_MEANING",
            )
        if task.geography.mode is GeographyMode.TRUSTED_REFERENCE \
                and task.geography.value not in set(tokens):
            return GuardedSemanticPlan(
                GuardedDisposition.INVALID_UNSAFE,
                empty,
                "Which trusted location did you mean?",
                "UNTRUSTED_GEOGRAPHY_REFERENCE",
            )
        if task.time.kind is SemanticTimeKind.TRUSTED_REFERENCE \
                and task.time.value not in set(tokens):
            return GuardedSemanticPlan(
                GuardedDisposition.INVALID_UNSAFE,
                empty,
                "Which earlier time period did you mean?",
                "UNTRUSTED_TIME_REFERENCE",
            )
        try:
            validate_semantic_task(
                task.domain,
                task.operation,
                task.subject,
                task.geography,
                task.source_scope,
            )
        except OrchestratorContractError:
            return GuardedSemanticPlan(
                GuardedDisposition.NEEDS_CLARIFICATION,
                empty,
                _clarification_for_task_conflict(task),
                "CONFLICTING_SEMANTIC_FIELDS",
            )

    try:
        compiled = compile_semantic_plan(
            plan,
            trusted_context=trusted_context,
            trusted_reference_tokens=tokens,
        )
    except SemanticCompilationError:
        return GuardedSemanticPlan(
            GuardedDisposition.INVALID_UNSAFE,
            empty,
            "I could not safely prepare that lookup. Could you narrow the request?",
            "COMPILER_CONTRACT_FAILURE",
        )
    if compiled.clarification_reason or not compiled.tool_requests:
        response, reason = _clarification_for_compilation(plan, compiled.clarification_reason)
        return GuardedSemanticPlan(
            GuardedDisposition.NEEDS_CLARIFICATION,
            compiled,
            response,
            reason,
        )
    return GuardedSemanticPlan(
        GuardedDisposition.EXECUTABLE,
        compiled,
        None,
        "REGISTERED_TOOLS_READY",
    )


def compile_semantic_plan(
    plan: SemanticPlan,
    *,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Iterable[str] = (),
) -> CompiledSemanticPlan:
    """Compile a validated semantic action without execution or state mutation."""
    tokens = tuple(trusted_reference_tokens)
    plan = _normalize_semantic_plan(plan, trusted_context, tokens)
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

    pending: List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]] = []
    status_sources: List[str] = []
    requires_resolution = False
    for task in plan.tasks:
        try:
            validate_semantic_task(
                task.domain,
                task.operation,
                task.subject,
                task.geography,
                task.source_scope,
            )
            if task.geography.mode is GeographyMode.TRUSTED_REFERENCE \
                    and task.geography.value not in set(tokens):
                raise SemanticCompilationError("The trusted location reference is unavailable.")
            if task.time.kind is SemanticTimeKind.TRUSTED_REFERENCE \
                    and task.time.value not in set(tokens):
                raise SemanticCompilationError("The trusted time reference is unavailable.")
            task_requests, task_sources, task_resolution = _compile_task(
                task,
                trusted_context=trusted_context,
                trusted_reference_tokens=tokens,
            )
        except (OrchestratorContractError, SemanticCompilationError) as exc:
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


def _normalize_semantic_plan(
    plan: SemanticPlan,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Sequence[str],
) -> SemanticPlan:
    """Canonicalize fields that deterministic capabilities do not consume."""
    if plan.action is not PlannerAction.GATHER_EVIDENCE:
        return plan
    tasks = tuple(_normalize_task(task) for task in plan.tasks)
    if tasks != plan.tasks:
        plan = SemanticPlan(PlannerAction.GATHER_EVIDENCE, tasks=tasks)
    if len(plan.tasks) != 1:
        return plan
    active_entity = trusted_context.get("active_entity")
    if not isinstance(active_entity, Mapping):
        return plan
    token = active_entity.get("token")
    if not isinstance(token, str) or token not in set(trusted_reference_tokens):
        return plan
    task = plan.tasks[0]
    selected_for_turn = trusted_context.get("selected_entity_for_turn") == token
    # Pronoun place/detail asks about a selected event stay in EVENTS, not LOCATION.
    if (
        selected_for_turn
        and task.domain is SemanticDomain.LOCATION
        and task.operation is SemanticOperation.FIND
    ):
        task = SemanticTask(
            SemanticDomain.EVENTS,
            SemanticOperation.FIND,
            "LOCATION",
            SemanticGeography(GeographyMode.NONE, None),
            SemanticTime(SemanticTimeKind.NONE, None),
            SourceScope.ANY,
            SemanticGoalClarity.CLEAR,
        )
        plan = SemanticPlan(PlannerAction.GATHER_EVIDENCE, tasks=(task,))
    if not selected_for_turn:
        if task.geography.mode in {GeographyMode.EXPLICIT, GeographyMode.MISSING}:
            return plan
        if task.time.kind not in {SemanticTimeKind.NONE, SemanticTimeKind.TRUSTED_REFERENCE}:
            return plan
    reference_used = (
        task.geography.mode is GeographyMode.TRUSTED_REFERENCE
        or task.time.kind is SemanticTimeKind.TRUSTED_REFERENCE
    )
    selected_event_shape = (
        task.domain is SemanticDomain.EVENTS
        and task.operation in {
            SemanticOperation.FIND, SemanticOperation.SUMMARIZE, SemanticOperation.EXPLAIN,
        }
        and (selected_for_turn or task.subject is not None or reference_used)
    )
    selected_event_document_shape = (
        task.domain is SemanticDomain.DOCUMENTS
        and task.operation in {
            SemanticOperation.FIND, SemanticOperation.SUMMARIZE, SemanticOperation.EXPLAIN,
        }
        and reference_used
    )
    if not selected_event_shape and not selected_event_document_shape:
        return plan
    canonical = SemanticTask(
        SemanticDomain.EVENTS,
        SemanticOperation.FIND,
        (
            task.subject
            if _event_detail_field(task.subject) != "GENERAL"
            else None
        ),
        SemanticGeography(GeographyMode.NONE, None),
        SemanticTime(SemanticTimeKind.NONE, None),
        SourceScope.ANY,
        task.goal_clarity,
    )
    return SemanticPlan(PlannerAction.GATHER_EVIDENCE, tasks=(canonical,))


def _normalize_task(task: SemanticTask) -> SemanticTask:
    if (
        task.domain is SemanticDomain.SERVICE_REQUESTS
        and task.operation in {SemanticOperation.GROUP, SemanticOperation.STATUS}
        and _is_status_subject(task.subject)
    ):
        # Open/closed breakdowns keep geography/time; strip status words from subject.
        return SemanticTask(
            task.domain,
            SemanticOperation.STATUS,
            None,
            task.geography,
            task.time,
            task.source_scope,
            task.goal_clarity,
        )
    if (
        task.operation is SemanticOperation.STATUS
        and task.subject is None
        and task.time.kind is SemanticTimeKind.NONE
    ):
        # Null-subject STATUS with NONE time is source freshness/coverage.
        return SemanticTask(
            task.domain,
            task.operation,
            None,
            SemanticGeography(GeographyMode.NONE, None),
            SemanticTime(SemanticTimeKind.NONE, None),
            task.source_scope,
            task.goal_clarity,
        )
    if (
        task.operation is SemanticOperation.STATUS
        and task.subject is None
        and task.time.kind in {SemanticTimeKind.NONE, SemanticTimeKind.MISSING}
        and task.geography.mode in {GeographyMode.NONE, GeographyMode.MISSING}
    ):
        return SemanticTask(
            task.domain,
            task.operation,
            None,
            SemanticGeography(GeographyMode.NONE, None),
            SemanticTime(SemanticTimeKind.NONE, None),
            task.source_scope,
            task.goal_clarity,
        )
    return task


def _is_status_subject(subject: Optional[str]) -> bool:
    if not subject:
        return False
    words = set(_normalized_public_text(subject).split()).difference({
        "a", "and", "by", "case", "cases", "complaint", "complaints",
        "distribution", "divide", "divided", "division", "for", "group",
        "grouped", "in", "mix", "of", "or", "record", "records", "report",
        "reports", "request", "requests", "service", "the", "versus", "vs",
    })
    return bool(words) and words.issubset({
        "status", "statuses", "open", "opened", "closed", "closure", "pending",
    })


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
            and task.subject is None \
            and task.geography.mode is GeographyMode.NONE \
            and task.time.kind is SemanticTimeKind.NONE:
        return [], _status_sources(task), False
    if (
        task.operation is SemanticOperation.STATUS
        and task.subject is None
        and task.time.kind is SemanticTimeKind.MISSING
        and task.geography.mode is GeographyMode.DEFAULT
    ):
        # Null-subject STATUS with DEFAULT place and MISSING time: retain a trusted
        # service-request period for open/closed breakdowns; otherwise treat as
        # source freshness/coverage (period immaterial).
        retained = _time_scope(
            task,
            trusted_reference_tokens,
            trusted_context=trusted_context,
        )
        if retained is None:
            return [], _status_sources(task), False
    if task.geography.mode is GeographyMode.MISSING:
        # Scope-only refinements after a trusted civic result may omit geography.
        # Fresh turns still clarify when no compatible trusted topic is present.
        # Prior event-list follow-ups may also omit geography when the trusted
        # summary already anchors the listing.
        summary = trusted_context.get("previous_result_summary")
        summary_kind = summary.get("kind") if isinstance(summary, Mapping) else None
        topic_compatible = (
            trusted_context.get("active_topic") == task.domain.value
            or (
                task.domain is SemanticDomain.EVENTS
                and summary_kind == "EVENT_LIST"
            )
            or (
                task.domain is SemanticDomain.SERVICE_REQUESTS
                and summary_kind in {"SERVICE_REQUEST_SUMMARY", "CITY_SERVICES_SUMMARY"}
            )
        )
        if not topic_compatible or not _default_location_id(trusted_context):
            raise SemanticCompilationError(
                "A location is required before evidence can be gathered."
            )
        task = SemanticTask(
            task.domain,
            task.operation,
            task.subject,
            SemanticGeography(GeographyMode.DEFAULT, None),
            task.time,
            task.source_scope,
            task.goal_clarity,
        )
    if task.time.kind is SemanticTimeKind.MISSING:
        retained = _time_scope(
            task,
            trusted_reference_tokens,
            trusted_context=trusted_context,
        )
        if retained is None:
            raise SemanticCompilationError(
                "A time period is required before evidence can be gathered."
            )
        # Trusted period retained; treat as NONE for downstream named-period mapping.
        task = SemanticTask(
            task.domain,
            task.operation,
            task.subject,
            task.geography,
            SemanticTime(SemanticTimeKind.NONE, None),
            task.source_scope,
            task.goal_clarity,
        )
    if task.geography.mode is GeographyMode.EXPLICIT \
            and not _explicit_matches_default(task, trusted_context):
        return ([
            (
                ToolId.RESOLVE_LOCATION,
                {"operation": "RESOLVE", "query": task.geography.value, "scope": None},
                (CivicDomain.LOCATION,),
            )
        ], [], True)

    location_id = _location_id(task, trusted_context, trusted_reference_tokens)
    time_scope = _time_scope(
        task,
        trusted_reference_tokens,
        trusted_context=trusted_context,
    )
    if (
        task.domain is SemanticDomain.EVENTS
        and task.operation in {SemanticOperation.FIND, SemanticOperation.CHECK_EXISTENCE}
        and task.time.kind is SemanticTimeKind.NONE
        and time_scope is None
    ):
        time_scope = {"period": SemanticPeriod.UPCOMING_DEFAULT.value}
    if (
        task.domain is SemanticDomain.SERVICE_REQUESTS
        and task.operation is not SemanticOperation.STATUS
        and task.time.kind is SemanticTimeKind.NONE
        and time_scope is None
    ):
        # Unqualified service-request aggregates default to the prior calendar month.
        time_scope = {"period": SemanticPeriod.PREVIOUS_CALENDAR_MONTH.value}
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
            {
                "operation": "DETAILS",
                "entity_token": active_entity["token"],
                "detail_field": _event_detail_field(task.subject),
            },
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


def _event_detail_field(subject: Optional[str]) -> str:
    """Map a model-owned semantic aspect to one reviewed event-detail field."""
    normalized = _normalized_public_text(subject or "").replace("_", " ")
    return {
        "location": "LOCATION",
        "date time": "DATE_TIME",
        "datetime": "DATE_TIME",
        "cost": "COST",
        "registration contact": "REGISTRATION_CONTACT",
        "organizer": "ORGANIZER",
    }.get(normalized, "GENERAL")


def _compile_service_requests(
    task: SemanticTask,
    location_id: Optional[str],
    time_scope: Optional[Dict[str, Any]],
) -> List[Tuple[ToolId, Dict[str, Any], Tuple[CivicDomain, ...]]]:
    if location_id is None:
        raise SemanticCompilationError("Service-request evidence requires a trusted location.")
    if time_scope is None:
        raise SemanticCompilationError("Service-request evidence requires a time period.")
    issue_id = _subject_id(task.subject, task.domain)
    # Issue-family quantity/caseload asks often arrive as FIND; align with crime by
    # compiling them to aggregate COUNT when a reviewed family is recognized.
    if (
        task.operation in {
            SemanticOperation.FIND,
            SemanticOperation.CHECK_EXISTENCE,
        }
        and issue_id
    ):
        return [(
            ToolId.AGGREGATE_311,
            {
                "operation": "COUNT",
                "issue_scope": "SPECIFIC",
                "issue_family_id": issue_id,
                "location_id": location_id,
                "time_scope": time_scope,
                "group_by": None,
                "limit": 10,
            },
            (CivicDomain.BOSTON_311,),
        )]
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
        "offense_category_id": _subject_id(task.subject, task.domain),
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


def _default_location_id(trusted_context: Mapping[str, Any]) -> Optional[str]:
    geography = trusted_context.get("default_geography")
    if isinstance(geography, Mapping) and isinstance(geography.get("location_id"), str):
        return geography["location_id"]
    return None


def _location_id(
    task: SemanticTask,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Sequence[str],
) -> Optional[str]:
    if task.geography.mode is GeographyMode.NONE:
        return None
    if task.geography.mode is GeographyMode.EXPLICIT \
            and _explicit_matches_default(task, trusted_context):
        return _default_location_id(trusted_context)
    if task.geography.mode is GeographyMode.TRUSTED_REFERENCE:
        token = task.geography.value
        references = trusted_context.get("location_references")
        if token in set(trusted_reference_tokens) and isinstance(references, Mapping):
            value = references.get(token)
            if isinstance(value, str):
                return value
        raise SemanticCompilationError("The trusted location reference cannot be resolved.")
    return _default_location_id(trusted_context)


def _time_scope(
    task: SemanticTask,
    trusted_reference_tokens: Sequence[str],
    *,
    trusted_context: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    kind = task.time.kind
    if kind in {SemanticTimeKind.NONE, SemanticTimeKind.MISSING}:
        active_topic = trusted_context.get("active_topic")
        active_time = trusted_context.get("time_scope")
        token = active_time.get("token") if isinstance(active_time, Mapping) else None
        if (
            active_topic == task.domain.value
            and isinstance(token, str)
            and token in set(trusted_reference_tokens)
        ):
            return {
                "period": SemanticPeriod.RETAIN_TRUSTED_PERIOD.value,
                "trusted_reference_token": token,
            }
        if kind is SemanticTimeKind.MISSING:
            return None
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
        if period is not None:
            return {"period": period.value}
        resolved = resolve_explicit_date_expression(task.time.value)
        if resolved is None:
            raise SemanticCompilationError("The semantic time range needs deterministic resolution.")
        named = _named_calendar_period(resolved.start_date, resolved.end_date_exclusive)
        if named is not None:
            return {"period": named.value}
        return {
            "period": SemanticPeriod.EXPLICIT_DATE_RANGE.value,
            "start_date": resolved.start_date,
            "end_date_exclusive": resolved.end_date_exclusive,
        }
    period = {
        SemanticTimeKind.CURRENT_WEEK: SemanticPeriod.CURRENT_WEEK,
        SemanticTimeKind.CURRENT_WEEKEND: SemanticPeriod.CURRENT_WEEKEND,
        SemanticTimeKind.NEXT_CALENDAR_WEEK: SemanticPeriod.NEXT_CALENDAR_WEEK,
        SemanticTimeKind.CURRENT_CALENDAR_MONTH: SemanticPeriod.CURRENT_CALENDAR_MONTH,
        SemanticTimeKind.PREVIOUS_CALENDAR_MONTH: SemanticPeriod.PREVIOUS_CALENDAR_MONTH,
    }.get(kind)
    if period is None:
        raise SemanticCompilationError("The semantic time period is unsupported.")
    return {"period": period.value}


def _semantic_range_period(value: str) -> Optional[SemanticPeriod]:
    normalized = " ".join(value.casefold().replace("_", " ").split())
    mapping = {
        "today": SemanticPeriod.CURRENT_DAY,
        "this weekend": SemanticPeriod.CURRENT_WEEKEND,
        "next weekend": SemanticPeriod.NEXT_WEEKEND,
        "previous calendar week": SemanticPeriod.PREVIOUS_CALENDAR_WEEK,
        "current calendar month": SemanticPeriod.CURRENT_CALENDAR_MONTH,
        "monthly": SemanticPeriod.PREVIOUS_CALENDAR_MONTH,
        "this month": SemanticPeriod.CURRENT_CALENDAR_MONTH,
        "last month": SemanticPeriod.PREVIOUS_CALENDAR_MONTH,
        "previous month": SemanticPeriod.PREVIOUS_CALENDAR_MONTH,
    }
    if normalized in mapping:
        return mapping[normalized]
    # Remaining weekdays through Sunday / week-end are the current week, not the weekend alone.
    if any(
        marker in normalized
        for marker in (
            "through sunday",
            "thru sunday",
            "until sunday",
            "before sunday",
            "close of sunday",
            "end of sunday",
            "rest of the week",
            "rest of this week",
            "now through sunday",
            "now until sunday",
            "week-end",
            "week end",
            "week-close",
            "week close",
            "end of the week",
            "end of this week",
            "through week-end",
            "through week-close",
        )
    ):
        return SemanticPeriod.CURRENT_WEEK
    if "saturday" in normalized and "sunday" in normalized and "week" not in normalized:
        return SemanticPeriod.CURRENT_WEEKEND
    return None


def _comparison_scope(current: Mapping[str, Any]) -> Dict[str, Any]:
    previous = {
        SemanticPeriod.CURRENT_WEEK.value: SemanticPeriod.PREVIOUS_CALENDAR_WEEK.value,
        SemanticPeriod.CURRENT_CALENDAR_MONTH.value: SemanticPeriod.PREVIOUS_CALENDAR_MONTH.value,
    }.get(current.get("period"))
    if previous is not None:
        return {"period": previous}
    if current.get("period") == SemanticPeriod.PREVIOUS_CALENDAR_MONTH.value:
        from datetime import timedelta

        this_month = resolve_app_now().date().replace(day=1)
        previous_start = (this_month - timedelta(days=1)).replace(day=1)
        prior_start = (previous_start - timedelta(days=1)).replace(day=1)
        return {
            "period": SemanticPeriod.EXPLICIT_DATE_RANGE.value,
            "start_date": prior_start.isoformat(),
            "end_date_exclusive": previous_start.isoformat(),
        }
    if current.get("period") == SemanticPeriod.EXPLICIT_DATE_RANGE.value:
        from datetime import date, timedelta

        try:
            start = date.fromisoformat(str(current["start_date"]))
            end = date.fromisoformat(str(current["end_date_exclusive"]))
            if end <= start:
                raise ValueError
            prior_start = start - timedelta(days=(end - start).days)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticCompilationError(
                "The requested comparison period is underspecified."
            ) from exc
        return {
            "period": SemanticPeriod.EXPLICIT_DATE_RANGE.value,
            "start_date": prior_start.isoformat(),
            "end_date_exclusive": start.isoformat(),
        }
    raise SemanticCompilationError("The requested comparison period is underspecified.")


def _named_calendar_period(start_value: str, end_value: str) -> Optional[SemanticPeriod]:
    from datetime import date, timedelta

    try:
        start = date.fromisoformat(start_value)
        end = date.fromisoformat(end_value)
    except ValueError:
        return None
    today = resolve_app_now().date()
    this_month = today.replace(day=1)
    previous_end = this_month
    previous_start = (previous_end - timedelta(days=1)).replace(day=1)
    if (start, end) == (previous_start, previous_end):
        return SemanticPeriod.PREVIOUS_CALENDAR_MONTH
    next_month = (this_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    if (start, end) == (this_month, next_month):
        return SemanticPeriod.CURRENT_CALENDAR_MONTH
    return None


def _subject_id(subject: Optional[str], domain: SemanticDomain) -> Optional[str]:
    if not subject:
        return None
    normalized = unicodedata.normalize("NFKD", subject).encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-z0-9]+", normalized.casefold())
    generic = {
        SemanticDomain.SERVICE_REQUESTS: {
            "311", "service", "services", "request", "requests", "report", "reports",
            "complaint", "complaints", "issue", "issues", "case", "cases",
            "boston", "category", "categories",
            "type", "types", "all", "everything", "overall", "total", "totals",
            "a", "an", "the", "of", "in", "for", "from", "by", "most", "common",
            "kind", "kinds", "breakdown", "mix", "family", "families", "volume",
            "partition", "slice", "across",
            "month", "monthly", "period", "previous", "prior", "change", "changes",
            "comparison", "compare", "compared", "over",
        },
        SemanticDomain.CRIME: {
            "crime", "crimes", "incident", "incidents", "report", "reports", "reported",
        },
    }.get(domain, set())
    meaningful = [word for word in words if word not in generic]
    if not meaningful:
        return None
    if domain is SemanticDomain.SERVICE_REQUESTS:
        family = canonical_issue_family_id(" ".join(meaningful))
        if family is None:
            raise SemanticCompilationError(
                "The service-request subject is outside the reviewed issue registry."
            )
        return family.casefold()
    value = "_".join(meaningful)
    if not value:
        raise SemanticCompilationError("The subject cannot be canonicalized safely.")
    if not value[0].isalpha():
        raise SemanticCompilationError("The subject requires a reviewed canonical identifier.")
    return value[:80]


def _explicit_matches_default(
    task: SemanticTask,
    trusted_context: Mapping[str, Any],
) -> bool:
    if task.geography.mode is not GeographyMode.EXPLICIT or not task.geography.value:
        return False
    geography = trusted_context.get("default_geography")
    if not isinstance(geography, Mapping):
        return False
    candidate = _normalized_public_text(task.geography.value)
    if candidate in {
        "here", "around here", "near here", "nearby", "locally",
        # Citywide civic wording still resolves to the resident default place.
        "boston", "city of boston", "boston ma", "boston massachusetts",
    }:
        return True
    return candidate in {
        _normalized_public_text(str(geography.get("name") or "")),
        _normalized_public_text(str(geography.get("location_id") or "")),
    }.difference({""})


def _normalized_public_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).split())


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
        # The current event adapter has no civic_source_status record. Do not
        # compile or report a source-status execution that cannot be performed.
        return []
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


def _clarification_for_missing(missing: MissingInformation) -> str:
    return {
        MissingInformation.SUBJECT: "What topic or issue should I look into?",
        MissingInformation.GEOGRAPHY: "Which Dorchester location do you mean?",
        MissingInformation.TIME: "What time period should I use?",
        MissingInformation.SOURCE_TYPE: "Which kind of source should I check?",
        MissingInformation.REFERENCE: "Which earlier item do you mean?",
        MissingInformation.USER_MEANING: "What would you like me to find out?",
    }[missing]


def _clarification_for_task_conflict(task: SemanticTask) -> str:
    if task.source_scope not in {SourceScope.ANY, SourceScope.OFFICIAL}:
        return "Should I search that source type for discussion of this topic?"
    if task.domain is SemanticDomain.DOCUMENTS:
        return "What should I look for in those documents?"
    return "What specific civic information would you like me to look up?"


def _clarification_for_compilation(
    plan: SemanticPlan,
    compiler_reason: Optional[str],
) -> Tuple[str, str]:
    if any(task.geography.mode is GeographyMode.MISSING for task in plan.tasks):
        return "Which Dorchester location do you mean?", "MISSING_GEOGRAPHY"
    if any(task.time.kind is SemanticTimeKind.MISSING for task in plan.tasks):
        return "What time period should I use?", "MISSING_TIME"
    reason = (compiler_reason or "").casefold()
    if "time period" in reason or "comparison period" in reason:
        return "What time period should I use?", "MISSING_TIME"
    if "location" in reason:
        return "Which Dorchester location do you mean?", "MISSING_GEOGRAPHY"
    if "selected event" in reason or "trusted" in reason:
        return "Which earlier item do you mean?", "MISSING_REFERENCE"
    return "Could you narrow what you want me to look up?", "UNSUPPORTED_SEMANTIC_COMBINATION"


__all__ = [
    "CANONICALIZATION_VERSION",
    "COMPILER_VERSION",
    "CompiledSemanticPlan",
    "DerivedStateEffect",
    "GuardedDisposition",
    "GuardedSemanticPlan",
    "SemanticCompilationError",
    "compile_semantic_plan",
    "guard_semantic_plan",
]
