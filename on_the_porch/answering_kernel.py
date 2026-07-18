"""Small shared contracts used by bounded structured civic answering paths.

This module intentionally contains no transport, database, or model code.  It
keeps the first structured civic slice explicit about its deadline, plan,
evidence, and answer mode without creating another chat stack.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


class PlannerSource(str, Enum):
    RULE = "RULE"
    LLM = "LLM"


class EvidenceStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    STALE_BUT_USABLE = "STALE_BUT_USABLE"
    PARTIALLY_COVERED = "PARTIALLY_COVERED"
    OUTSIDE_COVERAGE = "OUTSIDE_COVERAGE"
    NO_MATCHING_RECORDS = "NO_MATCHING_RECORDS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"


class AnswerMode(str, Enum):
    POLICY_FIXED = "POLICY_FIXED"
    REPLAY = "REPLAY"
    CLARIFICATION = "CLARIFICATION"
    DETERMINISTIC_DIRECT = "DETERMINISTIC_DIRECT"
    DETERMINISTIC_ENRICHED = "DETERMINISTIC_ENRICHED"
    EVIDENCE_BOUNDED_SYNTHESIS = "EVIDENCE_BOUNDED_SYNTHESIS"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"


class HybridRuntimeMode(str, Enum):
    DISABLED = "disabled"
    AUTO = "auto"
    REQUIRED = "required"


@dataclass(frozen=True)
class ModelRuntimePolicy:
    """Provider-neutral policy for bounded planner/synthesis use."""

    mode: HybridRuntimeMode
    provider_class: str
    provider_available: bool
    planner_enabled: bool
    synthesis_enabled: bool
    max_planner_calls: int = 1
    max_synthesis_calls: int = 1
    max_repair_calls: int = 1

    def __post_init__(self) -> None:
        if not self.provider_class or len(self.provider_class) > 64:
            raise ValueError("provider class must be a bounded diagnostic label")
        if any(value not in {0, 1} for value in (
            self.max_planner_calls,
            self.max_synthesis_calls,
            self.max_repair_calls,
        )):
            raise ValueError("structured model call limits must be zero or one")
        if not self.provider_available and (self.planner_enabled or self.synthesis_enabled):
            raise ValueError("model features cannot be active without a provider")

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "provider_class": self.provider_class,
            "provider_available": self.provider_available,
            "planner_enabled": self.planner_enabled,
            "synthesis_enabled": self.synthesis_enabled,
            "max_planner_calls": self.max_planner_calls,
            "max_synthesis_calls": self.max_synthesis_calls,
            "max_repair_calls": self.max_repair_calls,
        }


@dataclass(frozen=True)
class GlobalDeadline:
    """One monotonic deadline shared by every stage of a civic answer."""

    deadline: float
    persistence_reserve_seconds: float = 1.0

    @classmethod
    def from_started_at(
        cls,
        started_at: float,
        *,
        hard_ceiling_seconds: float = 15.0,
        persistence_reserve_seconds: float = 1.0,
    ) -> "GlobalDeadline":
        return cls(
            deadline=float(started_at) + max(1.0, float(hard_ceiling_seconds)),
            persistence_reserve_seconds=max(0.25, float(persistence_reserve_seconds)),
        )

    @classmethod
    def now(cls, *, hard_ceiling_seconds: float = 15.0) -> "GlobalDeadline":
        return cls.from_started_at(time.perf_counter(), hard_ceiling_seconds=hard_ceiling_seconds)

    def remaining(self, *, reserve_for_persistence: bool = False) -> float:
        remaining = self.deadline - time.perf_counter()
        if reserve_for_persistence:
            remaining -= self.persistence_reserve_seconds
        return max(0.0, remaining)

    def expired(self, *, reserve_for_persistence: bool = False) -> bool:
        return self.remaining(reserve_for_persistence=reserve_for_persistence) <= 0.0


def bounded_strings(values: Iterable[Any], *, limit: int, maximum_length: int = 96) -> Tuple[str, ...]:
    """Return a stable, bounded sequence of non-empty diagnostic-safe values."""
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()[:maximum_length]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


@dataclass(frozen=True)
class StrictToolPlan:
    """Validated, bounded plan for one deterministic structured civic request."""

    domain: str
    intent: str
    issue_family: Optional[str]
    geography: Mapping[str, Any]
    period: Mapping[str, Any]
    outputs: Tuple[str, ...]
    tools: Tuple[str, ...]
    needs_clarification: bool
    clarification_message: Optional[str]
    confidence: float
    planner_source: PlannerSource

    def __post_init__(self) -> None:
        if self.domain != "311":
            raise ValueError("structured civic plan must target the 311 domain")
        if not self.intent or len(self.intent) > 64:
            raise ValueError("plan intent must be bounded")
        if self.issue_family is not None and len(self.issue_family) > 64:
            raise ValueError("issue family must be bounded")
        if not isinstance(self.geography, Mapping) or not isinstance(self.period, Mapping):
            raise ValueError("plan geography and period must be objects")
        if len(self.outputs) > 7 or len(self.tools) > 7:
            raise ValueError("tool plan exceeds the bounded registry")
        if len(set(self.outputs)) != len(self.outputs) or len(set(self.tools)) != len(self.tools):
            raise ValueError("tool plan values must be unique")
        if self.needs_clarification and not self.clarification_message:
            raise ValueError("clarification plans require a resident-facing message")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("plan confidence must be between zero and one")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "intent": self.intent,
            "issue_family": self.issue_family,
            "geography": dict(self.geography),
            "period": dict(self.period),
            "outputs": list(self.outputs),
            "tools": list(self.tools),
            "needs_clarification": self.needs_clarification,
            "confidence": round(float(self.confidence), 3),
            "planner_source": self.planner_source.value,
        }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    required: bool
    timeout_seconds: float
    max_items: int

    def __post_init__(self) -> None:
        if not self.name or self.timeout_seconds <= 0 or self.max_items <= 0:
            raise ValueError("tool registry values must be bounded")


@dataclass
class EvidenceBundle:
    """Compact factual contract between deterministic tools and composition."""

    question_scope: Mapping[str, Any]
    issue_family: Optional[str]
    geography: Mapping[str, Any]
    requested_period: Mapping[str, Any]
    executed_period: Mapping[str, Any]
    freshness: Mapping[str, Any]
    coverage: Mapping[str, Any]
    facts: Dict[str, Any] = field(default_factory=dict)
    model_facts: Optional[Dict[str, Any]] = None
    tool_results: Dict[str, Any] = field(default_factory=dict)
    limitations: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    source_records: List[Dict[str, Any]] = field(default_factory=list)
    evidence_status: EvidenceStatus = EvidenceStatus.INSUFFICIENT_EVIDENCE
    partial_failures: List[str] = field(default_factory=list)

    def bounded_for_model(self) -> Dict[str, Any]:
        """Small resident-safe evidence payload for one optional synthesis call."""

        def model_period(raw: Mapping[str, Any]) -> Dict[str, Any]:
            value = {
                key: raw[key]
                for key in ("kind", "label", "start_date")
                if raw.get(key) is not None
            }
            end_exclusive = raw.get("end_date_exclusive")
            if end_exclusive:
                try:
                    value["end_date_inclusive"] = (
                        date.fromisoformat(str(end_exclusive)) - timedelta(days=1)
                    ).isoformat()
                except ValueError:
                    pass
            return value

        def public_value(raw: Any) -> Any:
            if isinstance(raw, Mapping):
                cleaned: Dict[str, Any] = {}
                for key, value in raw.items():
                    public_key = str(key)
                    if public_key in {"source_key", "family"}:
                        continue
                    if public_key == "end_date_exclusive":
                        try:
                            cleaned["end_date_inclusive"] = (
                                date.fromisoformat(str(value)) - timedelta(days=1)
                            ).isoformat()
                        except ValueError:
                            pass
                        continue
                    cleaned[public_key] = public_value(value)
                return cleaned
            if isinstance(raw, (list, tuple)):
                return [public_value(item) for item in raw]
            return raw

        def public_facts(raw: Mapping[str, Any]) -> Dict[str, Any]:
            cleaned: Dict[str, Any] = {}
            for key, value in raw.items():
                public_key = str(key)
                if public_key.startswith("get_311_"):
                    public_key = public_key[len("get_311_"):]
                elif public_key == "compare_311_periods":
                    public_key = "period_comparison"
                if public_key == "source_freshness":
                    continue
                cleaned[public_key] = public_value(value)
            return cleaned

        safe_sources = [
            {
                key: model_period(value)
                if key == "covered_period" and isinstance(value, Mapping)
                else value
                for key, value in dict(source).items()
                if key in {"type", "source", "authority", "data_through_date", "covered_period"}
            }
            for source in self.source_records[:2]
        ]
        safe_freshness = {
            key: value
            for key, value in dict(self.freshness).items()
            if key in {
                "data_through_date",
                "freshness_status",
                "coverage_status",
                "source_label",
                "source_authority",
            }
        }
        return {
            "scope": dict(self.question_scope),
            "issue_family": (
                self.question_scope.get("issue_label")
                or (str(self.issue_family).replace("_", " ").lower() if self.issue_family else None)
            ),
            "geography": dict(self.geography),
            "requested_period": model_period(self.requested_period),
            "executed_period": model_period(self.executed_period),
            "freshness": safe_freshness,
            "coverage": {
                key: value
                for key, value in dict(self.coverage).items()
                if key in {"status", "data_through_date"}
            },
            "facts": public_facts(
                self.model_facts if self.model_facts is not None else self.facts
            ),
            "limitations": self.limitations[:5],
            "sources": safe_sources,
            "evidence_status": self.evidence_status.value,
        }


@dataclass(frozen=True)
class CivicTelemetry:
    planner_source: PlannerSource
    planner_ms: int = 0
    tool_execution_ms: int = 0
    tool_count: int = 0
    synthesis_ms: int = 0
    validation_ms: int = 0
    repair_ms: int = 0
    persistence_ms: int = 0
    total_latency_ms: int = 0
    llm_call_count: int = 0
    planner_call_count: int = 0
    synthesis_call_count: int = 0
    repair_call_count: int = 0
    embedding_call_count: int = 0
    evidence_status: EvidenceStatus = EvidenceStatus.INSUFFICIENT_EVIDENCE
    answer_mode: AnswerMode = AnswerMode.DETERMINISTIC_FALLBACK
    fallback_used: bool = False
    timeout_stage: Optional[str] = None

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "planner_source": self.planner_source.value,
            "planner_ms": self.planner_ms,
            "tool_execution_ms": self.tool_execution_ms,
            "tool_count": self.tool_count,
            "synthesis_ms": self.synthesis_ms,
            "validation_ms": self.validation_ms,
            "repair_ms": self.repair_ms,
            "persistence_ms": self.persistence_ms,
            "total_latency_ms": self.total_latency_ms,
            "llm_call_count": self.llm_call_count,
            "planner_call_count": self.planner_call_count,
            "synthesis_call_count": self.synthesis_call_count,
            "repair_call_count": self.repair_call_count,
            "embedding_call_count": self.embedding_call_count,
            "evidence_status": self.evidence_status.value,
            "answer_mode": self.answer_mode.value,
            "fallback_used": self.fallback_used,
            "timeout_stage": self.timeout_stage,
        }
