from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ON_THE_PORCH = ROOT / "on_the_porch"
if str(ON_THE_PORCH) not in sys.path:
    sys.path.insert(0, str(ON_THE_PORCH))

import turn_observability as obs  # noqa: E402
from turn_observability_analysis import (  # noqa: E402
    aggregate_traces,
    load_traces,
    percentile,
)


class TurnObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = mock.patch.dict(
            os.environ,
            {
                "TURN_OBSERVABILITY_ENABLED": "true",
                "TURN_OBSERVABILITY_SINK": "none",
                "TURN_OBSERVABILITY_SAMPLE_RATE": "1",
                "TURN_OBSERVABILITY_LOG_TEXT": "false",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _trace(self):
        trace = obs.begin_turn(
            request_id="request-1",
            conversation_id="conversation-1",
            user_category="guest",
            transport="json",
            scenario_id="private-scenario",
        )
        self.assertIsNotNone(trace)
        return trace

    def test_trace_creation_and_no_scenario_text_by_default(self) -> None:
        trace = self._trace()
        with obs.bind_trace(trace):
            obs.record_fields(mode="rag", route="community")
        record = obs.finish_turn(trace)
        self.assertEqual(record["event"], "turn_trace")
        self.assertEqual(record["mode"], "rag")
        self.assertIsNone(record["scenario_id"])

    def test_nested_timing_identifies_parent(self) -> None:
        trace = self._trace()
        with obs.bind_trace(trace):
            with obs.stage("outer"):
                with obs.stage("inner"):
                    time.sleep(0.001)
        record = obs.finish_turn(trace)
        inner = next(item for item in record["stages"] if item["name"] == "inner")
        self.assertTrue(inner["nested"])
        self.assertEqual(inner["parent"], "outer")

    def test_failure_timing_preserves_exception(self) -> None:
        trace = self._trace()
        with obs.bind_trace(trace):
            with self.assertRaisesRegex(RuntimeError, "same exception"):
                with obs.stage("failure"):
                    raise RuntimeError("same exception")
        record = obs.finish_turn(trace)
        failed = record["stages"][0]
        self.assertFalse(failed["success"])
        self.assertEqual(failed["error_category"], "runtimeerror")

    def test_redaction(self) -> None:
        value = obs.redact(
            {
                "Authorization": "Bearer abc.def",
                "cookie": "session=secret",
                "email": "resident@example.com",
                "url": "mysql://user:pass@db.example/test",
                "public": "Dorchester",
            }
        )
        self.assertEqual(value["Authorization"], "[REDACTED]")
        self.assertEqual(value["cookie"], "[REDACTED]")
        self.assertEqual(value["email"], "[REDACTED_EMAIL]")
        self.assertEqual(value["url"], "[REDACTED_DATABASE_URL]")
        self.assertEqual(value["public"], "Dorchester")

    def test_disabled_observability(self) -> None:
        with mock.patch.dict(os.environ, {"TURN_OBSERVABILITY_ENABLED": "false"}):
            self.assertIsNone(obs.begin_turn(transport="json"))
            with obs.stage("noop"):
                self.assertIsNone(obs.get_current_trace())

    def test_sink_failure_is_nonfatal(self) -> None:
        trace = self._trace()
        with mock.patch.object(obs, "_emit_trace", side_effect=OSError("disk full")):
            record = obs.finish_turn(trace)
        self.assertEqual(record["completion_status"], "success")

    def test_model_call_accounting_in_request_thread(self) -> None:
        trace = self._trace()
        with obs.bind_trace(trace):
            with obs.model_call("routing", "model-a", input_size=12) as call:
                call.set_output_size(5)
        record = obs.finish_turn(trace)
        self.assertEqual(record["model_call_count"], 1)
        self.assertEqual(record["model_calls"][0]["output_chars_approx"], 5)

    def test_model_call_accounting_in_executor_thread(self) -> None:
        trace = self._trace()

        def worker() -> str:
            with obs.model_call("synthesis", "model-b", input_size=9):
                return "answer"

        with obs.bind_trace(trace):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = obs.submit_with_trace(executor, worker)
                self.assertEqual(future.result(), "answer")
        record = obs.finish_turn(trace)
        self.assertEqual(record["model_call_count"], 1)
        self.assertEqual(record["model_calls"][0]["purpose"], "synthesis")

    def test_aggregation_and_route_mode_grouping(self) -> None:
        traces = [
            {
                "event": "turn_trace",
                "duration_ms": 100,
                "completion_status": "success",
                "route": "events",
                "mode": "sql",
                "model_call_count": 1,
                "retry_count": 0,
                "stages": [{"name": "sql_execution", "duration_ms": 20}],
                "model_calls": [],
            },
            {
                "event": "turn_trace",
                "duration_ms": 300,
                "completion_status": "failure",
                "route": "community",
                "mode": "rag",
                "model_call_count": 2,
                "retry_count": 1,
                "timeout_category": "model",
                "error_category": "timeout",
                "stages": [{"name": "vector_retrieval", "duration_ms": 80}],
                "model_calls": [],
            },
        ]
        report = aggregate_traces(traces)
        self.assertEqual(report["turn_count"], 2)
        self.assertEqual(report["by_route"]["events"]["turn_count"], 1)
        self.assertEqual(report["by_mode"]["rag"]["turn_count"], 1)
        self.assertEqual(report["failure_categories"]["timeout"], 1)
        self.assertEqual(report["timeout_count"], 1)

    def test_percentile_calculation(self) -> None:
        self.assertEqual(percentile([], 0.5), 0.0)
        self.assertEqual(percentile([10], 0.99), 10.0)
        self.assertEqual(percentile([0, 100], 0.5), 50.0)

    def test_empty_trace_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            traces, stats = load_traces(Path(tmp) / "missing.jsonl")
        self.assertEqual(traces, [])
        self.assertEqual(stats["malformed"], 0)

    def test_malformed_trace_handling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "traces.jsonl"
            path.write_text(
                "not-json\n"
                + json.dumps({"event": "other"}) + "\n"
                + json.dumps({"event": "turn_trace", "duration_ms": 5}) + "\n",
                encoding="utf-8",
            )
            traces, stats = load_traces(path)
        self.assertEqual(len(traces), 1)
        self.assertEqual(stats["malformed"], 1)
        self.assertEqual(stats["ignored"], 1)

    def test_retry_counting(self) -> None:
        trace = self._trace()
        with obs.bind_trace(trace):
            obs.record_retry("gemini")
            obs.record_retry("gemini", 2)
        record = obs.finish_turn(trace)
        self.assertEqual(record["retry_count"], 3)
        self.assertEqual(record["retries_by_scope"]["gemini"], 3)

    def test_timeout_categorization(self) -> None:
        trace = self._trace()
        record = obs.finish_turn(trace, status="failure", error=TimeoutError("late"))
        self.assertEqual(record["error_category"], "timeout")
        self.assertEqual(record["timeout_category"], "request")

    def test_no_full_user_text_logged_by_default(self) -> None:
        secret_message = "My private resident question"
        trace = self._trace()
        with obs.bind_trace(trace):
            obs.record_fields(prompt_chars_approx=len(secret_message))
        payload = json.dumps(obs.finish_turn(trace))
        self.assertNotIn(secret_message, payload)

    def test_no_authorization_or_cookie_leakage(self) -> None:
        payload = json.dumps(
            obs.redact(
                {
                    "headers": {
                        "authorization": "Bearer top-secret",
                        "cookie": "auth_session=top-secret",
                    }
                }
            )
        )
        self.assertNotIn("top-secret", payload)

    def test_sql_records_only_fingerprint_and_row_count(self) -> None:
        trace = self._trace()
        raw_sql = "SELECT * FROM residents WHERE email='resident@example.com'"
        with obs.bind_trace(trace):
            obs.record_sql(raw_sql, row_count=2)
        payload = json.dumps(obs.finish_turn(trace))
        self.assertNotIn(raw_sql, payload)
        self.assertNotIn("resident@example.com", payload)
        self.assertEqual(json.loads(payload)["sql_row_count"], 2)

    def test_characterization_return_and_exception_behavior(self) -> None:
        def unchanged(value: str) -> str:
            with obs.stage("characterization"):
                if value == "raise":
                    raise ValueError("original")
                return value.upper()

        with mock.patch.dict(os.environ, {"TURN_OBSERVABILITY_ENABLED": "false"}):
            self.assertEqual(unchanged("same"), "SAME")
            with self.assertRaisesRegex(ValueError, "original"):
                unchanged("raise")
        trace = self._trace()
        with obs.bind_trace(trace):
            self.assertEqual(unchanged("same"), "SAME")
            with self.assertRaisesRegex(ValueError, "original"):
                unchanged("raise")
        obs.finish_turn(trace)


if __name__ == "__main__":
    unittest.main()
