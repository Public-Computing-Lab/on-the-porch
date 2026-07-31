from __future__ import annotations

import random

import pytest

from on_the_porch.request_budget import BudgetExhausted, create_turn_budget, use_turn_budget
from on_the_porch.retry_policy import RetryPolicy, classify_retry_error


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


class SQLSafetyError(RuntimeError):
    pass


class CapacityUnavailable(RuntimeError):
    pass


def policy(**changes) -> RetryPolicy:
    values = {
        "max_attempts": 2,
        "base_backoff_seconds": 0,
        "max_backoff_seconds": 0,
        "minimum_attempt_seconds": 0.01,
        "random_source": random.Random(1),
    }
    values.update(changes)
    return RetryPolicy(**values)


def test_successful_first_attempt_is_not_retried() -> None:
    calls = []
    assert policy().run(lambda attempt: calls.append(attempt) or "ok", stage="model") == "ok"
    assert calls == [1]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_http_error_retries_once(status: int) -> None:
    calls = []
    def operation(attempt: int):
        calls.append(attempt)
        if attempt == 1:
            raise ProviderError("provider error", status)
        return "ok"
    assert policy().run(operation, stage="model") == "ok"
    assert calls == [1, 2]


def test_connection_reset_retries_once() -> None:
    calls = []
    def operation(attempt: int):
        calls.append(attempt)
        if attempt == 1:
            raise ConnectionResetError("reset")
        return "ok"
    assert policy().run(operation, stage="model") == "ok"
    assert calls == [1, 2]


def test_permanent_4xx_is_not_retried() -> None:
    calls = []
    with pytest.raises(ProviderError):
        policy().run(
            lambda attempt: calls.append(attempt) or (_ for _ in ()).throw(ProviderError("bad", 400)),
            stage="model",
        )
    assert calls == [1]


@pytest.mark.parametrize(
    "error,category",
    [
        (PermissionError("forbidden"), "authorization"),
        (SQLSafetyError("blocked"), "sql_safety"),
        (CapacityUnavailable("capacity_unavailable"), "capacity_unavailable"),
        (ValueError("invalid"), "invalid_input"),
    ],
)
def test_nontransient_errors_are_not_retried(error: Exception, category: str) -> None:
    assert classify_retry_error(error) == category
    calls = []
    with pytest.raises(type(error)):
        policy().run(
            lambda attempt: calls.append(attempt) or (_ for _ in ()).throw(error),
            stage="operation",
        )
    assert calls == [1]


def test_exhausted_budget_prevents_first_attempt() -> None:
    budget = create_turn_budget("turn", total_seconds=0.01, finalization_reserve_seconds=0)
    budget.cancel("test")
    with use_turn_budget(budget), pytest.raises(BudgetExhausted):
        policy().run(lambda _attempt: "never", stage="model")


def test_insufficient_budget_prevents_retry() -> None:
    budget = create_turn_budget("turn", total_seconds=0.02, finalization_reserve_seconds=0)
    calls = []
    with use_turn_budget(budget), pytest.raises(TimeoutError):
        policy(minimum_attempt_seconds=1).run(
            lambda attempt: calls.append(attempt) or (_ for _ in ()).throw(TimeoutError()),
            stage="model",
        )
    assert calls == [1]


def test_backoff_is_capped_by_remaining_budget() -> None:
    budget = create_turn_budget("turn", total_seconds=0.2, finalization_reserve_seconds=0)
    decision = policy(
        base_backoff_seconds=0.1,
        max_backoff_seconds=0.1,
        minimum_attempt_seconds=0.05,
    ).decision(TimeoutError(), attempt=1, budget=budget, stage="model")
    assert decision.retry
    assert decision.backoff_seconds <= budget.remaining_seconds() - 0.05


def test_maximum_attempts_is_hard_clamped_to_two() -> None:
    assert RetryPolicy(max_attempts=99).max_attempts == 2


def test_observer_receives_retry_and_final_decision() -> None:
    decisions = []
    with pytest.raises(TimeoutError):
        policy().run(
            lambda _attempt: (_ for _ in ()).throw(TimeoutError()),
            stage="model",
            observe=decisions.append,
        )
    assert [decision.retry for decision in decisions] == [True, False]


def test_vector_store_uses_one_central_transient_retry(monkeypatch) -> None:
    import retrieval

    class FakeVectorDB:
        def __init__(self) -> None:
            self.calls = 0

        def similarity_search_by_vector(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("database is locked")
            return []

    vectordb = FakeVectorDB()
    monkeypatch.setattr(retrieval, "BM25_HYBRID_ENABLED", False)
    monkeypatch.setattr(retrieval, "get_query_embedding", lambda _query: [0.1])
    monkeypatch.setattr(retrieval, "_reset_vectordb_singleton", lambda: None)
    budget = create_turn_budget("turn", total_seconds=2, finalization_reserve_seconds=0)
    with use_turn_budget(budget):
        result = retrieval.retrieve("question", vectordb=vectordb)
    assert result["chunks"] == []
    assert vectordb.calls == 2
