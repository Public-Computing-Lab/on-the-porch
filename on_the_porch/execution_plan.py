"""ExecutionPlan and FactBundle contracts for plan → execute → synthesize pipeline.

Planner outputs ExecutionPlan (no user-facing prose). Executor returns FactBundle
(structured facts only). Synthesizer turns FactBundle into conversational answers.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

PlanPath = Literal["sql", "rag", "guardrail"]
PlanTier = Literal["A", "B"]

ALLOWED_PATHS = frozenset({"sql", "rag", "guardrail"})
ALLOWED_SQL_INTENTS = frozenset({
    "311_weekly_count",
    "311_issue_count",
    "311_count_with_types",
    "311_top_types",
    "311_open",
    "311_recent",
    "311_issue_recent",
    "crime_monthly_count",
    "crime_shooting_count",
    "crime_homicide_count",
    "crime_by_offense",
    "911_unavailable",
    "events_list",
    "events_by_source",
    "event_by_name",
})


@dataclass
class RagHints:
    """Retrieval hints for a RAG plan part."""

    k: int = 5
    transcript_tags: Optional[List[str]] = None
    policy_sources: Optional[List[str]] = None
    folder_categories: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class SynthesisConfig:
    conversational: bool = True
    must_preserve_numbers: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlanPart:
    path: PlanPath
    intent: str
    sub_question: str
    topic: Optional[str] = None
    supplemental_intent: Optional[str] = None
    rag_hints: Optional[RagHints] = None
    guardrail_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "path": self.path,
            "intent": self.intent,
            "sub_question": self.sub_question,
        }
        if self.topic:
            out["topic"] = self.topic
        if self.supplemental_intent:
            out["supplemental_intent"] = self.supplemental_intent
        if self.rag_hints:
            out["rag_hints"] = self.rag_hints.to_dict()
        if self.guardrail_message:
            out["guardrail_message"] = self.guardrail_message
        return out


@dataclass
class ExecutionPlan:
    question: str
    parts: List[PlanPart]
    tier: PlanTier = "A"
    synthesis: SynthesisConfig = field(default_factory=SynthesisConfig)
    routing_intent: Optional[str] = None
    routing_mode: str = "sql"

    def legacy_mode(self) -> str:
        paths = {p.path for p in self.parts}
        if paths == {"sql"}:
            return "sql"
        if paths == {"rag"}:
            return "rag"
        return "hybrid"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "tier": self.tier,
            "routing_mode": self.routing_mode,
            "routing_intent": self.routing_intent,
            "synthesis": self.synthesis.to_dict(),
            "parts": [p.to_dict() for p in self.parts],
        }


@dataclass
class FactPart:
    path: PlanPath
    intent: str
    topic: Optional[str]
    sub_question: str
    canonical_facts: str
    sql: str = ""
    result: Optional[Dict[str, Any]] = None
    supplemental_result: Optional[Dict[str, Any]] = None
    issue_type_breakdown: Optional[List[Dict[str, Any]]] = None
    rag_chunks: Optional[List[str]] = None
    rag_metadata: Optional[List[Dict[str, Any]]] = None
    row_analysis: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    source_freshness: Optional[Dict[str, Any]] = None
    freshness_flags: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "path": self.path,
            "intent": self.intent,
            "sub_question": self.sub_question,
            "canonical_facts": self.canonical_facts,
        }
        if self.topic:
            out["topic"] = self.topic
        if self.sql:
            out["sql"] = self.sql
        if self.result:
            out["row_count"] = len(self.result.get("rows") or [])
        if self.issue_type_breakdown:
            out["issue_type_breakdown"] = self.issue_type_breakdown[:10]
        if self.rag_chunks:
            out["rag_chunk_count"] = len(self.rag_chunks)
        if self.error:
            out["error"] = self.error
        if self.source_freshness:
            out["source_freshness"] = self.source_freshness
        return out


@dataclass
class FactBundle:
    question: str
    parts: List[FactPart]
    tier: PlanTier = "A"
    execute_ms: int = 0
    prepared_intent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "tier": self.tier,
            "execute_ms": self.execute_ms,
            "prepared_intent": self.prepared_intent,
            "parts": [p.to_dict() for p in self.parts],
        }

    def to_legacy_result(self, answer: str) -> Dict[str, Any]:
        """Shape compatible with existing _run_sql / _run_hybrid consumers."""
        sql_parts = [p for p in self.parts if p.path in ("sql", "guardrail")]
        rag_parts = [p for p in self.parts if p.path == "rag"]

        freshness = None
        freshness_flags: Dict[str, Any] = {}
        for part in sql_parts:
            if part.source_freshness and freshness is None:
                freshness = part.source_freshness
            if part.freshness_flags:
                freshness_flags.update(part.freshness_flags)

        if not rag_parts:
            primary_sql = sql_parts[0] if sql_parts else None
            out = {
                "answer": answer,
                "sql": "\n---\n".join(p.sql for p in sql_parts if p.sql),
                "result": (primary_sql.result if primary_sql else None) or {
                    "columns": [], "rows": [],
                },
                "prepared_intent": self.prepared_intent,
                "db_backed": any(p.path == "sql" for p in sql_parts),
            }
            if freshness:
                out["source_freshness"] = freshness
            if freshness_flags:
                out["freshness_flags"] = freshness_flags
            return out

        rag_part = rag_parts[0]
        rag_out = {
            "answer": answer,
            "chunks": rag_part.rag_chunks or [],
            "metadata": rag_part.rag_metadata or [],
        }
        if not sql_parts:
            return rag_out

        primary_sql = sql_parts[0]
        sql_out = {
            "answer": answer,
            "sql": "\n---\n".join(p.sql for p in sql_parts if p.sql),
            "result": (primary_sql.result if primary_sql else None) or {
                "columns": [], "rows": [],
            },
            "prepared_intent": self.prepared_intent,
            "db_backed": any(p.path == "sql" for p in sql_parts),
        }
        if freshness:
            sql_out["source_freshness"] = freshness
        out = {
            "answer": answer,
            "sql": sql_out,
            "rag": rag_out,
            "prepared_intent": self.prepared_intent,
        }
        if freshness:
            out["source_freshness"] = freshness
        if freshness_flags:
            out["freshness_flags"] = freshness_flags
        return out


def pipeline_logging_enabled() -> bool:
    return os.getenv("EXECUTION_PIPELINE_LOG", "true").strip().lower() in (
        "1", "true", "yes",
    )


def log_execution_plan(plan: ExecutionPlan) -> None:
    if not pipeline_logging_enabled():
        return
    print(f"  📋 ExecutionPlan (tier {plan.tier}): {json.dumps(plan.to_dict(), ensure_ascii=False)}")


def log_fact_bundle(bundle: FactBundle) -> None:
    if not pipeline_logging_enabled():
        return
    print(f"  📦 FactBundle ({bundle.execute_ms}ms): {json.dumps(bundle.to_dict(), ensure_ascii=False, default=str)}")
