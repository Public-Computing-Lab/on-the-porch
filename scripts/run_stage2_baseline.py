#!/usr/bin/env python3
"""Capture the Stage 2 conversational baseline from a confirmed local API."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = ROOT / "tests" / "conversation_regression"
ON_THE_PORCH = ROOT / "on_the_porch"
for path in (HARNESS_DIR, ON_THE_PORCH):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluators import evaluate_scenario, load_and_validate_corpus  # noqa: E402
from reporting import build_report  # noqa: E402
from turn_observability_analysis import distribution  # noqa: E402


REPRESENTATIVE_SCENARIOS = (
    "greet-001",
    "greet-005",
    "event-001",
    "event-003",
    "event-004",
    "event-007",
    "event-010",
    "311-001",
    "311-004",
    "311-006",
    "311-009",
    "311-010",
    "safety-001",
    "safety-002",
    "safety-004",
    "safety-005",
    "policy-001",
    "community-002",
    "community-007",
    "entity-001",
    "cross-001",
    "cross-003",
    "uncertain-001",
    "uncertain-004",
    "uncertain-006",
)


def _local_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise argparse.ArgumentTypeError("API URL must use HTTP or HTTPS")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise argparse.ArgumentTypeError("Refusing a non-loopback API URL")
    return value.rstrip("/")


def _load_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def confirm_safe_environment(root: Path, base_url: str) -> Dict[str, Any]:
    values = _load_env(root / ".env")
    host = values.get("MYSQL_HOST", "")
    database = values.get("MYSQL_DB", "")
    production_mode = values.get("PRODUCTION_MODE", "false").lower()
    if host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("Refusing live baseline: MySQL host is not local")
    if database != "sentiment_demo":
        raise RuntimeError("Refusing live baseline: database is not sentiment_demo")
    if production_mode in {"1", "true", "yes", "on"}:
        raise RuntimeError("Refusing live baseline: PRODUCTION_MODE is enabled")
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "database_host_category": "local",
        "database_name": "sentiment_demo",
        "database_user_category": (
            "demo" if values.get("MYSQL_USER") == "demo_user" else "local_redacted"
        ),
        "vector_provider": values.get("VECTOR_DB_PROVIDER", "chroma"),
        "vector_path_category": "local_repository_snapshot",
        "embedding_provider": values.get("EMBED_PROVIDER", "unknown"),
        "api_url": base_url,
        "frontend_port": None,
        "external_model_calls": bool(values.get("GEMINI_API_KEY")),
        "write_scope": "local demo users, conversations, messages, and interaction logs",
        "production_access": False,
    }


class LocalClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.token = ""
        self.csrf = ""

    def _request(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        authenticated: bool,
        scenario_marker: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-CSRF-Token"] = self.csrf
        if scenario_marker:
            headers["X-Stage2-Scenario-ID"] = scenario_marker
        request = urllib.request.Request(
            self.base_url + path,
            method="POST",
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
                return int(response.status), body if isinstance(body, dict) else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"error": f"HTTP {exc.code}"}
            return int(exc.code), parsed if isinstance(parsed, dict) else {}

    def create_guest(self) -> None:
        status, value = self._request("/auth/guest", {}, authenticated=False)
        if status != 201:
            raise RuntimeError(f"Local guest bootstrap failed with HTTP {status}")
        self.token = str(value.get("session_token") or "")
        self.csrf = str(value.get("csrf_token") or "")
        if not self.token or not self.csrf:
            raise RuntimeError("Local guest bootstrap omitted auth tokens")

    def create_thread(self, title: str) -> str:
        status, value = self._request(
            "/conversations",
            {"title": title},
            authenticated=True,
        )
        if status != 201:
            raise RuntimeError(f"Local thread creation failed with HTTP {status}")
        thread = value.get("thread") if isinstance(value.get("thread"), Mapping) else value
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        if not thread_id:
            raise RuntimeError("Local thread creation omitted the thread ID")
        return str(thread_id)

    def send(
        self,
        thread_id: str,
        message: str,
        scenario_marker: str,
    ) -> Tuple[int, Dict[str, Any]]:
        return self._request(
            f"/conversations/{urllib.parse.quote(thread_id)}/messages",
            {"message": message},
            authenticated=True,
            scenario_marker=scenario_marker,
        )


def _source_categories(source: Mapping[str, Any]) -> List[str]:
    category = str(source.get("category") or source.get("source_type") or "").strip()
    if category and category not in {"sql", "rag", "event"}:
        return [category]

    table = str(source.get("table") or "").lower()
    doc_type = str(source.get("doc_type") or "").lower()
    source_type = str(source.get("type") or "").lower()
    if source_type == "event" or "event" in table or doc_type == "calendar_event":
        return ["event_listing"]
    if source_type == "sql":
        return ["official_data"]
    if doc_type == "transcript":
        return ["meeting_transcript", "community_material"]
    if doc_type == "policy":
        return ["official_document"]
    return [category or source_type or "none"]


def _normalize_sources(value: Any) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for source in value if isinstance(value, list) else []:
        if not isinstance(source, Mapping):
            continue
        for category in _source_categories(source):
            output.append({**dict(source), "category": category})
    return output


def _markdown_summary(report: Mapping[str, Any]) -> str:
    totals = report["evaluation"]["totals"]
    latency = report["latency"]
    lines = [
        "# Stage 2 Conversational Baseline",
        "",
        f"- Environment: **{report['environment']['database_host_category']} demo**",
        f"- Scenarios attempted: **{report['scenario_count']}**",
        f"- Turns attempted: **{report['turn_count']}**",
        f"- Deterministic scenarios passed: **{totals['passed']}**",
        f"- Deterministic scenarios failed: **{totals['failed']}**",
        f"- Blocked scenarios: **{len(report['blocked_scenarios'])}**",
        (
            "- HTTP latency: "
            f"p50 **{latency['overall']['p50_ms']:.1f} ms**, "
            f"p90 **{latency['overall']['p90_ms']:.1f} ms**, "
            f"p95 **{latency['overall']['p95_ms']:.1f} ms**, "
            f"p99 **{latency['overall']['p99_ms']:.1f} ms**"
        ),
        "",
        "## Latency by Mode",
        "",
        "| Mode | Count | p50 ms | p90 ms | p95 ms | p99 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode, values in report["latency"]["by_mode"].items():
        lines.append(
            f"| {mode} | {values['count']} | {values['p50_ms']:.1f} | "
            f"{values['p90_ms']:.1f} | {values['p95_ms']:.1f} | "
            f"{values['p99_ms']:.1f} |"
        )
    lines.extend(["", "## Blocked Scenarios", ""])
    if report["blocked_scenarios"]:
        for item in report["blocked_scenarios"]:
            lines.append(f"- `{item['scenario_id']}`: {item['reason']}")
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "Qualitative review is recorded separately as "
            "`ENGINEER_REVIEW_BY_CODEX`; no external judge model was used.",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if not args.allow_local_api:
        raise RuntimeError("Live capture requires --allow-local-api")
    environment = confirm_safe_environment(ROOT, args.base_url)
    corpus, _ = load_and_validate_corpus(args.scenarios)
    wanted = set(args.scenario_id or ())
    if not wanted and not args.all:
        wanted = set(REPRESENTATIVE_SCENARIOS)
    scenarios = [
        scenario
        for scenario in corpus["scenarios"]
        if args.all or scenario["id"] in wanted
    ]
    if wanted:
        missing = wanted - {scenario["id"] for scenario in scenarios}
        if missing:
            raise RuntimeError(f"Unknown scenario IDs: {', '.join(sorted(missing))}")

    client = LocalClient(args.base_url, args.timeout)
    client.create_guest()
    response_map: Dict[str, List[Dict[str, Any]]] = {}
    captures: List[Dict[str, Any]] = []
    blocked: List[Dict[str, str]] = []

    for scenario in scenarios:
        scenario_id = str(scenario["id"])
        try:
            thread_id = client.create_thread(f"Stage 2: {scenario_id}")
        except Exception as exc:
            blocked.append(
                {"scenario_id": scenario_id, "reason": exc.__class__.__name__}
            )
            continue
        response_map[scenario_id] = []
        supplied_history: List[Dict[str, str]] = []
        scenario_capture = {
            "scenario_id": scenario_id,
            "title": scenario["title"],
            "domain": scenario["domain"],
            "conversation_category": scenario["conversation_category"],
            "turns": [],
        }
        for turn_index, turn in enumerate(scenario["turns"]):
            user_message = str(turn["user"])
            marker = f"{scenario_id}:{turn_index}"
            started = time.perf_counter()
            status = 0
            error = None
            timeout = False
            value: Dict[str, Any] = {}
            try:
                status, value = client.send(thread_id, user_message, marker)
            except TimeoutError:
                timeout = True
                error = "timeout"
            except Exception as exc:
                error = exc.__class__.__name__
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            assistant = (
                value.get("assistant_message")
                if isinstance(value.get("assistant_message"), Mapping)
                else {}
            )
            answer = str(assistant.get("content") or "")
            sources = _normalize_sources(assistant.get("sources") or value.get("sources"))
            mode = str(assistant.get("response_mode") or value.get("mode") or "unknown")
            evaluation_response = {
                "answer": answer,
                "sources": sources,
                "result_ids": value.get("result_ids") or [],
                "metadata": (
                    assistant.get("metadata")
                    if isinstance(assistant.get("metadata"), Mapping)
                    else {}
                ),
                "clarification": bool(value.get("clarification")),
            }
            response_map[scenario_id].append(evaluation_response)
            turn_capture = {
                "scenario_id": scenario_id,
                "turn_index": turn_index,
                "user_message": user_message,
                "conversation_history_supplied": list(supplied_history),
                "assistant_answer": answer,
                "sources": sources,
                "http_status": status,
                "transport": "json",
                "route": mode,
                "mode": mode,
                "latency_ms": latency_ms,
                "error": error or (
                    f"http_{status}" if status and status >= 400 else None
                ),
                "timeout": timeout,
                "environment": environment,
            }
            scenario_capture["turns"].append(turn_capture)
            captures.append(turn_capture)
            supplied_history.append({"role": "user", "content": user_message})
            supplied_history.append({"role": "assistant", "content": answer})
        args.scenario_output.mkdir(parents=True, exist_ok=True)
        (args.scenario_output / f"{scenario_id}.json").write_text(
            json.dumps(scenario_capture, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    evaluations = [
        evaluate_scenario(scenario, response_map[scenario["id"]])
        for scenario in scenarios
        if scenario["id"] in response_map
    ]
    skipped = [
        {"scenario_id": item["scenario_id"], "reason": item["reason"]}
        for item in blocked
    ]
    evaluation_report = build_report(
        evaluations,
        mode="stage2_local_api",
        baseline=corpus["baseline"],
        skipped=skipped,
    )
    latencies = [
        float(turn["latency_ms"])
        for turn in captures
        if not turn["error"] and not turn["timeout"]
    ]
    by_mode_values: Dict[str, List[float]] = {}
    for turn in captures:
        if turn["error"] or turn["timeout"]:
            continue
        by_mode_values.setdefault(str(turn["mode"]), []).append(
            float(turn["latency_ms"])
        )
    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment,
        "scenario_count": len(scenarios),
        "turn_count": len(captures),
        "blocked_scenarios": blocked,
        "latency": {
            "overall": distribution(latencies),
            "by_mode": {
                mode: distribution(values)
                for mode, values in sorted(by_mode_values.items())
            },
        },
        "evaluation": evaluation_report,
        "turns": captures,
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        type=_local_url,
        default="http://127.0.0.1:8888",
    )
    parser.add_argument("--allow-local-api", action="store_true")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=HARNESS_DIR / "scenarios.yaml",
    )
    parser.add_argument("--scenario-id", action="append")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "stage2" / "conversational_baseline.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=ROOT / "reports" / "stage2" / "conversational_baseline.md",
    )
    parser.add_argument(
        "--scenario-output",
        type=Path,
        default=ROOT / "reports" / "stage2" / "scenario_results",
    )
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        report = run(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        args.markdown_output.write_text(
            _markdown_summary(report),
            encoding="utf-8",
        )
        latency_path = args.output.with_name("latency_baseline.json")
        latency_path.write_text(
            json.dumps(report["latency"], indent=2) + "\n",
            encoding="utf-8",
        )
        latency_path.with_suffix(".md").write_text(
            _markdown_summary(report),
            encoding="utf-8",
        )
        blocked_path = args.output.with_name("blocked_scenarios.md")
        blocked_lines = ["# Blocked Stage 2 Scenarios", ""]
        if report["blocked_scenarios"]:
            blocked_lines.extend(
                f"- `{item['scenario_id']}`: {item['reason']}"
                for item in report["blocked_scenarios"]
            )
        else:
            blocked_lines.append("None.")
        blocked_path.write_text("\n".join(blocked_lines) + "\n", encoding="utf-8")
        print(
            f"Captured {report['scenario_count']} scenarios and "
            f"{report['turn_count']} turns."
        )
        print(f"Report: {args.output}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
