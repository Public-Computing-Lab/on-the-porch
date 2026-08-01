"""Rule-first intent router (Iteration 2).

Maps civic questions to sql / rag / hybrid plans without an LLM call when
confidence is high. Ambiguous questions fall through to the slim LLM router in
unified_chatbot._route_question_llm().
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from query_plan import build_query_plan

_DEFAULT_PLAN: Dict[str, Any] = {
    "transcript_tags": None,
    "policy_sources": None,
    "folder_categories": None,
    "k": 5,
}

_EVENT_HINTS = (
    "event", "events", "happening", "going on", "what's on", "whats on",
    "calendar", "schedule", "activities", "activity", "things to do",
    "workshop", "workshops", "meeting", "meetings",
)
_SCHEDULE_HINTS = (
    "today", "tomorrow", "this week", "next week", "weekend",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)
_CRIME_HINTS = (
    "crime", "crimes", "arrest", "arrests", "offense", "offenses",
    "homicide", "homicides", "shooting", "shootings", "shots fired",
    "safety incident", "violence", "violent", "police report",
)
_311_HINTS = ("311", "service request", "service requests", "city complaint")
_311_ISSUE_HINTS = (
    "pothole", "potholes", "complaint", "complaints", "trash", "graffiti",
    "rodent", "rodents", "rats", "rat ", " rat", "snow", "ice", "plow",
    "streetlight", "street light", "missed pickup", "illegal dumping", "dumping",
)
_STATS_HINTS = (
    "how many", "count", "number of", "total", "statistics", "stats",
    "trend", "trends", "rate", "compare", "comparison", "breakdown",
    "per month", "per week", "over time",
)
_OPINION_HINTS = (
    "what do people", "what do residents", "how do people", "how do residents",
    "what are people saying", "community feel", "community thinks",
    "perspective", "opinion", "opinions", "sentiment", "concerned about",
    "what concerns", "concerns do", "residents raise", "residents say",
    "safety concern", "safety concerns", "main concern", "main concerns",
    "main safety", "public safety",
)
_POLICY_HINTS = (
    "policy", "policies", "plan", "plans", "ordinance", "zoning",
    "imagine boston", "anti-displacement", "slow streets", "budget",
)
_RSS_NEWS_HINTS = (
    "news", "recent news", "latest news", "news about", "news from",
    "updates from", "recent updates", "what's going on in", "whats going on in",
    "what's happening in", "whats happening in", "what is happening in",
    "lately in", "dorchester reporter", "dotnews", "dot news",
)
_RSS_SOURCE_ALIASES = (
    "dorchester reporter",
    "dot reporter",
    "dotnews",
    "dot news",
    "csndc",
    "codman",
    "codman square neighborhood development corporation",
    "codman square library",
    "bpl codman square",
    "codman square health center",
    "codman.org",
    "codman square neighborhood council",
    "codman council",
    "south dorchester",
)

# Public alias for unified_chatbot and other consumers.
RSS_SOURCE_ALIASES = _RSS_SOURCE_ALIASES


def is_router_v2_enabled() -> bool:
    return os.getenv("INTENT_ROUTER_V2_ENABLED", "true").strip().lower() in (
        "1", "true", "yes",
    )


def rule_confidence_threshold() -> float:
    try:
        return float(os.getenv("ROUTER_RULE_CONFIDENCE_THRESHOLD", "0.75"))
    except ValueError:
        return 0.75


def infer_parse_failure_mode(question: str) -> str:
    """Default mode when LLM routing JSON fails — prefer rag over hybrid."""
    q = (question or "").lower()
    if any(h in q for h in _EVENT_HINTS + _311_HINTS + _CRIME_HINTS + _STATS_HINTS):
        if any(h in q for h in _OPINION_HINTS):
            return "hybrid"
        return "sql"
    if "safety" in q and any(h in q for h in ("concern", "concerns", "issue", "issues", "problem")):
        return "hybrid"
    if any(h in q for h in _POLICY_HINTS + _OPINION_HINTS + _RSS_NEWS_HINTS):
        return "rag"
    return "rag"


def _base_plan(
    *,
    mode: str,
    intent: str,
    confidence: float,
    k: int = 5,
    transcript_tags: Optional[List[str]] = None,
    policy_sources: Optional[List[str]] = None,
    folder_categories: Optional[List[str]] = None,
    evidence_domain: Optional[str] = None,
    required_concepts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    plan = {
        **_DEFAULT_PLAN,
        "mode": mode,
        "intent": intent,
        "confidence": confidence,
        "router": "rules",
        "k": k,
    }
    if transcript_tags is not None:
        plan["transcript_tags"] = transcript_tags
    if policy_sources is not None:
        plan["policy_sources"] = policy_sources
    if folder_categories is not None:
        plan["folder_categories"] = folder_categories
    if evidence_domain is not None:
        plan["evidence_domain"] = evidence_domain
    if required_concepts is not None:
        plan["required_concepts"] = required_concepts
    return plan


def _q(question: str) -> str:
    return (question or "").lower().strip()


def _mentions_stats(q: str) -> bool:
    return any(h in q for h in _STATS_HINTS)


def _mentions_opinion(q: str) -> bool:
    return any(h in q for h in _OPINION_HINTS)


def _looks_like_safety_overview(q: str) -> bool:
    """Broad safety questions — recent crime stats plus community/transcript context."""
    if "safety" not in q and "safe" not in q:
        return False
    return any(
        h in q
        for h in (
            "concern", "concerns", "issue", "issues", "problem", "problems",
            "how safe", "is it safe", "main concern", "biggest concern",
            "public safety", "safe neighborhood", "safe neighbourhood",
            "neighborhood", "neighbourhood",
        )
    )


def _mentions_rss_source(q: str) -> bool:
    if "event" in q:
        return False
    return any(alias in q for alias in _RSS_SOURCE_ALIASES)


def _looks_like_rss_news(q: str) -> bool:
    if _mentions_rss_source(q):
        return True
    if any(h in q for h in _RSS_NEWS_HINTS):
        return not any(h in q for h in _EVENT_HINTS + _SCHEDULE_HINTS)
    return False


def _looks_like_neighborhood_pulse(q: str) -> bool:
    """'What's going on in Dorchester lately?' — news + optional 311 context."""
    pulse = (
        "what's going on", "whats going on", "what is going on",
        "what's happening", "whats happening", "what is happening",
        "lately", "recently in", "updates in",
    )
    if not (any(p in q for p in pulse) and "dorchester" in q):
        return False
    # Calendar-specific windows → events SQL, not open-ended neighborhood pulse.
    if any(h in q for h in _SCHEDULE_HINTS + ("weekend",)):
        return False
    return True


def _looks_like_transcript_query(q: str) -> bool:
    if "transcript" in q:
        return True
    return "meeting" in q and any(
        h in q for h in ("say", "said", "residents", "displacement", "community", "discuss")
    )


def _looks_like_official_meeting_document_query(q: str) -> bool:
    """Recognize explicit requests for official meeting records."""
    council_record = any(
        phrase in q
        for phrase in (
            "city council minutes",
            "council meeting minutes",
            "city council meeting minutes",
            "council transcript",
            "city council transcript",
        )
    )
    return council_record or (
        "city council" in q and any(term in q for term in ("minutes", "transcript", "hearing"))
    )


def _is_911_call_question(q: str) -> bool:
    """True for 911 dispatch/call volume questions (we have no 911 call dataset)."""
    try:
        from sql_chat.prepared_sql import classify_911_question  # noqa: WPS433

        return classify_911_question(q) == "dispatch_unavailable"
    except Exception:
        if "911" not in q:
            return False
        if any(h in q for h in ("police report", "crime report", "incident report", "bpd")):
            return False
        return any(h in q for h in ("call", "calls", "dispatch", "emergency"))


def _is_911_crime_question(q: str) -> bool:
    """Colloquial 911 language that should route to BPD crime stats."""
    try:
        from sql_chat.prepared_sql import classify_911_question  # noqa: WPS433

        return classify_911_question(q) == "crime_redirect"
    except Exception:
        return False


def _is_911_advisory_question(q: str) -> bool:
    try:
        from sql_chat.prepared_sql import classify_911_question  # noqa: WPS433

        return classify_911_question(q) == "advisory"
    except Exception:
        return False


def match_rules(question: str) -> Optional[Dict[str, Any]]:
    """Return a routing plan when rules match with sufficient confidence."""
    q = _q(question)
    if not q:
        return None

    if _is_911_call_question(q):
        return _base_plan(mode="sql", intent="911_unavailable", confidence=0.95, k=5)

    if _is_911_advisory_question(q):
        return _base_plan(mode="sql", intent="911_advisory", confidence=0.95, k=5)

    if _is_911_crime_question(q):
        return _base_plan(mode="sql", intent="crime_stats", confidence=0.92, k=5)

    # 311 stats → sql (before events — "this week" appears in both)
    if any(h in q for h in _311_HINTS) or any(h in q for h in _311_ISSUE_HINTS):
        if _mentions_opinion(q) and _mentions_stats(q):
            return _base_plan(mode="hybrid", intent="311_hybrid", confidence=0.85, k=5)
        return _base_plan(mode="sql", intent="311_stats", confidence=0.92, k=5)

    # Crime / safety — factual BPD lookups → SQL; neighborhood-safety judgment → hybrid
    try:
        from sql_chat.prepared_sql import crime_routing_mode  # noqa: WPS433

        crime_mode = crime_routing_mode(question)
        if crime_mode == "hybrid":
            return _base_plan(
                mode="hybrid",
                intent="crime_safety_context",
                confidence=0.92,
                k=8,
                transcript_tags=["safety", "violence", "community"],
            )
        if crime_mode == "sql":
            return _base_plan(
                mode="sql",
                intent="crime_stats",
                confidence=0.95,
                k=5,
            )
    except Exception:
        pass

    # Crime routing fallback (before generic schedule hints)
    if any(h in q for h in _CRIME_HINTS):
        if _mentions_stats(q) and not _mentions_opinion(q):
            return _base_plan(mode="sql", intent="crime_stats", confidence=0.92, k=5)
        if _mentions_opinion(q) and not _mentions_stats(q):
            return _base_plan(
                mode="hybrid",
                intent="crime_opinion",
                confidence=0.88,
                k=5,
                transcript_tags=["safety", "violence"],
            )
        if _mentions_stats(q) and _mentions_opinion(q):
            return _base_plan(
                mode="hybrid",
                intent="crime_hybrid",
                confidence=0.9,
                k=5,
                transcript_tags=["safety", "violence"],
            )
        return _base_plan(
            mode="hybrid",
            intent="crime_general",
            confidence=0.8,
            k=5,
            transcript_tags=["safety"],
        )

    # Explicit official meeting records → RAG before generic meeting/event rules.
    if _looks_like_official_meeting_document_query(q):
        required_concepts = ["dorchester"] if "dorchester" in q else []
        if any(term in q for term in ("transportation", "transit", "traffic", "mobility")):
            required_concepts.append("transportation")
        return _base_plan(
            mode="rag",
            intent="official_meeting_documents",
            confidence=0.98,
            k=8,
            evidence_domain="official_council_minutes",
            required_concepts=required_concepts,
        )

    # Meeting transcripts / community voice → rag (before events — "meeting" is an event hint)
    if _looks_like_transcript_query(q):
        return _base_plan(
            mode="rag",
            intent="transcript",
            confidence=0.9,
            k=5,
            transcript_tags=["community", "displacement", "housing"],
        )

    # Neighborhood pulse → hybrid (before event "going on" heuristics)
    if _looks_like_neighborhood_pulse(q):
        return _base_plan(
            mode="hybrid",
            intent="neighborhood_pulse",
            confidence=0.88,
            k=5,
            folder_categories=["newsletters"],
        )

    # Events / calendar → sql
    if any(h in q for h in _EVENT_HINTS):
        return _base_plan(mode="sql", intent="events", confidence=0.95, k=5)
    if any(h in q for h in _SCHEDULE_HINTS) and any(
        h in q
        for h in ("happening", "going on", "what's on", "whats on", "on saturday", "on sunday")
    ):
        return _base_plan(mode="sql", intent="events", confidence=0.88, k=5)

    # Named RSS source → rag
    if _mentions_rss_source(q):
        return _base_plan(
            mode="rag",
            intent="news_source",
            confidence=0.9,
            k=5,
            folder_categories=["newsletters"],
        )

    # General news → rag
    if _looks_like_rss_news(q):
        return _base_plan(
            mode="rag",
            intent="news_rss",
            confidence=0.85,
            k=5,
            folder_categories=["newsletters"],
        )

    # Safety overview (before policy RAG — avoids stale plan documents)
    if _looks_like_safety_overview(q):
        return _base_plan(
            mode="hybrid",
            intent="safety_overview",
            confidence=0.9,
            k=5,
            transcript_tags=["safety", "violence", "community"],
        )

    # Policy documents → rag
    if any(h in q for h in _POLICY_HINTS):
        return _base_plan(mode="rag", intent="policy", confidence=0.88, k=5)

    # Resident opinion on safety/crime → hybrid (stats + community voice)
    if _mentions_opinion(q) and any(h in q for h in _CRIME_HINTS + ("safety",)):
        return _base_plan(
            mode="hybrid",
            intent="opinion_safety",
            confidence=0.88,
            k=5,
            transcript_tags=["safety", "violence", "community"],
        )

    # Pure opinion / community voice → rag
    if _mentions_opinion(q):
        return _base_plan(
            mode="rag",
            intent="opinion",
            confidence=0.85,
            k=5,
            transcript_tags=["community"],
        )

    return None


def route_with_rules(question: str) -> Optional[Dict[str, Any]]:
    """Return a plan if rule confidence meets the configured threshold."""
    plan = match_rules(question)
    if plan is None:
        return None
    if plan.get("confidence", 0) < rule_confidence_threshold():
        return None
    mode = str(plan.get("mode", "sql"))
    plan["query_plan"] = build_query_plan(question, mode=mode).to_dict()
    return plan


_COMMUNITY_NOTE_HINTS = (
    "community note", "community notes", "admin knowledge",
)


def detect_civic_topic(question: str) -> Optional[str]:
    """Broad topic label for follow-up routing and cache mismatch checks."""
    q = _q(question)
    if not q:
        return None

    if any(h in q for h in _COMMUNITY_NOTE_HINTS):
        return "community_note"

    if _mentions_opinion(q) and not any(h in q for h in _STATS_HINTS):
        if _looks_like_transcript_query(q):
            return "transcript"
        return "opinion"

    if any(h in q for h in _311_HINTS + _311_ISSUE_HINTS):
        return "311"

    try:
        from sql_chat.prepared_sql import classify_911_question  # noqa: WPS433

        kind = classify_911_question(question)
        if kind == "crime_redirect":
            return "crime"
        if kind in {"dispatch_unavailable", "advisory"}:
            return "911"
    except Exception:
        pass

    if _looks_like_safety_overview(q):
        return "safety"

    try:
        from sql_chat.prepared_sql import crime_routing_mode  # noqa: WPS433

        if crime_routing_mode(question) == "sql":
            return "crime"
        if crime_routing_mode(question) == "hybrid":
            return "safety"
    except Exception:
        pass

    if any(h in q for h in _CRIME_HINTS):
        return "crime"

    if any(h in q for h in _EVENT_HINTS):
        return "events"
    if any(h in q for h in _SCHEDULE_HINTS) and any(
        h in q for h in ("happening", "going on", "what's on", "whats on", "event", "on")
    ):
        return "events"

    if _looks_like_transcript_query(q):
        return "transcript"

    if any(h in q for h in _POLICY_HINTS):
        return "policy"

    if _looks_like_rss_news(q) or _mentions_rss_source(q):
        return "news"

    return None


def detect_cache_topic(cache: Optional[Dict[str, Any]]) -> Optional[str]:
    """Infer the dominant topic of cached retrieval data."""
    if not cache or not cache.get("mode"):
        return None

    mode = str(cache.get("mode") or "").lower()
    prior_topic = detect_civic_topic(str(cache.get("question") or ""))
    if mode in {"hybrid", "rag", "history"} and prior_topic:
        return prior_topic

    sql = str(cache.get("sql_query") or "").lower()
    if "service_requests_311" in sql or "mv_311" in sql:
        return "311"
    if "crime_incident" in sql or "mv_crime" in sql:
        return "crime"
    if "weekly_events" in sql or "events_materialized" in sql:
        return "events"

    meta = cache.get("rag_metadata") or []
    if isinstance(meta, list):
        doc_types = {
            str(m.get("doc_type") or "")
            for m in meta
            if isinstance(m, dict) and m.get("doc_type")
        }
        if "policy" in doc_types:
            return "policy"
        if "transcript" in doc_types:
            return "transcript"
        if "community_note" in doc_types:
            return "community_note"
        if doc_types & {"calendar_event", "boston_gov_answer", "client_upload"}:
            return "news"

    if prior_topic:
        return prior_topic
    return None


def topics_conflict(question: str, cache: Optional[Dict[str, Any]]) -> bool:
    """True when the new question is clearly about a different civic topic than the cache."""
    q_topic = detect_civic_topic(question)
    c_topic = detect_cache_topic(cache)
    if not q_topic or not c_topic:
        return False
    return q_topic != c_topic
