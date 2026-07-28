#!/usr/bin/env python3
"""Run the local, deterministic On the Porch conversation quality gate."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = HERE / "scenarios.yaml"
DEFAULT_FIXTURES = HERE / "fixtures" / "baseline_responses.json"
DEFAULT_REPORTS = HERE / "reports"
DEFAULT_QUALITY_GATE = HERE / "quality_gate.yaml"
DEFAULT_CALIBRATED_BASELINE = (
    HERE.parents[1] / "reports" / "stage2_5" / "calibrated_baseline.json"
)

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from evaluators import (  # noqa: E402
    ScenarioValidationError,
    evaluate_scenario,
    load_and_validate_corpus,
)
from reporting import build_report, write_report  # noqa: E402


def _load_responses(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("responses"), dict):
        raise ValueError("Fixture/response file must contain a responses object")
    responses: Dict[str, List[Dict[str, Any]]] = {}
    for scenario_id, turns in value["responses"].items():
        if not isinstance(turns, list):
            raise ValueError(f"Responses for {scenario_id} must be a list")
        responses[str(scenario_id)] = [
            dict(turn) if isinstance(turn, Mapping) else {} for turn in turns
        ]
    return responses


def _evaluate_response_map(
    corpus: Mapping[str, Any],
    responses: Mapping[str, List[Dict[str, Any]]],
    *,
    calibrated: bool = False,
) -> tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    reports: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for scenario in corpus["scenarios"]:
        scenario_id = scenario["id"]
        if scenario_id not in responses:
            skipped.append(
                {
                    "scenario_id": scenario_id,
                    "reason": "No fixture or captured response was supplied.",
                }
            )
            continue
        reports.append(
            evaluate_scenario(
                scenario,
                responses[scenario_id],
                calibrated=calibrated,
            )
        )
    return reports, skipped


def _add_quality_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--calibrated",
        action="store_true",
        help="Use the versioned Stage 2.5 evaluator calibration profile.",
    )
    parser.add_argument(
        "--tier",
        choices=("1", "2", "3"),
        help="Evaluate only the scenarios assigned to this quality tier.",
    )
    parser.add_argument(
        "--quality-gate",
        action="store_true",
        help="Apply the calibrated future-release gate to a complete run.",
    )
    parser.add_argument(
        "--quality-gate-config",
        type=Path,
        default=DEFAULT_QUALITY_GATE,
    )
    parser.add_argument(
        "--compare-baseline",
        type=Path,
        help="Compare gate behavior with a committed evaluator report.",
    )
    parser.add_argument(
        "--engineer-review",
        type=Path,
        help="Structured candidate ENGINEER_REVIEW_BY_CODEX JSON.",
    )
    parser.add_argument(
        "--compare-engineer-review",
        type=Path,
        help="Committed baseline ENGINEER_REVIEW_BY_CODEX JSON.",
    )
    parser.add_argument(
        "--known-defects",
        action="store_true",
        help="Evaluate only explicitly recorded Tier 1 known defects.",
    )


def _load_evaluator_report(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    if isinstance(value.get("calibrated_report"), dict):
        return dict(value["calibrated_report"])
    if isinstance(value.get("evaluation"), dict):
        return dict(value["evaluation"])
    if isinstance(value.get("totals"), dict) and isinstance(
        value.get("scenarios"), list
    ):
        return value
    raise ValueError(f"{path} does not contain an evaluator report")


def _filter_corpus(
    corpus: Mapping[str, Any],
    args: argparse.Namespace,
    gate: Mapping[str, Any],
) -> Dict[str, Any]:
    if args.tier and args.known_defects:
        raise ValueError("--tier and --known-defects cannot be combined")
    if args.quality_gate and (args.tier or args.known_defects):
        raise ValueError("--quality-gate requires the complete scenario corpus")
    wanted: Optional[set[str]] = None
    if args.tier:
        wanted = set(gate["tiers"][f"tier{args.tier}"])
    elif args.known_defects:
        wanted = set(gate["tier1_dispositions"]["known_baseline_defect"])
    if wanted is None:
        return dict(corpus)
    filtered = dict(corpus)
    filtered["scenarios"] = [
        scenario for scenario in corpus["scenarios"] if str(scenario["id"]) in wanted
    ]
    return filtered


def _local_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise argparse.ArgumentTypeError("Local API URL must use http or https")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise argparse.ArgumentTypeError("Refusing non-loopback API URL")
    return value.rstrip("/")


class LocalApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str,
        csrf_token: str,
        timeout: float,
    ) -> None:
        self.base_url = base_url
        self.bearer_token = bearer_token
        self.csrf_token = csrf_token
        self.timeout = timeout

    def _post(self, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
                "X-CSRF-Token": self.csrf_token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Local API returned HTTP {exc.code}: {body}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Local API response was not an object")
        return value

    def create_thread(self, title: str) -> str:
        value = self._post("/conversations", {"title": title})
        thread = (
            value.get("thread") if isinstance(value.get("thread"), Mapping) else value
        )
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        if not thread_id:
            raise RuntimeError("Local API did not return a conversation ID")
        return str(thread_id)

    def send(self, thread_id: str, message: str) -> Dict[str, Any]:
        value = self._post(
            f"/conversations/{urllib.parse.quote(thread_id)}/messages",
            {"message": message},
        )
        assistant = value.get("assistant_message")
        if not isinstance(assistant, Mapping):
            raise RuntimeError("Local API did not return an assistant message")
        sources = assistant.get("sources") or value.get("sources") or []
        normalized_sources = []
        for source in sources if isinstance(sources, list) else []:
            if not isinstance(source, Mapping):
                continue
            normalized_sources.append(
                {
                    **dict(source),
                    "category": (
                        source.get("category")
                        or source.get("source_type")
                        or source.get("type")
                        or "none"
                    ),
                }
            )
        metadata = assistant.get("metadata")
        return {
            "answer": str(assistant.get("content") or ""),
            "sources": normalized_sources,
            "result_ids": value.get("result_ids") or [],
            "metadata": metadata if isinstance(metadata, Mapping) else {},
            "clarification": bool(value.get("clarification")),
        }


def _run_local_api(
    corpus: Mapping[str, Any],
    args: argparse.Namespace,
) -> Dict[str, List[Dict[str, Any]]]:
    if not args.allow_local_api:
        raise RuntimeError("Local API mode requires --allow-local-api")
    if not args.bearer_token or not args.csrf_token:
        raise RuntimeError("Local API mode requires --bearer-token and --csrf-token")
    client = LocalApiClient(
        args.base_url,
        bearer_token=args.bearer_token,
        csrf_token=args.csrf_token,
        timeout=args.timeout,
    )
    wanted = set(args.scenario_id or [])
    responses: Dict[str, List[Dict[str, Any]]] = {}
    for scenario in corpus["scenarios"]:
        if wanted and scenario["id"] not in wanted:
            continue
        thread_id = client.create_thread(f"Regression: {scenario['id']}")
        responses[scenario["id"]] = [
            client.send(thread_id, turn["user"]) for turn in scenario["turns"]
        ]
    return responses


def _print_validation(summary: Mapping[str, Any]) -> None:
    print(f"Validated {summary['scenario_count']} scenarios.")
    print(f"Multi-turn scenarios: {summary['multi_turn_count']}")
    for category, count in summary["coverage_counts"].items():
        print(f"  {category}: {count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=DEFAULT_SCENARIOS,
        help="JSON-compatible YAML scenario corpus",
    )
    subparsers = parser.add_subparsers(dest="command")

    validate = subparsers.add_parser(
        "validate",
        help="Validate schema, tiers, and gate configuration.",
    )
    _add_quality_options(validate)

    fixture = subparsers.add_parser("fixture", help="Replay local fixture responses")
    fixture.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    fixture.add_argument("--output-dir", type=Path, default=DEFAULT_REPORTS)
    fixture.add_argument("--stem", default="fixture-replay")
    fixture.add_argument(
        "--require-all",
        action="store_true",
        help="Fail when a scenario has no fixture response",
    )
    _add_quality_options(fixture)

    report = subparsers.add_parser("report", help="Evaluate a captured response file")
    report.add_argument("--responses", type=Path, required=True)
    report.add_argument("--output-dir", type=Path, default=DEFAULT_REPORTS)
    report.add_argument("--stem", default="conversation-regression")
    report.add_argument("--require-all", action="store_true")
    _add_quality_options(report)

    api = subparsers.add_parser(
        "api",
        help="Explicitly run selected scenarios against a manually started local API",
    )
    api.add_argument("--base-url", type=_local_url, default="http://127.0.0.1:8888")
    api.add_argument("--allow-local-api", action="store_true")
    api.add_argument("--bearer-token")
    api.add_argument("--csrf-token")
    api.add_argument("--timeout", type=float, default=45.0)
    api.add_argument("--scenario-id", action="append")
    api.add_argument("--output-dir", type=Path, default=DEFAULT_REPORTS)
    api.add_argument("--stem", default="local-api")
    _add_quality_options(api)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "validate"
    try:
        corpus, summary = load_and_validate_corpus(args.scenarios)
        if command == "validate" and not hasattr(args, "quality_gate_config"):
            args.quality_gate_config = DEFAULT_QUALITY_GATE
            args.tier = None
            args.known_defects = False
            args.quality_gate = False
            args.calibrated = False
            args.compare_baseline = None
            args.engineer_review = None
            args.compare_engineer_review = None

        from calibration import (  # noqa: E402
            evaluate_quality_gate,
            load_quality_gate,
            validate_quality_gate,
        )

        gate = load_quality_gate(args.quality_gate_config)
        gate_summary = validate_quality_gate(
            gate,
            (str(item["id"]) for item in corpus["scenarios"]),
        )
        if command == "validate":
            _print_validation(summary)
            print(
                "Quality tiers: "
                + ", ".join(
                    f"{name}={count}"
                    for name, count in gate_summary["tier_counts"].items()
                )
            )
            return 0
        selected_corpus = _filter_corpus(corpus, args, gate)
        if command == "fixture":
            responses = _load_responses(args.fixtures)
            mode = "fixture"
        elif command == "report":
            responses = _load_responses(args.responses)
            mode = "captured_responses"
        elif command == "api":
            responses = _run_local_api(selected_corpus, args)
            mode = "local_api"
        else:
            raise RuntimeError(f"Unknown command: {command}")
        reports, skipped = _evaluate_response_map(
            selected_corpus,
            responses,
            calibrated=args.calibrated or args.quality_gate,
        )
        report = build_report(
            reports,
            mode=mode + ("_calibrated" if args.calibrated or args.quality_gate else ""),
            baseline=selected_corpus["baseline"],
            skipped=skipped,
        )
        paths = write_report(report, args.output_dir, args.stem)
        totals = report["totals"]
        print(
            f"Evaluated {totals['evaluated']}: "
            f"{totals['passed']} passed, {totals['failed']} failed, "
            f"{totals['skipped']} skipped."
        )
        print(f"JSON report: {paths['json']}")
        print(f"Markdown report: {paths['markdown']}")
        if getattr(args, "require_all", False) and skipped:
            return 1
        if args.quality_gate or args.known_defects:
            baseline_path = args.compare_baseline or DEFAULT_CALIBRATED_BASELINE
            baseline_report = _load_evaluator_report(baseline_path)
            gate_config = gate
            if args.known_defects:
                gate_config = json.loads(json.dumps(gate))
                gate_config["tier1_dispositions"]["hard_gate_passing_baseline"] = []
                gate_config["tier1_dispositions"][
                    "blocked_by_fixture_or_environment"
                ] = []
                gate_config["require_engineer_review_comparison"] = False
            candidate_engineer_review = (
                json.loads(args.engineer_review.read_text(encoding="utf-8"))
                if args.engineer_review
                else None
            )
            baseline_engineer_review = (
                json.loads(args.compare_engineer_review.read_text(encoding="utf-8"))
                if args.compare_engineer_review
                else None
            )
            gate_result = evaluate_quality_gate(
                report,
                baseline_report,
                gate_config,
                candidate_engineer_review=candidate_engineer_review,
                baseline_engineer_review=baseline_engineer_review,
            )
            print("Quality gate: " + ("PASS" if gate_result["passed"] else "FAIL"))
            for blocker in gate_result["blockers"]:
                print(f"  BLOCKER: {blocker}")
            return 0 if gate_result["passed"] else 1
        return 1 if totals["failed"] or totals["critical_failures"] else 0
    except (OSError, ValueError, ScenarioValidationError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
