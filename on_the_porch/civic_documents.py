"""Deterministic document / transcript / news evidence for model-led turns."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from answering_kernel import EvidenceBundle, EvidenceStatus, GlobalDeadline
from civic_evidence import (
    CivicEvidenceAtom,
    CivicSourceRecord,
    CivicSourceType,
    CivicToolResult,
    ToolResultStatus,
)
from source_provenance import (
    CORPUS_TO_SOURCE_IDS,
    is_resident_eligible,
    normalize_provenance,
    resident_label_for,
)


RetrieveFn = Callable[..., Mapping[str, Any]]
EnabledSourceLookup = Callable[[], Set[str]]
MAX_PASSAGE_CHARS = 900
MAX_RESULTS = 8

_CORPUS_META = {
    "POLICY": {
        "doc_type": "policy",
        "source_type": CivicSourceType.PLANNING_POLICY,
        "label": "Policy and planning references",
        "authority": "Curated policy references (verify official City/BPDA/ISD publications for binding rules)",
    },
    "TRANSCRIPT": {
        "doc_type": "transcript",
        "source_type": CivicSourceType.MEETING_DISCUSSION,
        "label": "Meeting transcripts",
        "authority": "Community / public meeting discussion",
    },
    "NEWS": {
        "doc_type": "news",
        "source_type": CivicSourceType.JOURNALISM,
        "label": "Local news",
        "authority": "Journalism / local news feeds",
    },
    "COMMUNITY": {
        "doc_type": "community",
        "source_type": CivicSourceType.COMMUNITY_OPINION,
        "label": "Community documents",
        "authority": "Neighborhood / community organizations",
    },
}


@dataclass(frozen=True)
class DocumentPlan:
    query: str
    corpora: Tuple[str, ...]
    location_id: Optional[str]
    time_scope: Optional[Mapping[str, Any]]
    source_types: Tuple[str, ...]
    limit: int = 8

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": "SEARCH",
            "query": self.query,
            "corpora": list(self.corpora),
            "location_id": self.location_id,
            "time_scope": dict(self.time_scope) if self.time_scope else None,
            "source_types": list(self.source_types),
            "limit": self.limit,
            "tools": ["search_documents"],
        }


@dataclass
class DocumentResult:
    answer: str
    sources: Tuple[Mapping[str, Any], ...]
    evidence: EvidenceBundle
    civic_tool_result: CivicToolResult
    plan: Optional[DocumentPlan] = None
    synthesis_call_count: int = 0

    def to_agent_result(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "mode": "model_led_documents",
            "sources": [dict(item) for item in self.sources],
            "result": {
                "documents": {
                    "plan": self.plan.to_dict() if self.plan else None,
                    "passage_count": len(self.civic_tool_result.records),
                    "evidence_status": self.evidence.evidence_status.value,
                }
            },
            "retrieval_cache": {},
            "llm_calls": self.synthesis_call_count,
            "execution_degraded": self.evidence.evidence_status in {
                EvidenceStatus.DEPENDENCY_UNAVAILABLE,
                EvidenceStatus.INSUFFICIENT_EVIDENCE,
            },
        }


def build_compiled_document_plan(*, arguments: Mapping[str, Any]) -> DocumentPlan:
    query = str(arguments.get("query") or "Dorchester civic information").strip()[:500]
    corpora = tuple(
        str(item).upper()
        for item in (arguments.get("corpora") or ("POLICY", "TRANSCRIPT", "NEWS", "COMMUNITY"))
        if str(item).upper() in _CORPUS_META
    ) or ("POLICY", "TRANSCRIPT", "NEWS", "COMMUNITY")
    source_types = tuple(
        str(item)
        for item in (arguments.get("source_types") or ())
        if isinstance(item, str)
    )
    return DocumentPlan(
        query=query,
        corpora=corpora,
        location_id=str(arguments["location_id"]) if arguments.get("location_id") else None,
        time_scope=dict(arguments["time_scope"]) if isinstance(arguments.get("time_scope"), Mapping) else None,
        source_types=source_types,
        limit=max(1, min(int(arguments.get("limit") or 8), MAX_RESULTS)),
    )


def _enabled_source_ids_from_factory(connection_factory: Optional[Callable[[], Any]] = None) -> Set[str]:
    """Load enabled, indexing-eligible, resident-eligible source IDs from MySQL."""
    if connection_factory is None:
        return set()
    conn = None
    try:
        conn = connection_factory()
        return _enabled_ids_from_conn(conn)
    except Exception:
        return set()
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _enabled_ids_from_conn(conn: Any) -> Set[str]:
    from source_registry import list_sources

    enabled: Set[str] = set()
    for row in list_sources(conn, enabled_only=True):
        if not row.get("indexing_eligible"):
            continue
        provenance = row.get("provenance_class")
        if not is_resident_eligible(str(provenance) if provenance else "OFFICIAL_SECONDARY"):
            continue
        enabled.add(str(row["source_id"]))
    return enabled


def execute_document_plan(
    message: str,
    *,
    plan: DocumentPlan,
    deadline: GlobalDeadline,
    retrieve: Optional[RetrieveFn] = None,
    enabled_sources: Optional[EnabledSourceLookup] = None,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> DocumentResult:
    del message
    if deadline.expired(reserve_for_persistence=True):
        return _empty_result(plan, unavailable=True)
    retriever = retrieve or _default_retrieve
    if enabled_sources is not None:
        allowed_sources = enabled_sources()
    else:
        allowed_sources = _enabled_source_ids_from_factory(connection_factory)
    passages: List[Dict[str, Any]] = []
    seen = set()
    per_corpus = max(1, plan.limit // max(1, len(plan.corpora)))
    for corpus in plan.corpora:
        meta = _CORPUS_META[corpus]
        corpus_source_ids = set(CORPUS_TO_SOURCE_IDS.get(corpus, ()))
        if allowed_sources is not None and corpus_source_ids and allowed_sources.isdisjoint(corpus_source_ids):
            # Entire corpus family disabled / ineligible.
            continue
        try:
            raw = retriever(plan.query, k=per_corpus + 4, doc_type=meta["doc_type"])
        except Exception:
            continue
        chunks = list(raw.get("chunks") or [])
        metadata = list(raw.get("metadata") or [])
        for index, chunk in enumerate(chunks):
            text = " ".join(str(chunk or "").split())
            if len(text) < 40:
                continue
            item_meta = metadata[index] if index < len(metadata) and isinstance(metadata[index], Mapping) else {}
            source_id = str(item_meta.get("source_id") or "")
            provenance = normalize_provenance(item_meta.get("provenance_class") or item_meta.get("provenance"))
            if not is_resident_eligible(provenance):
                continue
            source_blob = " ".join(
                str(item_meta.get(key) or "")
                for key in ("source", "path", "title", "document_id")
            ).casefold()
            if any(
                marker in source_blob
                for marker in (
                    "analysis",
                    "digest",
                    "reporting_note",
                    "template",
                    "fixtures/",
                    "zoning_reference_overview",
                )
            ):
                # Defense in depth against stale fixture vectors lacking provenance metadata.
                continue
            if allowed_sources:
                if source_id and source_id not in allowed_sources:
                    continue
                if not source_id and corpus_source_ids and allowed_sources.isdisjoint(corpus_source_ids):
                    continue
            fingerprint = re.sub(r"[^a-z0-9]+", "", text[:180].casefold())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            publisher = str(
                item_meta.get("publisher")
                or item_meta.get("source")
                or item_meta.get("title")
                or meta["label"]
            )[:160]
            path = item_meta.get("path") or item_meta.get("url") or item_meta.get("feed_url")
            if isinstance(path, str) and not (
                path.startswith("http://") or path.startswith("https://") or path.startswith("/")
            ):
                path = None
            passage_id = "doc." + hashlib.sha256(
                f"{corpus}:{publisher}:{fingerprint}".encode("utf-8")
            ).hexdigest()[:12]
            passages.append({
                "passage_id": passage_id,
                "corpus": corpus,
                "source_id": source_id or None,
                "source_type": meta["source_type"].value,
                "provenance_class": provenance,
                "provenance_label": resident_label_for(provenance, meta["source_type"].value),
                "source_name": publisher,
                "publisher": publisher,
                "authority": str(item_meta.get("authority") or meta["authority"]),
                "date": item_meta.get("date") or item_meta.get("published_at"),
                "path": path,
                "passage": text[:MAX_PASSAGE_CHARS],
                "limitation": _corpus_limitation(corpus, provenance),
            })
            if len(passages) >= plan.limit:
                break
        if len(passages) >= plan.limit:
            break

    if not passages:
        return _empty_result(plan, unavailable=False)

    sources: List[CivicSourceRecord] = []
    atoms: List[CivicEvidenceAtom] = []
    resident_sources: List[Dict[str, Any]] = []
    for index, passage in enumerate(passages):
        source_id = f"doc_source_{index + 1}"
        citation = str(passage.get("path") or f"source:{passage['passage_id']}")
        if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_.:/?=&%#-]{0,511}$", citation):
            citation = f"source:{passage['passage_id']}"
        source = CivicSourceRecord(
            internal_source_id=source_id,
            resident_source_name=str(passage["source_name"]),
            source_type=CivicSourceType(passage["source_type"]),
            authority=str(passage["authority"]),
            publication_or_data_date=(
                str(passage["date"])[:32] if passage.get("date") else None
            ),
            geography={"location_id": plan.location_id} if plan.location_id else None,
            approved_citation_reference=citation,
        )
        sources.append(source)
        atoms.append(CivicEvidenceAtom(
            evidence_id=str(passage["passage_id"]),
            claim_type="bounded_passage",
            value={"text": passage["passage"], "corpus": passage["corpus"]},
            source_id=source_id,
            citation_reference=citation,
            observed_at=source.publication_or_data_date,
        ))
        resident_sources.append({
            "source": source.resident_source_name,
            "label": source.resident_source_name,
            "path": passage.get("path"),
            "type": "rag",
            "source_type": source.source_type.value,
            "provenance_class": passage["provenance_class"],
            "provenance_label": passage["provenance_label"],
            "publisher": passage.get("publisher"),
            "date": source.publication_or_data_date,
            "doc_type": passage["corpus"].lower(),
        })

    answer = _deterministic_summary(plan, passages)
    limitations = sorted({str(item.get("limitation")) for item in passages if item.get("limitation")})
    limitations.append("Passages are bounded excerpts; they are not a complete document dump.")
    bundle = EvidenceBundle(
        question_scope={"domain": "DOCUMENTS", "query": plan.query, "corpora": list(plan.corpora)},
        issue_family=None,
        geography={"location_id": plan.location_id} if plan.location_id else {},
        requested_period=dict(plan.time_scope or {}),
        executed_period=dict(plan.time_scope or {}),
        freshness={},
        coverage={"status": "PARTIAL", "passage_count": len(passages)},
        facts={"passage_count": len(passages), "corpora": list(plan.corpora)},
        limitations=limitations,
        source_records=resident_sources,
        evidence_status=EvidenceStatus.PARTIAL,
    )
    tool_result = CivicToolResult(
        request_id="documents_1",
        tool_id="search_documents",
        status=ToolResultStatus.PARTIAL,
        requested_scope=plan.to_dict(),
        executed_scope=plan.to_dict(),
        typed_facts={"passage_count": len(passages)},
        records=tuple(passages),
        evidence_atoms=tuple(atoms),
        source_records=tuple(sources),
        coverage={"status": "PARTIAL", "passage_count": len(passages)},
        limitations=tuple(limitations[:12]),
    )
    return DocumentResult(
        answer=answer,
        sources=tuple(resident_sources),
        evidence=bundle,
        civic_tool_result=tool_result,
        plan=plan,
    )


def _default_retrieve(query: str, *, k: int, doc_type: str) -> Mapping[str, Any]:
    from retrieval import retrieve  # type: ignore

    if doc_type == "news":
        try:
            from retrieval import retrieve_rss  # type: ignore

            return retrieve_rss(query, k=k)
        except Exception:
            return retrieve(query, k=k, doc_type="news")
    if doc_type == "community":
        return retrieve(query, k=k, doc_type="community")
    return retrieve(query, k=k, doc_type=doc_type)


def _corpus_limitation(corpus: str, provenance: str) -> str:
    base = {
        "POLICY": "Policy excerpts are general information, not legal advice.",
        "TRANSCRIPT": "Meeting statements are discussion, not automatically official decisions.",
        "NEWS": "Journalism reports claims and should not be treated as official measurements.",
        "COMMUNITY": "Community documents reflect local concerns, not official incidence rates.",
    }.get(corpus, "Source type distinctions must be preserved.")
    if provenance == "OFFICIAL_SECONDARY":
        return base + " This is a curated secondary reference, not primary City/BPDA/ISD legal text."
    if provenance == "COMMUNITY_PUBLISHED":
        return base + " Community-published material is not official city policy."
    return base


def _deterministic_summary(plan: DocumentPlan, passages: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"I found {len(passages)} bounded source passage(s) related to “{plan.query}”.",
        "Source types and provenance are kept distinct below:",
    ]
    for index, passage in enumerate(passages[:6], start=1):
        date = f" ({passage['date']})" if passage.get("date") else ""
        excerpt = str(passage["passage"])[:220]
        label = passage.get("provenance_label") or passage["source_type"]
        lines.append(
            f"{index}. [{label}] {passage['source_name']}{date}: {excerpt}"
        )
        if passage.get("limitation"):
            lines.append(f"   Note: {passage['limitation']}")
    lines.append(
        "These excerpts are evidence candidates only; they do not invent additional civic facts."
    )
    return "\n".join(lines)


def _empty_result(plan: DocumentPlan, *, unavailable: bool) -> DocumentResult:
    if unavailable:
        answer = "I couldn't search civic documents right now because the request ran out of time."
        status = EvidenceStatus.DEPENDENCY_UNAVAILABLE
        tool_status = ToolResultStatus.TEMPORARILY_UNAVAILABLE
        failure = ("TOOL_DEADLINE_EXCEEDED",)
    else:
        answer = (
            "I didn't find matching policy, meeting, news, or community passages for that question "
            "in the current Dorchester evidence index."
        )
        status = EvidenceStatus.NO_MATCHING_RECORDS
        tool_status = ToolResultStatus.ABSENT
        failure = ()
    bundle = EvidenceBundle(
        question_scope={"domain": "DOCUMENTS", "query": plan.query},
        issue_family=None,
        geography={"location_id": plan.location_id} if plan.location_id else {},
        requested_period=dict(plan.time_scope or {}),
        executed_period=dict(plan.time_scope or {}),
        freshness={},
        coverage={"status": "ABSENT"},
        facts={"passage_count": 0},
        limitations=["No matching bounded passages were available."],
        missing_evidence=["NO_MATCHING_PASSAGES"] if not unavailable else ["TOOL_DEADLINE_EXCEEDED"],
        source_records=[],
        evidence_status=status,
        partial_failures=list(failure),
    )
    tool_result = CivicToolResult(
        request_id="documents_1",
        tool_id="search_documents",
        status=tool_status,
        requested_scope=plan.to_dict(),
        executed_scope=plan.to_dict(),
        limitations=("No matching bounded passages were available.",),
        missing_evidence=("NO_MATCHING_PASSAGES",) if not unavailable else ("TOOL_DEADLINE_EXCEEDED",),
        partial_failures=("TOOL_DEADLINE_EXCEEDED",) if unavailable else (),
    )
    return DocumentResult(
        answer=answer,
        sources=(),
        evidence=bundle,
        civic_tool_result=tool_result,
        plan=plan,
    )


__all__ = [
    "DocumentPlan",
    "DocumentResult",
    "build_compiled_document_plan",
    "execute_document_plan",
]
