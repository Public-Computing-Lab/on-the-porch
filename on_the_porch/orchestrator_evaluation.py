"""Provider-neutral, tool-free evaluation for the model-led orchestrator."""

from __future__ import annotations

import copy
import json
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from answering_kernel import GlobalDeadline
from civic_tool_catalog import (
    CivicDomain,
    SemanticPeriod,
    ToolContractError,
    ToolId,
    validate_tool_domain_compatibility,
)
from model_call_ledger import ModelCallLedger, ModelCallRole
from model_led_orchestrator import (
    OrchestratorContractError,
    OrchestratorDecision,
    build_orchestrator_prompt,
    direct_response_is_fact_safe,
    normalize_resident_input,
)


class EvaluationError(RuntimeError):
    """Raised when evaluation setup would violate isolation or holdout policy."""


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    provider_model: Optional[str] = None


class OrchestratorProvider(Protocol):
    def generate(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        """Return one strict JSON decision with no retry."""


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
            "cases": [
                item.to_safe_dict(include_decision=include_decisions)
                for item in self.case_results
            ],
        }


def load_evaluation_dataset(
    path: Path | str,
    *,
    allow_sealed_holdout: bool = False,
) -> Tuple[str, bool, Tuple[EvaluationCase, ...]]:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
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
    case_ids: set[str] = set()
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
        context = _json_object(
            family.get("trusted_context", default_context), "trusted context",
        )
        tokens = _bounded_string_tuple(
            family.get("trusted_reference_tokens", list(default_tokens)),
            0, 6, 96, "reference tokens",
        )
        expected = _expected_contract(family["expected"])
        if not set(expected["required_trusted_reference_tokens"]).issubset(tokens):
            raise EvaluationError("expected trusted references are unavailable to the case")
        for index, message in enumerate(messages, start=1):
            text = _bounded_string(message, 1200, "resident message")
            build_orchestrator_prompt(
                normalize_resident_input(text),
                trusted_context=context,
                available_trusted_reference_tokens=tokens,
            )
            case_id = f"{family_id}.{index}"
            if case_id in case_ids:
                raise EvaluationError("evaluation case IDs must be unique")
            case_ids.add(case_id)
            cases.append(EvaluationCase(
                case_id=case_id,
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
    per_case_deadline_seconds: float = 8.0,
    include_decisions: bool = False,
) -> EvaluationReport:
    if not cases:
        raise EvaluationError("evaluation requires at least one case")
    results = tuple(
        _evaluate_case(
            provider,
            case,
            per_case_deadline_seconds=per_case_deadline_seconds,
            include_decision=include_decisions,
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
    decision: OrchestratorDecision,
    expected: Mapping[str, Any],
) -> Tuple[Dict[str, Optional[float]], bool]:
    actual_domains = {item.value for item in decision.domains}
    expected_domains = set(expected["domains"])
    actual_tools = {item.tool_id.value for item in decision.tool_requests}
    expected_tools = {item["tool_id"] for item in expected["tools"]}
    expected_critical = {
        item["tool_id"]: item["critical_arguments"] for item in expected["tools"]
    }
    actual_by_tool = {item.tool_id.value: item.arguments for item in decision.tool_requests}
    critical = 1.0
    for tool_id, arguments in expected_critical.items():
        actual = actual_by_tool.get(tool_id)
        if actual is None or not _mapping_contains(actual, arguments):
            critical = 0.0
            break
    clarification_fields = set(decision.answer_requirements.clarification_fields)
    expected_fields = set(expected["required_clarification_fields"])
    allowed_changes = set(expected["allowed_state_changes"])
    actual_changes = _state_changes(decision)
    scores: Dict[str, Optional[float]] = {
        "strategy": float(decision.strategy.value == expected["strategy"]),
        "relationship": float(decision.relationship.value == expected["relationship"]),
        "domains": _set_score(actual_domains, expected_domains),
        "exact_tool_set": float(actual_tools == expected_tools),
        "critical_arguments": critical,
        "clarification": float(
            clarification_fields == expected_fields
            and bool(decision.clarification_question) == bool(expected_fields)
        ),
        "state_context": float(actual_changes.issubset(allowed_changes)),
        "geography_time": _geography_time_score(decision, expected),
        "trusted_references": (
            float(set(expected["required_trusted_reference_tokens"]).issubset(
                decision.trusted_reference_tokens
            ))
            if expected["required_trusted_reference_tokens"]
            else None
        ),
        "direct_response_basis": (
            float(decision.direct_response_basis.value == expected["direct_response_basis"])
            if expected["direct_response_basis"] is not None
            else None
        ),
    }
    unsafe = not direct_response_is_fact_safe(decision)
    if decision.strategy.value == "EXECUTE_TOOLS" and decision.direct_response:
        unsafe = True
    return scores, unsafe


def _evaluate_case(
    provider: OrchestratorProvider,
    case: EvaluationCase,
    *,
    per_case_deadline_seconds: float,
    include_decision: bool,
) -> CaseResult:
    context_before = json.dumps(case.trusted_context, sort_keys=True, ensure_ascii=True)
    deadline = GlobalDeadline.now(hard_ceiling_seconds=per_case_deadline_seconds)
    ledger = ModelCallLedger(deadline)
    started = time.perf_counter()
    response: Optional[ProviderResponse] = None
    decision: Optional[OrchestratorDecision] = None
    scores: Dict[str, Optional[float]] = _empty_scores()
    unsafe = False
    direct_response_fact_safe = True
    error_type: Optional[str] = None
    try:
        normalized = normalize_resident_input(case.resident_message)
        prompt = build_orchestrator_prompt(
            normalized,
            trusted_context=copy.deepcopy(case.trusted_context),
            available_trusted_reference_tokens=case.trusted_reference_tokens,
        )
        reservation = ledger.reserve(ModelCallRole.ORCHESTRATOR)
        response = _bounded_provider_call(
            provider,
            prompt,
            timeout_seconds=min(reservation.available_seconds, deadline.remaining()),
        )
        decision = OrchestratorDecision.from_json(
            response.text,
            available_trusted_reference_tokens=case.trusted_reference_tokens,
        )
        scores, unsafe = score_decision(decision, case.expected)
        direct_response_fact_safe = _direct_response_fact_safe_for_context(
            decision,
            case.trusted_context,
        )
        unsafe = unsafe or not direct_response_fact_safe
    except OrchestratorContractError:
        error_type = "SCHEMA_OR_SAFETY_FAILURE"
        unsafe = True
    except TimeoutError:
        error_type = "PROVIDER_TIMEOUT"
    except Exception:
        error_type = "PROVIDER_FAILURE"
    latency_ms = (time.perf_counter() - started) * 1000.0
    context_after = json.dumps(case.trusted_context, sort_keys=True, ensure_ascii=True)
    if context_before != context_after:
        raise EvaluationError("evaluation mutated frozen trusted state")
    return CaseResult(
        case_id=case.case_id,
        family_id=case.family_id,
        category=case.category,
        expected_strategy=str(case.expected["strategy"]),
        passed_schema=decision is not None,
        latency_ms=latency_ms,
        model_calls=ledger.total_calls,
        scores=scores,
        unsafe=unsafe,
        direct_response_fact_safe=direct_response_fact_safe,
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
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orchestrator-eval")
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


def _aggregate_metrics(results: Sequence[CaseResult]) -> Dict[str, Any]:
    if not results:
        return {}
    metric_names = tuple(_empty_scores())
    scores: Dict[str, float] = {}
    for name in metric_names:
        values = [float(item.scores[name]) for item in results if item.scores[name] is not None]
        scores[f"{name}_accuracy"] = round(statistics.fmean(values), 4) if values else None
    latencies = sorted(item.latency_ms for item in results)
    token_inputs = [item.input_tokens for item in results if item.input_tokens is not None]
    token_outputs = [item.output_tokens for item in results if item.output_tokens is not None]
    return {
        "case_count": len(results),
        **scores,
        "strategy_macro_accuracy": _strategy_macro_accuracy(results),
        "schema_failure_rate": round(sum(not item.passed_schema for item in results) / len(results), 4),
        "unsafe_decision_rate": round(sum(item.unsafe for item in results) / len(results), 4),
        "direct_response_fact_safety_failure_count": sum(
            not item.direct_response_fact_safe for item in results
        ),
        "context_isolation_rate": round(
            sum((item.scores.get("state_context") or 0.0) == 1.0 for item in results) / len(results), 4,
        ),
        "model_calls_per_case": round(statistics.fmean(item.model_calls for item in results), 4),
        "latency_ms_p50": round(_percentile(latencies, 0.50), 2),
        "latency_ms_p90": round(_percentile(latencies, 0.90), 2),
        "latency_ms_p95": round(_percentile(latencies, 0.95), 2),
        "estimated_input_tokens_total": sum(token_inputs) if token_inputs else None,
        "estimated_output_tokens_total": sum(token_outputs) if token_outputs else None,
        "provider_failure_count": sum(item.error_type == "PROVIDER_FAILURE" for item in results),
        "provider_timeout_count": sum(item.error_type == "PROVIDER_TIMEOUT" for item in results),
    }


def _strategy_macro_accuracy(results: Sequence[CaseResult]) -> float:
    by_strategy: Dict[str, List[float]] = {}
    for item in results:
        by_strategy.setdefault(item.expected_strategy, []).append(
            float(item.scores.get("strategy") or 0.0)
        )
    return round(statistics.fmean(statistics.fmean(values) for values in by_strategy.values()), 4)


_FACT_MARKER = re.compile(
    r"(?:\$\s*\d+(?:\.\d+)?|\b\d+(?:\.\d+)?\s*%|\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b|\b\d+(?:\.\d+)?\b)",
    re.IGNORECASE,
)


def _direct_response_fact_safe_for_context(
    decision: OrchestratorDecision,
    trusted_context: Mapping[str, Any],
) -> bool:
    if decision.strategy.value != "DIRECT_RESPONSE":
        return True
    if not direct_response_is_fact_safe(decision):
        return False
    if decision.direct_response_basis.value != "TRUSTED_SUMMARY_REPHRASE":
        return True
    summary = trusted_context.get("previous_result_summary")
    if not isinstance(summary, Mapping):
        return False
    token = summary.get("token")
    text = summary.get("text")
    if token not in decision.trusted_reference_tokens or not isinstance(text, str):
        return False
    response_markers = {item.casefold() for item in _FACT_MARKER.findall(decision.direct_response or "")}
    summary_markers = {item.casefold() for item in _FACT_MARKER.findall(text)}
    return response_markers.issubset(summary_markers)


def _expected_contract(raw: Any) -> Mapping[str, Any]:
    value = _json_object(raw, "expected behavior")
    required = {
        "strategy", "relationship", "domains", "tools",
        "required_clarification_fields", "allowed_state_changes",
        "geography_scope", "semantic_period", "forbidden_behavior",
    }
    optional = {"required_trusted_reference_tokens", "direct_response_basis"}
    if not required.issubset(value) or not set(value).issubset(required | optional):
        raise EvaluationError("expected behavior has invalid fields")
    value = dict(value)
    value.setdefault("required_trusted_reference_tokens", [])
    value.setdefault("direct_response_basis", None)
    if value["strategy"] not in {"DIRECT_RESPONSE", "ASK_CLARIFICATION", "EXECUTE_TOOLS"}:
        raise EvaluationError("expected strategy is invalid")
    if value["relationship"] not in {
        "STANDALONE", "FOLLOW_UP", "REFINEMENT", "CORRECTION", "NEW_TOPIC", "SMALL_TALK",
    }:
        raise EvaluationError("expected relationship is invalid")
    domains = _bounded_string_tuple(value["domains"], 0, 3, 64, "expected domains")
    try:
        parsed_domains = tuple(CivicDomain(domain) for domain in domains)
    except ValueError as exc:
        raise EvaluationError("expected domains contain an unknown value") from exc
    if not isinstance(value["tools"], list) or len(value["tools"]) > 4:
        raise EvaluationError("expected tools are invalid")
    tool_ids = []
    trusted_references = _bounded_string_tuple(
        value["required_trusted_reference_tokens"], 0, 6, 96,
        "required trusted reference tokens",
    )
    for tool in value["tools"]:
        if not isinstance(tool, Mapping) or set(tool) != {"tool_id", "critical_arguments"}:
            raise EvaluationError("expected tool contract is invalid")
        _bounded_string(tool["tool_id"], 80, "expected tool ID")
        try:
            ToolId(tool["tool_id"])
        except ValueError as exc:
            raise EvaluationError("expected tool is not registered") from exc
        critical_arguments = _json_object(tool["critical_arguments"], "critical arguments")
        try:
            validate_tool_domain_compatibility(
                ToolId(tool["tool_id"]),
                critical_arguments,
                parsed_domains,
            )
        except ToolContractError as exc:
            raise EvaluationError("expected tool is incompatible with expected domains") from exc
        tool_ids.append(tool["tool_id"])
    if len(tool_ids) != len(set(tool_ids)):
        raise EvaluationError("expected tool IDs must be unique")
    if (value["strategy"] == "EXECUTE_TOOLS") != bool(tool_ids):
        raise EvaluationError("expected strategy and tools are inconsistent")
    clarification_fields = _bounded_string_tuple(
        value["required_clarification_fields"], 0, 4, 48, "clarification fields",
    )
    if not set(clarification_fields).issubset({"location", "time_scope", "entity", "topic"}):
        raise EvaluationError("required clarification field is unknown")
    if (value["strategy"] == "ASK_CLARIFICATION") != bool(clarification_fields):
        raise EvaluationError("expected clarification fields are inconsistent with strategy")
    state_changes = _bounded_string_tuple(
        value["allowed_state_changes"], 0, 5, 48, "allowed state changes",
    )
    if not set(state_changes).issubset({
        "ACTIVE_TOPIC", "ACTIVE_ENTITY", "LOCATION", "TIME_SCOPE", "FILTERS",
    }):
        raise EvaluationError("allowed state change is unknown")
    if value["geography_scope"] is not None:
        _bounded_string(value["geography_scope"], 48, "expected geography scope")
        if value["geography_scope"] not in {
            "CITY", "NEIGHBORHOOD", "ADDRESS", "INTERSECTION", "RETAIN_TRUSTED",
        }:
            raise EvaluationError("expected geography scope is unknown")
    if value["semantic_period"] is not None:
        _bounded_string(value["semantic_period"], 48, "expected semantic period")
        try:
            SemanticPeriod(value["semantic_period"])
        except ValueError as exc:
            raise EvaluationError("expected semantic period is unknown") from exc
    if value["direct_response_basis"] not in {
        None, "NONE", "CONVERSATION_ONLY", "TRUSTED_SUMMARY_REPHRASE",
    }:
        raise EvaluationError("expected direct response basis is unknown")
    _bounded_string_tuple(value["forbidden_behavior"], 0, 8, 80, "forbidden behavior")
    return value


def _state_changes(decision: OrchestratorDecision) -> set[str]:
    state = decision.state_proposal
    changed = {item.value for item in state.clear}
    if state.active_topic is not None:
        changed.add("ACTIVE_TOPIC")
    if state.active_entity_token is not None:
        changed.add("ACTIVE_ENTITY")
    if state.location_id is not None:
        changed.add("LOCATION")
    if state.time_scope is not None:
        changed.add("TIME_SCOPE")
    if state.filters:
        changed.add("FILTERS")
    return changed


def _geography_time_score(
    decision: OrchestratorDecision,
    expected: Mapping[str, Any],
) -> Optional[float]:
    geography = decision.geography_reference.scope.value if decision.geography_reference else None
    period = decision.semantic_time_scope.period.value if decision.semantic_time_scope else None
    comparisons = []
    if expected["geography_scope"] is not None:
        comparisons.append(geography == expected["geography_scope"])
    if expected["semantic_period"] is not None:
        comparisons.append(period == expected["semantic_period"])
    return float(all(comparisons)) if comparisons else None


def _mapping_contains(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    for key, value in expected.items():
        if key not in actual:
            return False
        if isinstance(value, Mapping):
            if not isinstance(actual[key], Mapping) or not _mapping_contains(actual[key], value):
                return False
        elif isinstance(value, list) and isinstance(actual[key], list):
            if all(not isinstance(item, (Mapping, list)) for item in value + actual[key]):
                if set(actual[key]) != set(value):
                    return False
            elif actual[key] != value:
                return False
        elif actual[key] != value:
            return False
    return True


def _set_score(actual: set[str], expected: set[str]) -> float:
    if actual == expected:
        return 1.0
    union = actual | expected
    return len(actual & expected) / len(union) if union else 1.0


def _empty_scores() -> Dict[str, Optional[float]]:
    return {
        "strategy": 0.0,
        "relationship": 0.0,
        "domains": 0.0,
        "exact_tool_set": 0.0,
        "critical_arguments": 0.0,
        "clarification": 0.0,
        "state_context": 0.0,
        "geography_time": None,
        "trusted_references": None,
        "direct_response_basis": None,
    }


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


def _bounded_string_tuple(raw: Any, minimum: int, maximum: int, max_chars: int, label: str) -> Tuple[str, ...]:
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
    "ProviderResponse",
    "assert_disjoint_paraphrase_families",
    "evaluate_orchestrator",
    "load_evaluation_dataset",
    "score_decision",
]
