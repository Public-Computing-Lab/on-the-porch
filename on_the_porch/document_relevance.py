"""Deterministic topic/entity relevance gates for document evidence.

Nearest-neighbor retrieval alone is not answerability. This module extracts
canonical subject constraints from the resident query and admits only passages
that substantively support those constraints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


# Internal rejection codes — never shown to residents.
NO_RELEVANT_EVIDENCE = "NO_RELEVANT_EVIDENCE"
SOURCE_SCOPE_UNAVAILABLE = "SOURCE_SCOPE_UNAVAILABLE"
REQUESTED_PERIOD_UNAVAILABLE = "REQUESTED_PERIOD_UNAVAILABLE"
ONLY_WEAK_MATCHES = "ONLY_WEAK_MATCHES"

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "about",
    "tell", "me", "what", "is", "are", "was", "were", "how", "does", "do",
    "with", "from", "that", "this", "it", "you", "your", "please", "can",
})

_BUDGET_TERMS = (
    "budget", "fy", "fiscal year", "operating budget", "capital budget",
    "budget proposal", "budget ordinance", "appropriations", "mayoral budget",
    "mayor's budget", "mayors budget", "city budget",
)

_ZONING_TERMS = (
    "zoning", "zoning code", "zoning board", "setback", "lot coverage",
    "dimensional", "overlay", "use table",
)

_MEETING_TERMS = (
    "meeting", "transcript", "public comment", "agenda", "hearing",
    "discussion notes",
)

_HOUSING_POLICY_TERMS = (
    "housing policy", "affordable housing", "anti-displacement", "inclusionary",
    "development policy",
)
# Short tokens need word boundaries ("rent" must not match "currently").
_HOUSING_POLICY_TOKEN_RE = re.compile(r"\b(?:rent|rents|rental)\b")

_OFFICIAL_BUDGET_PUBLISHER_HINTS = (
    "city of boston", "mayor", "omb", "office of budget", "budget office",
    "appropriations", "city council budget",
)


@dataclass(frozen=True)
class DocumentConstraints:
    raw_query: str
    topics: Tuple[str, ...] = ()
    entities: Tuple[str, ...] = ()
    fiscal_years: Tuple[str, ...] = ()
    preferred_corpora: Tuple[str, ...] = ()
    require_official_budget: bool = False
    inventory_ask: bool = False
    meeting_about_budget: bool = False
    tokens: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "topics": list(self.topics),
            "entities": list(self.entities),
            "fiscal_years": list(self.fiscal_years),
            "preferred_corpora": list(self.preferred_corpora),
            "require_official_budget": self.require_official_budget,
            "inventory_ask": self.inventory_ask,
            "meeting_about_budget": self.meeting_about_budget,
            "tokens": list(self.tokens),
        }


@dataclass
class PassageRelevance:
    admitted: bool
    score: float
    reasons: List[str] = field(default_factory=list)
    strong_topic_match: bool = False
    year_match: bool = False


def extract_document_constraints(query: str) -> DocumentConstraints:
    raw = str(query or "").strip()
    lowered = raw.casefold()
    tokens = tuple(
        token for token in re.findall(r"[a-z0-9']+", lowered)
        if token not in _STOPWORDS and len(token) > 1
    )
    years = tuple(_extract_fiscal_years(lowered))
    topics: List[str] = []
    entities: List[str] = []
    preferred: List[str] = []
    require_official_budget = False
    inventory_ask = bool(
        re.search(
            r"\b(what|which)\b.*\b(budget|document|source)s?\b.*\b(have|available|cover|index)\b",
            lowered,
        )
        or re.search(r"\bbudget documents?\b.*\b(have|available)\b", lowered)
        or re.search(r"\bwhat budget documents\b", lowered)
    )
    meeting_about_budget = (
        any(term in lowered for term in ("meeting", "transcript", "hearing", "discussed"))
        and any(term in lowered for term in ("budget", "fy", "fiscal"))
    )

    if any(term in lowered for term in _BUDGET_TERMS) or "budget" in tokens:
        topics.append("budget")
    if re.search(r"\bmayor(?:'s|s)?\b", lowered) and "budget" in topics:
        topics.append("mayoral_budget")
        entities.append("city_of_boston_mayor")
        require_official_budget = True
        preferred.extend(["POLICY"])
    if any(term in lowered for term in ("operating budget", "capital budget", "budget proposal", "budget ordinance")):
        topics.append("official_budget_instrument")
        require_official_budget = True
        preferred.extend(["POLICY"])
    if any(term in lowered for term in _ZONING_TERMS):
        topics.append("zoning")
        preferred.extend(["POLICY"])
    if any(term in lowered for term in _HOUSING_POLICY_TERMS) or _HOUSING_POLICY_TOKEN_RE.search(lowered):
        topics.append("housing_policy")
        preferred.extend(["POLICY"])
    if any(term in lowered for term in _MEETING_TERMS) and "budget" not in topics:
        topics.append("meeting")
        preferred.extend(["TRANSCRIPT"])
    if "news" in lowered or "reported" in lowered:
        topics.append("news")
        preferred.extend(["NEWS"])
    if any(term in lowered for term in ("community", "residents said", "neighbors")):
        topics.append("community")
        preferred.extend(["COMMUNITY"])

    # Deduplicate preferred corpora while preserving order.
    seen = set()
    preferred_unique = []
    for item in preferred:
        if item not in seen:
            seen.add(item)
            preferred_unique.append(item)

    return DocumentConstraints(
        raw_query=raw,
        topics=tuple(dict.fromkeys(topics)),
        entities=tuple(dict.fromkeys(entities)),
        fiscal_years=years,
        preferred_corpora=tuple(preferred_unique),
        require_official_budget=require_official_budget,
        inventory_ask=inventory_ask,
        meeting_about_budget=meeting_about_budget,
        tokens=tokens,
    )


def _extract_fiscal_years(text: str) -> List[str]:
    years: List[str] = []
    for match in re.finditer(r"\bfy\s*[-/]?\s*((?:19|20)\d{2})\b", text):
        years.append(match.group(1))
    for match in re.finditer(r"\b((?:19|20)\d{2})\b", text):
        years.append(match.group(1))
    # Keep unique while preserving order.
    return list(dict.fromkeys(years))


def score_passage_relevance(
    passage: Mapping[str, Any],
    constraints: DocumentConstraints,
) -> PassageRelevance:
    blob = " ".join(
        str(passage.get(key) or "")
        for key in (
            "passage", "source_name", "publisher", "authority", "path",
            "title", "corpus", "provenance_class",
        )
    ).casefold()
    corpus = str(passage.get("corpus") or "").upper()
    reasons: List[str] = []
    score = 0.0

    # Generic lexical overlap with query tokens (weak signal).
    token_hits = sum(1 for token in constraints.tokens if token in blob)
    if constraints.tokens:
        overlap = token_hits / max(1, len(constraints.tokens))
        score += min(0.35, overlap)
        if overlap >= 0.25:
            reasons.append("token_overlap")

    strong_topic = False
    year_match = False

    if "budget" in constraints.topics:
        budget_hits = sum(1 for term in _BUDGET_TERMS if term in blob)
        if budget_hits:
            score += min(0.45, 0.15 * budget_hits)
            strong_topic = True
            reasons.append("budget_topic")
        else:
            reasons.append("missing_budget_topic")

        # Hard domain rejects for official mayoral/FY budget asks.
        if constraints.require_official_budget:
            if any(term in blob for term in ("zoning code", "zoning information", "zoning board")) and "budget" not in blob:
                return PassageRelevance(False, 0.0, ["unrelated_zoning_domain"], False, False)
            if any(term in blob for term in ("neighborhood development corporation", "civic association", "fields corner")) and "budget" not in blob:
                return PassageRelevance(False, 0.0, ["unrelated_org_profile"], False, False)
            if corpus in {"TRANSCRIPT", "COMMUNITY", "NEWS"} and not constraints.meeting_about_budget:
                # Official mayoral budget questions require official budget materials.
                if "budget" not in blob:
                    return PassageRelevance(False, 0.0, ["non_policy_corpus_without_budget"], False, False)
                # Mentions budget only in passing still insufficient without year/authority.
                score -= 0.2
                reasons.append("non_primary_budget_corpus")

            publisher_blob = " ".join(
                str(passage.get(key) or "")
                for key in ("publisher", "authority", "source_name")
            ).casefold()
            if any(hint in publisher_blob or hint in blob for hint in _OFFICIAL_BUDGET_PUBLISHER_HINTS):
                score += 0.25
                reasons.append("official_budget_publisher")

    if "zoning" in constraints.topics:
        if any(term in blob for term in _ZONING_TERMS):
            score += 0.4
            strong_topic = True
            reasons.append("zoning_topic")

    if "housing_policy" in constraints.topics:
        if any(term in blob for term in _HOUSING_POLICY_TERMS):
            score += 0.4
            strong_topic = True
            reasons.append("housing_policy_topic")

    if "meeting" in constraints.topics or constraints.meeting_about_budget:
        if corpus == "TRANSCRIPT" or any(term in blob for term in _MEETING_TERMS):
            score += 0.25
            reasons.append("meeting_corpus")
        if constraints.meeting_about_budget and "budget" in blob:
            strong_topic = True
            score += 0.3
            reasons.append("meeting_budget_topic")

    if constraints.fiscal_years:
        if any(year in blob or f"fy{year}" in blob or f"fy {year}" in blob for year in constraints.fiscal_years):
            year_match = True
            score += 0.35
            reasons.append("fiscal_year_match")
        else:
            reasons.append("fiscal_year_missing")
            if constraints.require_official_budget:
                # Year-specific official budget asks cannot use undated unrelated material.
                score -= 0.35

    if constraints.preferred_corpora and corpus in constraints.preferred_corpora:
        score += 0.1
        reasons.append("preferred_corpus")

    # Admission rules.
    admitted = score >= 0.45 and (strong_topic or not constraints.topics)
    if constraints.require_official_budget:
        admitted = bool(
            strong_topic
            and (year_match if constraints.fiscal_years else True)
            and score >= 0.55
        )
        if constraints.fiscal_years and not year_match:
            admitted = False
            reasons.append(REQUESTED_PERIOD_UNAVAILABLE)
        if not strong_topic:
            admitted = False
            reasons.append(NO_RELEVANT_EVIDENCE)

    if constraints.topics and not strong_topic and score < 0.6:
        admitted = False
        if ONLY_WEAK_MATCHES not in reasons:
            reasons.append(ONLY_WEAK_MATCHES)

    return PassageRelevance(
        admitted=admitted,
        score=round(score, 4),
        reasons=reasons,
        strong_topic_match=strong_topic,
        year_match=year_match,
    )


def filter_relevant_passages(
    passages: Sequence[Mapping[str, Any]],
    constraints: DocumentConstraints,
) -> Tuple[List[Dict[str, Any]], str]:
    """Return admitted passages and an internal rejection code when empty."""
    if constraints.inventory_ask and constraints.require_official_budget:
        # Inventory of mayoral budget coverage: do not dump unrelated nearest neighbors.
        return [], SOURCE_SCOPE_UNAVAILABLE
    if constraints.inventory_ask and "budget" in constraints.topics:
        return [], SOURCE_SCOPE_UNAVAILABLE

    scored: List[Tuple[float, Dict[str, Any], PassageRelevance]] = []
    saw_year_miss = False
    saw_weak = False
    for passage in passages:
        relevance = score_passage_relevance(passage, constraints)
        enriched = dict(passage)
        enriched["relevance_score"] = relevance.score
        enriched["relevance_reasons"] = list(relevance.reasons)
        if REQUESTED_PERIOD_UNAVAILABLE in relevance.reasons:
            saw_year_miss = True
        if ONLY_WEAK_MATCHES in relevance.reasons or not relevance.admitted:
            saw_weak = True
        if relevance.admitted:
            scored.append((relevance.score, enriched, relevance))

    scored.sort(key=lambda item: item[0], reverse=True)
    admitted = [item[1] for item in scored]
    if admitted:
        return admitted, ""

    if constraints.require_official_budget and constraints.fiscal_years and saw_year_miss:
        return [], REQUESTED_PERIOD_UNAVAILABLE
    if constraints.require_official_budget:
        return [], SOURCE_SCOPE_UNAVAILABLE
    if saw_weak:
        return [], ONLY_WEAK_MATCHES
    return [], NO_RELEVANT_EVIDENCE


def validate_answerable_bundle(
    passages: Sequence[Mapping[str, Any]],
    constraints: DocumentConstraints,
) -> Tuple[bool, str]:
    if constraints.inventory_ask:
        return True, ""
    if not passages:
        if constraints.require_official_budget and constraints.fiscal_years:
            return False, REQUESTED_PERIOD_UNAVAILABLE if constraints.fiscal_years else SOURCE_SCOPE_UNAVAILABLE
        return False, NO_RELEVANT_EVIDENCE
    # Require at least one strong topic match when constraints exist.
    if constraints.topics:
        strong = any(
            "budget_topic" in (passage.get("relevance_reasons") or [])
            or "zoning_topic" in (passage.get("relevance_reasons") or [])
            or "housing_policy_topic" in (passage.get("relevance_reasons") or [])
            or "meeting_budget_topic" in (passage.get("relevance_reasons") or [])
            for passage in passages
        )
        if not strong and constraints.require_official_budget:
            return False, NO_RELEVANT_EVIDENCE
    if constraints.require_official_budget and constraints.fiscal_years:
        if not any(passage.get("relevance_reasons") and "fiscal_year_match" in passage.get("relevance_reasons", []) for passage in passages):
            return False, REQUESTED_PERIOD_UNAVAILABLE
    return True, ""


def format_no_evidence_answer(constraints: DocumentConstraints, reason: str) -> str:
    if constraints.require_official_budget:
        if constraints.fiscal_years:
            year_bit = ", ".join(f"FY{year}" for year in constraints.fiscal_years)
            lead = (
                f"I don’t currently have an authoritative {year_bit} Boston mayoral budget "
                f"document in my available sources, so I can’t reliably summarize it."
            )
        else:
            lead = (
                "I don’t currently have an authoritative Boston mayoral budget document "
                "in my available sources, so I can’t reliably summarize it."
            )
        return (
            f"{lead} "
            f"I would need an official City of Boston / Mayor’s Office budget release, "
            f"budget proposal, or adopted budget ordinance"
            f"{' for that year' if constraints.fiscal_years else ''}. "
            f"You can ask what budget-related materials are currently available, or ask about "
            f"another policy topic that is in the index."
        )
    if constraints.inventory_ask and "budget" in constraints.topics:
        return (
            "I don’t currently have an ingested City of Boston mayoral operating-budget "
            "document set in the resident index. Capital-project snippets or unrelated civic "
            "documents are not a substitute for an official mayoral budget. "
            "If an official budget release is added to the approved sources, I can summarize it then."
        )
    if reason == REQUESTED_PERIOD_UNAVAILABLE:
        years = ", ".join(f"FY{year}" for year in constraints.fiscal_years) or "the requested period"
        return (
            f"I couldn’t find sources that cover {years} for that question in the current index, "
            f"so I can’t give a reliable period-specific summary."
        )
    if "zoning" in constraints.topics:
        return (
            "I couldn’t find a matching zoning reference for that question in the current evidence index."
        )
    if "meeting" in constraints.topics:
        return (
            "I couldn’t find a matching neighborhood meeting transcript for that question "
            "in the current evidence index."
        )
    return (
        "I don’t currently have enough relevant source material to answer that reliably. "
        "If you can name a document type or time period, I can check again."
    )


def format_resident_document_answer(
    plan_query: str,
    passages: Sequence[Mapping[str, Any]],
    constraints: DocumentConstraints,
) -> str:
    """Natural resident-facing answer without internal retrieval jargon."""
    del plan_query
    if not passages:
        return format_no_evidence_answer(constraints, NO_RELEVANT_EVIDENCE)

    lead_bits: List[str] = []
    if constraints.require_official_budget:
        year = constraints.fiscal_years[0] if constraints.fiscal_years else None
        lead_bits.append(
            "Here is what the available official or curated budget-related sources say"
            + (f" for FY{year}" if year else "")
            + "."
        )
    elif "zoning" in constraints.topics:
        lead_bits.append("Here is the zoning-related information available in the current sources.")
    elif "meeting" in constraints.topics or constraints.meeting_about_budget:
        lead_bits.append("Here is what the available meeting materials say.")
    else:
        lead_bits.append("Here is what the available sources say about that question.")

    lines = [" ".join(lead_bits)]
    for index, passage in enumerate(passages[:4], start=1):
        label = passage.get("provenance_label") or "Source"
        name = passage.get("source_name") or "Untitled source"
        date = f" ({passage['date']})" if passage.get("date") else ""
        excerpt = " ".join(str(passage.get("passage") or "").split())[:260]
        lines.append(f"{index}. {label} — {name}{date}: {excerpt}")
        if passage.get("limitation"):
            # Keep one short natural limitation, not internal scaffolding.
            note = str(passage["limitation"]).replace("OFFICIAL_SECONDARY", "secondary reference")
            lines.append(f"   Note: {note}")

    lines.append(
        "These are excerpts from the cited sources, not a complete document and not legal advice."
    )
    return "\n".join(lines)


def strip_internal_retrieval_language(text: str) -> str:
    """Defense in depth: remove leaked retrieval jargon from resident answers."""
    out = str(text or "")
    banned_phrases = (
        "bounded source passage",
        "evidence candidates",
        "vector search",
        "retrieval score",
        "NO_RELEVANT_EVIDENCE",
        "SOURCE_SCOPE_UNAVAILABLE",
        "REQUESTED_PERIOD_UNAVAILABLE",
        "ONLY_WEAK_MATCHES",
    )
    lowered = out.casefold()
    if any(term.casefold() in lowered for term in banned_phrases):
        return (
            "I don’t currently have enough relevant source material to answer that reliably "
            "from the available official documents."
        )
    # Soft-replace provenance enums if a model echoes them.
    replacements = (
        ("OFFICIAL_SECONDARY", "secondary curated reference"),
        ("COMMUNITY_PUBLISHED", "community-published source"),
        ("INTERNAL_ANALYSIS", "internal analysis"),
        ("DEMO_FIXTURE", "demo fixture"),
        ("TEST_ONLY", "test-only material"),
        ("OFFICIAL_PRIMARY", "official primary source"),
    )
    cleaned = out
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)
    return cleaned


__all__ = [
    "DocumentConstraints",
    "NO_RELEVANT_EVIDENCE",
    "ONLY_WEAK_MATCHES",
    "PassageRelevance",
    "REQUESTED_PERIOD_UNAVAILABLE",
    "SOURCE_SCOPE_UNAVAILABLE",
    "extract_document_constraints",
    "filter_relevant_passages",
    "format_no_evidence_answer",
    "format_resident_document_answer",
    "score_passage_relevance",
    "strip_internal_retrieval_language",
    "validate_answerable_bundle",
]
