#!/usr/bin/env python3
"""Deterministic fake-provider load characterization for Stage 4."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import resource
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "api", ROOT / "on_the_porch"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from concurrency_control import CapacityUnavailable, ConcurrencyGate  # noqa: E402
from request_budget import BudgetExhausted, create_turn_budget, use_turn_budget  # noqa: E402
from retry_policy import RetryPolicy  # noqa: E402


ROUTE_MIX = {
    "prepared_sql": 40,
    "rag": 30,
    "hybrid": 15,
    "follow_up": 10,
    "dynamic_sql": 5,
}
CONCURRENCY_LEVELS = (1, 5, 10, 20, 40)
WORKER_MATRIX = ((2, 4), (3, 4), (2, 8))


class TransientProviderError(RuntimeError):
    status_code = 503


@dataclass(frozen=True)
class WorkItem:
    index: int
    turn_id: str
    route: str
    transport: str
    duplicate_of: Optional[str] = None
    replay: bool = False
    transient: bool = False
    slow_model: bool = False
    slow_embedding: bool = False
    slow_enrichment: bool = False


@dataclass
class Result:
    turn_id: str
    route: str
    transport: str
    status: str
    latency_ms: float
    first_token_ms: Optional[float]
    model_calls: int
    embedding_calls: int
    rag_calls: int
    sql_calls: int
    retries: int
    replayed: bool
    duplicate_suppressed: bool
    queue_wait_ms: float
    sql_validation_ms: float
    persistence_ms: float
    mysql_pool_wait_ms: float


def percentile(values: Iterable[float], value: float) -> float:
    ordered = sorted(float(item) for item in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * value
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def build_workload(seed: int, *, base_count: int = 100) -> list[WorkItem]:
    if base_count % 100:
        raise ValueError("base_count must be divisible by 100")
    routes: list[str] = []
    multiplier = base_count // 100
    for route, percent in ROUTE_MIX.items():
        routes.extend([route] * percent * multiplier)
    random.Random(seed).shuffle(routes)
    items = [
        WorkItem(
            index=index,
            turn_id=f"turn-{index:04d}",
            route=route,
            transport="sse" if index % 3 == 0 else "json",
            transient=index % 37 == 0 and route != "prepared_sql",
            slow_model=index % 29 == 0 and route != "prepared_sql",
            slow_embedding=index % 31 == 0 and route in {"rag", "hybrid"},
            slow_enrichment=index % 43 == 0 and route in {"rag", "hybrid"},
        )
        for index, route in enumerate(routes)
    ]
    # Five completed replays and ten concurrent duplicate submissions.
    items.extend(
        WorkItem(
            base_count + index,
            f"replay-{index}",
            "prepared_sql",
            "json",
            replay=True,
        )
        for index in range(5)
    )
    items.extend(
        WorkItem(
            base_count + 5 + index,
            items[index].turn_id,
            items[index].route,
            "sse" if items[index].transport == "json" else "json",
            duplicate_of=items[index].turn_id,
        )
        for index in range(10)
    )
    random.Random(seed + 1).shuffle(items)
    return items


class TurnCoordinator:
    def __init__(self, completed: Iterable[str] = ()) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, threading.Event, Optional[Result]]] = {
            turn_id: ("completed", threading.Event(), None) for turn_id in completed
        }
        for _status, event, _result in self._entries.values():
            event.set()
        self.executions = 0
        self.duplicate_suppressions = 0

    def reserve(self, turn_id: str) -> tuple[str, threading.Event, Optional[Result]]:
        with self._lock:
            existing = self._entries.get(turn_id)
            if existing is not None:
                self.duplicate_suppressions += 1
                return existing
            event = threading.Event()
            self._entries[turn_id] = ("running", event, None)
            self.executions += 1
            return "owner", event, None

    def finish(self, turn_id: str, result: Result) -> None:
        with self._lock:
            _status, event, _old = self._entries[turn_id]
            self._entries[turn_id] = ("completed", event, result)
            event.set()

    def result(self, turn_id: str) -> Optional[Result]:
        with self._lock:
            entry = self._entries.get(turn_id)
            return entry[2] if entry else None


class FakeRuntime:
    def __init__(self, *, bounded: bool = True) -> None:
        limits = {
            "llm": 2 if bounded else 1_000,
            "embedding": 2 if bounded else 1_000,
            "heavy_rag": 3 if bounded else 1_000,
            "dynamic_sql": 1 if bounded else 1_000,
            "enrichment": 1 if bounded else 1_000,
        }
        wait = 0.008 if bounded else 1.0
        self._queue_lock = threading.Lock()
        self.queue_waits: list[float] = []
        self.gates = {
            name: ConcurrencyGate(
                name,
                limit,
                max_queue_wait_seconds=wait,
                observer=self._observe_gate,
            )
            for name, limit in limits.items()
        }
        self.provider_active = {name: 0 for name in limits}
        self.provider_max = {name: 0 for name in limits}
        self._active_lock = threading.Lock()
        self.retry_policy = RetryPolicy(
            max_attempts=2,
            base_backoff_seconds=0.001,
            max_backoff_seconds=0.001,
            minimum_attempt_seconds=0.002,
            jitter_ratio=0,
        )

    def _observe_gate(self, **fields) -> None:
        wait_ms = fields.get("gate_queue_wait_ms")
        if wait_ms is not None and fields.get("gate_result") in {"acquired", "rejected"}:
            with self._queue_lock:
                self.queue_waits.append(float(wait_ms))

    def call(self, gate_name: str, duration: float, budget, counters: dict[str, int]) -> None:
        with self.gates[gate_name].lease(
            budget=budget,
            minimum_execution_seconds=0.001,
        ):
            with self._active_lock:
                self.provider_active[gate_name] += 1
                self.provider_max[gate_name] = max(
                    self.provider_max[gate_name], self.provider_active[gate_name]
                )
            try:
                finished = time.monotonic() + duration
                while time.monotonic() < finished:
                    budget.raise_if_exhausted(gate_name)
                    time.sleep(min(0.002, max(0.0, finished - time.monotonic())))
                budget.raise_if_exhausted(gate_name)
                counters[f"{gate_name}_calls"] = counters.get(f"{gate_name}_calls", 0) + 1
            finally:
                with self._active_lock:
                    self.provider_active[gate_name] -= 1

    def execute(self, item: WorkItem, coordinator: TurnCoordinator) -> Result:
        started = time.monotonic()
        kind, event, stored = coordinator.reserve(item.turn_id)
        if kind == "completed":
            return Result(
                item.turn_id, item.route, item.transport, "completed",
                (time.monotonic() - started) * 1000, None, 0, 0, 0, 0, 0,
                True, True, 0, 0, 0, 0,
            )
        if kind == "running":
            event.wait(timeout=0.085)
            replay = coordinator.result(item.turn_id)
            return Result(
                item.turn_id, item.route, item.transport,
                replay.status if replay else "timed_out",
                (time.monotonic() - started) * 1000, None, 0, 0, 0, 0, 0,
                bool(replay), True, 0, 0, 0, 0,
            )

        budget = create_turn_budget(
            item.turn_id,
            total_seconds=0.08,
            finalization_reserve_seconds=0.01,
        )
        counters: dict[str, int] = {}
        retries = 0
        first_token_ms: Optional[float] = None
        status = "completed"
        queue_before = len(self.queue_waits)
        sql_validation_ms = 0.0
        persistence_ms = 0.0
        mysql_pool_wait_ms = 0.0

        def llm_call() -> None:
            nonlocal retries, first_token_ms
            def operation(attempt: int) -> None:
                nonlocal retries, first_token_ms
                if item.transient and attempt == 1:
                    retries += 1
                    raise TransientProviderError("transient")
                self.call("llm", 0.075 if item.slow_model else 0.004, budget, counters)
                if item.transport == "sse" and first_token_ms is None:
                    first_token_ms = (time.monotonic() - started) * 1000
            self.retry_policy.run(operation, stage="fake_llm", budget=budget)

        try:
            with use_turn_budget(budget):
                if item.route == "prepared_sql":
                    validation_started = time.monotonic()
                    time.sleep(0.002)
                    sql_validation_ms = (time.monotonic() - validation_started) * 1000
                    counters["sql_calls"] = 1
                elif item.route == "rag":
                    self.call("embedding", 0.075 if item.slow_embedding else 0.003, budget, counters)
                    self.call("heavy_rag", 0.006, budget, counters)
                    llm_call()
                elif item.route == "hybrid":
                    counters["sql_calls"] = 1
                    self.call("embedding", 0.075 if item.slow_embedding else 0.003, budget, counters)
                    self.call("heavy_rag", 0.008, budget, counters)
                    llm_call()
                elif item.route == "follow_up":
                    llm_call()
                else:
                    llm_call()
                    self.call("dynamic_sql", 0.006, budget, counters)
                if item.route in {"rag", "hybrid"} and item.index % 7 == 0:
                    self.call(
                        "enrichment",
                        0.075 if item.slow_enrichment else 0.003,
                        budget,
                        counters,
                    )
                budget.raise_if_exhausted("persistence", reserve_finalization=False)
                persistence_started = time.monotonic()
                time.sleep(0.001)
                persistence_ms = (time.monotonic() - persistence_started) * 1000
        except CapacityUnavailable:
            status = "overload"
        except BudgetExhausted:
            status = "timed_out"
        except Exception:
            status = "failed"

        queue_after = len(self.queue_waits)
        queue_wait_ms = (
            sum(self.queue_waits[queue_before:queue_after])
            if queue_after > queue_before
            else 0.0
        )
        result = Result(
            item.turn_id,
            item.route,
            item.transport,
            status,
            (time.monotonic() - started) * 1000,
            first_token_ms,
            counters.get("llm_calls", 0),
            counters.get("embedding_calls", 0),
            counters.get("heavy_rag_calls", 0),
            counters.get("sql_calls", 0) + counters.get("dynamic_sql_calls", 0),
            retries,
            False,
            False,
            queue_wait_ms,
            sql_validation_ms,
            persistence_ms,
            mysql_pool_wait_ms,
        )
        coordinator.finish(item.turn_id, result)
        return result


def _rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def summarize(
    results: list[Result],
    *,
    elapsed: float,
    runtime: FakeRuntime,
    coordinator: TurnCoordinator,
    concurrency: int,
    workers: int,
    threads: int,
    rss_before: float,
    rss_after: float,
    thread_before: int,
    thread_after: int,
) -> dict:
    latencies = [result.latency_ms for result in results]
    queue = list(runtime.queue_waits)
    statuses = {name: sum(result.status == name for result in results) for name in ("completed", "failed", "timed_out", "overload")}
    return {
        "concurrency": concurrency,
        "workers": workers,
        "threads_per_worker": threads,
        "requests": len(results),
        **statuses,
        "throughput_rps": round(len(results) / elapsed, 3),
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50), 3),
            "p90": round(percentile(latencies, 0.90), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "p99": round(percentile(latencies, 0.99), 3),
            "max": round(max(latencies, default=0), 3),
        },
        "first_sse_token_p50_ms": round(
            percentile([r.first_token_ms for r in results if r.first_token_ms is not None], 0.5), 3
        ),
        "queue_ms": {
            "p50": round(percentile(queue, 0.50), 3),
            "p95": round(percentile(queue, 0.95), 3),
            "p99": round(percentile(queue, 0.99), 3),
            "max": round(max(queue, default=0), 3),
        },
        "provider_max_active": dict(runtime.provider_max),
        "retry_count": sum(result.retries for result in results),
        "model_calls": sum(result.model_calls for result in results),
        "embedding_calls": sum(result.embedding_calls for result in results),
        "rag_calls": sum(result.rag_calls for result in results),
        "sql_calls": sum(result.sql_calls for result in results),
        "duplicate_suppressions": coordinator.duplicate_suppressions,
        "logical_executions": coordinator.executions,
        "replay_latency_p50_ms": round(
            percentile([r.latency_ms for r in results if r.replayed], 0.5), 3
        ),
        "sql_validation_p95_ms": round(
            percentile([r.sql_validation_ms for r in results if r.sql_validation_ms], 0.95), 3
        ),
        "persistence_p95_ms": round(
            percentile([r.persistence_ms for r in results if r.persistence_ms], 0.95), 3
        ),
        "mysql_pool_wait_p95_ms": round(
            percentile([r.mysql_pool_wait_ms for r in results], 0.95), 3
        ),
        "rss_before_mb": round(rss_before, 3),
        "rss_after_mb": round(rss_after, 3),
        "rss_growth_mb": round(rss_after - rss_before, 3),
        "threads_before": thread_before,
        "threads_after": thread_after,
        "unfinished_provider_calls": sum(runtime.provider_active.values()),
        "permits_released": all(gate.snapshot().active == 0 for gate in runtime.gates.values()),
    }


def run_once(
    *,
    seed: int,
    concurrency: int,
    workers: int,
    threads: int,
    bounded: bool = True,
) -> dict:
    workload = build_workload(seed)
    completed = [item.turn_id for item in workload if item.replay]
    coordinator = TurnCoordinator(completed)
    runtime = FakeRuntime(bounded=bounded)
    rss_before = _rss_mb()
    thread_before = threading.active_count()
    started = time.monotonic()
    pool_size = max(1, min(concurrency, workers * threads))
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        results = list(pool.map(lambda item: runtime.execute(item, coordinator), workload))
    elapsed = time.monotonic() - started
    deadline = time.monotonic() + 1
    while sum(runtime.provider_active.values()) and time.monotonic() < deadline:
        time.sleep(0.005)
    return summarize(
        results,
        elapsed=elapsed,
        runtime=runtime,
        coordinator=coordinator,
        concurrency=concurrency,
        workers=workers,
        threads=threads,
        rss_before=rss_before,
        rss_after=_rss_mb(),
        thread_before=thread_before,
        thread_after=threading.active_count(),
    )


def build_report(seed: int = 20260731) -> dict:
    levels = [
        run_once(seed=seed, concurrency=level, workers=2, threads=8)
        for level in CONCURRENCY_LEVELS
    ]
    matrix = [
        run_once(seed=seed, concurrency=20, workers=workers, threads=threads)
        for workers, threads in WORKER_MATRIX
    ]
    bounded = run_once(seed=seed, concurrency=40, workers=2, threads=8, bounded=True)
    unbounded = run_once(seed=seed, concurrency=40, workers=2, threads=8, bounded=False)
    return {
        "schema_version": 1,
        "seed": seed,
        "provider_mode": "deterministic_fake",
        "production_access": False,
        "traffic_mix_percent": ROUTE_MIX,
        "transports": ["json", "sse"],
        "concurrency_levels": list(CONCURRENCY_LEVELS),
        "worker_matrix": [{"workers": w, "threads": t} for w, t in WORKER_MATRIX],
        "levels": levels,
        "matrix": matrix,
        "bounded_comparison": {"bounded": bounded, "intentionally_unbounded": unbounded},
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Stage 4 Deterministic Load Results",
        "",
        f"Seed: `{report['seed']}`. Providers: deterministic fake. Production access: no.",
        "",
        "| Concurrency | Completed | Timed out | Overload | RPS | p50 | p95 | p99 | Max |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["levels"]:
        latency = row["latency_ms"]
        lines.append(
            f"| {row['concurrency']} | {row['completed']} | {row['timed_out']} | "
            f"{row['overload']} | {row['throughput_rps']} | {latency['p50']} | "
            f"{latency['p95']} | {latency['p99']} | {latency['max']} |"
        )
    lines.extend([
        "",
        "The worker/thread matrix is a local process-model characterization, not a "
        "DreamHost recommendation. Limits remain process-local and must be multiplied by "
        "the deployed worker count during production review.",
    ])
    return "\n".join(lines) + "\n"


def local_output_dir(value: str) -> Path:
    normalized = value.strip().lower()
    if "://" in normalized or normalized.startswith(("http:", "https:")):
        raise argparse.ArgumentTypeError("remote output is not supported")
    return Path(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--output-dir", type=local_output_dir, default=ROOT / "reports" / "stage4")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = build_report(args.seed)
    if args.check:
        print(json.dumps(report, sort_keys=True))
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "load_results.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "load_results.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
