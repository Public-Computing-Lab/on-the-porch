"""Tests for Stage 2.5 evaluator calibration and future quality gates."""

from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from calibration import (  # noqa: E402
    CalibrationValidationError,
    REPRODUCIBLE_REPORT_FIELDS,
    build_failure_triage,
    evaluate_captured_baseline,
    evaluate_quality_gate,
    load_decisions,
    load_quality_gate,
    validate_decisions,
    validate_quality_gate,
)
from evaluators import evaluate_turn, load_and_validate_corpus  # noqa: E402


def expected(**overrides):
    value = {
        "behavior": "Answer the resident.",
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
    value.update(overrides)
    return value


def actual(answer, *, source_categories=(), metadata=None):
    return {
        "answer": answer,
        "sources": [{"category": category} for category in source_categories],
        "result_ids": [],
        "metadata": metadata or {},
        "clarification": False,
    }


class EvaluatorCalibrationTests(unittest.TestCase):
    def test_phrase_alias_accepts_natural_greeting(self):
        result = evaluate_turn(
            expected(
                required_concepts=[{"name": "greeting", "any_of": ["good morning"]}]
            ),
            actual("Good to see you. What can I help with?"),
            calibrated=True,
        )
        self.assertTrue(result["passed"])

    def test_punctuation_and_ampersand_are_normalized(self):
        result = evaluate_turn(
            expected(
                required_concepts=[
                    {
                        "name": "organization",
                        "any_of": ["Crew and Cadet Street Parade"],
                    }
                ]
            ),
            actual("CREW & CADET STREET PARADE is listed for Monday."),
            calibrated=True,
        )
        self.assertTrue(result["passed"])

    def test_source_category_alias_is_accepted(self):
        result = evaluate_turn(
            expected(
                expected_source_categories=["official_data"],
                source_requirement="required",
            ),
            actual(
                "The city record shows 12 requests.",
                source_categories=["city_data"],
            ),
            calibrated=True,
        )
        self.assertTrue(result["passed"])

    def test_acceptable_paraphrase_is_not_a_false_positive(self):
        result = evaluate_turn(
            expected(
                required_concepts=[
                    {
                        "name": "ranking",
                        "any_of": ["most common"],
                    }
                ]
            ),
            actual("Missed trash pickup was the most frequent issue."),
            calibrated=True,
        )
        self.assertTrue(result["passed"])

    def test_calibrated_raw_sql_check_ignores_ordinary_prose(self):
        result = evaluate_turn(
            expected(),
            actual(
                "The Real Estate Update from the neighborhood meeting "
                "described a proposed project."
            ),
            calibrated=True,
        )
        self.assertTrue(result["passed"])

    def test_calibrated_raw_sql_check_still_rejects_sql(self):
        result = evaluate_turn(
            expected(),
            actual("SELECT case_id FROM service_requests_311"),
            calibrated=True,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["critical_failures"])

    def test_preferred_source_is_advisory_but_required_source_is_not(self):
        preferred = evaluate_turn(
            expected(
                expected_source_categories=["event_listing"],
                source_requirement="preferred",
            ),
            actual("The event is listed for Monday."),
            calibrated=True,
        )
        required = evaluate_turn(
            expected(
                expected_source_categories=["event_listing"],
                source_requirement="required",
            ),
            actual("The event is listed for Monday."),
            calibrated=True,
        )
        self.assertTrue(preferred["passed"])
        self.assertFalse(required["passed"])

    def test_present_incorrect_metadata_still_fails(self):
        result = evaluate_turn(
            expected(
                correction=True,
                metadata_assertions={"conversation.correction": True},
            ),
            actual(
                "Here is the corrected event.",
                metadata={"conversation": {"correction": False}},
            ),
            calibrated=True,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["critical_failures"])


class _CalibrationFixture:
    @classmethod
    def setUpClass(cls):
        cls.corpus, _ = load_and_validate_corpus(HERE / "scenarios.yaml")
        cls.stage2 = json.loads(
            (ROOT / "reports" / "stage2" / "conversational_baseline.json").read_text(
                encoding="utf-8"
            )
        )
        cls.decisions = load_decisions(HERE / "stage2_5_decisions.json")
        cls.gate = load_quality_gate(HERE / "quality_gate.yaml")
        cls.engineer_review = json.loads(
            (ROOT / "reports" / "stage2_5" / "engineer_review_by_codex.json").read_text(
                encoding="utf-8"
            )
        )
        cls.original = evaluate_captured_baseline(
            cls.corpus,
            cls.stage2,
            calibrated=False,
        )
        cls.calibrated = evaluate_captured_baseline(
            cls.corpus,
            cls.stage2,
            calibrated=True,
        )


class CalibrationInventoryTests(_CalibrationFixture, unittest.TestCase):
    def test_original_stage2_totals_are_reproducible(self):
        for field in REPRODUCIBLE_REPORT_FIELDS:
            self.assertEqual(
                self.original[field],
                self.stage2["evaluation"][field],
                field,
            )
        self.assertEqual(
            self.original["totals"],
            {
                "evaluated": 84,
                "passed": 13,
                "failed": 71,
                "skipped": 0,
                "critical_failures": 100,
            },
        )

    def test_every_failure_and_critical_check_is_classified(self):
        summary = validate_decisions(self.decisions, self.original)
        self.assertEqual(summary["failed_scenario_count"], 71)
        self.assertEqual(summary["failed_critical_check_count"], 100)
        triage = build_failure_triage(
            self.corpus,
            self.stage2,
            self.original,
            self.decisions,
            self.gate,
        )
        self.assertEqual(triage["failed_scenario_count"], 71)
        self.assertEqual(triage["failed_critical_check_count"], 100)
        self.assertEqual(
            triage["primary_classification_totals"]["DATA_COVERAGE_FAILURE"],
            6,
        )
        self.assertEqual(
            triage["primary_classification_totals"]["EVALUATOR_FALSE_POSITIVE"],
            7,
        )

    def test_all_scenarios_have_one_tier_and_tier1_disposition(self):
        summary = validate_quality_gate(
            self.gate,
            (item["id"] for item in self.corpus["scenarios"]),
        )
        self.assertEqual(
            summary["tier_counts"],
            {"tier1": 29, "tier2": 32, "tier3": 23},
        )
        self.assertEqual(
            sum(summary["tier1_disposition_counts"].values()),
            29,
        )

    def test_malformed_decision_is_rejected(self):
        malformed = deepcopy(self.decisions)
        malformed["decisions"]["event-001"].pop("note")
        with self.assertRaises(CalibrationValidationError):
            validate_decisions(malformed, self.original)

    def test_missing_tier_assignment_is_rejected(self):
        malformed = deepcopy(self.gate)
        malformed["tiers"]["tier1"].remove("greet-001")
        with self.assertRaises(CalibrationValidationError):
            validate_quality_gate(
                malformed,
                (item["id"] for item in self.corpus["scenarios"]),
            )

    def test_duplicate_tier_assignment_is_rejected(self):
        malformed = deepcopy(self.gate)
        malformed["tiers"]["tier2"].append("greet-001")
        with self.assertRaises(CalibrationValidationError):
            validate_quality_gate(
                malformed,
                (item["id"] for item in self.corpus["scenarios"]),
            )

    def test_missing_tier2_fixture_requirement_is_rejected(self):
        malformed = deepcopy(self.gate)
        malformed["tier2_requirements"].pop("event-005")
        with self.assertRaises(CalibrationValidationError):
            validate_quality_gate(
                malformed,
                (item["id"] for item in self.corpus["scenarios"]),
            )


class QualityGateTests(_CalibrationFixture, unittest.TestCase):
    def evaluate_gate(self, candidate, *, candidate_review=None):
        return evaluate_quality_gate(
            candidate,
            self.calibrated,
            self.gate,
            candidate_engineer_review=(
                self.engineer_review if candidate_review is None else candidate_review
            ),
            baseline_engineer_review=self.engineer_review,
        )

    def test_calibrated_baseline_passes_its_own_gate(self):
        result = self.evaluate_gate(self.calibrated)
        self.assertTrue(result["passed"])
        self.assertEqual(result["blocked_count"], 15)

    def test_tier1_hard_gate_failure_blocks(self):
        candidate = deepcopy(self.calibrated)
        scenario = next(
            item
            for item in candidate["scenarios"]
            if item["scenario_id"] == "greet-001"
        )
        scenario["passed"] = False
        result = self.evaluate_gate(candidate)
        self.assertFalse(result["passed"])
        self.assertIn(
            "hard-gate scenario failed: greet-001",
            result["blockers"],
        )

    def test_new_critical_failure_blocks(self):
        candidate = deepcopy(self.calibrated)
        scenario = next(
            item
            for item in candidate["scenarios"]
            if item["scenario_id"] == "greet-001"
        )
        failure = "Synthetic new critical failure."
        scenario["critical_failures"].append(failure)
        candidate["critical_failures"].append(
            {"scenario_id": "greet-001", "failure": failure}
        )
        result = self.evaluate_gate(candidate)
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("new critical failure" in item for item in result["blockers"])
        )

    def test_known_defect_may_not_worsen(self):
        candidate = deepcopy(self.calibrated)
        scenario = next(
            item
            for item in candidate["scenarios"]
            if item["scenario_id"] == "event-010"
        )
        scenario["turns"][0]["checks"].append(
            {
                "check": "synthetic_regression",
                "dimension": "intent_understanding",
                "passed": False,
                "message": "Synthetic regression.",
                "critical": False,
            }
        )
        result = self.evaluate_gate(candidate)
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("known defect worsened" in item for item in result["blockers"])
        )

    def test_material_baseline_decline_blocks(self):
        candidate = deepcopy(self.calibrated)
        candidate["by_dimension"]["source_relevance"]["average"] = 0
        result = self.evaluate_gate(candidate)
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("source_relevance decline" in item for item in result["blockers"])
        )

    def test_missing_engineer_review_blocks_full_gate(self):
        result = evaluate_quality_gate(
            self.calibrated,
            self.calibrated,
            self.gate,
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "engineer review comparison is required but was not supplied",
            result["blockers"],
        )

    def test_engineer_review_dimension_decline_blocks(self):
        candidate_review = deepcopy(self.engineer_review)
        detail = candidate_review["reviews"][0]["dimension_reviews"]["completeness"]
        self.assertIsNotNone(detail["score"])
        detail["score"] = max(0, float(detail["score"]) - 2)
        result = self.evaluate_gate(
            self.calibrated,
            candidate_review=candidate_review,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(
            any(
                "engineer review dimension declined" in item
                for item in result["blockers"]
            )
        )


if __name__ == "__main__":
    unittest.main()
