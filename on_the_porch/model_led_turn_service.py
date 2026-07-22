"""Guarded transport-neutral turn service for the Phase 2A canary.

The service owns one semantic planner call and deterministic capability dispatch.
Authentication, idempotency, persistence, and JSON/SSE transport remain outside.
"""

from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from answering_kernel import EvidenceBundle, EvidenceStatus, GlobalDeadline
from civic_311 import build_compiled_311_plan, execute_311_plan
from civic_tool_catalog import (
    RegisteredToolRequest,
    ResolvedSemanticTimeScope,
    ToolContractError,
    ToolId,
    resolve_semantic_time_scope,
)
from conversation_dimensions import (
    CanonicalFilters,
    CanonicalLocation,
    CanonicalTimeRange,
    CanonicalTopic,
    TimeGranularity,
    TimeLabelCode,
    geography_name,
    resolve_app_now,
    resolve_registered_geography,
)
from conversation_state import (
    ActiveEntity,
    ConversationState,
    ProposedTransition,
    ThreadStateEnvelope,
)
from event_context import (
    build_active_event_response,
    build_selected_event_response,
    resolve_event_by_id,
)
from event_query_context import (
    EventDiscoveryResult,
    compose_event_answer,
    discover_events,
    event_plan_from_compiled_scope,
)
from model_call_ledger import ModelCallLedger, ModelCallLedgerError, ModelCallRole
from model_led_orchestrator import normalize_resident_input
from orchestrator_evaluation import OrchestratorProvider
from sanitized_orchestrator_protocol import (
    FROZEN_OUTPUT_MODE,
    FROZEN_PLANNER_TIMEOUT_SECONDS,
    SanitizedPublicEvaluationProtocol,
)
from semantic_dialogue_controller import validate_dialogue_context
from semantic_plan_compiler import (
    GuardedDisposition,
    GuardedSemanticPlan,
    guard_semantic_plan,
)
from semantic_planner import (
    DirectResponseKind,
    GeographyMode,
    MissingInformation,
    PlannerAction,
    SemanticDomain,
    SemanticGeography,
    SemanticOperation,
    SemanticPlan,
    SemanticTask,
    SemanticTimeKind,
)
from source_freshness import (
    SOURCE_311,
    classify_freshness_status,
    load_source_status,
)


ConnectionFactory = Callable[[], Any]
MAX_TURN_RESULT_BYTES = 64_000


class TurnServiceStatus(str, Enum):
    COMPLETED = "COMPLETED"
    CLARIFICATION = "CLARIFICATION"
    NOT_YET_MIGRATED = "NOT_YET_MIGRATED"
    DEGRADED = "DEGRADED"
    INVALID_UNSAFE = "INVALID_UNSAFE"


@dataclass(frozen=True)
class TurnRequest:
    message: str
    state_envelope: ThreadStateEnvelope
    request_started_at: float = field(default_factory=time.perf_counter)
    now: Optional[datetime] = None
    selected_entity_token: Optional[str] = None

    def __post_init__(self) -> None:
        normalize_resident_input(self.message)
        if not isinstance(self.state_envelope, ThreadStateEnvelope):
            raise ValueError("turn request requires a trusted thread-state envelope")
        if not isinstance(self.request_started_at, (int, float)):
            raise ValueError("turn request start time is invalid")
        if self.selected_entity_token is not None:
            active_entity = self.state_envelope.conversation_state.active_entity
            if (
                active_entity is None
                or self.selected_entity_token != active_entity.source_ref
            ):
                raise ValueError("selected entity token is not present in trusted state")


@dataclass(frozen=True)
class TurnResult:
    status: TurnServiceStatus
    answer: Optional[str]
    mode: str
    sources: Tuple[Mapping[str, Any], ...]
    planner_action: Optional[str]
    guarded_disposition: str
    reason_code: str
    compiled_tools: Tuple[Mapping[str, Any], ...]
    evidence: Optional[Mapping[str, Any]]
    state_transition: ProposedTransition
    next_civic_311_context: Optional[Mapping[str, Any]]
    update_civic_311_context: bool
    next_dialogue_context: Optional[Mapping[str, Any]]
    update_dialogue_context: bool
    resolved_entity: Optional[Mapping[str, Any]]
    planner_call_count: int
    synthesis_call_count: int
    tool_call_count: int
    tool_execution_counts: Mapping[str, int]
    latency_ms: float
    provider_model: Optional[str] = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.planner_call_count not in {0, 1} or self.synthesis_call_count != 0:
            raise ValueError("Phase 2 turn results have invalid model-call counts")
        if any(not isinstance(value, int) or value < 0 for value in self.tool_execution_counts.values()):
            raise ValueError("tool execution counts are invalid")
        if self.answer is not None and len(self.answer) > 12_000:
            raise ValueError("turn answer exceeds the Phase 2 bound")
        encoded = json.dumps(self.to_safe_dict(), ensure_ascii=True, default=str).encode("utf-8")
        if len(encoded) > MAX_TURN_RESULT_BYTES:
            raise ValueError("turn result exceeds the Phase 2 bound")

    def to_safe_dict(self) -> Dict[str, Any]:
        return _json_transport_value({
            "status": self.status.value,
            "answer": self.answer,
            "mode": self.mode,
            "sources": [copy.deepcopy(dict(item)) for item in self.sources],
            "planner_action": self.planner_action,
            "guarded_disposition": self.guarded_disposition,
            "reason_code": self.reason_code,
            "compiled_tools": [copy.deepcopy(dict(item)) for item in self.compiled_tools],
            "evidence": copy.deepcopy(dict(self.evidence)) if self.evidence else None,
            "state_transition": {
                "action": self.state_transition.action.value,
                "expected_revision": self.state_transition.expected_revision,
                "next_state": (
                    self.state_transition.next_state.to_dict()
                    if self.state_transition.next_state is not None
                    else None
                ),
            },
            "next_civic_311_context": copy.deepcopy(self.next_civic_311_context),
            "update_civic_311_context": self.update_civic_311_context,
            "next_dialogue_context": copy.deepcopy(self.next_dialogue_context),
            "update_dialogue_context": self.update_dialogue_context,
            "resolved_entity": copy.deepcopy(self.resolved_entity),
            "planner_call_count": self.planner_call_count,
            "synthesis_call_count": self.synthesis_call_count,
            "tool_call_count": self.tool_call_count,
            "tool_execution_counts": dict(self.tool_execution_counts),
            "latency_ms": round(self.latency_ms, 2),
            "provider_model": self.provider_model,
            "diagnostics": copy.deepcopy(dict(self.diagnostics)),
        })

    def to_agent_result(self, *, retrieval_cache: Mapping[str, Any]) -> Dict[str, Any]:
        if self.answer is None:
            raise ValueError("a non-resident canary result cannot become an agent result")
        return {
            "answer": self.answer,
            "mode": self.mode,
            "sources": [dict(item) for item in self.sources],
            "result": {"model_led_turn": self.to_safe_dict()},
            "retrieval_cache": copy.deepcopy(dict(retrieval_cache)),
            "model_name": self.provider_model,
            "llm_calls": self.planner_call_count,
            "execution_degraded": self.status in {
                TurnServiceStatus.DEGRADED,
                TurnServiceStatus.INVALID_UNSAFE,
            },
            "resolved_entity": copy.deepcopy(self.resolved_entity),
            "civic_311_context": copy.deepcopy(self.next_civic_311_context),
            "civic_311_context_update": self.update_civic_311_context,
            "model_led_state_transition": self.state_transition,
            "model_led_dialogue_context": copy.deepcopy(self.next_dialogue_context),
        }


class ModelLedTurnService:
    """One planner, one compiler, and existing deterministic domain executors."""

    def __init__(
        self,
        *,
        provider: OrchestratorProvider,
        connection_factory: ConnectionFactory,
    ) -> None:
        self.provider = provider
        self.connection_factory = connection_factory
        self.protocol = SanitizedPublicEvaluationProtocol(output_mode=FROZEN_OUTPUT_MODE)

    def run(self, request: TurnRequest) -> TurnResult:
        started = time.perf_counter()
        deadline = GlobalDeadline.from_started_at(
            request.request_started_at,
            hard_ceiling_seconds=15.0,
        )
        ledger = ModelCallLedger(deadline)
        context, tokens = _trusted_planner_context(request.state_envelope)
        if request.selected_entity_token is not None:
            context["selected_entity_for_turn"] = request.selected_entity_token
        action: Optional[PlannerAction] = None
        provider_model: Optional[str] = None
        try:
            normalized = normalize_resident_input(request.message)
            prompt = self.protocol.build_prompt(
                normalized,
                trusted_context=context,
                available_trusted_reference_tokens=tokens,
            )
            reservation = ledger.reserve(ModelCallRole.ORCHESTRATOR)
            timeout = min(
                FROZEN_PLANNER_TIMEOUT_SECONDS,
                reservation.available_seconds,
                deadline.remaining(reserve_for_persistence=True),
            )
            if timeout <= 0:
                raise TimeoutError("planner deadline exhausted")
            response = self.provider.generate(prompt, timeout_seconds=timeout)
            provider_model = response.provider_model
            plan = self.protocol.parse_decision(
                response.text,
                trusted_context=context,
                available_trusted_reference_tokens=tokens,
            )
            action = plan.action
            guarded = guard_semantic_plan(
                plan,
                trusted_context=context,
                trusted_reference_tokens=tokens,
            )
        except (ModelCallLedgerError, TimeoutError):
            return self._planner_failure(
                request,
                started=started,
                planner_calls=ledger.total_calls,
                reason_code="PLANNER_TIMEOUT",
                provider_model=provider_model,
            )
        except Exception as exc:  # fail closed on provider/schema/contract errors
            return self._planner_failure(
                request,
                started=started,
                planner_calls=ledger.total_calls,
                reason_code="PLANNER_INVALID",
                provider_model=provider_model,
                error_type=type(exc).__name__,
            )

        common = {
            "request": request,
            "started": started,
            "action": action,
            "guarded": guarded,
            "provider_model": provider_model,
            "planner_calls": ledger.total_calls,
            "context": context,
            "tokens": tokens,
            "plan": plan,
            "deadline": deadline,
        }
        if guarded.disposition is GuardedDisposition.INVALID_UNSAFE:
            trusted_rephrase = _trusted_rephrase_fallback(plan, context, tokens)
            if trusted_rephrase is not None:
                return self._non_tool_result(
                    status=TurnServiceStatus.COMPLETED,
                    answer=trusted_rephrase,
                    mode="model_led_direct",
                    **common,
                )
            return self._non_tool_result(
                status=TurnServiceStatus.INVALID_UNSAFE,
                answer=guarded.resident_response,
                mode="model_led_safe_clarification",
                **common,
            )
        if guarded.disposition is GuardedDisposition.NEEDS_CLARIFICATION:
            return self._non_tool_result(
                status=TurnServiceStatus.CLARIFICATION,
                answer=guarded.resident_response,
                mode="model_led_clarification",
                **common,
            )
        if action is PlannerAction.RESPOND_DIRECTLY:
            return self._non_tool_result(
                status=TurnServiceStatus.COMPLETED,
                answer=guarded.resident_response,
                mode="model_led_direct",
                **common,
            )

        trusted_summary = self._trusted_summary_result(
            request=request,
            plan=plan,
            guarded=guarded,
            started=started,
            planner_calls=ledger.total_calls,
            provider_model=provider_model,
        )
        if trusted_summary is not None:
            return trusted_summary

        selected_domains = {task.domain for task in plan.tasks}
        migrated = {SemanticDomain.EVENTS, SemanticDomain.SERVICE_REQUESTS}
        if not selected_domains or not selected_domains.issubset(migrated):
            return self._not_migrated(**common)
        if len(selected_domains) != 1 or len(plan.tasks) != 1:
            return self._non_tool_result(
                status=TurnServiceStatus.CLARIFICATION,
                answer="Would you like me to check events or Boston 311 first?",
                mode="model_led_clarification",
                **common,
            )

        try:
            guarded, context, tokens, resolver_count = self._resolve_location_if_needed(
                plan,
                guarded,
                context=context,
                tokens=tokens,
            )
            if guarded.disposition is not GuardedDisposition.EXECUTABLE:
                return self._non_tool_result(
                    status=TurnServiceStatus.CLARIFICATION,
                    answer=guarded.resident_response,
                    mode="model_led_clarification",
                    request=request,
                    started=started,
                    action=action,
                    guarded=guarded,
                    provider_model=provider_model,
                    planner_calls=ledger.total_calls,
                    context=context,
                    tokens=tokens,
                    plan=plan,
                    deadline=deadline,
                    tool_counts={"resolve_location": resolver_count} if resolver_count else {},
                )
            core = tuple(
                item
                for item in guarded.tool_requests
                if item.tool_id not in {ToolId.GET_SOURCE_STATUS, ToolId.RESOLVE_LOCATION}
            )
            if not core and selected_domains == {SemanticDomain.SERVICE_REQUESTS}:
                return self._run_311_source_status(
                    request=request,
                    plan=plan,
                    guarded=guarded,
                    deadline=deadline,
                    started=started,
                    planner_calls=ledger.total_calls,
                    provider_model=provider_model,
                    resolver_count=resolver_count,
                )
            if len(core) != 1:
                raise ValueError("Phase 2 requires exactly one domain capability")
            tool = core[0]
            if tool.tool_id is ToolId.QUERY_EVENTS:
                return self._run_events(
                    request=request,
                    plan=plan,
                    guarded=guarded,
                    tool=tool,
                    context=context,
                    tokens=tokens,
                    deadline=deadline,
                    started=started,
                    planner_calls=ledger.total_calls,
                    provider_model=provider_model,
                    resolver_count=resolver_count,
                )
            if tool.tool_id in {ToolId.AGGREGATE_311, ToolId.SEARCH_311_RECORDS}:
                return self._run_311(
                    request=request,
                    plan=plan,
                    guarded=guarded,
                    tool=tool,
                    context=context,
                    deadline=deadline,
                    started=started,
                    planner_calls=ledger.total_calls,
                    provider_model=provider_model,
                    resolver_count=resolver_count,
                )
            raise ValueError("compiled capability is outside Phase 2")
        except Exception as exc:
            return self._capability_failure(
                request,
                plan=plan,
                guarded=guarded,
                started=started,
                planner_calls=ledger.total_calls,
                provider_model=provider_model,
                error_type=type(exc).__name__,
            )

    def _trusted_summary_result(
        self,
        *,
        request: TurnRequest,
        plan: SemanticPlan,
        guarded: GuardedSemanticPlan,
        started: float,
        planner_calls: int,
        provider_model: Optional[str],
    ) -> Optional[TurnResult]:
        """Answer only summary-sufficient evidence questions without re-querying."""
        if len(plan.tasks) != 1:
            return None
        task = plan.tasks[0]
        if (
            task.domain is not SemanticDomain.EVENTS
            or task.operation is not SemanticOperation.CHECK_EXISTENCE
            or task.time.kind is not SemanticTimeKind.TRUSTED_REFERENCE
            or task.geography.mode not in {
                GeographyMode.DEFAULT,
                GeographyMode.TRUSTED_REFERENCE,
            }
        ):
            return None
        dialogue = validate_dialogue_context(request.state_envelope.dialogue_context)
        summary = dialogue.get("previous_result_summary") or {}
        if summary.get("kind") != "EVENT_LIST":
            return None
        count = summary.get("returned_count")
        if not isinstance(count, int) or count < 0:
            return None
        if count:
            noun = "event" if count == 1 else "events"
            answer = (
                f"No. The previous lookup found {count} matching {noun} that had not "
                "already ended in that period."
            )
        else:
            answer = "Yes. The previous lookup found no matching events remaining in that period."
        return TurnResult(
            status=TurnServiceStatus.COMPLETED,
            answer=answer,
            mode="model_led_trusted_summary",
            sources=(),
            planner_action=PlannerAction.GATHER_EVIDENCE.value,
            guarded_disposition=guarded.disposition.value,
            reason_code="TRUSTED_SUMMARY_SUFFICIENT",
            compiled_tools=tuple(item.to_dict() for item in guarded.tool_requests),
            evidence={
                "domain": "EVENTS",
                "status": "COMPLETE",
                "facts": {"matching_event_count": count},
                "limitations": [],
            },
            state_transition=ProposedTransition.preserve(
                request.state_envelope.conversation_state,
            ),
            next_civic_311_context=request.state_envelope.civic_311_context,
            update_civic_311_context=False,
            next_dialogue_context=request.state_envelope.dialogue_context,
            update_dialogue_context=False,
            resolved_entity=None,
            planner_call_count=planner_calls,
            synthesis_call_count=0,
            tool_call_count=0,
            tool_execution_counts={},
            latency_ms=(time.perf_counter() - started) * 1000.0,
            provider_model=provider_model,
        )

    def _resolve_location_if_needed(
        self,
        plan: SemanticPlan,
        guarded: GuardedSemanticPlan,
        *,
        context: Mapping[str, Any],
        tokens: Sequence[str],
    ) -> Tuple[GuardedSemanticPlan, Mapping[str, Any], Tuple[str, ...], int]:
        if not guarded.compiled_plan.requires_location_resolution:
            return guarded, context, tuple(tokens), 0
        resolver = next(
            (item for item in guarded.tool_requests if item.tool_id is ToolId.RESOLVE_LOCATION),
            None,
        )
        if resolver is None:
            raise ValueError("location resolution was requested without a registered tool")
        location = resolve_registered_geography(resolver.arguments.get("query"))
        if location is None:
            return (
                GuardedSemanticPlan(
                    disposition=GuardedDisposition.NEEDS_CLARIFICATION,
                    compiled_plan=guarded.compiled_plan,
                    resident_response=(
                        "Which Dorchester area did you mean? I can currently use Dorchester, "
                        "Codman Square, or Fields Corner."
                    ),
                    reason_code="LOCATION_NOT_REGISTERED",
                ),
                context,
                tuple(tokens),
                1,
            )
        token = "location:resolved"
        next_context = copy.deepcopy(dict(context))
        references = dict(next_context.get("location_references") or {})
        references[token] = location.id
        next_context["location_references"] = references
        next_tokens = tuple(dict.fromkeys((*tokens, token)))
        tasks = tuple(
            SemanticTask(
                domain=task.domain,
                operation=task.operation,
                subject=task.subject,
                geography=SemanticGeography(GeographyMode.TRUSTED_REFERENCE, token),
                time=task.time,
                source_scope=task.source_scope,
                goal_clarity=task.goal_clarity,
            ) if task.geography.mode is GeographyMode.EXPLICIT else task
            for task in plan.tasks
        )
        rebound = SemanticPlan(PlannerAction.GATHER_EVIDENCE, tasks=tasks)
        rebound_guard = guard_semantic_plan(
            rebound,
            trusted_context=next_context,
            trusted_reference_tokens=next_tokens,
        )
        return rebound_guard, next_context, next_tokens, 1

    def _run_events(
        self,
        *,
        request: TurnRequest,
        plan: SemanticPlan,
        guarded: GuardedSemanticPlan,
        tool: RegisteredToolRequest,
        context: Mapping[str, Any],
        tokens: Sequence[str],
        deadline: GlobalDeadline,
        started: float,
        planner_calls: int,
        provider_model: Optional[str],
        resolver_count: int,
    ) -> TurnResult:
        if deadline.expired(reserve_for_persistence=True):
            raise TimeoutError("event capability deadline exhausted")
        operation = str(tool.arguments.get("operation") or "")
        conn = None
        if operation == "DETAILS":
            event_id = _trusted_event_id(
                tool.arguments.get("entity_token"),
                request.state_envelope.conversation_state,
            )
            conn = self.connection_factory()
            try:
                resolution = resolve_event_by_id(conn, event_id)
                detail_field = str(tool.arguments.get("detail_field") or "GENERAL")
                selected = (
                    build_active_event_response(
                        request.message,
                        resolution,
                        property_name=detail_field.lower(),
                    )
                    if detail_field != "GENERAL"
                    else build_selected_event_response(request.message, resolution)
                )
            finally:
                conn.close()
            evidence = {
                "domain": "EVENTS",
                "status": "COMPLETE" if selected.resolved_entity.get("id") else "ABSENT",
                "facts": {"event_id": event_id},
                "limitations": [],
            }
            transition = _event_state_transition(
                request.state_envelope.conversation_state,
                event_id=event_id if selected.resolved_entity.get("id") else None,
                location=None,
                time_range=None,
            )
            dialogue = _dialogue_context_after_event(
                request.state_envelope.dialogue_context,
                count=1 if selected.resolved_entity.get("id") else 0,
                event_id=event_id if selected.resolved_entity.get("id") else None,
                scope=None,
            )
            counts = {"query_events": 1}
            if resolver_count:
                counts["resolve_location"] = resolver_count
            return TurnResult(
                status=TurnServiceStatus.COMPLETED,
                answer=selected.answer,
                mode="model_led_events",
                sources=tuple(selected.sources),
                planner_action=PlannerAction.GATHER_EVIDENCE.value,
                guarded_disposition=guarded.disposition.value,
                reason_code=guarded.reason_code,
                compiled_tools=tuple(item.to_dict() for item in guarded.tool_requests),
                evidence=evidence,
                state_transition=transition,
                next_civic_311_context=None,
                update_civic_311_context=True,
                next_dialogue_context=dialogue,
                update_dialogue_context=True,
                resolved_entity=selected.resolved_entity,
                planner_call_count=planner_calls,
                synthesis_call_count=0,
                tool_call_count=sum(counts.values()),
                tool_execution_counts=counts,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                provider_model=provider_model,
            )

        resolved = _resolved_time_for_tool(
            tool.arguments.get("time_scope"),
            request=request,
        )
        event_plan = event_plan_from_compiled_scope(
            location_id=str(tool.arguments["location_id"]),
            semantic_start_date=resolved.start_date,
            semantic_end_date_exclusive=resolved.end_date_exclusive,
            semantic_label_code=resolved.event_label_code,
            upcoming=resolved.upcoming,
            modality=_event_modality(tool.arguments.get("filters")),
            now=request.now,
        )
        conn = self.connection_factory()
        try:
            result = discover_events(conn, event_plan, now=request.now)
        finally:
            conn.close()
        answer = _event_answer_for_operation(plan, result)
        evidence_bundle = _event_evidence_bundle(result)
        transition = _event_state_transition(
            request.state_envelope.conversation_state,
            event_id=None,
            location=event_plan.location,
            time_range=_canonical_time_range(resolved),
        )
        dialogue = _dialogue_context_after_event(
            request.state_envelope.dialogue_context,
            count=len(result.events),
            event_id=None,
            scope=resolved,
        )
        counts = {"query_events": 1}
        if resolver_count:
            counts["resolve_location"] = resolver_count
        return TurnResult(
            status=TurnServiceStatus.COMPLETED,
            answer=answer,
            mode="model_led_events",
            sources=result.sources,
            planner_action=PlannerAction.GATHER_EVIDENCE.value,
            guarded_disposition=guarded.disposition.value,
            reason_code=guarded.reason_code,
            compiled_tools=tuple(item.to_dict() for item in guarded.tool_requests),
            evidence=_safe_evidence(evidence_bundle),
            state_transition=transition,
            next_civic_311_context=None,
            update_civic_311_context=True,
            next_dialogue_context=dialogue,
            update_dialogue_context=True,
            resolved_entity=None,
            planner_call_count=planner_calls,
            synthesis_call_count=0,
            tool_call_count=sum(counts.values()),
            tool_execution_counts=counts,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            provider_model=provider_model,
        )

    def _run_311(
        self,
        *,
        request: TurnRequest,
        plan: SemanticPlan,
        guarded: GuardedSemanticPlan,
        tool: RegisteredToolRequest,
        context: Mapping[str, Any],
        deadline: GlobalDeadline,
        started: float,
        planner_calls: int,
        provider_model: Optional[str],
        resolver_count: int,
    ) -> TurnResult:
        if deadline.expired(reserve_for_persistence=True):
            raise TimeoutError("311 capability deadline exhausted")
        resolved = _resolved_time_for_tool(
            tool.arguments.get("time_scope"),
            request=request,
        )
        strict_plan = build_compiled_311_plan(
            tool_id=tool.tool_id.value,
            arguments=tool.arguments,
            resolved_period=resolved.to_dict(),
        )
        civic = execute_311_plan(
            request.message,
            plan=strict_plan,
            connection_factory=self.connection_factory,
            deadline=deadline,
            now=request.now,
            started_at=started,
            planner_call_count=0,
            synthesis_llm=None,
            repair_llm=None,
        )
        agent = civic.to_agent_result()
        degraded = civic.evidence.evidence_status in {
            EvidenceStatus.DEPENDENCY_UNAVAILABLE,
            EvidenceStatus.INSUFFICIENT_EVIDENCE,
        }
        transition = (
            ProposedTransition.preserve(request.state_envelope.conversation_state)
            if degraded
            else _311_state_transition(
                request.state_envelope.conversation_state,
                location_id=str(tool.arguments.get("location_id") or "dorchester"),
                semantic_time=_canonical_time_range(resolved),
            )
        )
        dialogue = _dialogue_context_after_311(
            request.state_envelope.dialogue_context,
            agent,
            civic.next_context,
        )
        counts = {name: 1 for name in civic.plan.tools}
        if resolver_count:
            counts["resolve_location"] = resolver_count
        return TurnResult(
            status=TurnServiceStatus.DEGRADED if degraded else TurnServiceStatus.COMPLETED,
            answer=civic.answer,
            mode="model_led_311",
            sources=tuple(civic.sources),
            planner_action=PlannerAction.GATHER_EVIDENCE.value,
            guarded_disposition=guarded.disposition.value,
            reason_code=guarded.reason_code,
            compiled_tools=tuple(item.to_dict() for item in guarded.tool_requests),
            evidence=_safe_evidence(civic.evidence),
            state_transition=transition,
            next_civic_311_context=(
                request.state_envelope.civic_311_context if degraded else civic.next_context
            ),
            update_civic_311_context=False if degraded else civic.update_context,
            next_dialogue_context=(
                request.state_envelope.dialogue_context if degraded else dialogue
            ),
            update_dialogue_context=not degraded,
            resolved_entity=None,
            planner_call_count=planner_calls,
            synthesis_call_count=0,
            tool_call_count=sum(counts.values()),
            tool_execution_counts=counts,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            provider_model=provider_model,
            diagnostics={"evidence_status": civic.evidence.evidence_status.value},
        )

    def _run_311_source_status(
        self,
        *,
        request: TurnRequest,
        plan: SemanticPlan,
        guarded: GuardedSemanticPlan,
        deadline: GlobalDeadline,
        started: float,
        planner_calls: int,
        provider_model: Optional[str],
        resolver_count: int,
    ) -> TurnResult:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="modelled311status")
        future = executor.submit(load_source_status, self.connection_factory, SOURCE_311)
        try:
            remaining = min(1.5, deadline.remaining(reserve_for_persistence=True))
            done, pending = wait((future,), timeout=max(0.0, remaining))
            if pending:
                future.cancel()
                raise TimeoutError("311 source status deadline exhausted")
            source_status = future.result()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if source_status is None or source_status.data_through_date is None:
            raise RuntimeError("311 source status is unavailable")
        freshness, age_days, reasons = classify_freshness_status(
            source_status,
            now=request.now,
        )
        through = source_status.data_through_date
        answer = (
            "The available Boston 311 service-request data runs through "
            f"{through.strftime('%B')} {through.day}, {through.year}."
        )
        if age_days is not None:
            unit = "day" if age_days == 1 else "days"
            answer += f" That is {age_days} {unit} behind the current date."
        answer += " Source: Boston 311 service-request data."
        counts = {"get_source_status": 1}
        if resolver_count:
            counts["resolve_location"] = resolver_count
        return TurnResult(
            status=TurnServiceStatus.COMPLETED,
            answer=answer,
            mode="model_led_311_status",
            sources=({
                "type": "official",
                "source": "Boston 311 service-request data",
                "authority": "Official City of Boston service-request data",
                "data_through_date": through.isoformat(),
            },),
            planner_action=PlannerAction.GATHER_EVIDENCE.value,
            guarded_disposition=guarded.disposition.value,
            reason_code=guarded.reason_code,
            compiled_tools=tuple(item.to_dict() for item in guarded.tool_requests),
            evidence={
                "domain": "SERVICE_REQUESTS",
                "status": "COMPLETE",
                "facts": {
                    "data_through_date": through.isoformat(),
                    "freshness_status": freshness.value,
                    "age_days": age_days,
                },
                "limitations": [],
            },
            state_transition=ProposedTransition.preserve(
                request.state_envelope.conversation_state,
            ),
            next_civic_311_context=request.state_envelope.civic_311_context,
            update_civic_311_context=False,
            next_dialogue_context=request.state_envelope.dialogue_context,
            update_dialogue_context=False,
            resolved_entity=None,
            planner_call_count=planner_calls,
            synthesis_call_count=0,
            tool_call_count=sum(counts.values()),
            tool_execution_counts=counts,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            provider_model=provider_model,
            diagnostics={"freshness_reason_codes": list(reasons)[:4]},
        )

    def _non_tool_result(
        self,
        *,
        status: TurnServiceStatus,
        answer: Optional[str],
        mode: str,
        request: TurnRequest,
        started: float,
        action: Optional[PlannerAction],
        guarded: GuardedSemanticPlan,
        provider_model: Optional[str],
        planner_calls: int,
        context: Mapping[str, Any],
        tokens: Sequence[str],
        plan: SemanticPlan,
        deadline: GlobalDeadline,
        tool_counts: Optional[Mapping[str, int]] = None,
    ) -> TurnResult:
        del context, tokens, deadline
        dialogue = _dialogue_context_after_non_tool(
            request.state_envelope.dialogue_context,
            plan=plan,
            guarded=guarded,
            status=status,
        )
        counts = dict(tool_counts or {})
        return TurnResult(
            status=status,
            answer=answer,
            mode=mode,
            sources=(),
            planner_action=action.value if action else None,
            guarded_disposition=guarded.disposition.value,
            reason_code=guarded.reason_code,
            compiled_tools=tuple(item.to_dict() for item in guarded.tool_requests),
            evidence=None,
            state_transition=ProposedTransition.preserve(
                request.state_envelope.conversation_state,
            ),
            next_civic_311_context=request.state_envelope.civic_311_context,
            update_civic_311_context=False,
            next_dialogue_context=dialogue,
            update_dialogue_context=dialogue is not None,
            resolved_entity=None,
            planner_call_count=planner_calls,
            synthesis_call_count=0,
            tool_call_count=sum(counts.values()),
            tool_execution_counts=counts,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            provider_model=provider_model,
        )

    def _not_migrated(self, **common: Any) -> TurnResult:
        request: TurnRequest = common["request"]
        guarded: GuardedSemanticPlan = common["guarded"]
        action: Optional[PlannerAction] = common["action"]
        return TurnResult(
            status=TurnServiceStatus.NOT_YET_MIGRATED,
            answer=None,
            mode="model_led_not_yet_migrated",
            sources=(),
            planner_action=action.value if action else None,
            guarded_disposition=guarded.disposition.value,
            reason_code="DOMAIN_NOT_YET_MIGRATED",
            compiled_tools=tuple(item.to_dict() for item in guarded.tool_requests),
            evidence=None,
            state_transition=ProposedTransition.preserve(
                request.state_envelope.conversation_state,
            ),
            next_civic_311_context=request.state_envelope.civic_311_context,
            update_civic_311_context=False,
            next_dialogue_context=request.state_envelope.dialogue_context,
            update_dialogue_context=False,
            resolved_entity=None,
            planner_call_count=int(common["planner_calls"]),
            synthesis_call_count=0,
            tool_call_count=0,
            tool_execution_counts={},
            latency_ms=(time.perf_counter() - float(common["started"])) * 1000.0,
            provider_model=common.get("provider_model"),
        )

    def _planner_failure(
        self,
        request: TurnRequest,
        *,
        started: float,
        planner_calls: int,
        reason_code: str,
        provider_model: Optional[str],
        error_type: Optional[str] = None,
    ) -> TurnResult:
        return TurnResult(
            status=TurnServiceStatus.DEGRADED,
            answer="I couldn't safely understand that request. Could you rephrase what you want to know?",
            mode="model_led_clarification",
            sources=(),
            planner_action=None,
            guarded_disposition=GuardedDisposition.INVALID_UNSAFE.value,
            reason_code=reason_code,
            compiled_tools=(),
            evidence=None,
            state_transition=ProposedTransition.preserve(
                request.state_envelope.conversation_state,
            ),
            next_civic_311_context=request.state_envelope.civic_311_context,
            update_civic_311_context=False,
            next_dialogue_context=request.state_envelope.dialogue_context,
            update_dialogue_context=False,
            resolved_entity=None,
            planner_call_count=planner_calls,
            synthesis_call_count=0,
            tool_call_count=0,
            tool_execution_counts={},
            latency_ms=(time.perf_counter() - started) * 1000.0,
            provider_model=provider_model,
            diagnostics={"failure_stage": "planner", "error_type": error_type},
        )

    def _capability_failure(
        self,
        request: TurnRequest,
        *,
        plan: SemanticPlan,
        guarded: GuardedSemanticPlan,
        started: float,
        planner_calls: int,
        provider_model: Optional[str],
        error_type: str,
    ) -> TurnResult:
        domain = next(iter({task.domain for task in plan.tasks}), None)
        answer = (
            "I couldn't load the current event listings right now."
            if domain is SemanticDomain.EVENTS
            else "I couldn't load the available Boston 311 service-request data right now."
        )
        return TurnResult(
            status=TurnServiceStatus.DEGRADED,
            answer=answer,
            mode="model_led_degraded",
            sources=(),
            planner_action=plan.action.value,
            guarded_disposition=guarded.disposition.value,
            reason_code="CAPABILITY_UNAVAILABLE",
            compiled_tools=tuple(item.to_dict() for item in guarded.tool_requests),
            evidence={"status": "TEMPORARILY_UNAVAILABLE"},
            state_transition=ProposedTransition.preserve(
                request.state_envelope.conversation_state,
            ),
            next_civic_311_context=request.state_envelope.civic_311_context,
            update_civic_311_context=False,
            next_dialogue_context=request.state_envelope.dialogue_context,
            update_dialogue_context=False,
            resolved_entity=None,
            planner_call_count=planner_calls,
            synthesis_call_count=0,
            tool_call_count=0,
            tool_execution_counts={},
            latency_ms=(time.perf_counter() - started) * 1000.0,
            provider_model=provider_model,
            diagnostics={"failure_stage": "capability", "error_type": error_type},
        )


def _trusted_planner_context(
    envelope: ThreadStateEnvelope,
) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    state = envelope.conversation_state
    location = state.location or resolve_registered_geography("dorchester")
    context: Dict[str, Any] = {
        "default_geography": {
            "location_id": location.id if location else "dorchester",
            "name": geography_name(location.id) if location else "Dorchester",
            "scope": location.kind.value if location else "neighborhood",
        },
        "active_topic": _planner_topic(state.active_topic),
        "location_references": {},
    }
    tokens = []
    if state.location is not None:
        token = "location:active"
        tokens.append(token)
        context["location_references"][token] = state.location.id
    if state.time_range is not None:
        token = "time:active"
        tokens.append(token)
        context["time_scope"] = {"token": token}
        context["time_references"] = {
            token: {
                "start_date": state.time_range.start_date,
                "end_date_exclusive": state.time_range.end_date_exclusive,
                "label": state.time_range.label_code.value.replace("_", " ").lower(),
                "event_label_code": state.time_range.label_code.value,
            }
        }
    if state.active_entity is not None:
        tokens.append(state.active_entity.source_ref)
        context["active_entity"] = {
            "token": state.active_entity.source_ref,
            "title": "selected event",
        }
    summary = _trusted_summary_text(envelope.dialogue_context)
    if summary:
        token = "summary:previous"
        tokens.append(token)
        context["previous_summary"] = {"token": token, "text": summary}
    return context, tuple(tokens)


def _trusted_summary_text(value: Optional[Mapping[str, Any]]) -> Optional[str]:
    context = validate_dialogue_context(value)
    summary = context.get("previous_result_summary") or {}
    if summary.get("kind") == "EVENT_LIST":
        count = summary.get("returned_count")
        if summary.get("status") == "NO_EVENTS":
            return "No matching events remained in the requested period."
        if isinstance(count, int):
            return f"The previous event lookup returned {count} matching listings."
    if summary.get("kind") == "CIVIC_311_COUNT" and isinstance(summary.get("count"), int):
        return (
            f"The previous official Boston 311 lookup found {summary['count']} "
            f"{summary.get('request_label') or 'service requests'} in "
            f"{summary.get('geography_label') or 'Dorchester'}."
        )
    return None


def _trusted_rephrase_fallback(
    plan: SemanticPlan,
    context: Mapping[str, Any],
    trusted_tokens: Sequence[str],
) -> Optional[str]:
    """Return only server-owned summary text when a model rephrase is invalid."""
    if (
        plan.action is not PlannerAction.RESPOND_DIRECTLY
        or plan.response_kind is not DirectResponseKind.REPHRASE_TRUSTED_SUMMARY
    ):
        return None
    summary = context.get("previous_summary")
    if not isinstance(summary, Mapping):
        return None
    token = summary.get("token")
    text = summary.get("text")
    if token not in set(trusted_tokens) or not isinstance(text, str) or not text.strip():
        return None
    return " ".join(text.split()).strip()


def _planner_topic(topic: Optional[CanonicalTopic]) -> Optional[str]:
    return {
        CanonicalTopic.EVENTS: SemanticDomain.EVENTS.value,
        CanonicalTopic.CITY_SERVICES: SemanticDomain.SERVICE_REQUESTS.value,
        CanonicalTopic.CRIME_FACTS: SemanticDomain.CRIME.value,
    }.get(topic)


def _resolved_time_for_tool(raw: Any, *, request: TurnRequest) -> ResolvedSemanticTimeScope:
    if not isinstance(raw, Mapping):
        raise ToolContractError("compiled capability has no time scope")
    trusted = None
    token = raw.get("trusted_reference_token")
    if token:
        context, _tokens = _trusted_planner_context(request.state_envelope)
        references = context.get("time_references")
        trusted = references.get(token) if isinstance(references, Mapping) else None
    today = resolve_app_now(request.now).date()
    return resolve_semantic_time_scope(raw, today=today, trusted_period=trusted)


def _event_modality(raw: Any) -> Optional[str]:
    if not isinstance(raw, Mapping):
        return None
    modality = str(raw.get("modality") or "ANY")
    return "virtual" if modality == "VIRTUAL" else None


def _event_answer_for_operation(plan: SemanticPlan, result: EventDiscoveryResult) -> str:
    operation = plan.tasks[0].operation
    if operation.value == "CHECK_EXISTENCE":
        if result.events:
            noun = "event" if len(result.events) == 1 else "events"
            scope = compose_event_answer(result.plan, result.events, candidate_count=result.candidate_count)
            return f"Yes. I found {len(result.events)} matching {noun}.\n\n{scope}"
        return compose_event_answer(result.plan, (), candidate_count=result.candidate_count)
    return compose_event_answer(
        result.plan,
        result.events,
        candidate_count=result.candidate_count,
    )


def _event_evidence_bundle(result: EventDiscoveryResult) -> EvidenceBundle:
    status = EvidenceStatus.COMPLETE if result.events else EvidenceStatus.NO_MATCHING_RECORDS
    location = result.plan.location
    return EvidenceBundle(
        question_scope={"domain": "EVENTS", "operation": "LIST"},
        issue_family=None,
        geography={
            "label": geography_name(location.id) if location else "Dorchester",
            "location_id": location.id if location else "dorchester",
        },
        requested_period={
            "start_date": result.plan.semantic_start_date,
            "end_date_exclusive": result.plan.semantic_end_date_exclusive,
            "label": result.plan.semantic_label_code,
        },
        executed_period={
            "start_date": result.plan.start_date,
            "end_date_exclusive": result.plan.end_date_exclusive,
            "label": result.plan.semantic_label_code,
        },
        freshness={},
        coverage={"candidate_count": result.candidate_count, "returned_count": len(result.events)},
        facts={"events": [copy.deepcopy(item) for item in result.events]},
        source_records=[dict(item) for item in result.sources],
        limitations=list(result.limitations),
        evidence_status=status,
    )


def _safe_evidence(bundle: EvidenceBundle) -> Dict[str, Any]:
    return {
        "question_scope": copy.deepcopy(dict(bundle.question_scope)),
        "geography": copy.deepcopy(dict(bundle.geography)),
        "requested_period": copy.deepcopy(dict(bundle.requested_period)),
        "executed_period": copy.deepcopy(dict(bundle.executed_period)),
        "freshness": copy.deepcopy(dict(bundle.freshness)),
        "coverage": copy.deepcopy(dict(bundle.coverage)),
        "facts": copy.deepcopy(bundle.facts),
        "limitations": list(bundle.limitations),
        "missing_evidence": list(bundle.missing_evidence),
        "status": bundle.evidence_status.value,
    }


def _json_transport_value(value: Any) -> Any:
    """Return a JSON-native copy without stringifying arbitrary objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, datetime_time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_transport_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_transport_value(item) for item in value]
    raise TypeError(f"turn result contains unsupported public value: {type(value).__name__}")


def _trusted_event_id(token: Any, state: ConversationState) -> str:
    if state.active_entity is None or token != state.active_entity.source_ref:
        raise ValueError("compiled event details reference is unavailable")
    return state.active_entity.entity_id


def _canonical_time_range(resolved: ResolvedSemanticTimeScope) -> CanonicalTimeRange:
    label = TimeLabelCode(resolved.event_label_code)
    granularity = {
        TimeLabelCode.TODAY: TimeGranularity.DAY,
        TimeLabelCode.THIS_WEEK: TimeGranularity.WEEK,
        TimeLabelCode.THIS_CALENDAR_WEEK: TimeGranularity.WEEK,
        TimeLabelCode.NEXT_CALENDAR_WEEK: TimeGranularity.WEEK,
        TimeLabelCode.LAST_WEEK: TimeGranularity.WEEK,
        TimeLabelCode.THIS_MONTH: TimeGranularity.MONTH,
        TimeLabelCode.LAST_MONTH: TimeGranularity.MONTH,
    }.get(label, TimeGranularity.RANGE)
    return CanonicalTimeRange(
        label_code=label,
        start_date=resolved.start_date,
        end_date_exclusive=resolved.end_date_exclusive,
        timezone="America/New_York",
        granularity=granularity,
    )


def _event_state_transition(
    current: ConversationState,
    *,
    event_id: Optional[str],
    location: Optional[CanonicalLocation],
    time_range: Optional[CanonicalTimeRange],
) -> ProposedTransition:
    next_state = ConversationState(
        revision=current.revision,
        active_topic=CanonicalTopic.EVENTS,
        active_entity=ActiveEntity.event(event_id) if event_id else None,
        location=location or current.location,
        time_range=time_range or current.time_range,
        filters=current.filters if current.active_topic is CanonicalTopic.EVENTS else CanonicalFilters(),
        previous_route="model_led",
        last_substantive_message_id=current.last_substantive_message_id,
    )
    return ProposedTransition.replace_state(current, next_state)


def _311_state_transition(
    current: ConversationState,
    *,
    location_id: str,
    semantic_time: CanonicalTimeRange,
) -> ProposedTransition:
    location = resolve_registered_geography(location_id)
    if location is None:
        raise ValueError("311 state location is not registered")
    next_state = ConversationState(
        revision=current.revision,
        active_topic=CanonicalTopic.CITY_SERVICES,
        active_entity=None,
        location=location,
        time_range=semantic_time,
        filters=CanonicalFilters(),
        previous_route="model_led",
        last_substantive_message_id=current.last_substantive_message_id,
    )
    return ProposedTransition.replace_state(current, next_state)


def _dialogue_base(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return validate_dialogue_context(value)


def _dialogue_context_after_event(
    previous: Optional[Mapping[str, Any]],
    *,
    count: int,
    event_id: Optional[str],
    scope: Optional[ResolvedSemanticTimeScope],
) -> Dict[str, Any]:
    current = _dialogue_base(previous)
    canonical = {
        "event_id": event_id,
        "start_date": scope.start_date if scope else None,
        "end_date_exclusive": scope.end_date_exclusive if scope else None,
        "semantic_time": scope.event_label_code if scope else None,
    }
    return validate_dialogue_context({
        **current,
        "previous_turn_kind": "FACTUAL_TOOL_QUERY",
        "previous_domain": "EVENTS",
        "previous_capability_intent": "EVENT_DETAILS" if event_id else "LIST_EVENTS",
        "pending_clarification_type": None,
        "pending_clarification_domain": None,
        "canonical_parameters": canonical,
        "previous_result_summary": {
            "kind": "EVENT_LIST",
            "status": "EVENTS_FOUND" if count else "NO_EVENTS",
            "returned_count": count,
        },
    })


def _dialogue_context_after_311(
    previous: Optional[Mapping[str, Any]],
    agent_result: Mapping[str, Any],
    civic_context: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    current = _dialogue_base(previous)
    canonical = {}
    if isinstance(civic_context, Mapping):
        canonical = {
            "issue_scope": civic_context.get("issue_scope"),
            "issue_family": civic_context.get("issue_family"),
            "geography": copy.deepcopy(civic_context.get("geography") or {}),
            "period": copy.deepcopy(civic_context.get("executed_period") or {}),
        }
    summary = agent_result.get("trusted_result_summary")
    if not isinstance(summary, Mapping):
        summary = {
            "kind": "CIVIC_311",
            "status": (
                str(civic_context.get("coverage_status") or "UNKNOWN")
                if isinstance(civic_context, Mapping)
                else "UNKNOWN"
            ),
        }
    return validate_dialogue_context({
        **current,
        "previous_turn_kind": "FACTUAL_TOOL_QUERY",
        "previous_domain": "311",
        "previous_capability_intent": "QUERY_311",
        "pending_clarification_type": None,
        "pending_clarification_domain": None,
        "canonical_parameters": canonical,
        "previous_result_summary": copy.deepcopy(dict(summary)),
    })


def _dialogue_context_after_non_tool(
    previous: Optional[Mapping[str, Any]],
    *,
    plan: SemanticPlan,
    guarded: GuardedSemanticPlan,
    status: TurnServiceStatus,
) -> Dict[str, Any]:
    current = _dialogue_base(previous)
    if status is TurnServiceStatus.COMPLETED:
        kind = (
            "RESPONSE_REPHRASE"
            if plan.response_kind is DirectResponseKind.REPHRASE_TRUSTED_SUMMARY
            else "SMALL_TALK"
        )
        return validate_dialogue_context({
            **current,
            "previous_turn_kind": kind,
            "previous_domain": current.get("previous_domain"),
            "previous_capability_intent": current.get("previous_capability_intent"),
            "pending_clarification_type": None,
            "pending_clarification_domain": None,
        })
    missing = plan.missing_information or MissingInformation.USER_MEANING
    domain = _dialogue_domain_for_plan(plan)
    return validate_dialogue_context({
        **current,
        "previous_turn_kind": "AMBIGUOUS",
        "previous_domain": domain,
        "previous_capability_intent": "NONE",
        "pending_clarification_type": f"SEMANTIC_{missing.value}",
        "pending_clarification_domain": domain,
    })


def _dialogue_domain_for_plan(plan: SemanticPlan) -> str:
    domain = plan.tasks[0].domain if plan.tasks else None
    return {
        SemanticDomain.EVENTS: "EVENTS",
        SemanticDomain.SERVICE_REQUESTS: "311",
        SemanticDomain.CRIME: "CRIME_AND_SAFETY",
    }.get(domain, "GENERAL")


__all__ = [
    "ModelLedTurnService",
    "TurnRequest",
    "TurnResult",
    "TurnServiceStatus",
]
