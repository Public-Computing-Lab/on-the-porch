"""Non-LLM plan executor — runs SQL, RAG, and guardrails in parallel."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional

from execution_plan import (
    ExecutionPlan,
    FactBundle,
    FactPart,
    PlanPart,
    log_fact_bundle,
)
from query_plan import resolve_period
from evidence_domains import filter_evidence_for_domain, resident_evidence_limitation
from request_budget import raise_if_exhausted, timeout_for
from concurrency import CapacityUnavailable
from request_budget import BudgetExhausted
from turn_observability import (
    increment as increment_observability,
    record_fields as record_observability_fields,
    submit_with_trace,
)

from sql_chat import prepared_sql


def _sanitize_rows(rows: List[Dict[str, Any]], *, limit: int = 10) -> List[Dict[str, Any]]:
    """Coerce MySQL Decimal values for JSON / LLM payloads."""
    cleaned: List[Dict[str, Any]] = []
    for row in (rows or [])[:limit]:
        if not isinstance(row, dict):
            continue
        cleaned.append({
            k: int(v) if hasattr(v, "__int__") and k in ("cnt", "count") else v
            for k, v in row.items()
        })
    return cleaned


_MAX_WORKERS = max(1, min(4, int(os.getenv("EXECUTION_PIPELINE_MAX_WORKERS", "4"))))


def _part_evidence_domain(part: PlanPart) -> Optional[str]:
    return part.rag_hints.evidence_domain if part.rag_hints else None


def _unavailable_part(part: PlanPart, question: str, error: str) -> FactPart:
    return FactPart(
        path=part.path,
        intent=part.intent,
        topic=part.topic,
        sub_question=part.sub_question,
        canonical_facts=resident_evidence_limitation(
            question,
            evidence_domain=_part_evidence_domain(part),
            unavailable=True,
        ),
        error=error,
    )


def _supplemental_sql_for_part(
    part: PlanPart,
    question: str,
    hit: prepared_sql.PreparedMatch,
) -> Optional[str]:
    if part.supplemental_intent == "311_top_types":
        window = resolve_period(question, 7)
        return hit.supplemental_sql or prepared_sql.sql_311_top_types(window)
    return hit.supplemental_sql


def _execute_sql_part(
    part: PlanPart,
    question: str,
    execute_sql: Callable[[str], Dict[str, Any]],
) -> FactPart:
    sub_q = part.sub_question or question
    hit = prepared_sql.match_prepared_sql(sub_q)
    if hit is None:
        return FactPart(
            path="sql",
            intent=part.intent,
            topic=part.topic,
            sub_question=sub_q,
            canonical_facts=resident_evidence_limitation(
                question,
                unavailable=True,
            ),
            error="no_prepared_match",
        )

    supplemental_sql = _supplemental_sql_for_part(part, question, hit)
    try:
        facts = prepared_sql.execute_prepared_facts(
            sub_q,
            hit,
            execute_sql,
            supplemental_sql=supplemental_sql,
        )
    except (BudgetExhausted, CapacityUnavailable):
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ SQL part failed ({part.intent}): {exc}")
        return FactPart(
            path="sql",
            intent=part.intent,
            topic=part.topic,
            sub_question=sub_q,
            canonical_facts=resident_evidence_limitation(
                question,
                unavailable=True,
            ),
            error=str(exc),
        )

    issue_breakdown = None
    supp = facts.get("supplemental_result")
    if supp and supp.get("rows"):
        issue_breakdown = _sanitize_rows(supp.get("rows") or [])

    return FactPart(
        path="sql",
        intent=facts["intent"],
        topic=part.topic,
        sub_question=sub_q,
        canonical_facts=facts["canonical_facts"],
        sql=facts.get("sql", ""),
        result=facts.get("result"),
        supplemental_result=supp,
        issue_type_breakdown=issue_breakdown,
        row_analysis=facts.get("row_analysis"),
    )


def _execute_guardrail_part(part: PlanPart) -> FactPart:
    message = part.guardrail_message or (
        "That information isn't available in this app yet."
    )
    return FactPart(
        path="guardrail",
        intent=part.intent,
        topic=part.topic,
        sub_question=part.sub_question,
        canonical_facts=message,
    )


def _execute_rag_part(
    part: PlanPart,
    question: str,
    retrieve_fn: Callable[[str, Dict[str, Any]], tuple[List[str], List[Dict[str, Any]]]],
) -> FactPart:
    hints = part.rag_hints
    plan_stub: Dict[str, Any] = {
        "k": hints.k if hints else 5,
        "transcript_tags": hints.transcript_tags if hints else None,
        "policy_sources": hints.policy_sources if hints else None,
        "folder_categories": hints.folder_categories if hints else None,
        "evidence_domain": hints.evidence_domain if hints else None,
        "required_concepts": hints.required_concepts if hints else None,
        "intent": part.intent,
    }
    try:
        chunks, metadata = retrieve_fn(question, plan_stub)
    except (BudgetExhausted, CapacityUnavailable):
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ RAG part failed: {exc}")
        return FactPart(
            path="rag",
            intent=part.intent,
            topic=part.topic,
            sub_question=part.sub_question,
            canonical_facts=resident_evidence_limitation(
                question,
                evidence_domain=hints.evidence_domain if hints else None,
                unavailable=True,
            ),
            error=str(exc),
        )

    chunks, metadata = filter_evidence_for_domain(
        chunks,
        metadata,
        evidence_domain=hints.evidence_domain if hints else None,
        required_concepts=hints.required_concepts if hints else None,
    )

    if not chunks:
        canonical = resident_evidence_limitation(
            question,
            evidence_domain=hints.evidence_domain if hints else None,
            unavailable=False,
        )
    else:
        canonical = f"Retrieved {len(chunks)} source passages for context."

    return FactPart(
        path="rag",
        intent=part.intent,
        topic=part.topic,
        sub_question=part.sub_question,
        canonical_facts=canonical,
        rag_chunks=chunks,
        rag_metadata=metadata,
    )


def _derive_prepared_intent(plan: ExecutionPlan, parts: List[FactPart]) -> Optional[str]:
    sql_parts = [p for p in parts if p.path in ("sql", "guardrail")]
    if len(sql_parts) > 1:
        topics = [p.topic for p in sql_parts if p.topic]
        return f"compound:{','.join(topics)}" if topics else "compound"
    if sql_parts:
        return sql_parts[0].intent
    return None


def execute_plan(
    plan: ExecutionPlan,
    question: str,
    *,
    execute_sql: Callable[[str], Dict[str, Any]],
    retrieve_rag: Callable[[str, Dict[str, Any]], tuple[List[str], List[Dict[str, Any]]]],
) -> FactBundle:
    """Execute all plan parts without LLM calls."""
    started = time.perf_counter()
    raise_if_exhausted("plan_execution")

    def _run(part: PlanPart) -> FactPart:
        if part.path == "sql":
            return _execute_sql_part(part, question, execute_sql)
        if part.path == "guardrail":
            return _execute_guardrail_part(part)
        if part.path == "rag":
            return _execute_rag_part(part, question, retrieve_rag)
        return FactPart(
            path="guardrail",
            intent="unknown",
            topic=part.topic,
            sub_question=part.sub_question,
            canonical_facts="Unsupported plan path.",
            error="unknown_path",
        )

    if len(plan.parts) == 1:
        fact_parts = [_run(plan.parts[0])]
    else:
        workers = min(_MAX_WORKERS, len(plan.parts))
        executor = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [submit_with_trace(executor, _run, part) for part in plan.parts]
            collection_timeout = timeout_for(
                "plan_parallel_collection",
                float(os.getenv("PLAN_COLLECTION_TIMEOUT_SECONDS", "12")),
                minimum_required=0.1,
            )
            done, pending = wait(set(futures), timeout=collection_timeout)
            canceled = sum(1 for future in pending if future.cancel())
            if canceled:
                increment_observability("canceled_future_count", canceled)
            if pending:
                record_observability_fields(partial_path_completion=bool(done))
            fact_parts = []
            for part, future in zip(plan.parts, futures):
                if future not in done:
                    fact_parts.append(_unavailable_part(part, question, f"{part.path}_timeout"))
                    continue
                try:
                    fact_parts.append(future.result())
                except BudgetExhausted:
                    fact_parts.append(_unavailable_part(part, question, f"{part.path}_timeout"))
                except CapacityUnavailable:
                    fact_parts.append(_unavailable_part(part, question, "capacity_unavailable"))
                except Exception as exc:  # noqa: BLE001
                    fact_parts.append(_unavailable_part(part, question, f"{part.path}_unavailable"))
                    print(f"  ⚠️ Plan part failed ({part.path}): {exc.__class__.__name__}")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    execute_ms = int((time.perf_counter() - started) * 1000)
    bundle = FactBundle(
        question=question,
        parts=fact_parts,
        tier=plan.tier,
        execute_ms=execute_ms,
        prepared_intent=_derive_prepared_intent(plan, fact_parts),
    )
    log_fact_bundle(bundle)
    return bundle
