from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from run_stage4_load import (
    CONCURRENCY_LEVELS,
    ROUTE_MIX,
    WORKER_MATRIX,
    FakeRuntime,
    TurnCoordinator,
    WorkItem,
    build_report,
    build_workload,
    local_output_dir,
    percentile,
    run_once,
)
from build_stage4_reports import artifacts


def test_workload_is_seeded_and_has_exact_logical_route_mix() -> None:
    first = build_workload(17)
    second = build_workload(17)
    assert first == second
    logical = [item for item in first if not item.replay and item.duplicate_of is None]
    assert Counter(item.route for item in logical) == ROUTE_MIX
    assert len(first) == 115
    assert {item.transport for item in first} == {"json", "sse"}


def test_workload_contains_failures_replays_and_duplicate_turns() -> None:
    workload = build_workload(20260731)
    assert any(item.transient for item in workload)
    assert any(item.slow_model for item in workload)
    assert any(item.slow_embedding for item in workload)
    assert any(item.slow_enrichment for item in workload)
    assert sum(item.replay for item in workload) == 5
    assert sum(item.duplicate_of is not None for item in workload) == 10


@pytest.mark.parametrize(
    ("values", "quantile", "expected"),
    [([], 0.5, 0), ([4], 0.99, 4), ([0, 10], 0.5, 5), ([0, 10, 20], 0.95, 19)],
)
def test_percentile_interpolates(values, quantile, expected) -> None:
    assert percentile(values, quantile) == pytest.approx(expected)


def test_duplicate_logical_turn_executes_once() -> None:
    item = WorkItem(1, "same", "prepared_sql", "json")
    coordinator = TurnCoordinator()
    runtime = FakeRuntime()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: runtime.execute(item, coordinator), range(2)))
    assert coordinator.executions == 1
    assert coordinator.duplicate_suppressions == 1
    assert sum(result.sql_calls for result in results) == 1


def test_bounded_run_respects_limits_and_cleans_up() -> None:
    result = run_once(seed=23, concurrency=20, workers=2, threads=8)
    assert result["provider_max_active"]["llm"] <= 2
    assert result["provider_max_active"]["embedding"] <= 2
    assert result["provider_max_active"]["heavy_rag"] <= 3
    assert result["queue_ms"]["max"] <= 20
    assert result["latency_ms"]["max"] <= 90
    assert result["unfinished_provider_calls"] == 0
    assert result["permits_released"] is True
    assert result["threads_after"] == result["threads_before"]


def test_replay_bypasses_all_expensive_work() -> None:
    coordinator = TurnCoordinator(["done"])
    result = FakeRuntime().execute(
        WorkItem(1, "done", "hybrid", "sse", replay=True), coordinator
    )
    assert result.replayed is True
    assert result.model_calls == result.embedding_calls == result.rag_calls == 0
    assert result.sql_calls == 0


def test_report_covers_configured_levels_matrix_and_safe_provider_mode() -> None:
    report = build_report(29)
    assert report["production_access"] is False
    assert report["provider_mode"] == "deterministic_fake"
    assert report["concurrency_levels"] == list(CONCURRENCY_LEVELS)
    assert report["worker_matrix"] == [
        {"workers": workers, "threads": threads} for workers, threads in WORKER_MATRIX
    ]
    assert all(row["permits_released"] for row in report["levels"])
    assert all(row["latency_ms"]["max"] <= 90 for row in report["levels"])


def test_derived_reports_preserve_load_and_quality_evidence() -> None:
    load = build_report(31)
    stage3_quality = {
        "authoritative_calibrated_gate": {
            "tier1_hard_gates_preserved": 9,
            "new_protected_critical_failures": 0,
        }
    }
    built = artifacts(load, stage3_quality)
    assert built["quality_gate.json"]["tier1_hard_gates_preserved"] == 9
    assert built["quality_gate.json"]["prompts_changed"] is False
    assert built["concurrency_results.json"]["permits_released"] is True
    assert built["performance_comparison.json"]["bounded"]["provider_max_active"]["llm"] <= 2


@pytest.mark.parametrize("value", ["https://example.com/report", "http:/example.com"])
def test_remote_output_destination_is_rejected(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        local_output_dir(value)


def test_local_output_destination_is_allowed(tmp_path) -> None:
    assert local_output_dir(str(tmp_path)) == tmp_path
