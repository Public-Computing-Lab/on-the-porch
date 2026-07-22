#!/usr/bin/env python3
"""Regression tests for reproducible authoritative freeze hashes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "on_the_porch"), str(ROOT)]

from authoritative_runtime_freeze import (  # noqa: E402
    compute_freeze_hashes,
    semantic_prompt_policy_text,
    verify_authoritative_freeze,
)


def test_prompt_policy_excludes_trusted_state() -> None:
    policy = semantic_prompt_policy_text()
    assert "TRUSTED_TOKENS=" not in policy
    assert "TRUSTED_STATE=" not in policy
    assert "RESIDENT_MESSAGE=" not in policy
    assert "Choose an action for the resident message." in policy


def test_freeze_hashes_are_reproducible() -> None:
    first = compute_freeze_hashes(root=ROOT)
    second = compute_freeze_hashes(root=ROOT)
    assert first == second
    assert len(first["planner_contract_hash"]) == 64
    assert first["prompt_policy_bytes"] == len(
        semantic_prompt_policy_text().encode("utf-8")
    )


def test_committed_freeze_matches_current_files() -> None:
    ok, report = verify_authoritative_freeze(root=ROOT)
    assert ok, report["mismatches"]


if __name__ == "__main__":
    test_prompt_policy_excludes_trusted_state()
    test_freeze_hashes_are_reproducible()
    test_committed_freeze_matches_current_files()
    print("authoritative runtime freeze tests passed")
