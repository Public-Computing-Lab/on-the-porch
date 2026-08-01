"""Unified LLM synthesizer and numeric validator for FactBundle answers."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from answer_voice import (
    classify_from_bundle_parts,
    format_conversation_prefix,
    polish_conversational_answer,
    resident_question_block,
    system_prompt,
    UNAVAILABLE_DATA_INTENTS,
)
from execution_plan import FactBundle, FactPart
from turn_observability import stage as observability_stage
from turn_observability import record_fields as record_observability_fields
from request_budget import BudgetExhausted
from concurrency import CapacityUnavailable

from sql_chat import prepared_sql


def is_answer_synthesis_enabled() -> bool:
    if os.getenv("ANSWER_SYNTHESIS_ENABLED", "true").strip().lower() in ("0", "false", "no"):
        return False
    return os.getenv("PREPARED_SQL_CONVERSATIONAL", "true").strip().lower() in (
        "1", "true", "yes",
    )


def _display_question(
    question: str,
    conversation_history: Optional[List[Dict[str, str]]],
) -> str:
    current = (question or "").strip()
    if current:
        return current
    if conversation_history:
        for msg in reversed(conversation_history):
            if msg.get("role") == "user":
                content = (msg.get("content") or "").strip()
                if content:
                    return content
    return question


def _blocks_from_bundle(
    bundle: FactBundle,
    *,
    for_validation: bool = False,
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    has_successful_evidence = any(
        not part.error
        and bool(
            part.rag_chunks
            or (part.result is not None)
            or (part.canonical_facts or "").strip()
        )
        for part in bundle.parts
    )
    for part in bundle.parts:
        if has_successful_evidence and part.error:
            continue
        block: Dict[str, Any] = {
            "path": part.path,
            "topic": part.topic,
            "intent": part.intent,
            "sub_question": part.sub_question,
            "canonical_facts": part.canonical_facts,
        }
        if part.issue_type_breakdown:
            block["issue_type_breakdown"] = part.issue_type_breakdown
        if part.row_analysis:
            block["row_analysis"] = part.row_analysis
        if part.result and part.result.get("rows"):
            limit = 8 if part.intent in prepared_sql.DETAIL_CIVIC_INTENTS else 5
            block["sample_rows"] = (part.result.get("rows") or [])[:limit]
        if part.intent == "crime_by_offense":
            block["notes"] = (
                "Criminal offense categories only (service calls excluded). Use these categories "
                "to answer what crimes are reported. Supplement with RAG for street safety, "
                "community programs, and neighborhood context — not as a substitute for these facts."
            )
        if for_validation and part.result:
            block["result"] = part.result
        if part.rag_chunks:
            context_parts: List[str] = []
            rag_sources: List[Dict[str, Any]] = []
            meta_list = part.rag_metadata or []
            for chunk, meta in zip(part.rag_chunks[:12], meta_list[:12]):
                safe_meta = meta or {}
                source = safe_meta.get("publisher") or safe_meta.get("source", "Unknown")
                source_details = {
                    key: safe_meta.get(key)
                    for key in (
                        "document_id",
                        "source_id",
                        "title",
                        "publisher",
                        "source_type",
                        "provenance_class",
                        "published_at",
                        "source_date",
                        "start_date",
                        "source_url",
                        "link",
                    )
                    if safe_meta.get(key) is not None
                }
                rag_sources.append(source_details)
                date_label = (
                    safe_meta.get("published_at")
                    or safe_meta.get("source_date")
                    or safe_meta.get("start_date")
                )
                label = str(source)
                if date_label:
                    label += f", {date_label}"
                context_parts.append(f"[{label}]\n{chunk}")
            block["rag_context"] = "\n\n".join(context_parts)
            block["rag_sources"] = rag_sources
        if (
            part.path == "guardrail"
            or part.intent in UNAVAILABLE_DATA_INTENTS
        ):
            block["data_unavailable"] = True
        blocks.append(block)
    return blocks


def _fallback_answer(bundle: FactBundle) -> str:
    parts: List[str] = []
    has_successful_evidence = any(
        not part.error
        and bool(
            part.rag_chunks
            or (part.result is not None)
            or (part.canonical_facts or "").strip()
        )
        for part in bundle.parts
    )
    for p in bundle.parts:
        if has_successful_evidence and p.error:
            continue
        if not p.canonical_facts or not p.canonical_facts.strip():
            continue
        parts.append(p.canonical_facts.strip())
    return "\n\n".join(parts)


def synthesize_answer(
    question: str,
    bundle: FactBundle,
    *,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    display_question: Optional[str] = None,
) -> str:
    """Single LLM call to turn structured facts into a conversational reply."""
    if bundle.parts and all(
        part.path == "rag" and not part.rag_chunks
        for part in bundle.parts
    ):
        return _fallback_answer(bundle)
    if not _fallback_answer(bundle):
        errors = [str(part.error or "") for part in bundle.parts]
        if errors and any(error == "capacity_unavailable" for error in errors):
            raise CapacityUnavailable("evidence", 0)
        if errors and any(error.endswith("_timeout") for error in errors):
            raise BudgetExhausted("evidence_retrieval")
    if not is_answer_synthesis_enabled():
        return _fallback_answer(bundle)

    from sql_chat import app4  # noqa: WPS433

    blocks = _blocks_from_bundle(bundle)
    has_rag = any(p.path == "rag" and p.rag_chunks for p in bundle.parts)
    kind = classify_from_bundle_parts(blocks, has_rag=has_rag)

    resident_asked = display_question or _display_question(question, conversation_history)
    full_prompt = system_prompt(kind, conversation_history) + "\n\n"
    full_prompt += format_conversation_prefix(conversation_history)
    full_prompt += resident_question_block(resident_asked, question)
    full_prompt += (
        f"\nVerified facts (JSON):\n"
        f"{json.dumps(blocks, ensure_ascii=False, default=str)}"
    )

    model = os.getenv("GEMINI_SUMMARY_MODEL", app4.GEMINI_SUMMARY_MODEL)
    temp = 0.2 if kind == "crime" else 0.35
    try:
        with observability_stage("rag_synthesis" if has_rag else "sql_synthesis"):
            return app4._call_gemini_with_logging(
                model, full_prompt, temperature=temp,
            ).strip()
    except (BudgetExhausted, CapacityUnavailable):
        fallback = _fallback_answer(bundle)
        if fallback:
            record_observability_fields(partial_path_completion=True)
            return fallback
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ Answer synthesis failed: {exc}")
        return _fallback_answer(bundle)


def validate_answer(
    answer: str,
    bundle: FactBundle,
    *,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    question: Optional[str] = None,
) -> str:
    """Ensure DB-backed numbers match query results across all SQL parts."""
    out = (answer or "").strip()
    if not out:
        out = _fallback_answer(bundle)

    blocks = _blocks_from_bundle(bundle, for_validation=True)
    out = prepared_sql.enforce_compound_canonical_facts(out, blocks)

    for part in bundle.parts:
        if part.path not in ("sql",):
            continue
        if part.intent in UNAVAILABLE_DATA_INTENTS:
            continue
        if part.result:
            out = prepared_sql.validate_prepared_answer(
                out,
                part.result,
                prepared_intent=part.intent,
                question=question or part.sub_question,
            )
    return polish_conversational_answer(
        out,
        conversation_history,
        synthesis_blocks=blocks,
        prepared_intent=bundle.prepared_intent,
        question=question,
    )


def synthesize_and_validate(
    question: str,
    bundle: FactBundle,
    *,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    display_question: Optional[str] = None,
) -> str:
    answer = synthesize_answer(
        question,
        bundle,
        conversation_history=conversation_history,
        display_question=display_question,
    )
    if not answer.strip():
        answer = _fallback_answer(bundle)
    try:
        with observability_stage("answer_validation"):
            return validate_answer(
                answer,
                bundle,
                conversation_history=conversation_history,
                question=question,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ Answer validation failed: {exc}")
        return answer.strip() or _fallback_answer(bundle)
