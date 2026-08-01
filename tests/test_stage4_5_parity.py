from __future__ import annotations

import json
from pathlib import Path

import answer_synthesizer
import config
import intent_router
import plan_builder
import unified_chatbot
from execution_plan import ExecutionPlan, FactBundle, FactPart, PlanPart, RagHints
from plan_executor import _execute_rag_part, _execute_sql_part


QUESTION = "Summarize the latest city council minutes related to Dorchester transportation projects."
FIXTURE = Path(__file__).parent / "fixtures" / "stage4_5" / "council_minutes.json"


def _plan(question: str = QUESTION) -> ExecutionPlan:
    route = intent_router.route_with_rules(question)
    assert route is not None
    plan = plan_builder.build_execution_plan_rules(question, route)
    assert plan is not None
    return plan


def test_exact_query_selects_official_meeting_evidence_domain() -> None:
    route = intent_router.route_with_rules(QUESTION)
    assert route is not None
    assert route["mode"] == "rag"
    assert route["intent"] == "official_meeting_documents"
    assert route["evidence_domain"] == "official_council_minutes"
    assert route["required_concepts"] == ["dorchester", "transportation"]

    part = _plan().parts[0]
    assert part.path == "rag"
    assert part.rag_hints is not None
    assert part.rag_hints.evidence_domain == "official_council_minutes"


def test_prior_311_wording_does_not_change_current_turn_domain() -> None:
    current = "Now summarize the latest city council minutes related to Dorchester transportation projects."
    route = intent_router.route_with_rules(current)
    assert route is not None
    assert route["mode"] == "rag"
    assert route["evidence_domain"] == "official_council_minutes"


def test_missing_council_evidence_is_domain_correct() -> None:
    part = _plan().parts[0]
    result = _execute_rag_part(part, QUESTION, lambda _q, _h: ([], []))
    answer = result.canonical_facts.lower()
    assert "city council minutes" in answer
    assert "transportation" in answer
    assert "311" not in answer
    assert result.rag_chunks == []

    bundle = FactBundle(question=QUESTION, parts=[result])
    assert answer_synthesizer.synthesize_answer(QUESTION, bundle) == result.canonical_facts


def test_relevant_official_fixture_is_kept_and_unrelated_housing_is_removed() -> None:
    docs = json.loads(FIXTURE.read_text(encoding="utf-8"))["documents"]
    chunks = [doc["text"] for doc in docs]
    metadata = [{key: value for key, value in doc.items() if key != "text"} for doc in docs]
    result = _execute_rag_part(
        _plan().parts[0],
        QUESTION,
        lambda _q, _h: (chunks, metadata),
    )
    assert result.rag_chunks == [docs[0]["text"]]
    assert result.rag_metadata == [metadata[0]]
    assert "Retrieved 1 source passage" in result.canonical_facts
    blocks = answer_synthesizer._blocks_from_bundle(FactBundle(question=QUESTION, parts=[result]))
    assert blocks[0]["rag_sources"][0]["source_id"] == docs[0]["id"]
    assert blocks[0]["rag_sources"][0]["published_at"] == "2026-06-18"
    assert "2026-06-18" in blocks[0]["rag_context"]


def test_community_text_cannot_self_assert_official_provenance() -> None:
    part = _plan().parts[0]
    chunk = (
        "A community post says Boston City Council minutes discussed Dorchester "
        "transportation, but it is not an official record."
    )
    result = _execute_rag_part(
        part,
        QUESTION,
        lambda _q, _h: (
            [chunk],
            [{"source_type": "community", "publisher": "Resident forum"}],
        ),
    )
    assert result.rag_chunks == []


def test_unrelated_311_sql_failure_cannot_label_council_minutes_question() -> None:
    part = PlanPart(
        path="sql",
        intent="311_issue_count",
        topic="Boston 311 service-request",
        sub_question="How many pothole requests were reported?",
    )

    def fail(_sql: str):
        raise RuntimeError("fixture database unavailable")

    result = _execute_sql_part(part, QUESTION, fail)
    assert result.error
    assert "city council minutes" in result.canonical_facts.lower()
    assert "311" not in result.canonical_facts
    assert "311_issue_count" not in result.canonical_facts


def test_successful_relevant_evidence_outranks_unrelated_failed_branch() -> None:
    bundle = FactBundle(
        question=QUESTION,
        parts=[
            FactPart(
                path="sql",
                intent="311_issue_count",
                topic="311",
                sub_question="stale question",
                canonical_facts="I couldn't retrieve the relevant Boston 311 information right now.",
                error="sql_timeout",
            ),
            FactPart(
                path="rag",
                intent="official_meeting_documents",
                topic=None,
                sub_question=QUESTION,
                canonical_facts="Retrieved 1 source passage for context.",
                rag_chunks=["Official Boston City Council minutes discuss Dorchester bus access."],
                rag_metadata=[{"source_type": "official"}],
            ),
        ],
    )
    assert answer_synthesizer._fallback_answer(bundle) == "Retrieved 1 source passage for context."
    blocks = answer_synthesizer._blocks_from_bundle(bundle)
    assert len(blocks) == 1
    assert blocks[0]["intent"] == "official_meeting_documents"


def test_generic_meeting_transcript_behavior_remains_available() -> None:
    route = intent_router.route_with_rules("What do meeting transcripts say about displacement?")
    assert route is not None
    assert route["intent"] == "transcript"
    assert route.get("evidence_domain") is None

    hearing_route = intent_router.route_with_rules("Summarize the hearing transcript about housing.")
    assert hearing_route is not None
    assert hearing_route["intent"] == "transcript"
    assert hearing_route.get("evidence_domain") is None


def test_event_route_is_unchanged() -> None:
    route = intent_router.route_with_rules("What events are happening this weekend?")
    assert route is not None
    assert route["mode"] == "sql"
    assert route["intent"] == "events"


def test_configured_vector_paths_are_honored(monkeypatch, tmp_path: Path) -> None:
    gemini_path = tmp_path / "gemini"
    bge_path = tmp_path / "bge"
    captured = {}
    monkeypatch.setattr(config, "VECTORDB_DIR", gemini_path)
    monkeypatch.setattr(config, "VECTORDB_BGE_DIR", bge_path)
    monkeypatch.setattr(
        unified_chatbot.retrieval,
        "configure_vectordb_paths",
        lambda **kwargs: captured.update(kwargs),
    )
    unified_chatbot._fix_retrieval_vectordb_path()
    assert captured == {"gemini_dir": gemini_path, "bge_dir": bge_path}
