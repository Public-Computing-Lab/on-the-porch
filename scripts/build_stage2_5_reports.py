#!/usr/bin/env python3
"""Build deterministic Stage 2.5 calibration, triage, and review reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping


ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = ROOT / "tests" / "conversation_regression"
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from calibration import (  # noqa: E402
    REPRODUCIBLE_REPORT_FIELDS,
    build_calibrated_baseline,
    build_engineer_review,
    build_failure_triage,
    calibrated_baseline_markdown,
    engineer_review_markdown,
    evaluate_captured_baseline,
    failure_triage_markdown,
    load_decisions,
    load_quality_gate,
    validate_decisions,
    validate_quality_gate,
)
from evaluators import load_and_validate_corpus  # noqa: E402


DEFAULT_SCENARIOS = HARNESS_DIR / "scenarios.yaml"
DEFAULT_DECISIONS = HARNESS_DIR / "stage2_5_decisions.json"
DEFAULT_GATE = HARNESS_DIR / "quality_gate.yaml"
DEFAULT_STAGE2 = ROOT / "reports" / "stage2" / "conversational_baseline.json"
DEFAULT_OUTPUT = ROOT / "reports" / "stage2_5"


def _load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _stable_report_timestamp(
    report: Dict[str, Any],
    stage2: Mapping[str, Any],
) -> None:
    report["generated_at"] = str(
        stage2.get("generated_at")
        or stage2.get("evaluation", {}).get("generated_at")
        or "2026-07-28T00:00:00+00:00"
    )


def build_artifacts(
    *,
    scenarios_path: Path,
    decisions_path: Path,
    gate_path: Path,
    stage2_path: Path,
) -> Dict[str, Any]:
    corpus, corpus_summary = load_and_validate_corpus(scenarios_path)
    stage2 = _load_object(stage2_path)
    original = stage2.get("evaluation")
    if not isinstance(original, dict):
        raise ValueError(f"{stage2_path} does not contain an evaluation report")

    decisions = load_decisions(decisions_path)
    gate = load_quality_gate(gate_path)
    gate_summary = validate_quality_gate(
        gate,
        (str(item["id"]) for item in corpus["scenarios"]),
    )

    reproduced = evaluate_captured_baseline(corpus, stage2, calibrated=False)
    calibrated = evaluate_captured_baseline(corpus, stage2, calibrated=True)
    _stable_report_timestamp(reproduced, stage2)
    _stable_report_timestamp(calibrated, stage2)

    reproduction_mismatches = [
        field
        for field in REPRODUCIBLE_REPORT_FIELDS
        if original.get(field) != reproduced.get(field)
    ]
    if reproduction_mismatches:
        raise ValueError(
            "Original Stage 2 deterministic report is not reproducible; "
            f"mismatched fields: {reproduction_mismatches}"
        )
    decision_summary = validate_decisions(decisions, reproduced)
    if decision_summary != {
        "failed_scenario_count": 71,
        "failed_critical_check_count": 100,
    }:
        raise ValueError(
            "Stage 2 failure inventory changed unexpectedly: " f"{decision_summary}"
        )

    triage = build_failure_triage(
        corpus,
        stage2,
        reproduced,
        decisions,
        gate,
    )
    if triage["failed_scenario_count"] != 71:
        raise ValueError("Failure triage must classify all 71 failed scenarios")
    if triage["failed_critical_check_count"] != 100:
        raise ValueError("Failure triage must classify all 100 critical failures")

    engineer_review = build_engineer_review(
        corpus,
        reproduced,
        decisions,
        gate,
    )
    if engineer_review["multi_turn_scenario_count"] != 43:
        raise ValueError("Engineer review must include all 43 multi-turn scenarios")

    calibrated_baseline = build_calibrated_baseline(
        original,
        reproduced,
        calibrated,
        triage,
        engineer_review,
        gate,
    )
    calibrated_baseline["corpus_summary"] = corpus_summary
    calibrated_baseline["quality_gate_validation"] = gate_summary

    return {
        "failure_triage.json": triage,
        "failure_triage.md": failure_triage_markdown(triage),
        "engineer_review_by_codex.json": engineer_review,
        "engineer_review_by_codex.md": engineer_review_markdown(engineer_review),
        "calibrated_baseline.json": calibrated_baseline,
        "calibrated_baseline.md": calibrated_baseline_markdown(calibrated_baseline),
    }


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value.rstrip() + "\n"
    return json.dumps(value, indent=2, ensure_ascii=True) + "\n"


def write_artifacts(
    artifacts: Mapping[str, Any],
    output_dir: Path,
    *,
    check: bool,
) -> None:
    mismatches = []
    for name, value in artifacts.items():
        path = output_dir / name
        rendered = _serialize(value)
        if check:
            existing = path.read_text(encoding="utf-8") if path.exists() else None
            if existing != rendered:
                mismatches.append(str(path))
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    if mismatches:
        raise ValueError(
            "Stage 2.5 reports are missing or stale: " + ", ".join(mismatches)
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--quality-gate", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--stage2-report", type=Path, default=DEFAULT_STAGE2)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if committed reports differ from regenerated content.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        artifacts = build_artifacts(
            scenarios_path=args.scenarios,
            decisions_path=args.decisions,
            gate_path=args.quality_gate,
            stage2_path=args.stage2_report,
        )
        write_artifacts(artifacts, args.output_dir, check=args.check)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    triage = artifacts["failure_triage.json"]
    calibrated = artifacts["calibrated_baseline.json"]
    action = "Verified" if args.check else "Wrote"
    print(
        f"{action} Stage 2.5 reports: "
        f"{triage['failed_scenario_count']} failed scenarios and "
        f"{triage['failed_critical_check_count']} critical failures classified."
    )
    print(
        "Calibrated deterministic score: "
        f"{calibrated['calibrated']['passed']}/"
        f"{calibrated['calibrated']['evaluated']} scenarios passed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
