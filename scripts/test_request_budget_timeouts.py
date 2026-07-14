#!/usr/bin/env python3
"""Regression tests for request-budget bounded executor and embedding paths."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "on_the_porch"))
sys.path.insert(0, str(ROOT / "on_the_porch" / "rag stuff"))

import plan_executor  # noqa: E402
import plan_builder  # noqa: E402
import request_budget  # noqa: E402
import retrieval  # noqa: E402
import unified_chatbot  # noqa: E402
from execution_plan import ExecutionPlan, FactBundle, FactPart, PlanPart  # noqa: E402
from semantic_cache import SemanticResponseCache  # noqa: E402


def _assert_fast(started: float, *, limit: float = 2.0) -> None:
    elapsed = time.perf_counter() - started
    assert elapsed < limit, f"expected bounded return under {limit}s, got {elapsed:.2f}s"


def _reset_budget(seconds: float = 1.0) -> None:
    request_budget.reset_request_budget(seconds)


def _clear_budget() -> None:
    request_budget.set_request_deadline(None)


def test_rag_retrieval_task_timeout_returns_empty() -> None:
    old_get_query_embedding = retrieval.get_query_embedding
    old_retrieve_transcripts = retrieval.retrieve_transcripts
    old_parallel = unified_chatbot._PARALLEL_RETRIEVAL
    try:
        retrieval.get_query_embedding = lambda _question: [0.1, 0.2]  # type: ignore[assignment]

        def _slow_retrieve(*_args, **_kwargs):
            time.sleep(2.5)
            return {"chunks": ["late chunk"], "metadata": [{"source": "late"}]}

        retrieval.retrieve_transcripts = _slow_retrieve  # type: ignore[assignment]
        unified_chatbot._PARALLEL_RETRIEVAL = False
        _reset_budget(2.5)
        started = time.perf_counter()
        chunks, metadata = unified_chatbot._retrieve_rag(
            "What did residents say about safety?",
            {"intent": "transcript", "k": 3},
        )
        _assert_fast(started, limit=3.0)
        assert chunks == []
        assert metadata == []
    finally:
        retrieval.get_query_embedding = old_get_query_embedding  # type: ignore[assignment]
        retrieval.retrieve_transcripts = old_retrieve_transcripts  # type: ignore[assignment]
        unified_chatbot._PARALLEL_RETRIEVAL = old_parallel
        _clear_budget()


def test_plan_executor_returns_completed_parts_without_waiting() -> None:
    plan = ExecutionPlan(
        question="How many 311 requests last month?",
        parts=[
            PlanPart(
                path="sql",
                intent="311_weekly_count",
                topic="311",
                sub_question="How many 311 requests were filed last week?",
            ),
            PlanPart(
                path="guardrail",
                intent="911_unavailable",
                topic="911",
                sub_question="How many 911 calls were filed last week?",
                guardrail_message="911 call data is unavailable.",
            ),
        ],
        routing_mode="hybrid",
    )

    def _slow_sql(_sql: str):
        time.sleep(2.5)
        return {"columns": ["cnt"], "rows": [{"cnt": 1}]}

    _reset_budget()
    started = time.perf_counter()
    bundle = plan_executor.execute_plan(
        plan,
        plan.question,
        execute_sql=_slow_sql,
        retrieve_rag=lambda _q, _p: ([], []),
    )
    _assert_fast(started)
    assert len(bundle.parts) == 2
    assert any(part.path == "guardrail" and not part.error for part in bundle.parts)
    timed_out_sql = [part for part in bundle.parts if part.path == "sql"]
    assert timed_out_sql
    assert timed_out_sql[0].error == "request_budget_exhausted"
    _clear_budget()


def test_hybrid_parts_return_partial_without_waiting() -> None:
    old_run_sql = unified_chatbot._run_sql
    old_run_rag = unified_chatbot._run_rag
    old_parallel = unified_chatbot._PARALLEL_RETRIEVAL
    try:
        def _slow_sql(*_args, **_kwargs):
            time.sleep(2.5)
            return {"answer": "late sql", "sql": "select 1", "result": {"rows": []}}

        def _fast_rag(*_args, **_kwargs):
            return {
                "answer": "community context",
                "chunks": ["context"],
                "metadata": [{"source": "meeting"}],
            }

        unified_chatbot._run_sql = _slow_sql  # type: ignore[assignment]
        unified_chatbot._run_rag = _fast_rag  # type: ignore[assignment]
        unified_chatbot._PARALLEL_RETRIEVAL = True
        _reset_budget()
        started = time.perf_counter()
        sql_part, rag_part = unified_chatbot._run_hybrid_parts(
            "Is Dorchester safe right now?",
            {"intent": "safety_overview", "k": 3},
        )
        _assert_fast(started)
        assert sql_part["error"] == "request_budget_exhausted"
        assert rag_part["chunks"] == ["context"]
    finally:
        unified_chatbot._run_sql = old_run_sql  # type: ignore[assignment]
        unified_chatbot._run_rag = old_run_rag  # type: ignore[assignment]
        unified_chatbot._PARALLEL_RETRIEVAL = old_parallel
        _clear_budget()


def test_pipeline_preserves_degraded_signal_after_legacy_conversion() -> None:
    plan = ExecutionPlan(
        question="What did residents discuss at the community meeting?",
        parts=[
            PlanPart(
                path="rag",
                intent="transcript",
                topic="community",
                sub_question="What did residents discuss at the community meeting?",
            ),
        ],
        routing_mode="rag",
    )
    facts = FactBundle(
        question=plan.question,
        parts=[
            FactPart(
                path="rag",
                intent="transcript",
                topic="community",
                sub_question=plan.question,
                canonical_facts="City-service information is temporarily unavailable.",
                error="request_budget_exhausted",
            ),
        ],
    )
    old_enabled = plan_builder.is_execution_pipeline_enabled
    old_build = plan_builder.build_execution_plan
    old_execute = plan_executor.execute_plan
    old_fallback = unified_chatbot._apply_default_fallback_if_needed
    try:
        plan_builder.is_execution_pipeline_enabled = lambda: True  # type: ignore[assignment]
        plan_builder.build_execution_plan = lambda *_args, **_kwargs: plan  # type: ignore[assignment]
        plan_executor.execute_plan = lambda *_args, **_kwargs: facts  # type: ignore[assignment]
        unified_chatbot._apply_default_fallback_if_needed = (  # type: ignore[assignment]
            lambda _question, answer: answer
        )
        _reset_budget(10.0)
        pipeline = unified_chatbot.run_execution_pipeline(
            plan.question,
            routing_plan={"mode": "rag", "intent": "transcript"},
        )
        assert pipeline is not None
        mode, result, metrics = pipeline
        assert mode == "rag"
        assert "error" not in result
        assert metrics["execution_degraded"] is True
    finally:
        plan_builder.is_execution_pipeline_enabled = old_enabled  # type: ignore[assignment]
        plan_builder.build_execution_plan = old_build  # type: ignore[assignment]
        plan_executor.execute_plan = old_execute  # type: ignore[assignment]
        unified_chatbot._apply_default_fallback_if_needed = old_fallback  # type: ignore[assignment]
        _clear_budget()


class _SlowGemini:
    def __init__(self) -> None:
        self.calls = 0

    def embed_content(self, **_kwargs):
        self.calls += 1
        time.sleep(2.5)
        return {"embedding": [0.1, 0.2]}


def _install_slow_gemini_embedding():
    old_configure = retrieval._configure_gemini
    old_genai = retrieval.genai
    old_get_embeddings = retrieval.get_embeddings
    fake_genai = _SlowGemini()
    retrieval._configure_gemini = lambda: None  # type: ignore[assignment]
    retrieval.genai = fake_genai  # type: ignore[assignment]
    embedding = retrieval.GeminiEmbeddings(model="models/fake")
    retrieval.get_embeddings = lambda: embedding  # type: ignore[assignment]
    return old_configure, old_genai, old_get_embeddings, fake_genai


def test_slow_gemini_embedding_degrades_before_vector_search() -> None:
    restore = _install_slow_gemini_embedding()
    old_retrieve_transcripts = retrieval.retrieve_transcripts
    vector_called = {"value": False}
    try:
        def _should_not_run(*_args, **_kwargs):
            vector_called["value"] = True
            return {"chunks": ["unexpected"], "metadata": []}

        retrieval.retrieve_transcripts = _should_not_run  # type: ignore[assignment]
        retrieval.clear_query_embedding_cache()
        _reset_budget(2.5)
        started = time.perf_counter()
        chunks, metadata = unified_chatbot._retrieve_rag(
            "slow embedding query",
            {"intent": "transcript", "k": 3},
        )
        _assert_fast(started, limit=3.0)
        assert chunks == []
        assert metadata == []
        assert not vector_called["value"]
    finally:
        old_configure, old_genai, old_get_embeddings, _fake_genai = restore
        retrieval._configure_gemini = old_configure  # type: ignore[assignment]
        retrieval.genai = old_genai  # type: ignore[assignment]
        retrieval.get_embeddings = old_get_embeddings  # type: ignore[assignment]
        retrieval.retrieve_transcripts = old_retrieve_transcripts  # type: ignore[assignment]
        retrieval.clear_query_embedding_cache()
        _clear_budget()


def test_semantic_cache_embedding_timeout_uses_same_budget_path() -> None:
    restore = _install_slow_gemini_embedding()
    try:
        cache = SemanticResponseCache(
            enabled=True,
            threshold=0.9,
            ttl_seconds=60,
            max_entries=10,
            embed_fn=retrieval.embed_text,
        )
        _reset_budget()
        started = time.perf_counter()
        cached = cache.get("slow semantic cache query")
        _assert_fast(started)
        assert cached is None
    finally:
        old_configure, old_genai, old_get_embeddings, _fake_genai = restore
        retrieval._configure_gemini = old_configure  # type: ignore[assignment]
        retrieval.genai = old_genai  # type: ignore[assignment]
        retrieval.get_embeddings = old_get_embeddings  # type: ignore[assignment]
        retrieval.clear_query_embedding_cache()
        _clear_budget()


def main() -> int:
    tests = [
        test_rag_retrieval_task_timeout_returns_empty,
        test_plan_executor_returns_completed_parts_without_waiting,
        test_hybrid_parts_return_partial_without_waiting,
        test_pipeline_preserves_degraded_signal_after_legacy_conversion,
        test_slow_gemini_embedding_degrades_before_vector_search,
        test_semantic_cache_embedding_timeout_uses_same_budget_path,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
