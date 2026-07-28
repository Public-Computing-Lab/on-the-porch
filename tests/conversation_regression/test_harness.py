"""Unit tests for the deterministic conversation regression harness."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from evaluators import (  # noqa: E402
    SCORING_DIMENSIONS,
    ScenarioValidationError,
    evaluate_turn,
    load_and_validate_corpus,
    validate_corpus,
)
from reporting import build_report, write_report  # noqa: E402


EXPECTED_DEFAULTS = {
    "behavior": "Provide a grounded resident-facing answer.",
    "context_required": False,
    "correction": False,
    "continuation": False,
    "topic_change": False,
    "clarification_allowed": False,
    "clarification_required": False,
    "required_concepts": [],
    "forbidden_concepts": [],
    "expected_source_categories": [],
    "forbidden_source_categories": [],
    "source_requirement": "none",
    "acceptable_limitations": [],
}


def expected(**overrides):
    value = deepcopy(EXPECTED_DEFAULTS)
    value.update(overrides)
    return value


def actual(
    answer: str,
    *,
    source_categories=(),
    result_ids=(),
    metadata=None,
    clarification=False,
):
    return {
        "answer": answer,
        "sources": [
            {"category": category, "id": f"source-{index}"}
            for index, category in enumerate(source_categories)
        ],
        "result_ids": list(result_ids),
        "metadata": metadata or {},
        "clarification": clarification,
    }


def failed_checks(report):
    return {item["check"] for item in report["checks"] if not item["passed"]}


class EvaluatorTests(unittest.TestCase):
    def test_passing_answer(self):
        report = evaluate_turn(
            expected(
                required_concepts=[
                    {"name": "weekend events", "any_of": ["events this weekend"]}
                ],
                expected_source_categories=["event_listing"],
                source_requirement="required",
            ),
            actual(
                "Here are events this weekend in Dorchester.",
                source_categories=["event_listing"],
            ),
        )
        self.assertTrue(report["passed"])

    def test_missing_required_concept(self):
        report = evaluate_turn(
            expected(
                required_concepts=[
                    {"name": "location", "any_of": ["Fields Corner"], "critical": True}
                ]
            ),
            actual("Here are several upcoming events."),
        )
        self.assertFalse(report["passed"])
        self.assertIn("required_concept:location", failed_checks(report))
        self.assertTrue(report["critical_failures"])

    def test_forbidden_internal_term(self):
        report = evaluate_turn(
            expected(),
            actual("The event_by_name lookup could not run."),
        )
        self.assertFalse(report["passed"])
        self.assertIn("internal_term:event_by_name", failed_checks(report))

    def test_plain_word_rag_is_not_treated_as_internal(self):
        report = evaluate_turn(
            expected(),
            actual("Bring a rag and gloves to the cleanup."),
        )
        self.assertTrue(report["passed"])

    def test_missing_required_source(self):
        report = evaluate_turn(
            expected(
                expected_source_categories=["official_data"],
                source_requirement="required",
            ),
            actual("There were 12 requests."),
        )
        self.assertFalse(report["passed"])
        self.assertIn("required_sources_present", failed_checks(report))
        self.assertIn("expected_source_categories", failed_checks(report))

    def test_correction_metadata_failure(self):
        report = evaluate_turn(
            expected(
                correction=True,
                context_required=True,
                metadata_assertions={"conversation.correction": True},
            ),
            actual(
                "Here is the corrected event.",
                metadata={"conversation": {"correction": False}},
            ),
        )
        self.assertFalse(report["passed"])
        self.assertIn("metadata:conversation.correction", failed_checks(report))
        self.assertTrue(report["critical_failures"])

    def test_repeated_output_after_more(self):
        previous = actual(
            "Here are the next two events in Dorchester.",
            result_ids=["event-1", "event-2"],
        )
        report = evaluate_turn(
            expected(
                continuation=True,
                context_required=True,
                must_not_repeat_previous_answer=True,
                must_not_repeat_result_ids=True,
            ),
            actual(
                "Here are the next two events in Dorchester.",
                result_ids=["event-2", "event-3"],
            ),
            previous_actual=previous,
        )
        self.assertFalse(report["passed"])
        self.assertIn("answer_not_repeated", failed_checks(report))
        self.assertIn("result_ids_not_repeated", failed_checks(report))

    def test_empty_answer(self):
        report = evaluate_turn(expected(), actual("  "))
        self.assertFalse(report["passed"])
        self.assertIn("answer_not_empty", failed_checks(report))

    def test_acceptable_no_evidence_answer(self):
        report = evaluate_turn(
            expected(
                required_concepts=[
                    {
                        "name": "limitation",
                        "any_of": ["available records"],
                        "critical": True,
                    }
                ],
                uncertainty_required=True,
            ),
            actual(
                "I could not find a matching item in the available records, "
                "which may be incomplete."
            ),
        )
        self.assertTrue(report["passed"])

    def test_inappropriate_certainty_claim(self):
        report = evaluate_turn(
            expected(uncertainty_required=True),
            actual("I can confirm that there has never been an incident there."),
        )
        self.assertFalse(report["passed"])
        self.assertIn("uncertainty_disclosed", failed_checks(report))


class CorpusTests(unittest.TestCase):
    def test_repository_corpus_meets_stage_one_minimums(self):
        _, summary = load_and_validate_corpus(HERE / "scenarios.yaml")
        self.assertEqual(summary["scenario_count"], 84)
        self.assertGreaterEqual(summary["multi_turn_count"], 15)
        self.assertGreaterEqual(summary["coverage_counts"]["events"], 12)
        self.assertGreaterEqual(summary["coverage_counts"]["general_mechanics"], 15)

    def test_malformed_scenario_is_rejected(self):
        malformed = {
            "schema_version": 1,
            "baseline": {},
            "evaluation_dimensions": list(SCORING_DIMENSIONS),
            "scenarios": [
                {
                    "id": "bad",
                    "title": "Missing required fields",
                    "turns": [],
                }
            ],
        }
        with self.assertRaises(ScenarioValidationError):
            validate_corpus(malformed, require_minimum=False)

    def test_report_generation_writes_json_and_markdown(self):
        scenario_report = {
            "scenario_id": "sample",
            "title": "Sample",
            "domain": "events",
            "conversation_category": "standalone",
            "passed": True,
            "critical_failures": [],
            "turns": [
                {
                    "turn_index": 0,
                    "user": "What is happening?",
                    "passed": True,
                    "checks": [],
                    "dimension_scores": {
                        dimension: 4 if dimension == "intent_understanding" else None
                        for dimension in SCORING_DIMENSIONS
                    },
                }
            ],
        }
        report = build_report(
            [scenario_report],
            mode="unit_test",
            baseline={"commit": "baseline"},
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(report, Path(directory), "sample")
            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["markdown"].exists())
            loaded = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(loaded["totals"]["passed"], 1)
            self.assertEqual(len(loaded["by_dimension"]), len(SCORING_DIMENSIONS))
            self.assertIn(
                "# Conversation Regression Report",
                paths["markdown"].read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
