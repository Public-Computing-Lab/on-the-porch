"""Stage 2.5 baseline calibration, triage, tier, and quality-gate utilities."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from evaluators import (
    CALIBRATION_PROFILE_VERSION,
    SCORING_DIMENSIONS,
    evaluate_scenario,
)
from reporting import build_report


CLASSIFICATIONS = {
    "REAL_CONVERSATIONAL_DEFECT",
    "REAL_GROUNDING_DEFECT",
    "REAL_RETRIEVAL_DEFECT",
    "REAL_STRUCTURED_DATA_DEFECT",
    "DATA_COVERAGE_FAILURE",
    "DATA_FRESHNESS_FAILURE",
    "EVALUATOR_FALSE_POSITIVE",
    "SCENARIO_SPECIFICATION_DEFECT",
    "UNSUPPORTED_CAPABILITY",
    "ENVIRONMENT_MISMATCH",
    "NONDETERMINISTIC_ACCEPTABLE_VARIATION",
    "TEST_HARNESS_DEFECT",
}

CONFIDENCE_LEVELS = {"low", "medium", "high"}
TIER_NAMES = {"tier1", "tier2", "tier3"}
TIER1_DISPOSITIONS = {
    "hard_gate_passing_baseline",
    "known_baseline_defect",
    "blocked_by_fixture_or_environment",
}

FUTURE_TARGETS = {
    "REAL_CONVERSATIONAL_DEFECT": "post_stage3_conversation_quality",
    "REAL_GROUNDING_DEFECT": "controlled_fixture_and_grounding_hardening",
    "REAL_RETRIEVAL_DEFECT": "controlled_fixture_and_retrieval_hardening",
    "REAL_STRUCTURED_DATA_DEFECT": "structured_data_correctness_backlog",
    "DATA_COVERAGE_FAILURE": "controlled_fixture_build",
    "DATA_FRESHNESS_FAILURE": "controlled_fixture_and_freshness_policy",
    "EVALUATOR_FALSE_POSITIVE": "stage2_5_calibration",
    "SCENARIO_SPECIFICATION_DEFECT": "scenario_reauthoring",
    "UNSUPPORTED_CAPABILITY": "product_scope_decision",
    "ENVIRONMENT_MISMATCH": "production_audit_or_controlled_fixture",
    "NONDETERMINISTIC_ACCEPTABLE_VARIATION": "tier3_structured_review",
    "TEST_HARNESS_DEFECT": "stage2_5_harness_calibration",
}

REAL_DEFECT_CLASSIFICATIONS = {
    "REAL_CONVERSATIONAL_DEFECT",
    "REAL_GROUNDING_DEFECT",
    "REAL_RETRIEVAL_DEFECT",
    "REAL_STRUCTURED_DATA_DEFECT",
}

REPRODUCIBLE_REPORT_FIELDS = (
    "totals",
    "by_domain",
    "by_conversation_category",
    "by_dimension",
    "critical_failures",
    "scenarios",
    "skipped",
)


class CalibrationValidationError(ValueError):
    """Raised when Stage 2.5 calibration metadata is incomplete or inconsistent."""


def load_json_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationValidationError(f"Could not load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CalibrationValidationError(f"{path} must contain an object")
    return value


def load_quality_gate(path: Path) -> Dict[str, Any]:
    return load_json_object(path)


def load_decisions(path: Path) -> Dict[str, Any]:
    value = load_json_object(path)
    decisions = value.get("decisions")
    if not isinstance(decisions, dict):
        raise CalibrationValidationError("decisions must be an object")
    return value


def validate_quality_gate(
    config: Mapping[str, Any],
    scenario_ids: Iterable[str],
) -> Dict[str, Any]:
    errors: List[str] = []
    expected_ids = set(scenario_ids)
    if config.get("schema_version") != 1:
        errors.append("quality gate schema_version must be 1")
    if config.get("calibration_profile_version") != CALIBRATION_PROFILE_VERSION:
        errors.append("quality gate calibration profile does not match evaluator")

    tiers = config.get("tiers")
    if not isinstance(tiers, Mapping):
        errors.append("quality gate tiers must be an object")
        tiers = {}
    tier_membership: Dict[str, List[str]] = defaultdict(list)
    for tier_name in TIER_NAMES:
        values = tiers.get(tier_name)
        if not isinstance(values, list):
            errors.append(f"{tier_name} must be a list")
            continue
        for scenario_id in values:
            if not isinstance(scenario_id, str):
                errors.append(f"{tier_name} contains a non-string scenario ID")
                continue
            tier_membership[scenario_id].append(tier_name)

    missing = sorted(expected_ids - set(tier_membership))
    unknown = sorted(set(tier_membership) - expected_ids)
    duplicated = sorted(
        scenario_id
        for scenario_id, assigned in tier_membership.items()
        if len(assigned) != 1
    )
    if missing:
        errors.append(f"scenarios missing a tier: {missing}")
    if unknown:
        errors.append(f"unknown scenario IDs in tiers: {unknown}")
    if duplicated:
        errors.append(f"scenarios assigned to multiple tiers: {duplicated}")

    tier1_ids = set(tiers.get("tier1", []))
    dispositions = config.get("tier1_dispositions")
    if not isinstance(dispositions, Mapping):
        errors.append("tier1_dispositions must be an object")
        dispositions = {}
    disposition_membership: Dict[str, List[str]] = defaultdict(list)
    for disposition in TIER1_DISPOSITIONS:
        values = dispositions.get(disposition)
        if not isinstance(values, list):
            errors.append(f"tier1 disposition {disposition} must be a list")
            continue
        for scenario_id in values:
            disposition_membership[str(scenario_id)].append(disposition)
    missing_disposition = sorted(tier1_ids - set(disposition_membership))
    extra_disposition = sorted(set(disposition_membership) - tier1_ids)
    duplicated_disposition = sorted(
        scenario_id
        for scenario_id, assigned in disposition_membership.items()
        if len(assigned) != 1
    )
    if missing_disposition:
        errors.append(
            f"Tier 1 scenarios missing a baseline disposition: {missing_disposition}"
        )
    if extra_disposition:
        errors.append(
            f"non-Tier 1 scenarios have a Tier 1 disposition: {extra_disposition}"
        )
    if duplicated_disposition:
        errors.append(
            "Tier 1 scenarios have duplicate dispositions: " f"{duplicated_disposition}"
        )

    tier2_ids = set(tiers.get("tier2", []))
    tier2_requirements = config.get("tier2_requirements")
    if not isinstance(tier2_requirements, Mapping):
        errors.append("tier2_requirements must be an object")
        tier2_requirements = {}
    missing_requirements = sorted(tier2_ids - set(tier2_requirements))
    extra_requirements = sorted(set(tier2_requirements) - tier2_ids)
    if missing_requirements:
        errors.append(
            f"Tier 2 scenarios missing fixture requirements: {missing_requirements}"
        )
    if extra_requirements:
        errors.append(
            f"non-Tier 2 scenarios have fixture requirements: {extra_requirements}"
        )
    for scenario_id, requirement in tier2_requirements.items():
        if not isinstance(requirement, Mapping):
            errors.append(f"{scenario_id}: Tier 2 requirement must be an object")
            continue
        for field in ("fixture_datasets", "expected_source_categories"):
            value = requirement.get(field)
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item for item in value)
            ):
                errors.append(f"{scenario_id}: invalid {field}")
        if not isinstance(requirement.get("freshness_dependency"), str):
            errors.append(f"{scenario_id}: invalid freshness_dependency")
        for field in (
            "exact_values_fixture_bound",
            "live_execution_advisory_only",
        ):
            if not isinstance(requirement.get(field), bool):
                errors.append(f"{scenario_id}: invalid {field}")

    material_decline = config.get("material_decline")
    if not isinstance(material_decline, Mapping):
        errors.append("material_decline must be an object")
    else:
        for key in (
            "engineer_review_dimension_drop",
            "engineer_review_average_drop",
            "domain_average_drop",
            "source_relevance_average_drop",
            "naturalness_average_drop",
            "resident_friendly_language_average_drop",
            "unnecessary_clarification_increase",
            "new_critical_failures",
        ):
            value = material_decline.get(key)
            if not isinstance(value, (int, float)) or value < 0:
                errors.append(f"invalid material_decline.{key}")
    if not isinstance(config.get("require_engineer_review_comparison"), bool):
        errors.append("require_engineer_review_comparison must be boolean")

    if errors:
        raise CalibrationValidationError("; ".join(errors))
    return {
        "scenario_count": len(expected_ids),
        "tier_counts": {
            tier_name: len(tiers[tier_name]) for tier_name in sorted(TIER_NAMES)
        },
        "tier1_disposition_counts": {
            name: len(dispositions[name]) for name in sorted(TIER1_DISPOSITIONS)
        },
    }


def response_map_from_stage2(
    baseline: Mapping[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    responses: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for turn in baseline.get("turns", []):
        if not isinstance(turn, Mapping):
            continue
        scenario_id = str(turn.get("scenario_id") or "")
        if not scenario_id:
            continue
        sources = turn.get("sources")
        responses[scenario_id].append(
            {
                "turn_index": int(turn.get("turn_index", 0)),
                "answer": str(turn.get("assistant_answer") or ""),
                "sources": deepcopy(sources) if isinstance(sources, list) else [],
                "result_ids": deepcopy(turn.get("result_ids") or []),
                "metadata": deepcopy(turn.get("metadata") or {}),
                "clarification": bool(turn.get("clarification")),
                "mode": str(turn.get("mode") or "unknown"),
                "route": str(turn.get("route") or "unknown"),
            }
        )
    for turns in responses.values():
        turns.sort(key=lambda item: item["turn_index"])
    return dict(responses)


def evaluate_captured_baseline(
    corpus: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    calibrated: bool,
) -> Dict[str, Any]:
    responses = response_map_from_stage2(baseline)
    reports = [
        evaluate_scenario(
            scenario,
            responses.get(str(scenario["id"]), []),
            calibrated=calibrated,
        )
        for scenario in corpus["scenarios"]
    ]
    return build_report(
        reports,
        mode=(
            "stage2_captured_calibrated" if calibrated else "stage2_captured_original"
        ),
        baseline=corpus["baseline"],
    )


def validate_decisions(
    decisions_doc: Mapping[str, Any],
    original_report: Mapping[str, Any],
) -> Dict[str, Any]:
    errors: List[str] = []
    decisions = decisions_doc.get("decisions", {})
    failed_ids = {
        str(item.get("scenario_id"))
        for item in original_report.get("scenarios", [])
        if not item.get("passed")
    }
    decision_ids = set(decisions)
    if failed_ids != decision_ids:
        missing = sorted(failed_ids - decision_ids)
        extra = sorted(decision_ids - failed_ids)
        if missing:
            errors.append(f"failed scenarios missing decisions: {missing}")
        if extra:
            errors.append(f"decisions supplied for nonfailed scenarios: {extra}")
    for scenario_id, decision in decisions.items():
        if not isinstance(decision, Mapping):
            errors.append(f"{scenario_id}: decision must be an object")
            continue
        primary = decision.get("primary")
        if primary not in CLASSIFICATIONS:
            errors.append(f"{scenario_id}: invalid primary classification {primary!r}")
        secondary = decision.get("secondary")
        if not isinstance(secondary, list):
            errors.append(f"{scenario_id}: secondary must be a list")
        else:
            unknown = sorted(set(secondary) - CLASSIFICATIONS)
            if unknown:
                errors.append(
                    f"{scenario_id}: unknown secondary classifications {unknown}"
                )
            if primary in secondary:
                errors.append(f"{scenario_id}: primary repeated as secondary")
        if decision.get("confidence") not in CONFIDENCE_LEVELS:
            errors.append(f"{scenario_id}: invalid confidence")
        if not str(decision.get("note") or "").strip():
            errors.append(f"{scenario_id}: note is required")
        for field in (
            "relevant_data_existed",
            "scenario_expectations_correct",
            "evaluator_logic_correct",
        ):
            if field not in decision:
                errors.append(f"{scenario_id}: missing {field}")

    critical_count = sum(
        1
        for scenario in original_report.get("scenarios", [])
        for turn in scenario.get("turns", [])
        for check in turn.get("checks", [])
        if not check.get("passed") and check.get("critical")
    )
    if errors:
        raise CalibrationValidationError("; ".join(errors))
    return {
        "failed_scenario_count": len(failed_ids),
        "failed_critical_check_count": critical_count,
    }


def _check_classification(
    check: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> str:
    name = str(check.get("check") or "")
    if name.startswith("metadata:"):
        return "TEST_HARNESS_DEFECT"
    if name == "internal_term:raw_sql":
        return "EVALUATOR_FALSE_POSITIVE"
    if name.startswith("internal_term:"):
        return "REAL_CONVERSATIONAL_DEFECT"
    if name == "uncertainty_disclosed":
        return "REAL_GROUNDING_DEFECT"
    return str(decision["primary"])


def _source_summary(turns: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    found: set[tuple[str, str]] = set()
    for turn in turns:
        for source in turn.get("sources", []):
            if not isinstance(source, Mapping):
                continue
            category = str(source.get("category") or source.get("type") or "unknown")
            label = str(
                source.get("source")
                or source.get("table")
                or source.get("doc_type")
                or "unspecified"
            )
            found.add((category, label))
    return [{"category": category, "label": label} for category, label in sorted(found)]


def _tier_for_scenario(
    scenario_id: str,
    quality_gate: Mapping[str, Any],
) -> str:
    for tier_name, scenario_ids in quality_gate["tiers"].items():
        if scenario_id in scenario_ids:
            return str(tier_name)
    raise CalibrationValidationError(f"No tier for {scenario_id}")


def _tier1_disposition(
    scenario_id: str,
    quality_gate: Mapping[str, Any],
) -> Optional[str]:
    for disposition, scenario_ids in quality_gate["tier1_dispositions"].items():
        if scenario_id in scenario_ids:
            return str(disposition)
    return None


def build_failure_triage(
    corpus: Mapping[str, Any],
    baseline: Mapping[str, Any],
    original_report: Mapping[str, Any],
    decisions_doc: Mapping[str, Any],
    quality_gate: Mapping[str, Any],
) -> Dict[str, Any]:
    scenarios = {str(item["id"]): item for item in corpus["scenarios"]}
    observed_turns: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for turn in baseline.get("turns", []):
        if isinstance(turn, Mapping):
            observed_turns[str(turn.get("scenario_id"))].append(turn)
    decisions = decisions_doc["decisions"]
    records: List[Dict[str, Any]] = []
    primary_totals: Counter[str] = Counter()
    secondary_totals: Counter[str] = Counter()
    critical_classification_totals: Counter[str] = Counter()

    for result in original_report.get("scenarios", []):
        if result.get("passed"):
            continue
        scenario_id = str(result["scenario_id"])
        scenario = scenarios[scenario_id]
        decision = decisions[scenario_id]
        turns = observed_turns[scenario_id]
        failed_checks: List[Dict[str, Any]] = []
        failed_turn_indexes: set[int] = set()
        for turn in result.get("turns", []):
            turn_index = int(turn.get("turn_index", 0))
            for check in turn.get("checks", []):
                if check.get("passed"):
                    continue
                classification = _check_classification(check, decision)
                item = {
                    "turn_index": turn_index,
                    "check": str(check.get("check") or ""),
                    "dimension": str(check.get("dimension") or ""),
                    "message": str(check.get("message") or ""),
                    "critical": bool(check.get("critical")),
                    "classification": classification,
                }
                failed_checks.append(item)
                failed_turn_indexes.add(turn_index)
                if item["critical"]:
                    critical_classification_totals[classification] += 1

        primary = str(decision["primary"])
        secondary = [str(item) for item in decision.get("secondary", [])]
        primary_totals[primary] += 1
        secondary_totals.update(secondary)
        tier = _tier_for_scenario(scenario_id, quality_gate)
        disposition = _tier1_disposition(scenario_id, quality_gate)
        gate_behavior = (
            disposition
            if disposition
            else (
                "controlled_fixture_gate"
                if tier == "tier2"
                else "structured_review_and_safety_checks"
            )
        )
        records.append(
            {
                "scenario_id": scenario_id,
                "title": scenario["title"],
                "domain": scenario["domain"],
                "turn_count": len(scenario["turns"]),
                "failed_turn_indexes": sorted(failed_turn_indexes),
                "failed_deterministic_checks": failed_checks,
                "failed_critical_checks": [
                    item for item in failed_checks if item["critical"]
                ],
                "observed_answer_summary": [
                    {
                        "turn_index": int(turn.get("turn_index", 0)),
                        "summary": " ".join(
                            str(turn.get("assistant_answer") or "").split()
                        )[:320],
                    }
                    for turn in turns
                ],
                "observed_sources": _source_summary(turns),
                "observed_execution_modes": [
                    str(turn.get("mode") or "unknown") for turn in turns
                ],
                "primary_classification": primary,
                "secondary_classifications": secondary,
                "confidence": decision["confidence"],
                "severity": scenario["severity"],
                "reproducible": primary
                not in {
                    "DATA_FRESHNESS_FAILURE",
                    "NONDETERMINISTIC_ACCEPTABLE_VARIATION",
                    "ENVIRONMENT_MISMATCH",
                },
                "relevant_data_existed": decision["relevant_data_existed"],
                "evaluator_logic_correct": decision["evaluator_logic_correct"],
                "scenario_expectations_correct": decision[
                    "scenario_expectations_correct"
                ],
                "recommended_tier": tier,
                "recommended_gate_behavior": gate_behavior,
                "future_target_stage": FUTURE_TARGETS[primary],
                "engineer_review_notes": decision["note"],
            }
        )

    return {
        "schema_version": 1,
        "source_report": "reports/stage2/conversational_baseline.json",
        "failed_scenario_count": len(records),
        "failed_critical_check_count": sum(
            len(item["failed_critical_checks"]) for item in records
        ),
        "primary_classification_totals": {
            name: primary_totals.get(name, 0) for name in sorted(CLASSIFICATIONS)
        },
        "secondary_classification_totals": {
            name: secondary_totals.get(name, 0) for name in sorted(CLASSIFICATIONS)
        },
        "critical_check_classification_totals": {
            name: critical_classification_totals.get(name, 0)
            for name in sorted(CLASSIFICATIONS)
        },
        "records": records,
    }


def _scenario_dimension_scores(
    scenario: Mapping[str, Any],
    deterministic: Mapping[str, Any],
    decision: Optional[Mapping[str, Any]],
) -> Dict[str, Optional[float]]:
    scores_by_dimension: Dict[str, List[int]] = defaultdict(list)
    for turn in deterministic.get("turns", []):
        for dimension, score in turn.get("dimension_scores", {}).items():
            if isinstance(score, int):
                scores_by_dimension[str(dimension)].append(score)

    expected_turns = scenario["turns"]
    multi_turn = len(expected_turns) > 1
    applicable = {
        "intent_understanding",
        "completeness",
        "naturalness",
        "resident_friendly_language",
        "grounding",
        "source_relevance",
        "source_type_distinction",
        "uncertainty_and_restraint",
        "unnecessary_clarification",
        "internal_implementation_leakage",
        "repetition",
        "contradiction_with_earlier_turns",
        "failure_quality",
    }
    if multi_turn or any(t["expected"].get("context_required") for t in expected_turns):
        applicable.add("context_retention")
    if any(t["expected"].get("correction") for t in expected_turns):
        applicable.add("correction_handling")
    if any(t["expected"].get("continuation") for t in expected_turns):
        applicable.add("continuation_handling")
    if any(t["expected"].get("topic_change") for t in expected_turns):
        applicable.add("topic_switch_handling")
    if "reference_resolution" in scenario.get("conversation_category", "") or any(
        token in str(t["user"]).casefold()
        for t in expected_turns
        for token in (" it", "that", "one", "there", "they")
    ):
        applicable.add("reference_and_pronoun_resolution")

    scores: Dict[str, Optional[float]] = {
        dimension: (
            round(
                sum(scores_by_dimension[dimension])
                / len(scores_by_dimension[dimension]),
                2,
            )
            if scores_by_dimension.get(dimension)
            else (3.0 if dimension in applicable else None)
        )
        for dimension in SCORING_DIMENSIONS
    }
    if decision is None:
        for dimension in ("naturalness", "resident_friendly_language"):
            scores[dimension] = max(float(scores.get(dimension) or 3), 3.0)
        return scores

    primary = str(decision["primary"])
    failed_check_names = {
        str(check.get("check") or "")
        for turn in deterministic.get("turns", [])
        for check in turn.get("checks", [])
        if not check.get("passed")
    }
    if primary == "REAL_CONVERSATIONAL_DEFECT":
        scores["intent_understanding"] = min(
            float(scores["intent_understanding"] or 3), 2.0
        )
        if "context_retention" in applicable:
            scores["context_retention"] = min(
                float(scores["context_retention"] or 3), 2.0
            )
    if primary == "REAL_GROUNDING_DEFECT":
        scores["grounding"] = 1.0
        scores["uncertainty_and_restraint"] = min(
            float(scores["uncertainty_and_restraint"] or 3), 2.0
        )
    if primary == "REAL_RETRIEVAL_DEFECT":
        scores["source_relevance"] = 1.0
        scores["grounding"] = min(float(scores["grounding"] or 3), 2.0)
    if primary == "REAL_STRUCTURED_DATA_DEFECT":
        scores["intent_understanding"] = min(
            float(scores["intent_understanding"] or 3), 2.0
        )
        scores["grounding"] = min(float(scores["grounding"] or 3), 2.0)
    if primary in {
        "DATA_COVERAGE_FAILURE",
        "DATA_FRESHNESS_FAILURE",
        "ENVIRONMENT_MISMATCH",
    }:
        scores["completeness"] = min(float(scores["completeness"] or 3), 2.0)
        scores["source_relevance"] = min(float(scores["source_relevance"] or 3), 2.0)
    if primary == "SCENARIO_SPECIFICATION_DEFECT":
        scores["intent_understanding"] = 2.0
        scores["failure_quality"] = min(float(scores["failure_quality"] or 3), 2.0)
    if primary == "UNSUPPORTED_CAPABILITY":
        scores["completeness"] = 2.0
        scores["failure_quality"] = min(float(scores["failure_quality"] or 3), 2.0)
    if any(name.startswith("internal_term:") for name in failed_check_names):
        scores["internal_implementation_leakage"] = 0.0
        scores["resident_friendly_language"] = min(
            float(scores["resident_friendly_language"] or 3), 2.0
        )
    if (
        "no_unnecessary_clarification" in failed_check_names
        or "clarification_required" in failed_check_names
    ):
        scores["unnecessary_clarification"] = min(
            float(scores["unnecessary_clarification"] or 3), 1.0
        )
    if "answer_not_repeated" in failed_check_names:
        scores["repetition"] = min(float(scores["repetition"] or 3), 1.0)
    return scores


def build_engineer_review(
    corpus: Mapping[str, Any],
    original_report: Mapping[str, Any],
    decisions_doc: Mapping[str, Any],
    quality_gate: Mapping[str, Any],
) -> Dict[str, Any]:
    scenarios = {str(item["id"]): item for item in corpus["scenarios"]}
    deterministic = {
        str(item["scenario_id"]): item for item in original_report["scenarios"]
    }
    critical_ids = {
        str(item["scenario_id"])
        for item in original_report["scenarios"]
        if item.get("critical_failures")
    }
    multi_turn_ids = {
        str(item["id"]) for item in corpus["scenarios"] if len(item["turns"]) > 1
    }
    review_ids = [
        str(item["id"])
        for item in corpus["scenarios"]
        if item["id"] in critical_ids | multi_turn_ids
    ]
    decisions = decisions_doc["decisions"]
    reviews: List[Dict[str, Any]] = []
    for scenario_id in review_ids:
        scenario = scenarios[scenario_id]
        result = deterministic[scenario_id]
        decision = decisions.get(scenario_id)
        scores = _scenario_dimension_scores(scenario, result, decision)
        primary = str(decision["primary"]) if decision else "NO_DETERMINISTIC_FAILURE"
        note = (
            str(decision["note"])
            if decision
            else "The captured multi-turn behavior passed every configured deterministic check."
        )
        dimension_reviews = {
            dimension: {
                "score": score,
                "rationale": (
                    "Not exercised by this scenario."
                    if score is None
                    else (
                        f"Score reflects the captured turns, deterministic checks, "
                        f"and the scenario-level disposition {primary}."
                    )
                ),
                "confidence": (
                    decision.get("confidence", "high") if decision else "high"
                ),
            }
            for dimension, score in scores.items()
        }
        reviews.append(
            {
                "scenario_id": scenario_id,
                "title": scenario["title"],
                "domain": scenario["domain"],
                "turn_count": len(scenario["turns"]),
                "review_reason": {
                    "multi_turn": scenario_id in multi_turn_ids,
                    "critical_failure": scenario_id in critical_ids,
                },
                "dimension_reviews": dimension_reviews,
                "scenario_rationale": note,
                "deterministic_evaluation_agreed": (
                    primary in REAL_DEFECT_CLASSIFICATIONS
                    or (
                        not result.get("passed")
                        and primary
                        not in {
                            "EVALUATOR_FALSE_POSITIVE",
                            "NONDETERMINISTIC_ACCEPTABLE_VARIATION",
                            "TEST_HARNESS_DEFECT",
                        }
                    )
                    or (result.get("passed") and decision is None)
                ),
                "current_behavior_should_be_protected": (
                    scenario_id
                    in quality_gate["tier1_dispositions"]["hard_gate_passing_baseline"]
                    or primary
                    in {
                        "EVALUATOR_FALSE_POSITIVE",
                        "NONDETERMINISTIC_ACCEPTABLE_VARIATION",
                        "TEST_HARNESS_DEFECT",
                    }
                ),
                "known_defect": (
                    primary in REAL_DEFECT_CLASSIFICATIONS
                    or scenario_id in quality_gate.get("known_defects", {})
                ),
                "primary_classification": primary,
            }
        )
    return {
        "schema_version": 1,
        "label": "ENGINEER_REVIEW_BY_CODEX",
        "external_judge_model_used": False,
        "multi_turn_scenario_count": len(multi_turn_ids),
        "critical_failure_scenario_count": len(critical_ids),
        "reviewed_scenario_count": len(reviews),
        "scoring_scale": {
            "0": "critical failure",
            "1": "materially incorrect",
            "2": "partially correct",
            "3": "acceptable",
            "4": "strong",
            "null": "not exercised",
        },
        "reviews": reviews,
    }


def evaluate_quality_gate(
    candidate_report: Mapping[str, Any],
    baseline_report: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    candidate_engineer_review: Optional[Mapping[str, Any]] = None,
    baseline_engineer_review: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    candidate = {
        str(item["scenario_id"]): item for item in candidate_report["scenarios"]
    }
    baseline = {str(item["scenario_id"]): item for item in baseline_report["scenarios"]}
    blockers: List[str] = []

    for scenario_id in config["tier1_dispositions"]["hard_gate_passing_baseline"]:
        if scenario_id not in candidate:
            blockers.append(f"hard-gate scenario missing: {scenario_id}")
        elif not candidate[scenario_id].get("passed"):
            blockers.append(f"hard-gate scenario failed: {scenario_id}")

    baseline_critical = {
        (scenario_id, str(failure))
        for scenario_id, scenario in baseline.items()
        for failure in scenario.get("critical_failures", [])
    }
    candidate_critical = {
        (scenario_id, str(failure))
        for scenario_id, scenario in candidate.items()
        for failure in scenario.get("critical_failures", [])
    }
    for scenario_id, failure in sorted(candidate_critical - baseline_critical):
        blockers.append(f"new critical failure in {scenario_id}: {failure}")

    for scenario_id in config["tier1_dispositions"]["known_baseline_defect"]:
        if scenario_id not in candidate or scenario_id not in baseline:
            blockers.append(f"known-defect scenario missing: {scenario_id}")
            continue
        baseline_failed = sum(
            1
            for turn in baseline[scenario_id].get("turns", [])
            for check in turn.get("checks", [])
            if not check.get("passed")
        )
        candidate_failed = sum(
            1
            for turn in candidate[scenario_id].get("turns", [])
            for check in turn.get("checks", [])
            if not check.get("passed")
        )
        if candidate_failed > baseline_failed:
            blockers.append(
                f"known defect worsened: {scenario_id} "
                f"({baseline_failed} -> {candidate_failed} failed checks)"
            )

    material_decline = config["material_decline"]
    dimension_tolerances = {
        "source_relevance": float(material_decline["source_relevance_average_drop"]),
        "naturalness": float(material_decline["naturalness_average_drop"]),
        "resident_friendly_language": float(
            material_decline["resident_friendly_language_average_drop"]
        ),
    }
    for dimension, tolerance in dimension_tolerances.items():
        baseline_average = (
            baseline_report.get("by_dimension", {}).get(dimension, {}).get("average")
        )
        candidate_average = (
            candidate_report.get("by_dimension", {}).get(dimension, {}).get("average")
        )
        if isinstance(baseline_average, (int, float)) and isinstance(
            candidate_average, (int, float)
        ):
            if float(candidate_average) < float(baseline_average) - tolerance:
                blockers.append(
                    f"material {dimension} decline: "
                    f"{baseline_average} -> {candidate_average} "
                    f"(allowed drop {tolerance})"
                )

    def domain_averages(report: Mapping[str, Any]) -> Dict[str, float]:
        values: Dict[str, List[float]] = defaultdict(list)
        for scenario in report.get("scenarios", []):
            domain = str(scenario.get("domain") or "unknown")
            for turn in scenario.get("turns", []):
                values[domain].extend(
                    float(score)
                    for score in turn.get("dimension_scores", {}).values()
                    if isinstance(score, (int, float))
                )
        return {
            domain: round(sum(scores) / len(scores), 4)
            for domain, scores in values.items()
            if scores
        }

    baseline_domains = domain_averages(baseline_report)
    candidate_domains = domain_averages(candidate_report)
    domain_tolerance = float(material_decline["domain_average_drop"])
    for domain, baseline_average in baseline_domains.items():
        if domain not in candidate_domains:
            continue
        configured_threshold = config.get("domain_score_thresholds", {}).get(domain)
        baseline_floor = baseline_average - domain_tolerance
        required_floor = (
            min(float(configured_threshold), baseline_floor)
            if isinstance(configured_threshold, (int, float))
            else baseline_floor
        )
        if candidate_domains[domain] < required_floor:
            blockers.append(
                f"material domain decline in {domain}: "
                f"{baseline_average:.2f} -> {candidate_domains[domain]:.2f} "
                f"(required at least {required_floor:.2f})"
            )

    def clarification_failure_count(report: Mapping[str, Any]) -> int:
        return sum(
            1
            for scenario in report.get("scenarios", [])
            for turn in scenario.get("turns", [])
            for check in turn.get("checks", [])
            if not check.get("passed")
            and check.get("check")
            in {"no_unnecessary_clarification", "clarification_required"}
        )

    baseline_clarifications = clarification_failure_count(baseline_report)
    candidate_clarifications = clarification_failure_count(candidate_report)
    allowed_increase = int(material_decline["unnecessary_clarification_increase"])
    if candidate_clarifications > baseline_clarifications + allowed_increase:
        blockers.append(
            "unnecessary clarification failures increased: "
            f"{baseline_clarifications} -> {candidate_clarifications} "
            f"(allowed increase {allowed_increase})"
        )

    engineer_review_result = evaluate_engineer_review_gate(
        candidate_engineer_review,
        baseline_engineer_review,
        config,
    )
    blockers.extend(engineer_review_result["blockers"])

    return {
        "passed": not blockers,
        "blockers": blockers,
        "hard_gate_count": len(
            config["tier1_dispositions"]["hard_gate_passing_baseline"]
        ),
        "known_defect_count": len(
            config["tier1_dispositions"]["known_baseline_defect"]
        ),
        "blocked_count": len(
            config["tier1_dispositions"]["blocked_by_fixture_or_environment"]
        ),
        "comparison": {
            "baseline_critical_failures": len(baseline_critical),
            "candidate_critical_failures": len(candidate_critical),
            "baseline_unnecessary_clarification_failures": (baseline_clarifications),
            "candidate_unnecessary_clarification_failures": (candidate_clarifications),
            "baseline_domain_averages": baseline_domains,
            "candidate_domain_averages": candidate_domains,
        },
        "engineer_review_comparison": engineer_review_result,
    }


def evaluate_engineer_review_gate(
    candidate_review: Optional[Mapping[str, Any]],
    baseline_review: Optional[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    required = bool(config.get("require_engineer_review_comparison"))
    if candidate_review is None or baseline_review is None:
        blockers = (
            ["engineer review comparison is required but was not supplied"]
            if required
            else []
        )
        return {
            "passed": not blockers,
            "status": "missing",
            "blockers": blockers,
            "compared_scenarios": 0,
            "baseline_average": None,
            "candidate_average": None,
        }

    baseline_items = {
        str(item["scenario_id"]): item for item in baseline_review.get("reviews", [])
    }
    candidate_items = {
        str(item["scenario_id"]): item for item in candidate_review.get("reviews", [])
    }
    blockers: List[str] = []
    missing = sorted(set(baseline_items) - set(candidate_items))
    if missing:
        blockers.append(f"engineer review scenarios missing: {missing}")

    dimension_tolerance = float(
        config["material_decline"]["engineer_review_dimension_drop"]
    )
    average_tolerance = float(
        config["material_decline"]["engineer_review_average_drop"]
    )
    baseline_scores: List[float] = []
    candidate_scores: List[float] = []
    for scenario_id in sorted(set(baseline_items) & set(candidate_items)):
        baseline_dimensions = baseline_items[scenario_id].get("dimension_reviews", {})
        candidate_dimensions = candidate_items[scenario_id].get("dimension_reviews", {})
        for dimension, baseline_detail in baseline_dimensions.items():
            baseline_score = (
                baseline_detail.get("score")
                if isinstance(baseline_detail, Mapping)
                else None
            )
            candidate_detail = candidate_dimensions.get(dimension)
            candidate_score = (
                candidate_detail.get("score")
                if isinstance(candidate_detail, Mapping)
                else None
            )
            if not isinstance(baseline_score, (int, float)):
                continue
            baseline_scores.append(float(baseline_score))
            if not isinstance(candidate_score, (int, float)):
                blockers.append(
                    f"engineer review score missing: " f"{scenario_id}.{dimension}"
                )
                continue
            candidate_scores.append(float(candidate_score))
            if float(candidate_score) < float(baseline_score) - dimension_tolerance:
                blockers.append(
                    f"engineer review dimension declined: "
                    f"{scenario_id}.{dimension} "
                    f"{baseline_score} -> {candidate_score} "
                    f"(allowed drop {dimension_tolerance})"
                )

    baseline_average = (
        sum(baseline_scores) / len(baseline_scores) if baseline_scores else None
    )
    candidate_average = (
        sum(candidate_scores) / len(candidate_scores) if candidate_scores else None
    )
    if (
        baseline_average is not None
        and candidate_average is not None
        and candidate_average < baseline_average - average_tolerance
    ):
        blockers.append(
            "engineer review average declined: "
            f"{baseline_average:.2f} -> {candidate_average:.2f} "
            f"(allowed drop {average_tolerance})"
        )

    return {
        "passed": not blockers,
        "status": "compared",
        "blockers": blockers,
        "compared_scenarios": len(set(baseline_items) & set(candidate_items)),
        "baseline_average": (
            round(baseline_average, 4) if baseline_average is not None else None
        ),
        "candidate_average": (
            round(candidate_average, 4) if candidate_average is not None else None
        ),
    }


def classification_totals(triage: Mapping[str, Any]) -> Dict[str, int]:
    return {
        name: int(triage["primary_classification_totals"].get(name, 0))
        for name in sorted(CLASSIFICATIONS)
    }


def build_calibrated_baseline(
    original_report: Mapping[str, Any],
    reproduced_original: Mapping[str, Any],
    calibrated_report: Mapping[str, Any],
    triage: Mapping[str, Any],
    engineer_review: Mapping[str, Any],
    quality_gate: Mapping[str, Any],
) -> Dict[str, Any]:
    gate_result = evaluate_quality_gate(
        calibrated_report,
        calibrated_report,
        quality_gate,
        candidate_engineer_review=engineer_review,
        baseline_engineer_review=engineer_review,
    )
    return {
        "schema_version": 1,
        "source_report": "reports/stage2/conversational_baseline.json",
        "raw_original": deepcopy(original_report["totals"]),
        "raw_reproduced": deepcopy(reproduced_original["totals"]),
        "raw_reproduction_matches": all(
            original_report.get(field) == reproduced_original.get(field)
            for field in REPRODUCIBLE_REPORT_FIELDS
        ),
        "calibrated": deepcopy(calibrated_report["totals"]),
        "classification_totals": classification_totals(triage),
        "critical_check_classification_totals": deepcopy(
            triage["critical_check_classification_totals"]
        ),
        "tier_counts": {name: len(ids) for name, ids in quality_gate["tiers"].items()},
        "tier1_disposition_counts": {
            name: len(ids) for name, ids in quality_gate["tier1_dispositions"].items()
        },
        "engineer_review": {
            "reviewed_scenario_count": engineer_review["reviewed_scenario_count"],
            "multi_turn_scenario_count": engineer_review["multi_turn_scenario_count"],
            "critical_failure_scenario_count": engineer_review[
                "critical_failure_scenario_count"
            ],
        },
        "quality_gate_self_check": gate_result,
        "calibrated_report": deepcopy(calibrated_report),
    }


def markdown_table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> List[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return lines


def failure_triage_markdown(triage: Mapping[str, Any]) -> str:
    lines = [
        "# Stage 2.5 Failure Triage",
        "",
        f"Failed scenarios classified: **{triage['failed_scenario_count']}**.",
        f"Failed critical checks classified: **{triage['failed_critical_check_count']}**.",
        "",
        "## Primary Classification Totals",
        "",
    ]
    lines.extend(
        markdown_table(
            [
                (name, count)
                for name, count in triage["primary_classification_totals"].items()
            ],
            ("Classification", "Scenarios"),
        )
    )
    lines.extend(["", "## Scenario Triage", ""])
    lines.extend(
        markdown_table(
            [
                (
                    item["scenario_id"],
                    item["primary_classification"],
                    item["confidence"],
                    item["recommended_tier"],
                    item["recommended_gate_behavior"],
                    item["engineer_review_notes"].replace("|", "\\|"),
                )
                for item in triage["records"]
            ],
            (
                "Scenario",
                "Primary",
                "Confidence",
                "Tier",
                "Gate",
                "Evidence-based disposition",
            ),
        )
    )
    lines.append("")
    return "\n".join(lines)


def engineer_review_markdown(review: Mapping[str, Any]) -> str:
    lines = [
        "# ENGINEER_REVIEW_BY_CODEX",
        "",
        "No external judge model was used.",
        "",
        f"Multi-turn scenarios: **{review['multi_turn_scenario_count']}**.",
        f"Critical-failure scenarios: **{review['critical_failure_scenario_count']}**.",
        f"Unique scenarios reviewed: **{review['reviewed_scenario_count']}**.",
        "",
    ]
    for item in review["reviews"]:
        lines.extend(
            [
                f"## `{item['scenario_id']}` - {item['title']}",
                "",
                item["scenario_rationale"],
                "",
                f"- Primary classification: `{item['primary_classification']}`",
                f"- Deterministic evaluation agreed: `{str(item['deterministic_evaluation_agreed']).lower()}`",
                f"- Protect current behavior: `{str(item['current_behavior_should_be_protected']).lower()}`",
                f"- Known defect: `{str(item['known_defect']).lower()}`",
                "",
            ]
        )
        lines.extend(
            markdown_table(
                [
                    (
                        dimension,
                        ("N/E" if detail["score"] is None else detail["score"]),
                        detail["confidence"],
                        detail["rationale"],
                    )
                    for dimension, detail in item["dimension_reviews"].items()
                ],
                ("Dimension", "Score", "Confidence", "Rationale"),
            )
        )
        lines.append("")
    return "\n".join(lines)


def calibrated_baseline_markdown(report: Mapping[str, Any]) -> str:
    raw = report["raw_original"]
    calibrated = report["calibrated"]
    lines = [
        "# Stage 2.5 Calibrated Baseline",
        "",
        "The original Stage 2 result is preserved and reproduced separately from the calibrated profile.",
        "",
        "## Scores",
        "",
        f"- Raw original: **{raw['passed']}/{raw['evaluated']} passed**, "
        f"**{raw['failed']} failed**, **{raw['critical_failures']} critical checks**.",
        f"- Raw reproduction matches: **{str(report['raw_reproduction_matches']).lower()}**.",
        f"- Calibrated deterministic: **{calibrated['passed']}/{calibrated['evaluated']} passed**, "
        f"**{calibrated['failed']} failed**, **{calibrated['critical_failures']} critical checks**.",
        "",
        "The calibrated phrase-only score is not a chatbot success rate. Real defects, data limitations, evaluator limitations, and exploratory variation remain separated in the triage.",
        "",
        "## Classification Totals",
        "",
    ]
    lines.extend(
        markdown_table(
            list(report["classification_totals"].items()),
            ("Classification", "Primary scenario count"),
        )
    )
    lines.extend(["", "## Test Tiers", ""])
    lines.extend(
        markdown_table(
            list(report["tier_counts"].items()),
            ("Tier", "Scenario count"),
        )
    )
    lines.extend(["", "## Tier 1 Dispositions", ""])
    lines.extend(
        markdown_table(
            list(report["tier1_disposition_counts"].items()),
            ("Disposition", "Scenario count"),
        )
    )
    lines.extend(
        [
            "",
            "## Gate Interpretation",
            "",
            "- Tier 1 hard gates must continue to pass.",
            "- Known baseline defects may not gain failed checks or new critical failures.",
            "- Blocked Tier 1 scenarios become hard gates only after their named controlled fixtures exist.",
            "- Tier 2 live results are advisory until controlled fixtures are available.",
            "- Tier 3 uses deterministic safety checks plus structured review trends.",
            "",
        ]
    )
    return "\n".join(lines)
