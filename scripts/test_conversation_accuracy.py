#!/usr/bin/env python3
"""Conversation accuracy: topic switches, history mode safety, and fact grounding."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

import intent_router  # noqa: E402
from unified_chatbot import (  # noqa: E402
    _check_if_needs_new_data,
    _fast_followup_needs_new_data,
    _history_mode_allowed,
    _is_self_contained_civic_lookup,
    resolve_effective_question,
)


def _crime_cache() -> dict:
    return {
        "mode": "sql",
        "question": "what crimes are most frequently reported in Dorchester?",
        "sql_query": "FROM `mv_crime_monthly`",
        "sql_result": {
            "rows": [
                {"offense_group": "THEFT", "cnt": 42},
                {"offense_group": "ASSAULT", "cnt": 18},
            ],
        },
        "answer": "Theft and assault lead the list.",
    }


def _311_count_cache() -> dict:
    return {
        "mode": "sql",
        "question": "How many 311 requests last month?",
        "sql_query": "FROM `service_requests_311`",
        "sql_result": {"rows": [{"cnt": 3183}]},
        "answer": "There were 3,183 requests.",
    }


def _policy_rag_cache() -> dict:
    return {
        "mode": "rag",
        "question": "What does the Boston Slow Streets plan say about Dorchester?",
        "rag_metadata": [{"doc_type": "policy", "source": "Boston Slow Streets Plan Analysis.txt"}],
        "rag_chunks": ["Neighborhood Slow Streets improves safety for walkers and bikers."],
        "answer": "The plan focuses on slow streets safety.",
    }


def _transcript_rag_cache() -> dict:
    return {
        "mode": "rag",
        "question": "What did residents say about safety at the community meeting?",
        "rag_metadata": [{"doc_type": "transcript", "source": "meeting.txt"}],
        "rag_chunks": ["Residents raised concerns about traffic calming."],
        "answer": "Traffic calming came up often.",
    }


# --- topic detection ---


def test_detect_topics():
    assert intent_router.detect_civic_topic("How many 311 requests last month?") == "311"
    assert intent_router.detect_civic_topic("what crimes are most common?") == "crime"
    assert intent_router.detect_civic_topic("What events are on this week?") == "events"
    assert intent_router.detect_civic_topic("What does the anti-displacement plan say?") == "policy"
    assert intent_router.detect_civic_topic(
        "What did residents say about safety at the community meeting?"
    ) == "transcript"
    assert intent_router.detect_civic_topic("latest Dorchester Reporter news") == "news"
    assert intent_router.detect_civic_topic("show community notes about summer programs") == "community_note"


def test_cache_topic_from_hybrid_question():
    cache = {
        "mode": "hybrid",
        "question": "Is Dorchester a safe neighbourhood?",
        "sql_query": "FROM `mv_crime_monthly`",
        "rag_metadata": [{"doc_type": "transcript"}],
    }
    assert intent_router.detect_cache_topic(cache) == "safety"


def test_topics_conflict_matrix():
    crime = _crime_cache()
    assert intent_router.topics_conflict("How many 311 requests last month?", crime)
    assert intent_router.topics_conflict("What events are happening this week?", crime)
    assert intent_router.topics_conflict(
        "What does the Boston Slow Streets plan say?", crime,
    )
    assert intent_router.topics_conflict(
        "What did residents say about safety in meetings?", crime,
    )
    assert not intent_router.topics_conflict("tell me more", crime)
    assert not intent_router.topics_conflict("can you explain that?", crime)


# --- fresh lookup vs history mode ---


def test_self_contained_always_fresh():
    assert _is_self_contained_civic_lookup("How many 311 requests were filed last month?")
    assert _is_self_contained_civic_lookup(
        "What does the Boston Anti-Displacement plan say about Dorchester?"
    )
    assert _is_self_contained_civic_lookup(
        "What are people saying about safety in community meetings?"
    )


def test_topic_switch_forces_fresh():
    result = _fast_followup_needs_new_data(
        "How many 311 requests were filed last month?",
        [{"role": "user", "content": "crime stats?"}],
        _crime_cache(),
    )
    assert result is not None and result["needs_new_data"] is True


def test_same_topic_referential_allows_cache():
    result = _fast_followup_needs_new_data(
        "tell me more",
        [{"role": "user", "content": "crime stats?"}],
        _crime_cache(),
    )
    assert result is not None and result["needs_new_data"] is False


def test_count_only_tell_me_more_needs_breakdown():
    cache = _311_count_cache()
    assert not _history_mode_allowed("tell me more about the 311 requests", cache)
    result = _fast_followup_needs_new_data(
        "tell me more about the 311 requests",
        [{"role": "user", "content": cache["question"]}],
        cache,
    )
    assert result is not None and result["needs_new_data"] is True


def test_policy_after_transcript_is_fresh():
    result = _fast_followup_needs_new_data(
        "What does the Boston Slow Streets plan say about Dorchester?",
        [{"role": "user", "content": "meeting transcripts?"}],
        _transcript_rag_cache(),
    )
    assert result is not None and result["needs_new_data"] is True


def test_transcript_followup_same_topic_can_use_cache():
    cache = _transcript_rag_cache()
    assert _history_mode_allowed("can you explain that?", cache)
    result = _fast_followup_needs_new_data(
        "can you explain that?",
        [{"role": "user", "content": cache["question"]}],
        cache,
    )
    assert result is not None and result["needs_new_data"] is False


def test_new_policy_question_not_rewritten_to_crime():
    q = "What does the Boston Slow Streets plan say about Dorchester?"
    rewritten, force = resolve_effective_question(
        q,
        [{"role": "user", "content": "what crimes are most common?"}],
        _crime_cache(),
    )
    assert rewritten == q
    assert force is False


def test_check_if_needs_new_data_blocks_unsafe_history():
    check = _check_if_needs_new_data(
        "How many shootings in Dorchester last month?",
        [{"role": "user", "content": "311 count?"}],
        _311_count_cache(),
    )
    assert check["needs_new_data"] is True


def test_ordinal_followup_on_events_allowed():
    cache = {
        "mode": "sql",
        "question": "events this week?",
        "sql_query": "FROM `weekly_events`",
        "sql_result": {
            "rows": [
                {"event_name": "Farmers Market", "start_date": "2026-06-05"},
                {"event_name": "Story Time", "start_date": "2026-06-06"},
            ],
        },
    }
    assert _history_mode_allowed("what about the second one?", cache)


def test_prepared_sql_routes_independently_of_prior_turn():
    from sql_chat.prepared_sql import match_prepared_sql  # noqa: WPS433

    cases = [
        ("How many 311 requests last month?", "311"),
        ("What crimes are most frequently reported?", "crime"),
        ("What events are happening this week?", "event"),
    ]
    for question, prefix in cases:
        hit = match_prepared_sql(question)
        assert hit is not None, question
        if prefix == "event":
            assert hit.intent.startswith("event"), hit.intent
        else:
            assert hit.intent.startswith(prefix), (question, hit.intent)


def main() -> int:
    tests = [
        test_detect_topics,
        test_cache_topic_from_hybrid_question,
        test_topics_conflict_matrix,
        test_self_contained_always_fresh,
        test_topic_switch_forces_fresh,
        test_same_topic_referential_allows_cache,
        test_count_only_tell_me_more_needs_breakdown,
        test_policy_after_transcript_is_fresh,
        test_transcript_followup_same_topic_can_use_cache,
        test_new_policy_question_not_rewritten_to_crime,
        test_check_if_needs_new_data_blocks_unsafe_history,
        test_ordinal_followup_on_events_allowed,
        test_prepared_sql_routes_independently_of_prior_turn,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
