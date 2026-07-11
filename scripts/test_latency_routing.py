#!/usr/bin/env python3
"""Routing and retrieval scope tests for chat latency fixes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

import intent_router  # noqa: E402
from sql_chat import prepared_sql  # noqa: E402
from unified_chatbot import (  # noqa: E402
    _rag_retrieval_task_names,
    _looks_like_entity_news_query,
    _route_question,
)


ROUTING_CASES = [
    (
        "What's happening with shootings in Dorchester?",
        "hybrid",
        {"crime_general", "crime_hybrid", "crime_safety_context"},
    ),
    (
        "How many shootings were there last month and what concerns do residents raise?",
        "hybrid",
        {"crime_hybrid"},
    ),
    (
        "What do residents say about potholes?",
        "hybrid",
        {"311_opinion", "311_hybrid"},
    ),
    (
        "What are the most common 311 issue types last month?",
        "sql",
        {"311_stats"},
    ),
    (
        "What 311 issues are residents most concerned about?",
        "hybrid",
        {"311_opinion", "311_hybrid"},
    ),
    (
        "How many pothole complaints were submitted last month?",
        "sql",
        {"311_stats"},
    ),
    (
        "What are residents saying about potholes?",
        "hybrid",
        {"311_opinion", "311_hybrid"},
    ),
    (
        "Tell me about meetings in Dorchester",
        "rag",
        {"transcript"},
    ),
    (
        "What events are happening this weekend?",
        "sql",
        {"events"},
    ),
    (
        "How many 311 requests were filed last week?",
        "sql",
        {"311_stats"},
    ),
    (
        "Is Dorchester safe right now?",
        "hybrid",
        {"safety_overview", "crime_safety_context", "opinion_safety"},
    ),
    (
        "What are residents concerned about near Codman Square?",
        "rag",
        {"opinion"},
    ),
]


def test_eversource_routes_without_llm():
    q = "What's going on with Eversource"
    plan = intent_router.route_with_rules(q)
    assert plan is not None, "expected rule router match"
    assert plan["mode"] == "rag"
    assert plan["intent"] == "news_rss"
    assert plan.get("router") == "rules"
    assert "newsletters" in (plan.get("folder_categories") or [])


def test_entity_news_detection():
    assert _looks_like_entity_news_query("whats going on with eversource")
    assert not _looks_like_entity_news_query("what events are happening this weekend")


def test_news_query_scopes_to_rss_only():
    plan = {
        "intent": "news_rss",
        "k": 5,
        "folder_categories": ["newsletters"],
    }
    names = _rag_retrieval_task_names(
        "What's going on with Eversource",
        plan,
        transcript_first=False,
        folders=["newsletters"],
    )
    assert names == ["rss"]


def test_policy_query_scopes_to_policies_only():
    plan = {"intent": "policy", "k": 5}
    names = _rag_retrieval_task_names(
        "What does the anti-displacement plan say?",
        plan,
        transcript_first=False,
        folders=None,
    )
    assert names == ["policies"]


def test_requested_production_queries_route_correctly():
    for question, expected_mode, expected_intents in ROUTING_CASES:
        plan = _route_question(question)
        prepared = prepared_sql.match_prepared_sql(question)
        prepared_intent = prepared.intent if prepared is not None else None
        actual_mode = plan.get("mode")
        actual_intent = plan.get("intent")
        print(
            f"{question} -> mode={actual_mode} intent={actual_intent} "
            f"prepared={prepared_intent}"
        )
        assert actual_mode == expected_mode, (
            f"{question!r}: expected mode {expected_mode}, got {actual_mode}"
        )
        assert actual_intent in expected_intents, (
            f"{question!r}: expected intent in {sorted(expected_intents)}, "
            f"got {actual_intent}"
        )


if __name__ == "__main__":
    test_eversource_routes_without_llm()
    test_entity_news_detection()
    test_news_query_scopes_to_rss_only()
    test_policy_query_scopes_to_policies_only()
    test_requested_production_queries_route_correctly()
    print("All latency routing tests passed.")
