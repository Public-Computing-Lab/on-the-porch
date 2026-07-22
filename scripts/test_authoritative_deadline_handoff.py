#!/usr/bin/env python3
"""Unit tests for the shared 15s authoritative legacy-handoff deadline."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "api"), str(ROOT / "on_the_porch")]

from request_budget import (  # noqa: E402
    current_deadline,
    remaining_seconds,
    reset_request_budget,
    set_request_deadline,
)
from request_metrics import reset_request_metrics  # noqa: E402
from answering_kernel import GlobalDeadline  # noqa: E402


def test_reset_metrics_can_preserve_deadline() -> None:
    reset_request_budget(30.0)
    first = current_deadline()
    assert first is not None
    time.sleep(0.05)
    reset_request_metrics(reset_budget=False)
    assert current_deadline() == first
    reset_request_metrics(reset_budget=True)
    assert current_deadline() != first


def test_authoritative_deadline_is_fifteen_seconds() -> None:
    started = time.perf_counter()
    deadline = GlobalDeadline.from_started_at(started, hard_ceiling_seconds=15.0)
    set_request_deadline(deadline.deadline)
    remaining = remaining_seconds(floor=0.0)
    assert 14.0 <= remaining <= 15.0
    assert deadline.remaining(reserve_for_persistence=True) < remaining
    assert deadline.persistence_reserve_seconds >= 0.25


def test_thirty_second_budget_must_not_override_installed_deadline() -> None:
    started = time.perf_counter() - 5.0
    deadline = GlobalDeadline.from_started_at(started, hard_ceiling_seconds=15.0)
    set_request_deadline(deadline.deadline)
    before = current_deadline()
    reset_request_metrics(reset_budget=False)
    assert current_deadline() == before
    assert remaining_seconds(floor=0.0) <= 10.0 + 0.5


def test_legacy_handoff_budget_helper_requires_four_seconds() -> None:
    import api_v2

    started = time.perf_counter() - 12.0  # ~3s wall remaining before reserve
    deadline = GlobalDeadline.from_started_at(started, hard_ceiling_seconds=15.0)
    assert api_v2._legacy_handoff_budget_exhausted(deadline)
    started_fresh = time.perf_counter()
    fresh = GlobalDeadline.from_started_at(started_fresh, hard_ceiling_seconds=15.0)
    assert not api_v2._legacy_handoff_budget_exhausted(fresh)


def test_application_ceiling_stops_before_public_deadline() -> None:
    started = time.perf_counter() - 14.05
    deadline = GlobalDeadline.from_started_at(started, hard_ceiling_seconds=15.0)
    assert deadline.application_remaining(reserve_for_persistence=False) == 0.0
    assert deadline.remaining(reserve_for_persistence=False) > 0.0
    assert deadline.expired(reserve_for_persistence=False)
    import api_v2

    assert api_v2._legacy_handoff_budget_exhausted(deadline)


if __name__ == "__main__":
    test_reset_metrics_can_preserve_deadline()
    test_authoritative_deadline_is_fifteen_seconds()
    test_thirty_second_budget_must_not_override_installed_deadline()
    test_legacy_handoff_budget_helper_requires_four_seconds()
    test_application_ceiling_stops_before_public_deadline()
    print("authoritative deadline handoff tests passed")
