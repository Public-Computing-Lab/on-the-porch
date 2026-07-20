"""Provider-neutral, tool-free evaluation for compact semantic planning."""

from __future__ import annotations

import copy
import json
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from answering_kernel import GlobalDeadline
from model_call_ledger import ModelCallLedger, ModelCallRole
from model_led_orchestrator import OrchestratorContractError, normalize_resident_input
from semantic_plan_compiler import SemanticCompilationError, compile_semantic_plan
from semantic_planner import (
    EvidenceNeed,
    GeographyMode,
    PublicSourceType,
    SemanticDomain,
    SemanticGoal,
    SemanticPlan,
    SemanticStrategy,
    SemanticTimeKind,
    StateIntent,
    SubjectKind,
    build_semantic_planner_prompt,
)


class EvaluationError(RuntimeError):
    """Raised when evaluation setup violates isolation or holdout policy."""


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    provider_model: Optional[str] = None


class OrchestratorProvider(Protocol):
    def generate(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        """Return one strict semantic-plan JSON object with no retry."""


class OrchestratorEvaluationProtocol(Protocol):
    def build_prompt(
        self,
        resident_input: Any,
        *,
        trusted_context: Mapping[str, Any],
        available_trusted_reference_tokens: Iterable[str],
    ) -> str: ...

    def parse_decision(
        self,
        raw: str,
        *,
        available_trusted_reference_tokens: Iterable[str],
    ) -> SemanticPlan: ...


class ProductionCompatibleEvaluationProtocol:
    """Compact local protocol; it remains disconnected from resident traffic."""

    def build_prompt(
        self,
        resident_input: Any,
        *,
        trusted_context: Mapping[str, Any],
        available_trusted_reference_tokens: Iterable[str],
    ) -> str:
        return build_semantic_planner_prompt(
            resident_input,
            trusted_context=trusted_context,
            available_trusted_reference_tokens=available_trusted_reference_tokens,
        )

    def parse_decision(
        self,
        raw: str,
        *,
        available_trusted_reference_tokens: Iterable[str],
    ) -> SemanticPlan:
        return SemanticPlan.from_json(
            raw,
            available_trusted_reference_tokens=available_trusted_reference_tokens,
        )


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    family_id: str
    category: str
    resident_message: str
    trusted_context: Mapping[str, Any]
    trusted_reference_tokens: Tuple[str, ...]
    expected: Mapping[str, Any]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    family_id: str
    category: str
    expected_strategy: str
    passed_schema: bool
    latency_ms: float
    model_calls: int
    scores: Mapping[str, Optional[float]]
    unsafe: bool
    direct_response_fact_safe: bool
    error_type: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    decision: Optional[Mapping[str, Any]] = None

    def to_safe_dict(self, *, include_decision: bool = False) -> Dict[str, Any]:
        value: Dict[str, Any] = {
            "case_id": self.case_id,
            "family_id": self.family_id,
            "category": self.category,
            "passed_schema": self.passed_schema,
            "latency_ms": round(self.latency_ms, 2),
            "model_calls": self.model_calls,
            "scores": dict(self.scores),
            "unsafe": self.unsafe,
            "direct_response_fact_safe": self.direct_response_fact_safe,
            "error_type": self.error_type,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
        if include_decision:
            value["decision"] = copy.deepcopy(self.decision)
        return value


@dataclass(frozen=True)
class EvaluationReport:
    dataset_name: str
    sealed: bool
    model_name: str
    case_results: Tuple[CaseResult, ...]
    metrics: Mapping[str, Any]
    category_metrics: Mapping[str, Mapping[str, Any]]

    def to_safe_dict(self, *, include_decisions: bool = False) -> Dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "sealed": self.sealed,
            "model_name": self.model_name,
            "metrics": copy.deepcopy(self.metrics),
            "category_metrics": copy.deepcopy(self.category_metrics),
            "cases": [item.to_safe_dict(include_decision=include_decisions) for item in self.case_results],
        }


def load_evaluation_dataset(
    path: Path | str,
    *,
    allow_sealed_holdout: bool = False,
) -> Tuple[str, bool, Tuple[EvaluationCase, ...]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise EvaluationError("evaluation dataset must be an object")
    required = {"schema_version", "dataset_name", "sealed", "defaults", "families"}
    if set(raw) != required or raw["schema_version"] != 1:
        raise EvaluationError("evaluation dataset has invalid top-level fields")
    sealed = raw["sealed"]
    if not isinstance(sealed, bool):
        raise EvaluationError("dataset sealed flag must be boolean")
    if sealed and not allow_sealed_holdout:
        raise EvaluationError("sealed holdout labels require explicit one-time evaluation access")
    dataset_name = _bounded_string(raw["dataset_name"], 80, "dataset name")
    defaults = _json_object(raw["defaults"], "dataset defaults")
    if set(defaults) != {"trusted_context", "trusted_reference_tokens"}:
        raise EvaluationError("dataset defaults have invalid fields")
    default_context = _json_object(defaults["trusted_context"], "default trusted context")
    default_tokens = _bounded_string_tuple(
        defaults["trusted_reference_tokens"], 0, 6, 96, "default reference tokens",
    )
    families = raw["families"]
    if not isinstance(families, list) or not families:
        raise EvaluationError("evaluation dataset requires paraphrase families")
    cases: List[EvaluationCase] = []
    family_ids: set[str] = set()
    for family in families:
        if not isinstance(family, Mapping) or not {
            "family_id", "category", "messages", "expected",
        }.issubset(family) or not set(family).issubset({
            "family_id", "category", "messages", "trusted_context",
            "trusted_reference_tokens", "expected",
        }):
            raise EvaluationError("evaluation family has invalid fields")
        family_id = _bounded_string(family["family_id"], 80, "family ID")
        if family_id in family_ids:
            raise EvaluationError("evaluation family IDs must be unique")
        family_ids.add(family_id)
        category = _bounded_string(family["category"], 80, "category")
        messages = family["messages"]
        if not isinstance(messages, list) or not 1 <= len(messages) <= 6:
            raise EvaluationError("paraphrase family must have one through six messages")
        context = _json_object(family.get("trusted_context", default_context), "trusted context")
        tokens = _bounded_string_tuple(
            family.get("trusted_reference_tokens", list(default_tokens)),
            0, 6, 96, "reference tokens",
        )
        expected = _expected_contract(family["expected"], available_tokens=tokens)
        for index, message in enumerate(messages, start=1):
            text = _bounded_string(message, 1200, "resident message")
            build_semantic_planner_prompt(
                normalize_resident_input(text),
                trusted_context=context,
                available_trusted_reference_tokens=tokens,
            )
            cases.append(EvaluationCase(
                case_id=f"{family_id}.{index}",
                family_id=family_id,
                category=category,
                resident_message=text,
                trusted_context=copy.deepcopy(context),
                trusted_reference_tokens=tokens,
                expected=copy.deepcopy(expected),
            ))
    return dataset_name, sealed, tuple(cases)


def assert_disjoint_paraphrase_families(
    development_cases: Iterable[EvaluationCase],
    holdout_cases: Iterable[EvaluationCase],
) -> None:
    overlap = {item.family_id for item in development_cases}.intersection(
        item.family_id for item in holdout_cases
    )
    if overlap:
        raise EvaluationError("development and holdout paraphrase families overlap")


def evaluate_orchestrator(
    provider: OrchestratorProvider,
    cases: Sequence[EvaluationCase],
    *,
    dataset_name: str,
    sealed: bool,
    model_name: str,
    per_case_deadline_seconds: float = 10.0,
    include_decisions: bool = False,
    protocol: Optional[OrchestratorEvaluationProtocol] = None,
) -> EvaluationReport:
    if not cases:
        raise EvaluationError("evaluation requires at least one case")
    selected_protocol = protocol or ProductionCompatibleEvaluationProtocol()
    results = tuple(
        _evaluate_case(
            provider,
            case,
            per_case_deadline_seconds=per_case_deadline_seconds,
            include_decision=include_decisions,
            protocol=selected_protocol,
        )
        for case in cases
    )
    return EvaluationReport(
        dataset_name=dataset_name,
        sealed=sealed,
        model_name=model_name,
        case_results=results,
        metrics=_aggregate_metrics(results),
        category_metrics={
            category: _aggregate_metrics(tuple(item for item in results if item.category == category))
            for category in sorted({item.category for item in results})
        },
    )


def score_decision(
    decision: SemanticPlan,
    expected: Mapping[str, Any],
    *,
    trusted_context: Mapping[str, Any],
    trusted_reference_tokens: Iterable[str],
) -> Tuple[Dict[str, Optional[float]], bool, bool]:
    expected_plan = _expected_plan(expected, trusted_reference_tokens)
    actual_domains = {item.domain.value for item in decision.tasks}
    expected_domains = {item.domain.value for item in expected_plan.tasks}
    actual_goals = {(item.domain.value, item.goal.value) for item in decision.tasks}
    expected_goals = {(item.domain.value, item.goal.value) for item in expected_plan.tasks}
    actual_needs = {
        (item.domain.value, need.value) for item in decision.tasks for need in item.evidence_needs
    }
    expected_needs = {
        (item.domain.value, need.value) for item in expected_plan.tasks for need in item.evidence_needs
    }
    actual_geography = {(item.domain.value, item.geography.mode.value) for item in decision.tasks}
    expected_geography = {(item.domain.value, item.geography.mode.value) for item in expected_plan.tasks}
    actual_time = {(item.domain.value, item.time.kind.value) for item in decision.tasks}
    expected_time = {(item.domain.value, item.time.kind.value) for item in expected_plan.tasks}
    actual_subjects = {
        (item.domain.value, item.subject.kind.value, (item.subject.text or "").casefold())
        for item in decision.tasks
    }
    expected_subjects = {
        (item.domain.value, item.subject.kind.value, (item.subject.text or "").casefold())
        for item in expected_plan.tasks
    }
    direct_safe = _direct_response_fact_safe_for_context(decision, trusted_context)
    unsafe = not direct_safe
    compiled_actual: set[str] = set()
    compiled_expected: set[str] = set()
    try:
        compiled_actual = {
            item.tool_id.value for item in compile_semantic_plan(
                decision,
                trusted_context=trusted_context,
                trusted_reference_tokens=trusted_reference_tokens,
            ).tool_requests
        }
        compiled_expected = {
            item.tool_id.value for item in compile_semantic_plan(
                expected_plan,
                trusted_context=trusted_context,
                trusted_reference_tokens=trusted_reference_tokens,
            ).tool_requests
        }
    except SemanticCompilationError:
        unsafe = True
    scores: Dict[str, Optional[float]] = {
        "strategy": float(decision.strategy is expected_plan.strategy),
        "relationship": float(decision.relationship is expected_plan.relationship),
        "domains": _set_score(actual_domains, expected_domains),
        "goals": _set_score(actual_goals, expected_goals),
        "evidence_needs": _set_score(actual_needs, expected_needs),
        "subjects": _set_score(actual_subjects, expected_subjects),
        "geography": _set_score(actual_geography, expected_geography),
        "time": _set_score(actual_time, expected_time),
        "clarification": float(
            bool(decision.clarification_question) == bool(expected["clarification_required"])
        ),
        "state_intent": float(decision.state_intent is expected_plan.state_intent),
        "trusted_reference": (
            float(decision.trusted_summary_reference == expected_plan.trusted_summary_reference)
            if expected_plan.trusted_summary_reference is not None else None
        ),
        "compiled_tool_set": float(compiled_actual == compiled_expected),
    }
    if decision.strategy is SemanticStrategy.GATHER_EVIDENCE and decision.direct_response:
        unsafe = True
    return scores, unsafe, direct_safe


def _evaluate_case(
    provider: OrchestratorProvider,
    case: EvaluationCase,
    *,
    per_case_deadline_seconds: float,
    include_decision: bool,
    protocol: OrchestratorEvaluationProtocol,
) -> CaseResult:
    context_before = json.dumps(case.trusted_context, sort_keys=True, ensure_ascii=True)
    deadline = GlobalDeadline.now(hard_ceiling_seconds=per_case_deadline_seconds)
    ledger = ModelCallLedger(deadline)
    started = time.perf_counter()
    response: Optional[ProviderResponse] = None
    decision: Optional[SemanticPlan] = None
    scores = _empty_scores()
    unsafe = False
    direct_safe = True
    error_type: Optional[str] = None
    try:
        prompt = protocol.build_prompt(
            normalize_resident_input(case.resident_message),
            trusted_context=copy.deepcopy(case.trusted_context),
            available_trusted_reference_tokens=case.trusted_reference_tokens,
        )
        reservation = ledger.reserve(ModelCallRole.ORCHESTRATOR)
        response = _bounded_provider_call(
            provider,
            prompt,
            timeout_seconds=min(reservation.available_seconds, deadline.remaining()),
        )
        decision = protocol.parse_decision(
            response.text,
            available_trusted_reference_tokens=case.trusted_reference_tokens,
        )
        scores, unsafe, direct_safe = score_decision(
            decision,
            case.expected,
            trusted_context=case.trusted_context,
            trusted_reference_tokens=case.trusted_reference_tokens,
        )
    except OrchestratorContractError:
        error_type = "SCHEMA_OR_SAFETY_FAILURE"
        unsafe = True
    except TimeoutError:
        error_type = "PROVIDER_TIMEOUT"
    except Exception:
        error_type = "PROVIDER_FAILURE"
    latency_ms = (time.perf_counter() - started) * 1000.0
    if context_before != json.dumps(case.trusted_context, sort_keys=True, ensure_ascii=True):
        raise EvaluationError("evaluation mutated frozen trusted state")
    return CaseResult(
        case_id=case.case_id,
        family_id=case.family_id,
        category=case.category,
        expected_strategy=case.expected["strategy"],
        passed_schema=decision is not None,
        latency_ms=latency_ms,
        model_calls=ledger.total_calls,
        scores=scores,
        unsafe=unsafe,
        direct_response_fact_safe=direct_safe,
        error_type=error_type,
        input_tokens=response.input_tokens if response else None,
        output_tokens=response.output_tokens if response else None,
        decision=decision.to_dict() if decision and include_decision else None,
    )


def _bounded_provider_call(
    provider: OrchestratorProvider,
    prompt: str,
    *,
    timeout_seconds: float,
) -> ProviderResponse:
    if timeout_seconds <= 0:
        raise TimeoutError("orchestrator evaluation deadline exhausted")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="semantic-plan-eval")
    future = executor.submit(provider.generate, prompt, timeout_seconds=timeout_seconds)
    try:
        result = future.result(timeout=timeout_seconds)
        if not isinstance(result, ProviderResponse):
            raise TypeError("provider returned an invalid response contract")
        return result
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError("orchestrator provider exceeded its deadline") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _expected_contract(raw: Any, *, available_tokens: Tuple[str, ...]) -> Mapping[str, Any]:
    value = _json_object(raw, "expected behavior")
    required = {
        "strategy", "relationship", "tasks", "trusted_summary_reference",
        "state_intent", "clarification_required", "forbidden_behavior",
    }
    if set(value) != required:
        raise EvaluationError("expected semantic behavior has invalid fields")
    try:
        _expected_plan(value, available_tokens)
    except OrchestratorContractError as exc:
        raise EvaluationError("expected semantic behavior is invalid") from exc
    if not isinstance(value["clarification_required"], bool):
        raise EvaluationError("expected clarification flag is invalid")
    _bounded_string_tuple(value["forbidden_behavior"], 0, 8, 80, "forbidden behavior")
    return value


def _expected_plan(
    expected: Mapping[str, Any],
    available_tokens: Iterable[str],
) -> SemanticPlan:
    strategy = expected["strategy"]
    mapping = {
        "schema_version": 1,
        "strategy": strategy,
        "relationship": expected["relationship"],
        "user_goal": "Represent the expected semantic behavior.",
        "tasks": copy.deepcopy(expected["tasks"]),
        "trusted_summary_reference": expected["trusted_summary_reference"],
        "state_intent": expected["state_intent"],
        "direct_response": "Understood." if strategy == "DIRECT_RESPONSE" else None,
        "clarification_question": "What information should I use?"
        if strategy == "ASK_CLARIFICATION" else None,
        "confidence": 1.0,
    }
    return SemanticPlan.from_mapping(
        mapping,
        available_trusted_reference_tokens=available_tokens,
    )


_FACT_MARKER = re.compile(
    r"(?:\$\s*\d+(?:\.\d+)?|\b\d+(?:\.\d+)?\s*%|\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b|\b\d+(?:\.\d+)?\b)",
    re.IGNORECASE,
)


def _direct_response_fact_safe_for_context(
    decision: SemanticPlan,
    trusted_context: Mapping[str, Any],
) -> bool:
    if decision.strategy is not SemanticStrategy.DIRECT_RESPONSE:
        return True
    if decision.trusted_summary_reference is None:
        return not bool(_FACT_MARKER.search(decision.direct_response or ""))
    summary = trusted_context.get("previous_result_summary")
    if not isinstance(summary, Mapping):
        return False
    if summary.get("token") != decision.trusted_summary_reference or not isinstance(summary.get("text"), str):
        return False
    response_markers = {item.casefold() for item in _FACT_MARKER.findall(decision.direct_response or "")}
    summary_markers = {item.casefold() for item in _FACT_MARKER.findall(summary["text"])}
    return response_markers.issubset(summary_markers)


def _aggregate_metrics(results: Sequence[CaseResult]) -> Dict[str, Any]:
    if not results:
        return {}
    metric_values: Dict[str, Optional[float]] = {}
    for name in _empty_scores():
        values = [float(item.scores[name]) for item in results if item.scores[name] is not None]
        metric_values[f"{name}_accuracy"] = round(statistics.fmean(values), 4) if values else None
    latencies = sorted(item.latency_ms for item in results)
    token_inputs = [item.input_tokens for item in results if item.input_tokens is not None]
    token_outputs = [item.output_tokens for item in results if item.output_tokens is not None]
    return {
        "case_count": len(results),
        **metric_values,
        "strategy_macro_accuracy": _strategy_macro_accuracy(results),
        "schema_compliance": round(sum(item.passed_schema for item in results) / len(results), 4),
        "schema_failure_rate": round(sum(not item.passed_schema for item in results) / len(results), 4),
        "unsafe_decision_rate": round(sum(item.unsafe for item in results) / len(results), 4),
        "direct_response_fact_safety_failure_count": sum(
            not item.direct_response_fact_safe for item in results
        ),
        "context_isolation_rate": round(
            sum((item.scores.get("state_intent") or 0.0) == 1.0 for item in results) / len(results), 4,
        ),
        "model_calls_per_case": round(statistics.fmean(item.model_calls for item in results), 4),
        "latency_ms_p50": round(_percentile(latencies, 0.50), 2),
        "latency_ms_p90": round(_percentile(latencies, 0.90), 2),
        "latency_ms_p95": round(_percentile(latencies, 0.95), 2),
        "estimated_input_tokens_total": sum(token_inputs) if token_inputs else None,
        "estimated_output_tokens_total": sum(token_outputs) if token_outputs else None,
        "provider_failure_count": sum(item.error_type == "PROVIDER_FAILURE" for item in results),
        "provider_timeout_count": sum(item.error_type == "PROVIDER_TIMEOUT" for item in results),
        "one_call_compliance": round(sum(item.model_calls == 1 for item in results) / len(results), 4),
    }


def _strategy_macro_accuracy(results: Sequence[CaseResult]) -> float:
    by_strategy: Dict[str, List[float]] = {}
    for item in results:
        by_strategy.setdefault(item.expected_strategy, []).append(float(item.scores.get("strategy") or 0.0))
    return round(statistics.fmean(statistics.fmean(values) for values in by_strategy.values()), 4)


def _empty_scores() -> Dict[str, Optional[float]]:
    return {
        "strategy": 0.0,
        "relationship": 0.0,
        "domains": 0.0,
        "goals": 0.0,
        "evidence_needs": 0.0,
        "subjects": 0.0,
        "geography": 0.0,
        "time": 0.0,
        "clarification": 0.0,
        "state_intent": 0.0,
        "trusted_reference": None,
        "compiled_tool_set": 0.0,
    }


def _set_score(actual: set[Any], expected: set[Any]) -> float:
    if actual == expected:
        return 1.0
    union = actual | expected
    return len(actual & expected) / len(union) if union else 1.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _bounded_string(value: Any, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise EvaluationError(f"{label} is invalid")
    return value


def _bounded_string_tuple(
    raw: Any, minimum: int, maximum: int, max_chars: int, label: str,
) -> Tuple[str, ...]:
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise EvaluationError(f"{label} has invalid size")
    values = tuple(_bounded_string(item, max_chars, label) for item in raw)
    if len(values) != len(set(values)):
        raise EvaluationError(f"{label} contains duplicates")
    return values


def _json_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationError(f"{label} must be an object")
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > 16_000:
        raise EvaluationError(f"{label} exceeds its bound")
    return json.loads(encoded)


__all__ = [
    "CaseResult",
    "EvaluationCase",
    "EvaluationError",
    "EvaluationReport",
    "OrchestratorProvider",
    "OrchestratorEvaluationProtocol",
    "ProductionCompatibleEvaluationProtocol",
    "ProviderResponse",
    "assert_disjoint_paraphrase_families",
    "evaluate_orchestrator",
    "load_evaluation_dataset",
    "score_decision",
]
