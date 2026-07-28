"""Deterministic evaluation for conversational regression scenarios.

The evaluator deliberately avoids model calls and exact-answer snapshots. Scenarios
describe semantic requirements, source expectations, state assertions, and known
failure patterns. Qualitative judging is represented by a transport-neutral
interface but is disabled by the default runner.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence


SCORING_DIMENSIONS = (
    "intent_understanding",
    "context_retention",
    "correction_handling",
    "continuation_handling",
    "topic_switch_handling",
    "reference_and_pronoun_resolution",
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
)

SOURCE_CATEGORIES = {
    "official_data",
    "official_document",
    "event_listing",
    "local_journalism",
    "meeting_transcript",
    "community_material",
    "organization",
    "none",
}

SEVERITIES = {"low", "medium", "high", "critical"}
SOURCE_REQUIREMENTS = {"none", "preferred", "required"}
BASELINE_BRANCH = "main"
BASELINE_COMMIT = "840ec54dd1265b4f2f42cc18b66b54cbf2a99b89"

INTERNAL_TERM_PATTERNS = {
    "prepared_sql": re.compile(r"\bprepared[_\s-]?sql\b", re.IGNORECASE),
    "event_by_name": re.compile(r"\bevent_by_name\b", re.IGNORECASE),
    "raw_sql": re.compile(
        r"\b(?:select|insert|update|delete)\s+.+\b(?:from|into|set)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "rag_pipeline": re.compile(
        r"\bRAG\s+(?:pipeline|retrieval|route|mode)\b",
        re.IGNORECASE,
    ),
    "vector_store": re.compile(r"\b(?:Chroma|Qdrant)\b", re.IGNORECASE),
    "tool_language": re.compile(
        r"\b(?:tool call|retrieval failure|SQL execution|routing intent|plan executor)\b",
        re.IGNORECASE,
    ),
    "snake_case_intent": re.compile(
        r"\b(?:crime_monthly_count|311_issue_count|safety_overview)\b",
        re.IGNORECASE,
    ),
}

OVERCONFIDENT_PATTERNS = (
    re.compile(r"\bdefinitely\b", re.IGNORECASE),
    re.compile(r"\bwithout (?:a )?doubt\b", re.IGNORECASE),
    re.compile(r"\bI can confirm that\b", re.IGNORECASE),
    re.compile(r"\bthere is no possibility\b", re.IGNORECASE),
)

LIMITATION_PATTERNS = (
    re.compile(r"\b(?:could not|couldn't|did not|didn't) find\b", re.IGNORECASE),
    re.compile(r"\bavailable (?:data|sources|records|evidence)\b", re.IGNORECASE),
    re.compile(r"\b(?:may|might|can) (?:lag|be incomplete|not include)\b", re.IGNORECASE),
    re.compile(r"\bnot (?:available|confirmed|included|known)\b", re.IGNORECASE),
    re.compile(r"\binsufficient evidence\b", re.IGNORECASE),
)

GENERIC_CLARIFICATION_PATTERNS = (
    re.compile(
        r"^\s*(?:could|can|would) you (?:clarify|be more specific)",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*what (?:exactly|specifically) do you mean", re.IGNORECASE),
)


class ScenarioValidationError(ValueError):
    """Raised when the scenario corpus violates the Stage 1 contract."""


@dataclass(frozen=True)
class CheckResult:
    check: str
    dimension: str
    passed: bool
    message: str
    critical: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "dimension": self.dimension,
            "passed": self.passed,
            "message": self.message,
            "critical": self.critical,
        }


class QualitativeEvaluator(Protocol):
    """Optional future human/model evaluator. Never invoked by default."""

    name: str

    def evaluate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return dimension scores and narrative notes for one scenario."""


def _normalized(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _concept_matches(answer: str, concept: Mapping[str, Any]) -> bool:
    normalized_answer = _normalized(answer)
    for phrase in concept.get("any_of", []):
        if _normalized(phrase) in normalized_answer:
            return True
    pattern = concept.get("regex")
    return bool(
        pattern and re.search(str(pattern), answer, flags=re.IGNORECASE)
    )


def _lookup_path(value: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = value
    for segment in dotted_path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _answer_similarity(left: str, right: str) -> float:
    left_tokens = set(_normalized(left).split())
    right_tokens = set(_normalized(right).split())
    if not left_tokens and not right_tokens:
        return 1.0
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def _check(
    output: List[CheckResult],
    *,
    name: str,
    dimension: str,
    passed: bool,
    message: str,
    critical: bool = False,
) -> None:
    output.append(
        CheckResult(
            name,
            dimension,
            bool(passed),
            message,
            critical and not passed,
        )
    )


def evaluate_turn(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    previous_actual: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate one assistant turn using deterministic semantic checks."""
    answer = str(actual.get("answer") or "")
    sources = actual.get("sources") if isinstance(actual.get("sources"), list) else []
    result_ids = {
        str(item)
        for item in actual.get("result_ids", [])
        if item is not None and str(item).strip()
    }
    checks: List[CheckResult] = []

    _check(
        checks,
        name="answer_not_empty",
        dimension="failure_quality",
        passed=bool(answer.strip()),
        message="Assistant answer must not be empty.",
        critical=True,
    )

    for concept in expected.get("required_concepts", []):
        _check(
            checks,
            name=f"required_concept:{concept.get('name', 'unnamed')}",
            dimension=str(concept.get("dimension") or "completeness"),
            passed=_concept_matches(answer, concept),
            message=f"Answer must express concept: {concept.get('name', 'unnamed')}.",
            critical=bool(concept.get("critical")),
        )

    for concept in expected.get("forbidden_concepts", []):
        _check(
            checks,
            name=f"forbidden_concept:{concept.get('name', 'unnamed')}",
            dimension=str(concept.get("dimension") or "grounding"),
            passed=not _concept_matches(answer, concept),
            message=f"Answer must not express concept: {concept.get('name', 'unnamed')}.",
            critical=bool(concept.get("critical", True)),
        )

    for label, pattern in INTERNAL_TERM_PATTERNS.items():
        _check(
            checks,
            name=f"internal_term:{label}",
            dimension="internal_implementation_leakage",
            passed=not pattern.search(answer),
            message=f"Resident answer must not expose internal term family: {label}.",
            critical=True,
        )

    expected_categories = set(expected.get("expected_source_categories", []))
    actual_categories = {
        str(source.get("category") or "").strip()
        for source in sources
        if isinstance(source, Mapping)
    }
    source_requirement = expected.get("source_requirement", "none")
    if source_requirement == "required":
        _check(
            checks,
            name="required_sources_present",
            dimension="grounding",
            passed=bool(sources),
            message="At least one supporting source is required.",
            critical=True,
        )
    if expected_categories:
        _check(
            checks,
            name="expected_source_categories",
            dimension="source_type_distinction",
            passed=expected_categories.issubset(actual_categories),
            message=(
                "Expected source categories "
                f"{sorted(expected_categories)}; found {sorted(actual_categories)}."
            ),
            critical=source_requirement == "required",
        )
    forbidden_categories = set(expected.get("forbidden_source_categories", []))
    forbidden_found = forbidden_categories & actual_categories
    if forbidden_categories:
        _check(
            checks,
            name="forbidden_source_categories",
            dimension="source_relevance",
            passed=not forbidden_found,
            message=f"Forbidden source categories found: {sorted(forbidden_found)}.",
            critical=True,
        )

    metadata = (
        actual.get("metadata")
        if isinstance(actual.get("metadata"), Mapping)
        else {}
    )
    for path, wanted in expected.get("metadata_assertions", {}).items():
        found = _lookup_path(metadata, path)
        _check(
            checks,
            name=f"metadata:{path}",
            dimension=(
                "correction_handling"
                if expected.get("correction")
                else "context_retention"
            ),
            passed=found == wanted,
            message=f"Metadata path {path!r} expected {wanted!r}, found {found!r}.",
            critical=bool(expected.get("correction")),
        )

    if expected.get("clarification_required"):
        clarification = bool(actual.get("clarification")) or answer.rstrip().endswith("?")
        _check(
            checks,
            name="clarification_required",
            dimension="unnecessary_clarification",
            passed=clarification,
            message="This genuinely ambiguous turn requires a targeted clarification.",
        )
    elif not expected.get("clarification_allowed", False):
        generic = bool(actual.get("clarification")) or any(
            pattern.search(answer) for pattern in GENERIC_CLARIFICATION_PATTERNS
        )
        _check(
            checks,
            name="no_unnecessary_clarification",
            dimension="unnecessary_clarification",
            passed=not generic,
            message="The turn is answerable and must not receive a generic clarification.",
        )

    if expected.get("uncertainty_required"):
        has_limitation = any(pattern.search(answer) for pattern in LIMITATION_PATTERNS)
        overconfident = any(pattern.search(answer) for pattern in OVERCONFIDENT_PATTERNS)
        _check(
            checks,
            name="uncertainty_disclosed",
            dimension="uncertainty_and_restraint",
            passed=has_limitation and not overconfident,
            message="Answer must state the evidence limitation without claiming certainty.",
            critical=True,
        )

    if previous_actual is not None and expected.get("must_not_repeat_previous_answer"):
        similarity = _answer_similarity(answer, str(previous_actual.get("answer") or ""))
        _check(
            checks,
            name="answer_not_repeated",
            dimension="repetition",
            passed=similarity < 0.82,
            message=f"Continuation answer similarity was {similarity:.2f}; expected below 0.82.",
            critical=True,
        )

    if previous_actual is not None and expected.get("must_not_repeat_result_ids"):
        previous_ids = {
            str(item)
            for item in previous_actual.get("result_ids", [])
            if item is not None and str(item).strip()
        }
        overlap = previous_ids & result_ids
        _check(
            checks,
            name="result_ids_not_repeated",
            dimension="continuation_handling",
            passed=not overlap,
            message=f"Continuation repeated stable result IDs: {sorted(overlap)}.",
            critical=True,
        )

    by_dimension: Dict[str, List[CheckResult]] = {}
    for item in checks:
        by_dimension.setdefault(item.dimension, []).append(item)
    scores: Dict[str, Optional[int]] = {}
    for dimension in SCORING_DIMENSIONS:
        items = by_dimension.get(dimension, [])
        if not items:
            scores[dimension] = None
        elif all(item.passed for item in items):
            scores[dimension] = 4
        elif any(item.critical and not item.passed for item in items):
            scores[dimension] = 0
        elif any(item.passed for item in items):
            scores[dimension] = 2
        else:
            scores[dimension] = 1

    critical_failures = [
        item.message for item in checks if item.critical and not item.passed
    ]
    return {
        "passed": all(item.passed for item in checks),
        "checks": [item.to_dict() for item in checks],
        "dimension_scores": scores,
        "critical_failures": critical_failures,
    }


def evaluate_scenario(
    scenario: Mapping[str, Any],
    actual_turns: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Evaluate all turns of one scenario while preserving turn order."""
    expected_turns = scenario["turns"]
    turn_reports: List[Dict[str, Any]] = []
    previous: Optional[Mapping[str, Any]] = None
    for index, turn in enumerate(expected_turns):
        actual = actual_turns[index] if index < len(actual_turns) else {}
        report = evaluate_turn(turn["expected"], actual, previous_actual=previous)
        report.update(
            {
                "turn_index": index,
                "user": turn["user"],
                "expected_behavior": turn["expected"]["behavior"],
            }
        )
        turn_reports.append(report)
        previous = actual
    extra_turns = max(0, len(actual_turns) - len(expected_turns))
    critical = [
        failure
        for report in turn_reports
        for failure in report["critical_failures"]
    ]
    return {
        "scenario_id": scenario["id"],
        "title": scenario["title"],
        "domain": scenario["domain"],
        "conversation_category": scenario["conversation_category"],
        "coverage_tags": scenario["coverage_tags"],
        "severity": scenario["severity"],
        "passed": all(report["passed"] for report in turn_reports)
        and extra_turns == 0,
        "turns": turn_reports,
        "extra_actual_turns": extra_turns,
        "critical_failures": critical,
    }


def build_qualitative_payload(
    scenario: Mapping[str, Any],
    actual_turns: Sequence[Mapping[str, Any]],
    deterministic_report: Mapping[str, Any],
) -> Dict[str, Any]:
    """Create a sanitized handoff for an optional, explicitly enabled judge."""
    return {
        "scenario": {
            "id": scenario["id"],
            "title": scenario["title"],
            "description": scenario["description"],
            "initial_context": scenario["initial_context"],
            "turns": scenario["turns"],
        },
        "actual_turns": list(actual_turns),
        "deterministic_report": dict(deterministic_report),
        "scoring_dimensions": list(SCORING_DIMENSIONS),
        "score_scale": {
            "0": "failed",
            "1": "materially incorrect",
            "2": "partially correct",
            "3": "acceptable",
            "4": "strong",
        },
    }


def load_json_compatible_yaml(path: Path) -> Dict[str, Any]:
    """Load JSON syntax, which is valid YAML 1.2, without a YAML dependency."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(f"Could not load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ScenarioValidationError("Scenario corpus root must be an object")
    return value


def expand_corpus_defaults(corpus: Mapping[str, Any]) -> Dict[str, Any]:
    """Expand authoring defaults so every validated turn has the full contract."""
    expanded = deepcopy(dict(corpus))
    expected_defaults = expanded.get("expected_defaults", {})
    if not isinstance(expected_defaults, dict):
        raise ScenarioValidationError("corpus.expected_defaults must be an object")
    for scenario in expanded.get("scenarios", []):
        if not isinstance(scenario, dict):
            continue
        for turn in scenario.get("turns", []):
            if not isinstance(turn, dict):
                continue
            expected = turn.get("expected", {})
            if not isinstance(expected, dict):
                continue
            turn["expected"] = {**deepcopy(expected_defaults), **expected}
    return expanded


def _require_type(
    errors: List[str],
    value: Mapping[str, Any],
    key: str,
    expected_type: type,
    location: str,
) -> None:
    if key not in value:
        errors.append(f"{location}: missing {key}")
    elif not isinstance(value[key], expected_type):
        errors.append(f"{location}.{key}: expected {expected_type.__name__}")


def validate_corpus(
    corpus: Mapping[str, Any],
    *,
    require_minimum: bool = True,
) -> Dict[str, Any]:
    """Validate the Stage 1 scenario contract without third-party packages."""
    errors: List[str] = []
    _require_type(errors, corpus, "schema_version", int, "corpus")
    _require_type(errors, corpus, "baseline", dict, "corpus")
    _require_type(errors, corpus, "evaluation_dimensions", list, "corpus")
    _require_type(errors, corpus, "scenarios", list, "corpus")
    baseline = corpus.get("baseline")
    if isinstance(baseline, dict):
        for key in ("branch", "commit", "created", "purpose"):
            _require_type(errors, baseline, key, str, "corpus.baseline")
        if baseline.get("branch") != BASELINE_BRANCH:
            errors.append(
                f"corpus.baseline.branch: expected {BASELINE_BRANCH!r}"
            )
        if baseline.get("commit") != BASELINE_COMMIT:
            errors.append(
                f"corpus.baseline.commit: expected {BASELINE_COMMIT}"
            )
    scenarios = (
        corpus.get("scenarios")
        if isinstance(corpus.get("scenarios"), list)
        else []
    )
    ids: set[str] = set()
    coverage: Dict[str, int] = {}
    multi_turn = 0

    if require_minimum and len(scenarios) < 80:
        errors.append(f"corpus.scenarios: expected at least 80, found {len(scenarios)}")

    required_scenario_fields = {
        "id": str,
        "title": str,
        "domain": str,
        "conversation_category": str,
        "coverage_tags": list,
        "description": str,
        "initial_context": dict,
        "turns": list,
        "severity": str,
        "notes": str,
    }
    required_expected_fields = {
        "behavior": str,
        "context_required": bool,
        "correction": bool,
        "continuation": bool,
        "topic_change": bool,
        "clarification_allowed": bool,
        "clarification_required": bool,
        "required_concepts": list,
        "forbidden_concepts": list,
        "expected_source_categories": list,
        "forbidden_source_categories": list,
        "source_requirement": str,
        "acceptable_limitations": list,
    }

    for scenario_index, scenario in enumerate(scenarios):
        location = f"scenarios[{scenario_index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{location}: expected object")
            continue
        for key, expected_type in required_scenario_fields.items():
            _require_type(errors, scenario, key, expected_type, location)
        scenario_id = scenario.get("id")
        if isinstance(scenario_id, str):
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", scenario_id):
                errors.append(f"{location}.id: invalid format")
            if scenario_id in ids:
                errors.append(f"{location}.id: duplicate {scenario_id}")
            ids.add(scenario_id)
        if scenario.get("severity") not in SEVERITIES:
            errors.append(f"{location}.severity: invalid value")
        tags = scenario.get("coverage_tags", [])
        if isinstance(tags, list):
            for tag in tags:
                if not isinstance(tag, str) or not tag:
                    errors.append(f"{location}.coverage_tags: invalid tag")
                else:
                    coverage[tag] = coverage.get(tag, 0) + 1
        turns = scenario.get("turns", [])
        if isinstance(turns, list):
            if not turns:
                errors.append(f"{location}.turns: at least one turn is required")
            if len(turns) > 1:
                multi_turn += 1
            for turn_index, turn in enumerate(turns):
                turn_location = f"{location}.turns[{turn_index}]"
                if not isinstance(turn, dict):
                    errors.append(f"{turn_location}: expected object")
                    continue
                _require_type(errors, turn, "user", str, turn_location)
                _require_type(errors, turn, "expected", dict, turn_location)
                expected = turn.get("expected")
                if not isinstance(expected, dict):
                    continue
                for key, expected_type in required_expected_fields.items():
                    _require_type(
                        errors,
                        expected,
                        key,
                        expected_type,
                        f"{turn_location}.expected",
                    )
                if expected.get("source_requirement") not in SOURCE_REQUIREMENTS:
                    errors.append(
                        f"{turn_location}.expected.source_requirement: invalid"
                    )
                for source_key in (
                    "expected_source_categories",
                    "forbidden_source_categories",
                ):
                    for category in expected.get(source_key, []):
                        if category not in SOURCE_CATEGORIES:
                            errors.append(
                                f"{turn_location}.expected.{source_key}: "
                                f"unknown category {category!r}"
                            )
                if expected.get("clarification_required") and not expected.get(
                    "clarification_allowed"
                ):
                    errors.append(
                        f"{turn_location}.expected: required clarification must be allowed"
                    )
                optional_expected_types = {
                    "metadata_assertions": dict,
                    "uncertainty_required": bool,
                    "must_not_repeat_previous_answer": bool,
                    "must_not_repeat_result_ids": bool,
                }
                for key, expected_type in optional_expected_types.items():
                    if key in expected and not isinstance(expected[key], expected_type):
                        errors.append(
                            f"{turn_location}.expected.{key}: "
                            f"expected {expected_type.__name__}"
                        )
                for concept_key in ("required_concepts", "forbidden_concepts"):
                    for concept_index, concept in enumerate(
                        expected.get(concept_key, [])
                    ):
                        concept_location = (
                            f"{turn_location}.expected.{concept_key}[{concept_index}]"
                        )
                        if not isinstance(concept, dict):
                            errors.append(f"{concept_location}: expected object")
                            continue
                        if not isinstance(concept.get("name"), str):
                            errors.append(f"{concept_location}.name: required string")
                        any_of = concept.get("any_of", [])
                        if not isinstance(any_of, list) or any(
                            not isinstance(item, str) or not item
                            for item in any_of
                        ):
                            errors.append(f"{concept_location}.any_of: invalid")
                        if not any_of and not isinstance(concept.get("regex"), str):
                            errors.append(
                                f"{concept_location}: any_of or regex is required"
                            )
                        dimension = concept.get("dimension")
                        if dimension is not None and dimension not in SCORING_DIMENSIONS:
                            errors.append(
                                f"{concept_location}.dimension: unknown dimension"
                            )

    configured_dimensions = corpus.get("evaluation_dimensions", [])
    missing_dimensions = set(SCORING_DIMENSIONS) - set(configured_dimensions)
    if missing_dimensions:
        errors.append(
            "corpus.evaluation_dimensions missing: "
            + ", ".join(sorted(missing_dimensions))
        )

    required_coverage = {
        "greetings": 5,
        "events": 12,
        "boston_311": 10,
        "crime_public_safety": 10,
        "policy_budget_zoning_development": 10,
        "meetings_news_community": 10,
        "entities_organizations": 8,
        "cross_domain_synthesis": 5,
        "no_evidence_uncertainty": 5,
        "general_mechanics": 15,
    }
    if require_minimum:
        for category, minimum in required_coverage.items():
            found = coverage.get(category, 0)
            if found < minimum:
                errors.append(
                    f"coverage {category}: expected at least {minimum}, found {found}"
                )

    if errors:
        raise ScenarioValidationError("\n".join(errors))
    return {
        "scenario_count": len(scenarios),
        "multi_turn_count": multi_turn,
        "coverage_counts": dict(sorted(coverage.items())),
        "ids": sorted(ids),
    }


def load_and_validate_corpus(
    path: Path,
    *,
    require_minimum: bool = True,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    corpus = expand_corpus_defaults(load_json_compatible_yaml(path))
    return corpus, validate_corpus(corpus, require_minimum=require_minimum)
