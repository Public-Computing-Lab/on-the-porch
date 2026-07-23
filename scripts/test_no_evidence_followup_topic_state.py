#!/usr/bin/env python3
"""Multi-turn tests for no-evidence follow-up topic retention."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api"), str(ROOT / "on_the_porch")]

from conversation_state import (  # noqa: E402
    ActiveEntity,
    ConversationState,
    ThreadStateEnvelope,
)
from conversation_dimensions import CanonicalTopic  # noqa: E402
from document_relevance import (  # noqa: E402
    extract_document_constraints,
    followup_constraint_query,
    format_no_evidence_answer,
)
from model_led_turn_service import (  # noqa: E402
    _ambiguous_document_referents,
    _bound_document_subject,
    _dialogue_context_after_domain,
    _trusted_planner_context,
)
from semantic_dialogue_controller import validate_dialogue_context  # noqa: E402
from semantic_plan_compiler import _compile_documents  # noqa: E402
from semantic_planner import (  # noqa: E402
    GeographyMode,
    SemanticDomain,
    SemanticGeography,
    SemanticGoalClarity,
    SemanticOperation,
    SemanticTask,
    SemanticTime,
    SemanticTimeKind,
    SourceScope,
)


def _doc_dialogue(subject: str, *, fiscal_year: str | None = "2027", status: str = "NO_EVIDENCE"):
    summary = {
        "kind": "DOCUMENT_EVIDENCE",
        "status": status,
        "subject": subject,
        "source_scope": "OFFICIAL",
        "geography_label": "Boston",
        "passage_count": 0,
        "evidence_status": "INSUFFICIENT_EVIDENCE",
    }
    if fiscal_year:
        summary["fiscal_year"] = fiscal_year
    return _dialogue_context_after_domain(
        None,
        domain="DOCUMENTS",
        capability="SEARCH_DOCUMENTS",
        summary=summary,
    )


def test_a_budget_no_evidence_persists_and_binds_news_followup():
    dialogue = _doc_dialogue("Boston mayoral budget FY2027")
    assert dialogue["previous_result_summary"]["subject"] == "Boston mayoral budget FY2027"
    env = ThreadStateEnvelope(
        retrieval_cache={},
        conversation_state=ConversationState(),
        dialogue_context=dialogue,
    )
    context, tokens = _trusted_planner_context(env)
    assert context["previous_document"]["subject"] == "Boston mayoral budget FY2027"
    assert "previous_document" in context
    # Document subjects are exposed compactly without a verbose summary token.
    bound = _bound_document_subject(
        planner_subject="this",
        resident_message="has there been anything in the news recently about this?",
        prior_document=dialogue["previous_result_summary"],
        trusted_context=context,
    )
    assert bound == "Boston mayoral budget FY2027"
    query = followup_constraint_query(
        "has there been anything in the news recently about this?",
        prior_subject=bound,
    )
    constraints = extract_document_constraints(query)
    assert "news" in constraints.topics
    assert "budget" in constraints.topics
    answer = format_no_evidence_answer(constraints, "NO_RELEVANT_EVIDENCE")
    assert "news coverage" in answer.casefold()
    assert "clarify" not in answer.casefold()
    assert "bounded" not in answer.casefold()


def test_b_budget_inventory_followup_keeps_topic():
    dialogue = _doc_dialogue("Boston mayoral budget FY2027")
    query = followup_constraint_query(
        "What is the latest year you do have?",
        prior_subject=dialogue["previous_result_summary"]["subject"],
    )
    assert "Boston mayoral budget FY2027" in query


def test_c_entity_profile_persists_for_news_followup():
    dialogue = _dialogue_context_after_domain(
        None,
        domain="DOCUMENTS",
        capability="EXPLAIN_ENTITY",
        summary={
            "kind": "DOCUMENT_EVIDENCE",
            "status": "EVIDENCE_FOUND",
            "subject": "Eversource",
            "source_scope": "ANY",
            "geography_label": "Boston",
            "passage_count": 1,
            "evidence_status": "COMPLETE",
        },
    )
    env = ThreadStateEnvelope(
        retrieval_cache={},
        conversation_state=ConversationState(),
        dialogue_context=dialogue,
    )
    context, _tokens = _trusted_planner_context(env)
    assert context["previous_document"]["subject"] == "Eversource"
    bound = _bound_document_subject(
        planner_subject="it",
        resident_message="has there been recent news about it?",
        prior_document=dialogue["previous_result_summary"],
        trusted_context=context,
    )
    assert bound == "Eversource"


def test_d_meeting_to_policy_binds_subject():
    dialogue = _doc_dialogue("Boston housing policy", fiscal_year=None)
    bound = _bound_document_subject(
        planner_subject="this",
        resident_message="Is there an official policy about this?",
        prior_document=dialogue["previous_result_summary"],
        trusted_context={},
    )
    assert bound == "Boston housing policy"


def test_e_policy_to_meeting_binds_subject():
    dialogue = _doc_dialogue("Boston zoning", fiscal_year=None)
    bound = _bound_document_subject(
        planner_subject="this",
        resident_message="Was this discussed at a public meeting?",
        prior_document=dialogue["previous_result_summary"],
        trusted_context={},
    )
    assert bound == "Boston zoning"


def test_f_why_unavailable_keeps_topic():
    dialogue = _doc_dialogue("Boston mayoral budget FY2027")
    query = followup_constraint_query(
        "Why don’t you have information about it?",
        prior_subject=dialogue["previous_result_summary"]["subject"],
    )
    assert "Boston mayoral budget FY2027" in query


def test_g_ambiguous_multiple_referents_asks_targeted_clarification():
    dialogue = _doc_dialogue("Boston mayoral budget FY2027")
    # Simulate a competing selected event referent.
    state = ConversationState(
        active_topic=CanonicalTopic.EVENTS,
        active_entity=ActiveEntity.event("435"),
    )
    answer = _ambiguous_document_referents(
        "Has there been news about this?",
        dialogue_context=dialogue,
        conversation_state=state,
    )
    assert answer is not None
    assert "Boston mayoral budget FY2027" in answer
    assert "previously selected event" in answer


def test_h_provider_failure_helpers_do_not_clear_prior_summary():
    prior = _doc_dialogue("Boston mayoral budget FY2027")
    # Preserve path used on planner failure: keep prior dialogue untouched.
    preserved = validate_dialogue_context(prior)
    assert preserved["previous_result_summary"]["subject"] == "Boston mayoral budget FY2027"


def test_i_compiler_binds_anaphoric_document_query():
    task = SemanticTask(
        domain=SemanticDomain.DOCUMENTS,
        operation=SemanticOperation.SUMMARIZE,
        subject="this",
        geography=SemanticGeography(mode=GeographyMode.DEFAULT, value=None),
        time=SemanticTime(kind=SemanticTimeKind.NONE, value=None),
        source_scope=SourceScope.NEWS,
        goal_clarity=SemanticGoalClarity.CLEAR,
    )
    compiled = _compile_documents(
        task,
        "dorchester",
        None,
        trusted_context={
            "previous_document": {
                "subject": "Boston mayoral budget FY2027",
                "fiscal_year": "2027",
                "source_scope": "OFFICIAL",
                "status": "NO_EVIDENCE",
            }
        },
    )
    _tool, args, _domains = compiled[0]
    assert args["query"] == "Boston mayoral budget FY2027"
    assert "NEWS" in args["corpora"]


def main() -> int:
    tests = [
        test_a_budget_no_evidence_persists_and_binds_news_followup,
        test_b_budget_inventory_followup_keeps_topic,
        test_c_entity_profile_persists_for_news_followup,
        test_d_meeting_to_policy_binds_subject,
        test_e_policy_to_meeting_binds_subject,
        test_f_why_unavailable_keeps_topic,
        test_g_ambiguous_multiple_referents_asks_targeted_clarification,
        test_h_provider_failure_helpers_do_not_clear_prior_summary,
        test_i_compiler_binds_anaphoric_document_query,
    ]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
