from __future__ import annotations

import time

import pytest

from api.concurrency_control import CapacityUnavailable
from on_the_porch.execution_plan import ExecutionPlan, FactBundle, FactPart, PlanPart
from on_the_porch.request_budget import BudgetExhausted, create_turn_budget, use_turn_budget


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        question="question",
        routing_mode="hybrid",
        parts=[
            PlanPart("sql", "events_list", "events", topic="events"),
            PlanPart("rag", "community", "community", topic="community"),
        ],
    )


def _fact(part: PlanPart) -> FactPart:
    return FactPart(
        path=part.path,
        intent=part.intent,
        topic=part.topic,
        sub_question=part.sub_question,
        canonical_facts=f"{part.path} completed",
        rag_chunks=["source"] if part.path == "rag" else None,
        rag_metadata=[{"source": "test"}] if part.path == "rag" else None,
        result={"columns": ["count"], "rows": [{"count": 1}]} if part.path == "sql" else None,
    )


@pytest.mark.parametrize("slow_path", ["sql", "rag"])
def test_successful_parallel_branch_survives_secondary_timeout(monkeypatch, slow_path) -> None:
    from on_the_porch import plan_executor

    def run(part, *_args, **_kwargs):
        if part.path == slow_path:
            time.sleep(0.3)
        return _fact(part)

    monkeypatch.setattr(plan_executor, "_execute_sql_part", run)
    monkeypatch.setattr(plan_executor, "_execute_rag_part", run)
    budget = create_turn_budget("turn", total_seconds=0.15, finalization_reserve_seconds=0)
    with use_turn_budget(budget):
        bundle = plan_executor.execute_plan(
            _plan(),
            "question",
            execute_sql=lambda _sql: {},
            retrieve_rag=lambda _question, _plan: ([], []),
        )
    completed = [part for part in bundle.parts if not part.error]
    failed = [part for part in bundle.parts if part.error]
    assert len(completed) == 1
    assert completed[0].path != slow_path
    assert len(failed) == 1
    assert failed[0].error == f"{slow_path}_timeout"


def test_all_parallel_paths_time_out_without_hanging(monkeypatch) -> None:
    from on_the_porch import plan_executor

    def slow(part, *_args, **_kwargs):
        time.sleep(0.3)
        return _fact(part)

    monkeypatch.setattr(plan_executor, "_execute_sql_part", slow)
    monkeypatch.setattr(plan_executor, "_execute_rag_part", slow)
    budget = create_turn_budget("turn", total_seconds=0.12, finalization_reserve_seconds=0)
    started = time.monotonic()
    with use_turn_budget(budget):
        bundle = plan_executor.execute_plan(
            _plan(),
            "question",
            execute_sql=lambda _sql: {},
            retrieve_rag=lambda _question, _plan: ([], []),
        )
    assert time.monotonic() - started < 0.22
    assert all(part.error and part.error.endswith("_timeout") for part in bundle.parts)


def test_fallback_uses_only_completed_canonical_facts(monkeypatch) -> None:
    from on_the_porch import answer_synthesizer

    monkeypatch.setenv("ANSWER_SYNTHESIS_ENABLED", "false")
    bundle = FactBundle(
        question="question",
        parts=[
            _fact(_plan().parts[0]),
            FactPart("rag", "community", "community", "community", "", error="rag_timeout"),
        ],
    )
    answer = answer_synthesizer.synthesize_answer("question", bundle)
    assert answer == "sql completed"
    assert "timeout" not in answer


def test_full_success_fallback_format_is_unchanged(monkeypatch) -> None:
    from on_the_porch import answer_synthesizer

    monkeypatch.setenv("ANSWER_SYNTHESIS_ENABLED", "false")
    bundle = FactBundle(question="question", parts=[_fact(part) for part in _plan().parts])
    assert answer_synthesizer.synthesize_answer("question", bundle) == (
        "sql completed\n\nrag completed"
    )


def test_all_timeout_evidence_uses_controlled_timeout_path(monkeypatch) -> None:
    from on_the_porch import answer_synthesizer

    monkeypatch.setenv("ANSWER_SYNTHESIS_ENABLED", "false")
    bundle = FactBundle(
        question="question",
        parts=[
            FactPart("sql", "events", "events", "events", "", error="sql_timeout"),
            FactPart("rag", "community", "community", "community", "", error="rag_timeout"),
        ],
    )
    with pytest.raises(BudgetExhausted):
        answer_synthesizer.synthesize_answer("question", bundle)


def test_all_capacity_evidence_uses_controlled_overload_path(monkeypatch) -> None:
    from on_the_porch import answer_synthesizer

    monkeypatch.setenv("ANSWER_SYNTHESIS_ENABLED", "false")
    bundle = FactBundle(
        question="question",
        parts=[
            FactPart("sql", "events", "events", "events", "", error="capacity_unavailable"),
            FactPart("rag", "community", "community", "community", "", error="capacity_unavailable"),
        ],
    )
    with pytest.raises(CapacityUnavailable):
        answer_synthesizer.synthesize_answer("question", bundle)


def test_partial_capacity_failure_uses_only_completed_facts(monkeypatch) -> None:
    from on_the_porch import answer_synthesizer

    monkeypatch.setenv("ANSWER_SYNTHESIS_ENABLED", "false")
    bundle = FactBundle(
        question="question",
        parts=[
            _fact(_plan().parts[0]),
            FactPart("rag", "community", "community", "community", "", error="capacity_unavailable"),
        ],
    )
    assert answer_synthesizer.synthesize_answer("question", bundle) == "sql completed"


def test_optional_enrichment_timeout_keeps_original_answer(monkeypatch) -> None:
    from on_the_porch import unified_chatbot

    monkeypatch.setattr(unified_chatbot, "_should_trigger_boston_gov_fallback", lambda _answer: True)
    monkeypatch.setattr(
        unified_chatbot,
        "run_bounded_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BudgetExhausted("enrichment")),
    )
    assert unified_chatbot.try_boston_gov_enrichment(
        "question", "confirmed original", timeout_seconds=1
    ) == "confirmed original"
