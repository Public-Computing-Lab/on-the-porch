"""Non-LLM plan executor — runs SQL, RAG, and guardrails in parallel."""

from __future__ import annotations

import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional

from execution_plan import (
    ExecutionPlan,
    FactBundle,
    FactPart,
    PlanPart,
    log_fact_bundle,
)
from query_plan import resolve_period

from sql_chat import prepared_sql
from resident_response import execution_failure_message


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
_BUDGET_WAIT_MARGIN_SECONDS = float(os.getenv("REQUEST_BUDGET_WAIT_MARGIN_SECONDS", "0.25"))


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
            canonical_facts=execution_failure_message(
                path="sql",
                intent=part.intent,
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
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ SQL part failed ({part.intent}): {exc}")
        return FactPart(
            path="sql",
            intent=part.intent,
            topic=part.topic,
            sub_question=sub_q,
            canonical_facts=execution_failure_message(
                path="sql",
                intent=part.intent,
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
        "intent": part.intent,
    }
    try:
        chunks, metadata = retrieve_fn(question, plan_stub)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ RAG part failed: {exc}")
        return FactPart(
            path="rag",
            intent=part.intent,
            topic=part.topic,
            sub_question=part.sub_question,
            canonical_facts="",
            error=str(exc),
        )

    if not chunks:
        canonical = "No relevant information found."
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


def _timeout_fact_part(part: PlanPart) -> FactPart:
    message = part.guardrail_message or execution_failure_message(
        path=part.path,
        intent=part.intent,
        timed_out=True,
    )
    return FactPart(
        path=part.path,
        intent=part.intent,
        topic=part.topic,
        sub_question=part.sub_question,
        canonical_facts=message,
        error="request_budget_exhausted",
    )


def _exception_fact_part(part: PlanPart, exc: Exception) -> FactPart:
    print(f"  ⚠️ Plan part failed ({part.path}/{part.intent}): {exc}")
    message = part.guardrail_message or execution_failure_message(
        path=part.path,
        intent=part.intent,
    )
    return FactPart(
        path=part.path,
        intent=part.intent,
        topic=part.topic,
        sub_question=part.sub_question,
        canonical_facts=message,
        error=str(exc),
    )


def execute_plan(
    plan: ExecutionPlan,
    question: str,
    *,
    execute_sql: Callable[[str], Dict[str, Any]],
    retrieve_rag: Callable[[str, Dict[str, Any]], tuple[List[str], List[Dict[str, Any]]]],
) -> FactBundle:
    """Execute all plan parts without LLM calls."""
    started = time.perf_counter()
    try:
        from request_budget import (  # noqa: WPS433
            current_deadline,
            deadline_exhausted,
            use_request_deadline,
            wait_timeout_cap,
        )

        deadline = current_deadline()
    except Exception:
        deadline = None
        deadline_exhausted = None  # type: ignore[assignment]
        use_request_deadline = None  # type: ignore[assignment]
        wait_timeout_cap = None  # type: ignore[assignment]

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

    def _run_with_budget(part: PlanPart) -> FactPart:
        if use_request_deadline is None:
            return _run(part)
        with use_request_deadline(deadline):
            return _run(part)

    def _has_budget() -> bool:
        if deadline is None or deadline_exhausted is None:
            return True
        return not deadline_exhausted(
            deadline,
            safety_margin=_BUDGET_WAIT_MARGIN_SECONDS,
        )

    if deadline is None and len(plan.parts) == 1:
        fact_parts = [_run_with_budget(plan.parts[0])]
    else:
        workers = min(_MAX_WORKERS, len(plan.parts))
        executor = ThreadPoolExecutor(max_workers=workers)
        future_map: Dict[Future[FactPart], tuple[int, PlanPart]] = {}
        collected: Dict[int, FactPart] = {}
        pending: set[Future[FactPart]] = set()
        try:
            for index, part in enumerate(plan.parts):
                future = executor.submit(_run_with_budget, part)
                future_map[future] = (index, part)
                pending.add(future)

            while pending:
                if not _has_budget():
                    break
                if deadline is None or wait_timeout_cap is None:
                    timeout = None
                else:
                    timeout = wait_timeout_cap(
                        5.0,
                        deadline,
                        safety_margin=_BUDGET_WAIT_MARGIN_SECONDS,
                    )
                    if timeout <= 0.0:
                        break
                done, pending = wait(
                    pending,
                    timeout=timeout,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    break
                for future in done:
                    index, part = future_map[future]
                    try:
                        collected[index] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        collected[index] = _exception_fact_part(part, exc)

            for future in pending:
                future.cancel()
            for future in pending:
                index, part = future_map[future]
                collected.setdefault(index, _timeout_fact_part(part))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        fact_parts = [
            collected[index]
            for index in range(len(plan.parts))
            if index in collected
        ]

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
