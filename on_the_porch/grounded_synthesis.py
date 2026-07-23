"""Optional one-shot grounded synthesis with claim/citation validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from answering_kernel import EvidenceBundle, GlobalDeadline
from civic_evidence import CivicEvidenceBundle, CivicToolResult


SynthesisGenerate = Callable[[str, float], str]
MAX_SYNTHESIS_TIMEOUT_SECONDS = 4.0
_INTERNAL_TERMS = re.compile(
    r"\b(?:aggregate_crime|search_documents|mv_crime|chroma|tool_id|sql|vector store)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b")
_ISO_DATE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
_ACCORDING_TO = re.compile(
    r"\b(?:according to|per)\s+((?:the\s+)?[A-Za-z][A-Za-z0-9 &'-]{1,60})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SynthesisResult:
    answer: Optional[str]
    valid: bool
    issues: Tuple[str, ...]
    synthesis_call_count: int


def synthesize_grounded_answer(
    *,
    resident_goal: str,
    trusted_state: Mapping[str, Any],
    tool_results: Sequence[CivicToolResult],
    fallback_answer: str,
    generate: Optional[SynthesisGenerate],
    deadline: GlobalDeadline,
    evidence_bundle: Optional[EvidenceBundle] = None,
) -> SynthesisResult:
    """Run at most one synthesis call; invalid output falls back deterministically."""
    if generate is None or not tool_results:
        return SynthesisResult(fallback_answer, True, (), 0)
    if deadline.application_remaining(reserve_for_persistence=True) < 1.0:
        return SynthesisResult(fallback_answer, True, ("deadline_skip",), 0)
    prompt = build_synthesis_prompt(
        resident_goal=resident_goal,
        trusted_state=trusted_state,
        tool_results=tool_results,
    )
    timeout = min(
        MAX_SYNTHESIS_TIMEOUT_SECONDS,
        deadline.application_remaining(reserve_for_persistence=True),
    )
    try:
        raw = generate(prompt, timeout)
    except Exception:
        return SynthesisResult(fallback_answer, False, ("provider_failure",), 1)
    answer = " ".join(str(raw or "").split()).strip()
    ok, issues = validate_synthesis_against_evidence(
        answer,
        tool_results,
        evidence_bundle=evidence_bundle,
    )
    if not ok or not answer:
        return SynthesisResult(fallback_answer, False, issues, 1)
    return SynthesisResult(answer, True, (), 1)


def build_synthesis_prompt(
    *,
    resident_goal: str,
    trusted_state: Mapping[str, Any],
    tool_results: Sequence[CivicToolResult],
) -> str:
    bundle = CivicEvidenceBundle(results=tuple(tool_results[:4]))
    payload = {
        "resident_goal": resident_goal[:500],
        "trusted_state": {
            key: trusted_state.get(key)
            for key in ("default_geography", "active_topic", "pending_clarification")
            if key in trusted_state
        },
        "evidence": bundle.bounded_for_model(),
        "instructions": [
            "Answer the resident question first using only the evidence bundle.",
            "Distinguish official measured facts, 311 requests, meeting statements, journalism, and community reports.",
            "State period and geography when relevant.",
            "Disclose missing or stale coverage.",
            "Cite only source_id or citation_reference values present in the evidence.",
            "Do not invent dates, counts, organizations, or policies.",
            "Do not expose internal table or tool names.",
            "Do not give legal advice or definitive zoning conclusions beyond the sources.",
        ],
    }
    return (
        "Grounded civic synthesis. Return plain resident-facing prose only.\n"
        + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )


_PLANNER_ENVELOPE = re.compile(
    r'^\s*\{\s*"action"\s*:\s*"(?:respond_directly|ask_clarification|gather_evidence)"',
    re.IGNORECASE,
)


def validate_synthesis_against_evidence(
    answer: str,
    tool_results: Sequence[CivicToolResult],
    *,
    evidence_bundle: Optional[EvidenceBundle] = None,
) -> Tuple[bool, Tuple[str, ...]]:
    text = str(answer or "").strip()
    issues = []
    if not text:
        issues.append("empty_answer")
        return False, tuple(issues)
    # Planner function-call JSON must never surface as resident prose.
    if _PLANNER_ENVELOPE.search(text) or (
        text.startswith("{")
        and '"arguments"' in text
        and any(
            key in text
            for key in ('"response_text"', '"tasks"', '"response_kind"')
        )
    ):
        issues.append("protocol_envelope")
        return False, tuple(issues)
    if _INTERNAL_TERMS.search(text):
        issues.append("internal_term")
    allowed_citations = set()
    allowed_numbers = set()
    allowed_dates = set()
    allowed_orgs = set()
    for result in tool_results:
        for source in result.source_records:
            allowed_citations.add(source.approved_citation_reference.casefold())
            allowed_citations.add(source.internal_source_id.casefold())
            allowed_orgs.add(source.resident_source_name.casefold())
            if source.publication_or_data_date:
                allowed_dates.add(str(source.publication_or_data_date))
        for atom in result.evidence_atoms:
            allowed_citations.add(atom.evidence_id.casefold())
            allowed_citations.add(atom.citation_reference.casefold())
            _collect_literals(atom.value, allowed_numbers, allowed_dates, allowed_orgs)
        _collect_literals(result.typed_facts, allowed_numbers, allowed_dates, allowed_orgs)
        for record in result.records:
            _collect_literals(record, allowed_numbers, allowed_dates, allowed_orgs)
    if evidence_bundle is not None:
        _collect_literals(evidence_bundle.facts, allowed_numbers, allowed_dates, allowed_orgs)
        for source in evidence_bundle.source_records:
            if isinstance(source, Mapping):
                for key in ("source", "label", "path", "name"):
                    value = source.get(key)
                    if isinstance(value, str) and value.strip():
                        allowed_citations.add(value.casefold())
                        allowed_orgs.add(value.casefold())
                if source.get("date"):
                    allowed_dates.add(str(source["date"]))

    for match in _NUMBER.findall(text):
        normalized = match.replace(",", "")
        if normalized not in allowed_numbers and match not in allowed_numbers:
            # Allow small structural numbers like list indexes 1-8.
            try:
                if float(normalized) <= 8:
                    continue
            except ValueError:
                pass
            issues.append(f"unsupported_number:{match}")
    for match in _ISO_DATE.findall(text):
        if match not in allowed_dates:
            issues.append(f"unsupported_date:{match}")

    citation_mentions = re.findall(
        r"\b(?:source|citation)\s*[:=]?\s*([A-Za-z0-9][A-Za-z0-9_.:/?=&%#-]{0,120})",
        text,
        flags=re.IGNORECASE,
    )
    for mention in citation_mentions:
        if mention.casefold() not in allowed_citations:
            issues.append(f"invalid_citation:{mention}")

    # If the answer asserts a named organization not present in evidence, reject.
    for org in allowed_orgs:
        pass
    invented_org = _ACCORDING_TO.search(text)
    if invented_org:
        name = invented_org.group(1).strip().casefold()
        name = re.sub(r"^the\s+", "", name)
        if name not in allowed_orgs and not any(name in item or item in name for item in allowed_orgs):
            issues.append(f"unsupported_organization:{invented_org.group(1)}")

    return (not issues), tuple(issues[:12])


def _collect_literals(
    value: Any,
    numbers: set[str],
    dates: set[str],
    orgs: set[str],
) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        numbers.add(str(int(value)) if float(value).is_integer() else str(value))
        numbers.add(f"{int(value):,}" if float(value).is_integer() else str(value))
        return
    if isinstance(value, str):
        for match in _NUMBER.findall(value):
            numbers.add(match)
            numbers.add(match.replace(",", ""))
        for match in _ISO_DATE.findall(value):
            dates.add(match)
        if len(value.split()) <= 6:
            orgs.add(value.casefold())
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _collect_literals(item, numbers, dates, orgs)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_literals(item, numbers, dates, orgs)


__all__ = [
    "SynthesisResult",
    "build_synthesis_prompt",
    "synthesize_grounded_answer",
    "validate_synthesis_against_evidence",
]
