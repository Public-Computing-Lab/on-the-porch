#!/usr/bin/env python3
"""Regression tests for document relevance and budget no-evidence behavior."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api"), str(ROOT / "on_the_porch")]

from answering_kernel import GlobalDeadline  # noqa: E402
from civic_documents import DocumentPlan, execute_document_plan  # noqa: E402
from document_relevance import (  # noqa: E402
    extract_document_constraints,
    filter_relevant_passages,
    format_no_evidence_answer,
    score_passage_relevance,
    strip_internal_retrieval_language,
)


def _passage(**kwargs):
    base = {
        "passage_id": "doc.test",
        "corpus": "POLICY",
        "source_name": "Test source",
        "publisher": "Test publisher",
        "authority": "Test",
        "path": None,
        "date": None,
        "passage": "generic civic text " * 8,
        "source_type": "PLANNING_POLICY",
        "provenance_class": "OFFICIAL_SECONDARY",
        "provenance_label": "Secondary official / curated reference",
        "limitation": "Policy excerpts are general information, not legal advice.",
    }
    base.update(kwargs)
    return base


def test_constraints_extract_mayoral_budget_year():
    c = extract_document_constraints("tell me about the mayors budget for 2027")
    assert "budget" in c.topics
    assert "mayoral_budget" in c.topics
    assert "2027" in c.fiscal_years
    assert c.require_official_budget is True


def test_unrelated_zoning_rejected_for_budget():
    c = extract_document_constraints("mayor's budget for 2027")
    zoning = _passage(
        corpus="POLICY",
        source_name="Where to find official Boston zoning information",
        passage="This note explains where to find the Boston Zoning Code and BPDA guidance.",
    )
    rel = score_passage_relevance(zoning, c)
    assert rel.admitted is False


def test_unrelated_meeting_and_community_rejected():
    c = extract_document_constraints("Boston FY2027 budget proposal")
    meeting = _passage(
        corpus="TRANSCRIPT",
        source_name="Codman Square neighborhood meeting notes",
        passage="Neighbors discussed traffic calming and local business hours at the meeting.",
    )
    community = _passage(
        corpus="COMMUNITY",
        source_name="Fields Corner Civic Association priorities",
        passage="The association listed street cleanliness and local storefronts as priorities.",
    )
    assert score_passage_relevance(meeting, c).admitted is False
    assert score_passage_relevance(community, c).admitted is False


def test_sparse_index_adversarial_all_unrelated_yields_empty():
    c = extract_document_constraints("tell me about the mayor's budget for 2027")
    unrelated = [
        _passage(
            corpus="POLICY",
            source_name="Zoning pointer",
            passage="Official Boston zoning information is published by BPDA and ISD.",
        ),
        _passage(
            corpus="TRANSCRIPT",
            source_name="Neighborhood meeting",
            passage="The meeting covered parks maintenance and school pickup congestion.",
        ),
        _passage(
            corpus="COMMUNITY",
            source_name="NDC overview",
            passage="Codman Square NDC supports local housing and commercial revitalization.",
        ),
    ]
    admitted, reason = filter_relevant_passages(unrelated, c)
    assert admitted == []
    assert reason in {
        "SOURCE_SCOPE_UNAVAILABLE",
        "REQUESTED_PERIOD_UNAVAILABLE",
        "NO_RELEVANT_EVIDENCE",
        "ONLY_WEAK_MATCHES",
    }


def test_matching_budget_passage_admitted():
    c = extract_document_constraints("what is in Boston’s FY2027 budget proposal?")
    good = _passage(
        corpus="POLICY",
        source_name="City of Boston Mayor’s Office FY2027 Budget Proposal",
        publisher="City of Boston Mayor's Office",
        authority="City of Boston",
        date="2026-04-10",
        passage=(
            "The Mayor’s FY2027 operating budget proposal outlines appropriations for "
            "public schools, public safety, and neighborhood services."
        ),
    )
    rel = score_passage_relevance(good, c)
    assert rel.admitted is True
    assert rel.year_match is True
    admitted, reason = filter_relevant_passages([good], c)
    assert len(admitted) == 1
    assert reason == ""


def test_year_mismatch_rejected():
    c = extract_document_constraints("mayor budget 2027")
    old = _passage(
        corpus="POLICY",
        source_name="City of Boston Mayor’s Office budget",
        publisher="City of Boston Mayor's Office",
        passage="The Mayor’s FY2025 operating budget proposal increased school funding.",
    )
    rel = score_passage_relevance(old, c)
    assert rel.admitted is False


def test_no_evidence_answer_is_natural():
    c = extract_document_constraints("tell me about the mayors budget for 2027")
    answer = format_no_evidence_answer(c, "SOURCE_SCOPE_UNAVAILABLE")
    low = answer.casefold()
    assert "bounded source passage" not in low
    assert "evidence candidates" not in low
    assert "official_secondary" not in low
    assert "fy2027" in low or "2027" in low
    assert "can’t reliably summarize" in low or "cannot reliably summarize" in low or "don’t currently have" in low


def test_execute_plan_rejects_sparse_unrelated_retriever():
    plan = DocumentPlan(
        query="tell me about the mayors budget for 2027",
        corpora=("POLICY", "TRANSCRIPT", "COMMUNITY"),
        location_id="dorchester",
        time_scope=None,
        source_types=(),
        limit=8,
    )

    def fake_retrieve(query: str, *, k: int, doc_type: str):
        del query, k
        if doc_type == "policy":
            return {
                "chunks": [
                    "Where to find official Boston zoning information. This is not the Zoning Code."
                ],
                "metadata": [{"source": "zoning pointer", "provenance_class": "OFFICIAL_SECONDARY"}],
            }
        if doc_type == "transcript":
            return {
                "chunks": [
                    "Codman Square neighborhood meeting notes discussed parks and traffic only."
                ],
                "metadata": [{"source": "meeting notes", "provenance_class": "COMMUNITY_PUBLISHED"}],
            }
        return {
            "chunks": [
                "Fields Corner Civic Association priorities: storefronts and cleanliness."
            ],
            "metadata": [{"source": "civic assoc", "provenance_class": "COMMUNITY_PUBLISHED"}],
        }

    result = execute_document_plan(
        "tell me about the mayors budget for 2027",
        plan=plan,
        deadline=GlobalDeadline.from_started_at(time.perf_counter()),
        retrieve=fake_retrieve,
        enabled_sources=lambda: {
            "boston.policy_corpus",
            "dorchester.meeting_transcripts",
            "dorchester.community_documents",
        },
    )
    low = result.answer.casefold()
    assert result.civic_tool_result.records == ()
    assert result.sources == ()
    assert "bounded source passage" not in low
    assert "evidence candidates" not in low
    assert "zoning" not in low
    assert "fields corner" not in low
    assert "authoritative" in low or "don’t currently have" in low or "do not currently have" in low


def test_zoning_question_still_admits_zoning():
    c = extract_document_constraints("how does zoning work in Boston?")
    zoning = _passage(
        corpus="POLICY",
        source_name="Where to find official Boston zoning information",
        passage="Boston zoning information is published through BPDA and ISD official channels.",
    )
    assert score_passage_relevance(zoning, c).admitted is True


def test_meeting_budget_question_can_use_transcript():
    c = extract_document_constraints("what did a neighborhood meeting say about the budget?")
    meeting = _passage(
        corpus="TRANSCRIPT",
        source_name="Neighborhood meeting notes",
        passage="Residents asked how the city budget would fund library hours next year.",
    )
    assert score_passage_relevance(meeting, c).admitted is True


def test_strip_internal_language():
    cleaned = strip_internal_retrieval_language(
        "I found 4 bounded source passage(s) related to budget. These are evidence candidates only."
    )
    assert "bounded source passage" not in cleaned.casefold()
    assert "evidence candidates" not in cleaned.casefold()


def test_no_exact_query_routing_in_module():
    src = (ROOT / "on_the_porch" / "document_relevance.py").read_text(encoding="utf-8")
    assert "tell me about the mayors budget for 2027" not in src


def main() -> int:
    tests = [
        test_constraints_extract_mayoral_budget_year,
        test_unrelated_zoning_rejected_for_budget,
        test_unrelated_meeting_and_community_rejected,
        test_sparse_index_adversarial_all_unrelated_yields_empty,
        test_matching_budget_passage_admitted,
        test_year_mismatch_rejected,
        test_no_evidence_answer_is_natural,
        test_execute_plan_rejects_sparse_unrelated_retriever,
        test_zoning_question_still_admits_zoning,
        test_meeting_budget_question_can_use_transcript,
        test_strip_internal_language,
        test_no_exact_query_routing_in_module,
    ]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
