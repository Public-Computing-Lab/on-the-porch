"""Rule-first (Tier A) and optional LLM (Tier B) execution plan builders."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from civic_decompose import decompose_civic_question
from execution_plan import (
    ALLOWED_PATHS,
    ALLOWED_SQL_INTENTS,
    ExecutionPlan,
    PlanPart,
    RagHints,
    SynthesisConfig,
    log_execution_plan,
)

from sql_chat import prepared_sql


def is_execution_pipeline_enabled() -> bool:
    return os.getenv("EXECUTION_PIPELINE_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def is_llm_planner_enabled() -> bool:
    return os.getenv("EXECUTION_LLM_PLANNER_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _rag_hints_from_routing(routing: Dict[str, Any]) -> RagHints:
    return RagHints(
        k=int(routing.get("k") or 5),
        transcript_tags=routing.get("transcript_tags"),
        policy_sources=routing.get("policy_sources"),
        folder_categories=routing.get("folder_categories"),
    )


def _sql_part(
    question: str,
    *,
    topic: Optional[str] = None,
    sub_question: Optional[str] = None,
) -> Optional[PlanPart]:
    sub_q = sub_question or question
    hit = prepared_sql.match_prepared_sql(sub_q)
    if hit is None:
        return None

    if hit.intent == "911_unavailable":
        return PlanPart(
            path="guardrail",
            intent="911_unavailable",
            sub_question=sub_q,
            topic=topic,
            guardrail_message=hit.answer_fn(sub_q, {"columns": [], "rows": []}),
        )

    supplemental_intent: Optional[str] = None
    if prepared_sql.should_include_311_breakdown(question, hit.intent):
        supplemental_intent = "311_top_types"

    return PlanPart(
        path="sql",
        intent=hit.intent,
        sub_question=sub_q,
        topic=topic,
        supplemental_intent=supplemental_intent,
    )


def _needs_llm_planner(question: str, routing: Dict[str, Any]) -> bool:
    """Tier B when rules are ambiguous or stats+voice are mixed without a prepared SQL hit."""
    confidence = float(routing.get("confidence") or 0.0)
    if confidence < 0.75:
        return True
    mode = str(routing.get("mode", "")).lower()
    if mode != "hybrid":
        return False
    if prepared_sql.match_prepared_sql(question):
        return False
    if prepared_sql.has_opinion_angle(question):
        return True
    return bool(re.search(r"\band\b", question, re.I))


def _build_compound_plan(question: str, routing: Dict[str, Any]) -> Optional[ExecutionPlan]:
    parts = decompose_civic_question(question)
    if len(parts) < 2:
        return None

    plan_parts: List[PlanPart] = []
    for civic_part in parts:
        sql_part = _sql_part(
            question,
            topic=civic_part.topic,
            sub_question=civic_part.sub_question,
        )
        if sql_part:
            plan_parts.append(sql_part)
        else:
            plan_parts.append(PlanPart(
                path="guardrail",
                intent="unavailable",
                sub_question=civic_part.sub_question,
                topic=civic_part.topic,
                guardrail_message=(
                    f"I couldn't run a prepared query for the **{civic_part.topic}** "
                    "part of your question."
                ),
            ))

    if len(plan_parts) < 2:
        return None

    return ExecutionPlan(
        question=question,
        parts=plan_parts,
        tier="A",
        routing_intent=routing.get("intent"),
        routing_mode="sql",
        synthesis=SynthesisConfig(),
    )


def _build_sql_plan(question: str, routing: Dict[str, Any]) -> Optional[ExecutionPlan]:
    sql_part = _sql_part(question)
    if sql_part is None:
        return None
    return ExecutionPlan(
        question=question,
        parts=[sql_part],
        tier="A",
        routing_intent=routing.get("intent"),
        routing_mode="sql",
        synthesis=SynthesisConfig(),
    )


def _build_rag_plan(question: str, routing: Dict[str, Any]) -> Optional[ExecutionPlan]:
    return ExecutionPlan(
        question=question,
        parts=[PlanPart(
            path="rag",
            intent=str(routing.get("intent") or "rag"),
            sub_question=question,
            rag_hints=_rag_hints_from_routing(routing),
        )],
        tier="A",
        routing_intent=routing.get("intent"),
        routing_mode="rag",
        synthesis=SynthesisConfig(),
    )


def _build_hybrid_plan(question: str, routing: Dict[str, Any]) -> Optional[ExecutionPlan]:
    plan_parts: List[PlanPart] = []
    sql_part = _sql_part(question)
    if sql_part:
        plan_parts.append(sql_part)
    elif prepared_sql.is_factual_db_question(question):
        return None

    plan_parts.append(PlanPart(
        path="rag",
        intent=str(routing.get("intent") or "hybrid_rag"),
        sub_question=question,
        rag_hints=_rag_hints_from_routing(routing),
    ))
    return ExecutionPlan(
        question=question,
        parts=plan_parts,
        tier="A",
        routing_intent=routing.get("intent"),
        routing_mode="hybrid",
        synthesis=SynthesisConfig(),
    )


def build_execution_plan_rules(
    question: str,
    routing: Dict[str, Any],
) -> Optional[ExecutionPlan]:
    """Tier A — rules only, no LLM."""
    if not question or not question.strip():
        return None

    compound = _build_compound_plan(question, routing)
    if compound is not None:
        return compound

    mode = str(routing.get("mode", "rag")).lower()
    if mode == "sql":
        return _build_sql_plan(question, routing)
    if mode == "rag":
        return _build_rag_plan(question, routing)
    if mode == "hybrid":
        return _build_hybrid_plan(question, routing)
    return None


def _parse_llm_plan_json(content: str, question: str) -> Optional[ExecutionPlan]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in ("json", "javascript", "js"):
            text = "\n".join(lines[1:]).strip()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None

    parts_raw = raw.get("parts")
    if not isinstance(parts_raw, list) or not parts_raw:
        return None

    plan_parts: List[PlanPart] = []
    for item in parts_raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).lower()
        if path not in ALLOWED_PATHS:
            continue
        intent = str(item.get("intent", "")).strip()
        sub_q = str(item.get("sub_question") or question).strip()
        if path == "sql" and intent not in ALLOWED_SQL_INTENTS:
            continue
        rag_hints = None
        hints_raw = item.get("rag_hints")
        if isinstance(hints_raw, dict):
            rag_hints = RagHints(
                k=int(hints_raw.get("k") or 5),
                transcript_tags=hints_raw.get("transcript_tags"),
                policy_sources=hints_raw.get("policy_sources"),
                folder_categories=hints_raw.get("folder_categories"),
            )
        plan_parts.append(PlanPart(
            path=path,  # type: ignore[arg-type]
            intent=intent or path,
            sub_question=sub_q,
            topic=item.get("topic"),
            supplemental_intent=item.get("supplemental_intent"),
            rag_hints=rag_hints,
            guardrail_message=item.get("guardrail_message"),
        ))

    if not plan_parts:
        return None

    return ExecutionPlan(
        question=question,
        parts=plan_parts,
        tier="B",
        routing_intent=raw.get("routing_intent"),
        routing_mode=str(raw.get("routing_mode") or "hybrid"),
        synthesis=SynthesisConfig(),
    )


def build_execution_plan_llm(
    question: str,
    routing: Dict[str, Any],
) -> Optional[ExecutionPlan]:
    """Tier B — Flash Lite JSON planner for ambiguous multi-clause questions."""
    from unified_chatbot import (  # noqa: WPS433
        GEMINI_FAST_MODEL,
        _generate_content,
        _get_llm_client,
    )

    allowed = ", ".join(sorted(ALLOWED_SQL_INTENTS))
    system_prompt = (
        "You are a planner for a Dorchester civic chatbot.\n"
        "Return ONLY JSON with keys: routing_mode, routing_intent, parts.\n"
        "Each part: path (sql|rag|guardrail), intent, sub_question, optional topic, "
        "optional supplemental_intent, optional rag_hints.\n"
        f"SQL intents must be one of: {allowed}.\n"
        "Split multi-topic questions into separate sql parts with focused sub_questions.\n"
        "Use rag for community voice, news, policy, transcripts.\n"
        "Use guardrail only for unavailable data (e.g. 911 dispatch).\n"
        "Do not write user-facing answers."
    )
    user_prompt = (
        f"Question:\n{question}\n\n"
        f"Router hint: mode={routing.get('mode')}, intent={routing.get('intent')}\n"
        "Output JSON only."
    )
    try:
        client = _get_llm_client()
        model = client.GenerativeModel(GEMINI_FAST_MODEL)
        resp = _generate_content(
            model,
            f"{system_prompt}\n\n{user_prompt}",
            generation_config={"temperature": 0},
        )
        return _parse_llm_plan_json(resp.text or "", question)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ LLM planner failed: {exc}")
        return None


def build_execution_plan(
    question: str,
    routing: Dict[str, Any],
) -> Optional[ExecutionPlan]:
    """Build an execution plan: rules first, LLM planner when needed."""
    plan = build_execution_plan_rules(question, routing)
    if plan is None and is_llm_planner_enabled() and _needs_llm_planner(question, routing):
        plan = build_execution_plan_llm(question, routing)
    if plan is not None:
        log_execution_plan(plan)
    return plan
