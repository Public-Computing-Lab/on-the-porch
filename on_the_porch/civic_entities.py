"""Canonical civic entity profiles for grounded organization explanations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from civic_evidence import CivicSourceRecord, CivicSourceType


PROFILES_PATH = Path(__file__).resolve().parent / "civic_entity_profiles.json"


@dataclass(frozen=True)
class CivicEntityProfile:
    stable_id: str
    display_name: str
    aliases: Tuple[str, ...]
    entity_kind: str
    role_summary: str
    resident_relevance: str
    official_url: Optional[str]
    source_authority: str
    verification_status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stable_id": self.stable_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "entity_kind": self.entity_kind,
            "role_summary": self.role_summary,
            "resident_relevance": self.resident_relevance,
            "official_url": self.official_url,
            "source_authority": self.source_authority,
            "verification_status": self.verification_status,
        }


@dataclass(frozen=True)
class EntityResolution:
    status: str
    profile: Optional[CivicEntityProfile] = None
    candidates: Tuple[CivicEntityProfile, ...] = ()
    answer: str = ""
    sources: Tuple[Mapping[str, Any], ...] = ()


def resolve_entity(query: Any) -> EntityResolution:
    text = _normalize(query)
    if not text:
        return EntityResolution(
            status="MISSING",
            answer="Which organization or agency did you mean?",
        )
    match = re.match(
        r"^(?:what|who) is\s+(.+?)(?:\?|$)|tell me about\s+(.+?)(?:\?|$)",
        text,
    )
    if match:
        text = _normalize(match.group(1) or match.group(2) or text)
    profiles = load_entity_profiles()
    exact = [
        item
        for item in profiles
        if text == _normalize(item.display_name) or text in item.aliases
    ]
    if len(exact) == 1:
        return explain_entity(exact[0])
    if len(exact) > 1:
        return _ambiguous(exact)
    partial = [
        item for item in profiles
        if text in _normalize(item.display_name)
        or any(text in alias or alias in text for alias in item.aliases)
    ]
    if len(partial) == 1:
        return explain_entity(partial[0])
    if len(partial) > 1:
        return _ambiguous(partial)
    return EntityResolution(
        status="NOT_FOUND",
        answer=(
            "I don't have a verified organization profile for that name yet. "
            "If you share the full agency or utility name, I can try again."
        ),
    )


def explain_entity(profile: CivicEntityProfile) -> EntityResolution:
    parts = [
        f"{profile.display_name} is a {profile.entity_kind.replace('_', ' ')}. "
        f"{profile.role_summary}",
        profile.resident_relevance,
    ]
    if profile.official_url:
        parts.append(f"Official source: {profile.official_url}")
    parts.append(
        "This explanation comes from a verified stored profile, not from an unsupported model guess."
    )
    source = {
        "source": profile.display_name,
        "label": profile.display_name,
        "path": profile.official_url,
        "type": "entity_profile",
        "source_type": CivicSourceType.OFFICIAL_OPERATIONAL_DATA.value,
        "authority": profile.source_authority,
    }
    return EntityResolution(
        status="RESOLVED",
        profile=profile,
        answer=" ".join(parts),
        sources=(source,),
    )


def entity_source_record(profile: CivicEntityProfile) -> CivicSourceRecord:
    citation = profile.official_url or f"entity:{profile.stable_id}"
    return CivicSourceRecord(
        internal_source_id=f"entity.{profile.stable_id}",
        resident_source_name=profile.display_name,
        source_type=CivicSourceType.OFFICIAL_OPERATIONAL_DATA,
        authority=profile.source_authority,
        publication_or_data_date=None,
        geography={"name": "Boston / Dorchester"},
        approved_citation_reference=citation,
    )


@lru_cache(maxsize=1)
def load_entity_profiles() -> Tuple[CivicEntityProfile, ...]:
    payload = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    profiles: List[CivicEntityProfile] = []
    for raw in payload.get("profiles") or []:
        if not isinstance(raw, Mapping):
            continue
        aliases = tuple(
            _normalize(item)
            for item in (raw.get("aliases") or [])
            if isinstance(item, str) and item.strip()
        )
        profiles.append(CivicEntityProfile(
            stable_id=str(raw["stable_id"]),
            display_name=str(raw["display_name"]),
            aliases=aliases,
            entity_kind=str(raw.get("entity_kind") or "organization"),
            role_summary=str(raw.get("role_summary") or "").strip(),
            resident_relevance=str(raw.get("resident_relevance") or "").strip(),
            official_url=(
                str(raw["official_url"]).strip()
                if isinstance(raw.get("official_url"), str) and raw.get("official_url")
                else None
            ),
            source_authority=str(raw.get("source_authority") or "verified profile"),
            verification_status=str(raw.get("verification_status") or "verified_profile"),
        ))
    return tuple(profiles)


def looks_like_entity_question(message: str, subject: Optional[str] = None) -> bool:
    text = _normalize(message)
    if re.match(r"^(what|who) is\b", text) or text.startswith("tell me about "):
        return True
    if subject and resolve_entity(subject).status in {"RESOLVED", "AMBIGUOUS"}:
        return True
    return False


def _ambiguous(candidates: Sequence[CivicEntityProfile]) -> EntityResolution:
    names = ", ".join(item.display_name for item in candidates[:5])
    return EntityResolution(
        status="AMBIGUOUS",
        candidates=tuple(candidates[:5]),
        answer=f"I found more than one matching organization: {names}. Which one did you mean?",
    )


def _normalize(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split()).strip()


__all__ = [
    "CivicEntityProfile",
    "EntityResolution",
    "entity_source_record",
    "explain_entity",
    "load_entity_profiles",
    "looks_like_entity_question",
    "resolve_entity",
]
