"""Provider-neutral registry for model-selected civic capabilities.

The registry describes what a future orchestrator may request. It contains no
database, vector-store, transport, or provider code and does not execute tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


MAX_TOOL_REQUESTS = 4
MAX_DOCUMENT_QUERY_CHARS = 500
MAX_LOCATION_QUERY_CHARS = 160
_CANONICAL_ID = re.compile(r"^[a-z][a-z0-9_:-]{0,79}$")
_REQUEST_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_SQL = re.compile(
    r"(?:\bselect\b.{0,120}\bfrom\b|\binsert\s+into\b|\bupdate\b.{0,80}\bset\b|"
    r"\bdelete\s+from\b|\bdrop\s+(?:table|database)\b|\balter\s+table\b|"
    r"\bcreate\s+(?:table|database)\b|\bunion\s+select\b)",
    re.IGNORECASE | re.DOTALL,
)


class ToolContractError(ValueError):
    """Raised when model-selected tool metadata is not strictly valid."""


class CivicDomain(str, Enum):
    GENERAL = "GENERAL"
    LOCATION = "LOCATION"
    EVENTS = "EVENTS"
    BOSTON_311 = "BOSTON_311"
    CRIME_AND_SAFETY = "CRIME_AND_SAFETY"
    POLICY = "POLICY"
    MEETING_TRANSCRIPTS = "MEETING_TRANSCRIPTS"
    NEWS = "NEWS"
    COMMUNITY_DOCUMENTS = "COMMUNITY_DOCUMENTS"


class ToolId(str, Enum):
    RESOLVE_LOCATION = "resolve_location"
    QUERY_EVENTS = "query_events"
    AGGREGATE_311 = "aggregate_311"
    SEARCH_311_RECORDS = "search_311_records"
    AGGREGATE_CRIME = "aggregate_crime"
    SEARCH_DOCUMENTS = "search_documents"
    GET_SOURCE_STATUS = "get_source_status"


class SemanticPeriod(str, Enum):
    CURRENT_DAY = "CURRENT_DAY"
    CURRENT_WEEK = "CURRENT_WEEK"
    CURRENT_WEEKEND = "CURRENT_WEEKEND"
    NEXT_CALENDAR_WEEK = "NEXT_CALENDAR_WEEK"
    NEXT_WEEKEND = "NEXT_WEEKEND"
    PREVIOUS_CALENDAR_WEEK = "PREVIOUS_CALENDAR_WEEK"
    CURRENT_CALENDAR_MONTH = "CURRENT_CALENDAR_MONTH"
    PREVIOUS_CALENDAR_MONTH = "PREVIOUS_CALENDAR_MONTH"
    LAST_N_DAYS = "LAST_N_DAYS"
    EXPLICIT_DATE_RANGE = "EXPLICIT_DATE_RANGE"
    RETAIN_TRUSTED_PERIOD = "RETAIN_TRUSTED_PERIOD"


class DocumentCorpus(str, Enum):
    POLICY = "POLICY"
    TRANSCRIPT = "TRANSCRIPT"
    NEWS = "NEWS"
    COMMUNITY = "COMMUNITY"


class SourceTypeFilter(str, Enum):
    BINDING_LAW = "BINDING_LAW"
    PLANNING_POLICY = "PLANNING_POLICY"
    OFFICIAL_OPERATIONAL_DATA = "OFFICIAL_OPERATIONAL_DATA"
    MEETING_DISCUSSION = "MEETING_DISCUSSION"
    JOURNALISM = "JOURNALISM"
    COMMUNITY_OPINION = "COMMUNITY_OPINION"


@dataclass(frozen=True)
class SemanticTimeScope:
    period: SemanticPeriod
    n_days: Optional[int] = None
    start_date: Optional[str] = None
    end_date_exclusive: Optional[str] = None
    trusted_reference_token: Optional[str] = None

    @classmethod
    def from_mapping(
        cls,
        raw: Any,
        *,
        trusted_reference_tokens: Iterable[str] = (),
    ) -> "SemanticTimeScope":
        value = _mapping(raw, "semantic time scope")
        _exact_keys(
            value,
            required={"period"},
            optional={"n_days", "start_date", "end_date_exclusive", "trusted_reference_token"},
            label="semantic time scope",
        )
        try:
            period = SemanticPeriod(value["period"])
        except (TypeError, ValueError) as exc:
            raise ToolContractError("semantic period is not registered") from exc
        n_days = value.get("n_days")
        start = value.get("start_date")
        end = value.get("end_date_exclusive")
        token = value.get("trusted_reference_token")
        if period is SemanticPeriod.LAST_N_DAYS:
            if isinstance(n_days, bool) or not isinstance(n_days, int) or not 1 <= n_days <= 365:
                raise ToolContractError("LAST_N_DAYS requires n_days from 1 through 365")
            if any(item is not None for item in (start, end, token)):
                raise ToolContractError("LAST_N_DAYS contains incompatible fields")
        elif period is SemanticPeriod.EXPLICIT_DATE_RANGE:
            start_date = _iso_date(start, "start_date")
            end_date = _iso_date(end, "end_date_exclusive")
            if end_date <= start_date:
                raise ToolContractError("explicit date range must be non-empty")
            if n_days is not None or token is not None:
                raise ToolContractError("explicit date range contains incompatible fields")
        elif period is SemanticPeriod.RETAIN_TRUSTED_PERIOD:
            if not isinstance(token, str) or token not in set(trusted_reference_tokens):
                raise ToolContractError("trusted period token is unavailable")
            if any(item is not None for item in (n_days, start, end)):
                raise ToolContractError("trusted period contains incompatible fields")
        elif any(item is not None for item in (n_days, start, end, token)):
            raise ToolContractError("named semantic periods cannot contain calculated dates")
        return cls(period, n_days, start, end, token)

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in {
                "period": self.period.value,
                "n_days": self.n_days,
                "start_date": self.start_date,
                "end_date_exclusive": self.end_date_exclusive,
                "trusted_reference_token": self.trusted_reference_token,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: ToolId
    compatible_domains: Tuple[CivicDomain, ...]
    operations: Tuple[str, ...]
    description: str

    def prompt_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id.value,
            "domains": [item.value for item in self.compatible_domains],
            "operations": list(self.operations),
            "description": self.description,
            "argument_contract": _argument_contract(self.tool_id),
        }


TOOL_CATALOG: Dict[ToolId, ToolDefinition] = {
    ToolId.RESOLVE_LOCATION: ToolDefinition(
        ToolId.RESOLVE_LOCATION,
        (CivicDomain.LOCATION,),
        ("RESOLVE",),
        "Resolve a bounded resident place, address, or intersection to a canonical location.",
    ),
    ToolId.QUERY_EVENTS: ToolDefinition(
        ToolId.QUERY_EVENTS,
        (CivicDomain.EVENTS,),
        ("LIST", "DETAILS"),
        "List events or retrieve one trusted event by stable reference.",
    ),
    ToolId.AGGREGATE_311: ToolDefinition(
        ToolId.AGGREGATE_311,
        (CivicDomain.BOSTON_311,),
        ("COUNT", "GROUP", "COMPARE", "STATUS"),
        "Count, group, compare, or summarize statuses in official Boston 311 data.",
    ),
    ToolId.SEARCH_311_RECORDS: ToolDefinition(
        ToolId.SEARCH_311_RECORDS,
        (CivicDomain.BOSTON_311,),
        ("SEARCH",),
        "Return a bounded set of official Boston 311 records matching canonical filters.",
    ),
    ToolId.AGGREGATE_CRIME: ToolDefinition(
        ToolId.AGGREGATE_CRIME,
        (CivicDomain.CRIME_AND_SAFETY,),
        ("COUNT", "GROUP", "COMPARE"),
        "Count, group, or compare published Boston crime incident data.",
    ),
    ToolId.SEARCH_DOCUMENTS: ToolDefinition(
        ToolId.SEARCH_DOCUMENTS,
        (
            CivicDomain.POLICY,
            CivicDomain.MEETING_TRANSCRIPTS,
            CivicDomain.NEWS,
            CivicDomain.COMMUNITY_DOCUMENTS,
        ),
        ("SEARCH",),
        "Search bounded policy, transcript, journalism, or community-document corpora.",
    ),
    ToolId.GET_SOURCE_STATUS: ToolDefinition(
        ToolId.GET_SOURCE_STATUS,
        (
            CivicDomain.EVENTS,
            CivicDomain.BOSTON_311,
            CivicDomain.CRIME_AND_SAFETY,
            CivicDomain.POLICY,
            CivicDomain.MEETING_TRANSCRIPTS,
            CivicDomain.NEWS,
            CivicDomain.COMMUNITY_DOCUMENTS,
        ),
        ("STATUS",),
        "Read freshness and coverage metadata for registered civic sources.",
    ),
}


@dataclass(frozen=True)
class RegisteredToolRequest:
    request_id: str
    tool_id: ToolId
    arguments: Mapping[str, Any]

    @classmethod
    def from_mapping(
        cls,
        raw: Any,
        *,
        selected_domains: Iterable[CivicDomain],
        trusted_reference_tokens: Iterable[str] = (),
    ) -> "RegisteredToolRequest":
        value = _mapping(raw, "tool request")
        _exact_keys(
            value,
            required={"request_id", "tool_id", "arguments"},
            optional=set(),
            label="tool request",
        )
        request_id = value["request_id"]
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise ToolContractError("tool request_id is invalid")
        try:
            tool_id = ToolId(value["tool_id"])
        except (TypeError, ValueError) as exc:
            raise ToolContractError("tool is not registered") from exc
        domains = set(selected_domains)
        arguments = validate_tool_arguments(
            tool_id,
            value["arguments"],
            trusted_reference_tokens=trusted_reference_tokens,
        )
        validate_tool_domain_compatibility(tool_id, arguments, domains)
        return cls(request_id, tool_id, arguments)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tool_id": self.tool_id.value,
            "arguments": _json_copy(self.arguments),
        }


def validate_tool_arguments(
    tool_id: ToolId,
    raw: Any,
    *,
    trusted_reference_tokens: Iterable[str] = (),
) -> Dict[str, Any]:
    value = _mapping(raw, f"{tool_id.value} arguments")
    trusted = set(trusted_reference_tokens)
    if tool_id is ToolId.RESOLVE_LOCATION:
        return _validate_location_args(value)
    if tool_id is ToolId.QUERY_EVENTS:
        return _validate_event_args(value, trusted)
    if tool_id is ToolId.AGGREGATE_311:
        return _validate_311_aggregate_args(value, trusted)
    if tool_id is ToolId.SEARCH_311_RECORDS:
        return _validate_311_search_args(value, trusted)
    if tool_id is ToolId.AGGREGATE_CRIME:
        return _validate_crime_args(value, trusted)
    if tool_id is ToolId.SEARCH_DOCUMENTS:
        return _validate_document_args(value, trusted)
    if tool_id is ToolId.GET_SOURCE_STATUS:
        return _validate_source_status_args(value)
    raise ToolContractError("tool is not registered")


def catalog_for_prompt() -> Tuple[Dict[str, Any], ...]:
    return tuple(TOOL_CATALOG[item].prompt_dict() for item in ToolId)


def validate_tool_domain_compatibility(
    tool_id: ToolId,
    arguments: Mapping[str, Any],
    selected_domains: Iterable[CivicDomain],
) -> None:
    definition = TOOL_CATALOG[tool_id]
    domains = set(selected_domains)
    if not domains.intersection(definition.compatible_domains):
        raise ToolContractError("tool is incompatible with the selected domains")
    _validate_argument_domain_compatibility(tool_id, arguments, domains)


def _argument_contract(tool_id: ToolId) -> Dict[str, Any]:
    time_scope = {
        "period": "registered SemanticPeriod",
        "n_days": "1..365 only for LAST_N_DAYS",
        "start_date": "ISO date only for EXPLICIT_DATE_RANGE",
        "end_date_exclusive": "ISO date only for EXPLICIT_DATE_RANGE",
        "trusted_reference_token": "server token only for RETAIN_TRUSTED_PERIOD",
    }
    contracts: Dict[ToolId, Dict[str, Any]] = {
        ToolId.RESOLVE_LOCATION: {
            "required": {"operation": "RESOLVE", "query": "bounded resident place"},
            "optional": {"scope": ["NEIGHBORHOOD", "ADDRESS", "INTERSECTION"]},
        },
        ToolId.QUERY_EVENTS: {
            "required": {"operation": ["LIST", "DETAILS"]},
            "constraints": (
                "DETAILS accepts only entity_token; LIST requires location_id and time_scope "
                "and cannot contain entity_token"
            ),
            "optional": {
                "entity_token": "trusted token; required for DETAILS",
                "location_id": "canonical ID; required for LIST",
                "time_scope": {**time_scope, "required_for": "LIST"},
                "filters": {
                    "cost": ["ANY", "FREE", "PAID"],
                    "modality": ["ANY", "IN_PERSON", "VIRTUAL", "HYBRID"],
                    "registration": ["ANY", "REQUIRED", "NOT_REQUIRED"],
                },
                "limit": "1..20",
            },
        },
        ToolId.AGGREGATE_311: {
            "required": {
                "operation": ["COUNT", "GROUP", "COMPARE", "STATUS"],
                "issue_scope": ["ALL", "SPECIFIC"],
            },
            "optional": {
                "issue_family_id": "canonical ID; required for SPECIFIC",
                "location_id": "canonical ID; required",
                "time_scope": {**time_scope, "required_for": "all operations"},
                "group_by": ["CATEGORY", "STATUS", "STREET"],
                "comparison_time_scope": time_scope,
                "limit": "1..25",
            },
        },
        ToolId.SEARCH_311_RECORDS: {
            "required": {"operation": "SEARCH"},
            "optional": {
                "issue_family_id": "canonical ID",
                "location_id": "canonical ID; required",
                "time_scope": {**time_scope, "required_for": "SEARCH"},
                "status": ["ANY", "OPEN", "CLOSED"],
                "limit": "1..20",
            },
        },
        ToolId.AGGREGATE_CRIME: {
            "required": {"operation": ["COUNT", "GROUP", "COMPARE"]},
            "optional": {
                "offense_category_id": "canonical ID",
                "location_id": "canonical ID; required",
                "time_scope": {**time_scope, "required_for": "all operations"},
                "group_by": ["OFFENSE", "DATE", "AREA"],
                "comparison_time_scope": time_scope,
                "limit": "1..25",
            },
        },
        ToolId.SEARCH_DOCUMENTS: {
            "required": {
                "operation": "SEARCH",
                "query": "bounded semantic query",
                "corpora": ["POLICY", "TRANSCRIPT", "NEWS", "COMMUNITY"],
            },
            "optional": {
                "location_id": "canonical ID",
                "time_scope": time_scope,
                "source_types": [item.value for item in SourceTypeFilter],
                "limit": "1..12",
            },
        },
        ToolId.GET_SOURCE_STATUS: {
            "required": {
                "operation": "STATUS",
                "source_ids": [
                    "boston_311", "bpd_crime", "events", "policy_documents",
                    "meeting_transcripts", "news", "community_documents",
                ],
            },
            "optional": {},
        },
    }
    return contracts[tool_id]


def _validate_location_args(value: Mapping[str, Any]) -> Dict[str, Any]:
    _exact_keys(value, {"operation", "query"}, {"scope"}, "resolve_location arguments")
    _operation(value, ("RESOLVE",))
    query = _bounded_text(value["query"], MAX_LOCATION_QUERY_CHARS, "location query")
    _safe_free_text(query, "location query")
    scope = _optional_enum(value.get("scope"), {"NEIGHBORHOOD", "ADDRESS", "INTERSECTION"}, "location scope")
    return _clean({"operation": "RESOLVE", "query": query, "scope": scope})


def _validate_event_args(value: Mapping[str, Any], trusted: set[str]) -> Dict[str, Any]:
    _exact_keys(
        value,
        {"operation"},
        {"entity_token", "location_id", "time_scope", "filters", "limit"},
        "query_events arguments",
    )
    operation = _operation(value, ("LIST", "DETAILS"))
    token = _trusted_token(value.get("entity_token"), trusted, required=operation == "DETAILS")
    location = _optional_canonical_id(value.get("location_id"), "event location")
    time_scope = _optional_time_scope(value.get("time_scope"), trusted)
    filters = _event_filters(value.get("filters"))
    limit = _bounded_limit(value.get("limit", 10), 1, 20, "event limit")
    if operation == "DETAILS":
        if any(item is not None for item in (location, time_scope)) or filters or "limit" in value:
            raise ToolContractError("event details must use only the trusted entity")
        return {"operation": operation, "entity_token": token}
    if token is not None:
        raise ToolContractError("event listing cannot contain an entity token")
    if location is None or time_scope is None:
        raise ToolContractError("event listing requires canonical location and time scope")
    return _clean({
        "operation": operation,
        "entity_token": token,
        "location_id": location,
        "time_scope": time_scope,
        "filters": filters or None,
        "limit": limit,
    })


def _validate_311_aggregate_args(value: Mapping[str, Any], trusted: set[str]) -> Dict[str, Any]:
    _exact_keys(
        value,
        {"operation", "issue_scope"},
        {"issue_family_id", "location_id", "time_scope", "group_by", "comparison_time_scope", "limit"},
        "aggregate_311 arguments",
    )
    operation = _operation(value, ("COUNT", "GROUP", "COMPARE", "STATUS"))
    issue_scope = _required_enum(value.get("issue_scope"), {"ALL", "SPECIFIC"}, "311 issue scope")
    family = _optional_canonical_id(value.get("issue_family_id"), "311 issue family")
    if issue_scope == "SPECIFIC" and not family:
        raise ToolContractError("specific 311 scope requires issue_family_id")
    if issue_scope == "ALL" and family:
        raise ToolContractError("all-311 scope cannot contain issue_family_id")
    group_by = _optional_enum(value.get("group_by"), {"CATEGORY", "STATUS", "STREET"}, "311 grouping")
    comparison = _optional_time_scope(value.get("comparison_time_scope"), trusted)
    if operation == "GROUP" and group_by is None:
        raise ToolContractError("311 GROUP requires group_by")
    if operation == "COMPARE" and comparison is None:
        raise ToolContractError("311 COMPARE requires comparison_time_scope")
    if operation not in {"GROUP"} and group_by is not None:
        raise ToolContractError("group_by is only valid for 311 GROUP")
    if operation != "COMPARE" and comparison is not None:
        raise ToolContractError("comparison_time_scope is only valid for 311 COMPARE")
    location = _optional_canonical_id(value.get("location_id"), "311 location")
    time_scope = _optional_time_scope(value.get("time_scope"), trusted)
    if location is None or time_scope is None:
        raise ToolContractError("311 aggregate requires canonical location and time scope")
    return _clean({
        "operation": operation,
        "issue_scope": issue_scope,
        "issue_family_id": family,
        "location_id": location,
        "time_scope": time_scope,
        "group_by": group_by,
        "comparison_time_scope": comparison,
        "limit": _bounded_limit(value.get("limit", 10), 1, 25, "311 aggregate limit"),
    })


def _validate_311_search_args(value: Mapping[str, Any], trusted: set[str]) -> Dict[str, Any]:
    _exact_keys(
        value,
        {"operation"},
        {"issue_family_id", "location_id", "time_scope", "status", "limit"},
        "search_311_records arguments",
    )
    _operation(value, ("SEARCH",))
    location = _optional_canonical_id(value.get("location_id"), "311 location")
    time_scope = _optional_time_scope(value.get("time_scope"), trusted)
    if location is None or time_scope is None:
        raise ToolContractError("311 record search requires canonical location and time scope")
    return _clean({
        "operation": "SEARCH",
        "issue_family_id": _optional_canonical_id(value.get("issue_family_id"), "311 issue family"),
        "location_id": location,
        "time_scope": time_scope,
        "status": _optional_enum(value.get("status"), {"ANY", "OPEN", "CLOSED"}, "311 status"),
        "limit": _bounded_limit(value.get("limit", 8), 1, 20, "311 search limit"),
    })


def _validate_crime_args(value: Mapping[str, Any], trusted: set[str]) -> Dict[str, Any]:
    _exact_keys(
        value,
        {"operation"},
        {"offense_category_id", "location_id", "time_scope", "group_by", "comparison_time_scope", "limit"},
        "aggregate_crime arguments",
    )
    operation = _operation(value, ("COUNT", "GROUP", "COMPARE"))
    group_by = _optional_enum(value.get("group_by"), {"OFFENSE", "DATE", "AREA"}, "crime grouping")
    comparison = _optional_time_scope(value.get("comparison_time_scope"), trusted)
    if operation == "GROUP" and group_by is None:
        raise ToolContractError("crime GROUP requires group_by")
    if operation == "COMPARE" and comparison is None:
        raise ToolContractError("crime COMPARE requires comparison_time_scope")
    if operation != "GROUP" and group_by is not None:
        raise ToolContractError("group_by is only valid for crime GROUP")
    if operation != "COMPARE" and comparison is not None:
        raise ToolContractError("comparison_time_scope is only valid for crime COMPARE")
    location = _optional_canonical_id(value.get("location_id"), "crime location")
    time_scope = _optional_time_scope(value.get("time_scope"), trusted)
    if location is None or time_scope is None:
        raise ToolContractError("crime aggregate requires canonical location and time scope")
    return _clean({
        "operation": operation,
        "offense_category_id": _optional_canonical_id(value.get("offense_category_id"), "offense category"),
        "location_id": location,
        "time_scope": time_scope,
        "group_by": group_by,
        "comparison_time_scope": comparison,
        "limit": _bounded_limit(value.get("limit", 10), 1, 25, "crime aggregate limit"),
    })


def _validate_document_args(value: Mapping[str, Any], trusted: set[str]) -> Dict[str, Any]:
    _exact_keys(
        value,
        {"operation", "query", "corpora"},
        {"location_id", "time_scope", "source_types", "limit"},
        "search_documents arguments",
    )
    _operation(value, ("SEARCH",))
    query = _bounded_text(value["query"], MAX_DOCUMENT_QUERY_CHARS, "document query")
    _safe_free_text(query, "document query")
    corpora = _enum_list(value["corpora"], DocumentCorpus, 1, 4, "document corpora")
    source_types = _enum_list(
        value.get("source_types", []), SourceTypeFilter, 0, 6, "document source types",
    )
    return _clean({
        "operation": "SEARCH",
        "query": query,
        "corpora": [item.value for item in corpora],
        "location_id": _optional_canonical_id(value.get("location_id"), "document location"),
        "time_scope": _optional_time_scope(value.get("time_scope"), trusted),
        "source_types": [item.value for item in source_types],
        "limit": _bounded_limit(value.get("limit", 8), 1, 12, "document limit"),
    })


def _validate_source_status_args(value: Mapping[str, Any]) -> Dict[str, Any]:
    _exact_keys(value, {"operation", "source_ids"}, set(), "get_source_status arguments")
    _operation(value, ("STATUS",))
    allowed = {
        "boston_311", "bpd_crime", "events", "policy_documents",
        "meeting_transcripts", "news", "community_documents",
    }
    raw_ids = value["source_ids"]
    if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 7:
        raise ToolContractError("source_ids must contain one through seven values")
    source_ids = []
    for item in raw_ids:
        if item not in allowed:
            raise ToolContractError("source status request contains an unknown source")
        if item not in source_ids:
            source_ids.append(item)
    return {"operation": "STATUS", "source_ids": source_ids}


_DOCUMENT_CORPUS_DOMAINS = {
    DocumentCorpus.POLICY.value: CivicDomain.POLICY,
    DocumentCorpus.TRANSCRIPT.value: CivicDomain.MEETING_TRANSCRIPTS,
    DocumentCorpus.NEWS.value: CivicDomain.NEWS,
    DocumentCorpus.COMMUNITY.value: CivicDomain.COMMUNITY_DOCUMENTS,
}

_SOURCE_ID_DOMAINS = {
    "boston_311": CivicDomain.BOSTON_311,
    "bpd_crime": CivicDomain.CRIME_AND_SAFETY,
    "events": CivicDomain.EVENTS,
    "policy_documents": CivicDomain.POLICY,
    "meeting_transcripts": CivicDomain.MEETING_TRANSCRIPTS,
    "news": CivicDomain.NEWS,
    "community_documents": CivicDomain.COMMUNITY_DOCUMENTS,
}


def _validate_argument_domain_compatibility(
    tool_id: ToolId,
    arguments: Mapping[str, Any],
    selected_domains: set[CivicDomain],
) -> None:
    if tool_id is ToolId.SEARCH_DOCUMENTS:
        required = {_DOCUMENT_CORPUS_DOMAINS[item] for item in arguments["corpora"]}
    elif tool_id is ToolId.GET_SOURCE_STATUS:
        required = {_SOURCE_ID_DOMAINS[item] for item in arguments["source_ids"]}
    else:
        return
    if not required.issubset(selected_domains):
        raise ToolContractError("tool arguments are incompatible with the selected domains")


def _event_filters(raw: Any) -> Dict[str, str]:
    if raw in (None, {}):
        return {}
    value = _mapping(raw, "event filters")
    _exact_keys(value, set(), {"cost", "modality", "registration"}, "event filters")
    return _clean({
        "cost": _optional_enum(value.get("cost"), {"ANY", "FREE", "PAID"}, "event cost"),
        "modality": _optional_enum(
            value.get("modality"), {"ANY", "IN_PERSON", "VIRTUAL", "HYBRID"}, "event modality",
        ),
        "registration": _optional_enum(
            value.get("registration"), {"ANY", "REQUIRED", "NOT_REQUIRED"}, "event registration",
        ),
    })


def _optional_time_scope(raw: Any, trusted: set[str]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    return SemanticTimeScope.from_mapping(raw, trusted_reference_tokens=trusted).to_dict()


def _operation(value: Mapping[str, Any], allowed: Tuple[str, ...]) -> str:
    operation = value.get("operation")
    if operation not in allowed:
        raise ToolContractError("tool operation is not registered")
    return str(operation)


def _trusted_token(raw: Any, trusted: set[str], *, required: bool = False) -> Optional[str]:
    if raw is None:
        if required:
            raise ToolContractError("trusted entity token is required")
        return None
    if not isinstance(raw, str) or raw not in trusted:
        raise ToolContractError("trusted reference token is unavailable")
    return raw


def _optional_canonical_id(raw: Any, label: str) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not _CANONICAL_ID.fullmatch(raw):
        raise ToolContractError(f"{label} is not a canonical ID")
    return raw


def _bounded_limit(raw: Any, minimum: int, maximum: int, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not minimum <= raw <= maximum:
        raise ToolContractError(f"{label} is outside its allowed range")
    return raw


def _optional_enum(raw: Any, allowed: set[str], label: str) -> Optional[str]:
    if raw is None:
        return None
    if raw not in allowed:
        raise ToolContractError(f"{label} is not registered")
    return str(raw)


def _required_enum(raw: Any, allowed: set[str], label: str) -> str:
    value = _optional_enum(raw, allowed, label)
    if value is None:
        raise ToolContractError(f"{label} is required")
    return value


def _enum_list(raw: Any, enum_type: type[Enum], minimum: int, maximum: int, label: str) -> Tuple[Enum, ...]:
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise ToolContractError(f"{label} has an invalid size")
    out = []
    for item in raw:
        try:
            parsed = enum_type(item)
        except (TypeError, ValueError) as exc:
            raise ToolContractError(f"{label} contains an unknown value") from exc
        if parsed not in out:
            out.append(parsed)
    return tuple(out)


def _bounded_text(raw: Any, maximum: int, label: str) -> str:
    if not isinstance(raw, str):
        raise ToolContractError(f"{label} must be text")
    value = " ".join(raw.split()).strip()
    if not value or len(value) > maximum:
        raise ToolContractError(f"{label} is empty or too long")
    return value


def _safe_free_text(value: str, label: str) -> None:
    if _URL.search(value):
        raise ToolContractError(f"{label} cannot contain a URL")
    if _SQL.search(value):
        raise ToolContractError(f"{label} cannot contain SQL")


def _iso_date(raw: Any, label: str) -> date:
    if not isinstance(raw, str):
        raise ToolContractError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ToolContractError(f"{label} must be an ISO date") from exc


def _mapping(raw: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ToolContractError(f"{label} must be an object")
    return raw


def _exact_keys(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise ToolContractError(f"{label} has invalid fields")


def _clean(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False))


__all__ = [
    "CivicDomain",
    "DocumentCorpus",
    "MAX_TOOL_REQUESTS",
    "RegisteredToolRequest",
    "SemanticPeriod",
    "SemanticTimeScope",
    "SourceTypeFilter",
    "TOOL_CATALOG",
    "ToolContractError",
    "ToolDefinition",
    "ToolId",
    "catalog_for_prompt",
    "validate_tool_domain_compatibility",
    "validate_tool_arguments",
]
