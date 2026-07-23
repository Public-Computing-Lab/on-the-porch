"""Deterministic Boston crime / public-safety evidence for model-led turns."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from answering_kernel import EvidenceBundle, EvidenceStatus, GlobalDeadline
from civic_evidence import (
    CivicEvidenceAtom,
    CivicSourceRecord,
    CivicSourceType,
    CivicToolResult,
    ToolResultStatus,
)
from source_freshness import SOURCE_CRIME, classify_freshness_status, load_source_status


ConnectionFactory = Callable[[], Any]
_DORCHESTER_CRIME = "`district` = 'C11'"
_BPD_SOURCE_URL = "https://data.boston.gov/dataset/crime-incident-reports-august-2015-to-date-source-new-system"
MAX_GROUPS = 10
# Prefer offense_code_group when populated; fall back to offense_description.
# BPD "new system" exports often leave offense_code_group empty.
_OFFENSE_CATEGORY_SQL = (
    "COALESCE("
    "NULLIF(TRIM(`offense_code_group`), ''), "
    "NULLIF(TRIM(`offense_description`), '')"
    ")"
)
# Minimum share of incidents with a resolvable category label before leading
# categories are treated as a reliable overview.
_MIN_CATEGORY_COVERAGE = 0.40
_INTERNAL_UNKNOWN = "__UNMAPPED__"

# BPD incident reports include many non-criminal police responses. For broad
# public-safety overviews, lead with criminal/safety-relevant categories while
# still reporting the full incident-report total.
_NON_CRIME_OFFENSE_PATTERNS = (
    "sick assist",
    "investigate person",
    "investigate property",
    "towed motor vehicle",
    "m/v accident",
    "motor vehicle accident",
    "property - lost",
    "property - found",
    "missing person",
    "verbal dispute",
    "search warrant",
    "death investigation",
    "sick/injured",
    "landlord",
    "ballistics evidence",
    "medical assist",
    "service to other",
    "other investigation",
    "warrant arrest",
    "assist pd",
    "property recovered",
    "found property",
    "recovered property",
    "locate person",
    "fire report",
    "license plate",
    "val -",
    "val violation",
)

# Deterministic family mapping from cleaned source labels → resident-readable
# categories. First matching pattern wins; unmatched readable labels fall back
# to the cleaned source string (never a synthetic Unknown bucket).
_OFFENSE_FAMILY_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    # More specific patterns first.
    ("Motor vehicle leaving scene", ("leaving scene", "hit and run")),
    ("Auto theft", ("auto theft", "motor vehicle theft")),
    ("Assault", ("assault", "affray")),
    ("Robbery", ("robbery",)),
    ("Burglary", ("burglary", "b&e", "breaking and entering")),
    ("Larceny", ("larceny", "shoplifting", "stolen property")),
    ("Vandalism", ("vandalism", "graffiti")),
    ("Drugs", ("drug", "narcotic")),
    ("Weapons", ("firearm", "weapon", "ballistic", "gun")),
    ("Harassment / threats", ("harassment", "threat", "intimidation", "stalking")),
    ("Fraud", ("fraud", "forgery", "embezzlement", "counterfeit", "identity")),
    ("Sex offense", ("sex offense", "rape", "indecent")),
    ("Arson", ("arson",)),
    ("Homicide", ("homicide", "murder", "manslaughter")),
    ("Disorderly conduct", ("disorderly", "disturbing the peace", "noisy")),
    ("Trespassing", ("trespass",)),
    ("Kidnapping", ("kidnap",)),
    ("Operating under influence", ("operating under", "oui", "dui")),
)


@dataclass(frozen=True)
class CrimePlan:
    operation: str
    location_id: str
    period: Mapping[str, Any]
    comparison_period: Optional[Mapping[str, Any]] = None
    offense_category_id: Optional[str] = None
    group_by: Optional[str] = None
    limit: int = 10
    tools: Tuple[str, ...] = ("aggregate_crime",)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "location_id": self.location_id,
            "period": dict(self.period),
            "comparison_period": dict(self.comparison_period) if self.comparison_period else None,
            "offense_category_id": self.offense_category_id,
            "group_by": self.group_by,
            "limit": self.limit,
            "tools": list(self.tools),
        }


@dataclass
class CrimeResult:
    answer: str
    sources: Tuple[Mapping[str, Any], ...]
    evidence: EvidenceBundle
    civic_tool_result: CivicToolResult
    next_context: Optional[Mapping[str, Any]] = None
    update_context: bool = False
    plan: Optional[CrimePlan] = None
    synthesis_call_count: int = 0

    def to_agent_result(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "mode": "model_led_crime",
            "sources": [dict(item) for item in self.sources],
            "result": {
                "crime": {
                    "plan": self.plan.to_dict() if self.plan else None,
                    "facts": dict(self.evidence.facts),
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


def build_compiled_crime_plan(
    *,
    tool_id: str,
    arguments: Mapping[str, Any],
    resolved_period: Mapping[str, Any],
    comparison_period: Optional[Mapping[str, Any]] = None,
) -> CrimePlan:
    if tool_id != "aggregate_crime":
        raise ValueError("crime plan requires aggregate_crime")
    operation = str(arguments.get("operation") or "COUNT").upper()
    if operation not in {"COUNT", "GROUP", "COMPARE"}:
        raise ValueError("unsupported crime operation")
    location_id = str(arguments.get("location_id") or "dorchester")
    return CrimePlan(
        operation=operation,
        location_id=location_id,
        period=dict(resolved_period),
        comparison_period=dict(comparison_period) if comparison_period else None,
        offense_category_id=(
            str(arguments["offense_category_id"])
            if isinstance(arguments.get("offense_category_id"), str)
            else None
        ),
        group_by=str(arguments["group_by"]) if arguments.get("group_by") else None,
        limit=max(1, min(int(arguments.get("limit") or 10), 25)),
    )


def execute_crime_plan(
    message: str,
    *,
    plan: CrimePlan,
    connection_factory: ConnectionFactory,
    deadline: GlobalDeadline,
    now: Optional[datetime] = None,
) -> CrimeResult:
    del message
    if deadline.expired(reserve_for_persistence=True):
        return _dependency_result(plan, "TOOL_DEADLINE_EXCEEDED")
    freshness_map: Dict[str, Any] = {}
    connection = None
    groups: List[Dict[str, Any]] = []
    facts: Dict[str, Any] = {}
    try:
        status = load_source_status(connection_factory, SOURCE_CRIME)
        freshness_status, _age, _reasons = classify_freshness_status(status, now=now)
        if status is not None:
            freshness_map = {
                "status": freshness_status.value,
                "data_through_date": (
                    status.data_through_date.isoformat()
                    if status.data_through_date is not None
                    else None
                ),
            }
        connection = connection_factory()
        if plan.operation == "COMPARE":
            current_count = _count_incidents(connection, plan.period, plan.offense_category_id)
            prior = plan.comparison_period or _prior_equal_period(plan.period)
            prior_count = _count_incidents(connection, prior, plan.offense_category_id)
            facts = {
                "incident_report_count": current_count,
                "comparison_incident_report_count": prior_count,
                "period_label": _period_label(plan.period),
                "comparison_period_label": _period_label(prior),
            }
        elif plan.operation == "GROUP":
            current_count = _count_incidents(connection, plan.period, plan.offense_category_id)
            unmapped_count = _count_unmapped_incidents(connection, plan.period)
            # Fetch enough raw labels to cover a full month of Dorchester categories
            # (~tens of distinct offense_description values), not only the display limit.
            raw_groups = _group_incidents(connection, plan.period, limit=200)
            overview = _build_category_overview(
                raw_groups,
                total_count=current_count,
                unmapped_count=unmapped_count,
                limit=plan.limit,
            )
            groups = list(overview["leading_categories"])
            facts = {
                "incident_report_count": current_count,
                "top_offense_groups": groups,
                "period_label": _period_label(plan.period),
                "category_coverage": overview["category_coverage"],
                "categorized_incident_count": overview["categorized_incident_count"],
                "unmapped_incident_count": overview["unmapped_incident_count"],
                "category_coverage_reliable": overview["category_coverage_reliable"],
                "raw_offense_samples": overview.get("raw_offense_samples") or [],
            }
        else:
            current_count = _count_incidents(connection, plan.period, plan.offense_category_id)
            facts = {
                "incident_report_count": current_count,
                "period_label": _period_label(plan.period),
            }
    except Exception:
        return _dependency_result(plan, "SOURCE_RESPONSE_INVALID")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    answer = _compose_answer(plan, facts, freshness_map)
    source = CivicSourceRecord(
        internal_source_id="bpd_crime",
        resident_source_name="Boston Police Department incident reports",
        source_type=CivicSourceType.OFFICIAL_OPERATIONAL_DATA,
        authority="City of Boston / BPD Open Data",
        publication_or_data_date=freshness_map.get("data_through_date"),
        geography={"location_id": plan.location_id, "name": "Dorchester"},
        approved_citation_reference=_BPD_SOURCE_URL,
    )
    atoms = (
        CivicEvidenceAtom(
            evidence_id="crime.count",
            claim_type="incident_report_count",
            value=facts.get("incident_report_count"),
            source_id=source.internal_source_id,
            citation_reference=source.approved_citation_reference,
        ),
    )
    limitations = [
        "Counts are published BPD incident reports, not convictions or proven guilt.",
        "311 complaints are not included in these crime totals.",
    ]
    if plan.operation == "GROUP":
        limitations.append(
            "BPD incident reports include criminal offenses and non-criminal police responses."
        )
        unmapped = int(facts.get("unmapped_incident_count") or 0)
        total = int(facts.get("incident_report_count") or 0)
        if unmapped and total and unmapped < total:
            pct = round(100.0 * unmapped / total)
            limitations.append(
                f"About {pct}% of reports in this period lack a usable offense category label."
            )
        if not facts.get("category_coverage_reliable", True):
            limitations.append(
                "Offense-category coverage for this period is too incomplete for a reliable "
                "category overview."
            )
    evidence_status = EvidenceStatus.COMPLETE
    if str(freshness_map.get("status") or "").upper() in {"STALE", "DELAYED"}:
        evidence_status = EvidenceStatus.STALE_BUT_USABLE
        limitations.append("Crime source coverage may be incomplete for the newest days.")
    if plan.operation == "GROUP" and not facts.get("category_coverage_reliable", True):
        evidence_status = EvidenceStatus.INSUFFICIENT_EVIDENCE
    bundle = EvidenceBundle(
        question_scope={"domain": "CRIME", "operation": plan.operation},
        issue_family=plan.offense_category_id,
        geography={"location_id": plan.location_id, "name": "Dorchester"},
        requested_period=dict(plan.period),
        executed_period=dict(plan.period),
        freshness=dict(freshness_map),
        coverage={
            "status": (
                "INCOMPLETE"
                if plan.operation == "GROUP" and not facts.get("category_coverage_reliable", True)
                else "COVERED"
            ),
            "category_coverage": facts.get("category_coverage"),
            "unmapped_incident_count": facts.get("unmapped_incident_count"),
        },
        facts=dict(facts),
        limitations=list(limitations),
        source_records=[{
            "source": source.resident_source_name,
            "authority": source.authority,
            "path": source.approved_citation_reference,
            "type": "sql",
            "table": "crime_incident_reports",
            "source_type": source.source_type.value,
            "date": source.publication_or_data_date,
        }],
        evidence_status=evidence_status,
    )
    tool_result = CivicToolResult(
        request_id="crime_1",
        tool_id="aggregate_crime",
        status=(
            ToolResultStatus.STALE_BUT_USABLE
            if evidence_status is EvidenceStatus.STALE_BUT_USABLE
            else (
                ToolResultStatus.PARTIAL
                if evidence_status is EvidenceStatus.INSUFFICIENT_EVIDENCE
                else ToolResultStatus.COMPLETE
            )
        ),
        requested_scope=plan.to_dict(),
        executed_scope=plan.to_dict(),
        typed_facts=dict(facts),
        records=tuple(groups),
        evidence_atoms=atoms,
        source_records=(source,),
        freshness=dict(freshness_map),
        coverage=dict(bundle.coverage),
        limitations=tuple(limitations),
    )
    return CrimeResult(
        answer=answer,
        sources=tuple(bundle.source_records),
        evidence=bundle,
        civic_tool_result=tool_result,
        plan=plan,
    )


def _dependency_result(plan: CrimePlan, code: str) -> CrimeResult:
    answer = (
        "I couldn't load the official Boston crime incident reports right now. "
        "Please try again in a moment."
    )
    bundle = EvidenceBundle(
        question_scope={"domain": "CRIME", "operation": plan.operation},
        issue_family=None,
        geography={"location_id": plan.location_id},
        requested_period=dict(plan.period),
        executed_period=dict(plan.period),
        freshness={},
        coverage={"status": "UNAVAILABLE"},
        facts={},
        limitations=["Official crime data was temporarily unavailable."],
        missing_evidence=[code],
        source_records=[],
        evidence_status=EvidenceStatus.DEPENDENCY_UNAVAILABLE,
        partial_failures=[code],
    )
    source = CivicSourceRecord(
        internal_source_id="bpd_crime",
        resident_source_name="Boston Police Department incident reports",
        source_type=CivicSourceType.OFFICIAL_OPERATIONAL_DATA,
        authority="City of Boston / BPD Open Data",
        publication_or_data_date=None,
        geography={"location_id": plan.location_id},
        approved_citation_reference=_BPD_SOURCE_URL,
    )
    tool_result = CivicToolResult(
        request_id="crime_1",
        tool_id="aggregate_crime",
        status=ToolResultStatus.TEMPORARILY_UNAVAILABLE,
        requested_scope=plan.to_dict(),
        executed_scope=plan.to_dict(),
        source_records=(source,),
        limitations=("Official crime data was temporarily unavailable.",),
        missing_evidence=(code,),
        partial_failures=(code if code in {
            "SOURCE_TEMPORARILY_UNAVAILABLE",
            "SOURCE_TIMEOUT",
            "SOURCE_RESPONSE_INVALID",
            "TOOL_DEADLINE_EXCEEDED",
            "TOOL_RESULT_INCOMPLETE",
            "LEGACY_PARTIAL_FAILURE",
        } else "SOURCE_TEMPORARILY_UNAVAILABLE",),
    )
    return CrimeResult(
        answer=answer,
        sources=(),
        evidence=bundle,
        civic_tool_result=tool_result,
        plan=plan,
    )


def _period_label(period: Mapping[str, Any]) -> str:
    label = period.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    start = period.get("start_date")
    end = period.get("end_date_exclusive")
    if isinstance(start, str) and isinstance(end, str):
        try:
            end_inclusive = date.fromisoformat(end) - timedelta(days=1)
            return f"{start} to {end_inclusive.isoformat()}"
        except ValueError:
            pass
    return "the selected period"


def _period_bounds(period: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "start_date": period.get("start_date"),
        "end_date_exclusive": period.get("end_date_exclusive"),
        "label": _period_label(period),
    }


def _prior_equal_period(period: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        start = date.fromisoformat(str(period["start_date"]))
        end = date.fromisoformat(str(period["end_date_exclusive"]))
    except (KeyError, TypeError, ValueError):
        return dict(period)
    span = max(1, (end - start).days)
    prior_end = start
    prior_start = prior_end - timedelta(days=span)
    return {
        "start_date": prior_start.isoformat(),
        "end_date_exclusive": prior_end.isoformat(),
        "label": f"{prior_start.isoformat()} to {(prior_end - timedelta(days=1)).isoformat()}",
        "kind": period.get("kind") or "EXPLICIT_DATE_RANGE",
    }


def _validated_iso_date(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid crime {field_name}") from exc


def _period_params(period: Mapping[str, Any]) -> Tuple[str, List[Any]]:
    start = period.get("start_date")
    end = period.get("end_date_exclusive")
    if isinstance(start, str) and isinstance(end, str):
        return (
            "`occurred_on_date` >= %s AND `occurred_on_date` < %s",
            [
                _validated_iso_date(start, field_name="start_date"),
                _validated_iso_date(end, field_name="end_date_exclusive"),
            ],
        )
    return ("`occurred_on_date` >= DATE_SUB(CURDATE(), INTERVAL 31 DAY)", [])


def _offense_params(offense_category_id: Optional[str]) -> Tuple[str, List[Any]]:
    if not offense_category_id:
        return ("1=1", [])
    raw = str(offense_category_id).strip().casefold()
    if not re.fullmatch(r"[a-z0-9_]{1,64}", raw):
        raise ValueError("invalid crime offense category")
    base = raw.replace("_", " ").strip()
    variants = {base}
    # Light plural normalization so "assaults" matches "ASSAULT - SIMPLE".
    if base.endswith("ies") and len(base) > 4:
        variants.add(base[:-3] + "y")
    elif base.endswith("s") and len(base) > 3 and not base.endswith("ss"):
        variants.add(base[:-1])
    tokens = [f"%{item}%" for item in sorted(variants)]
    # Identifier comes only from server-owned _OFFENSE_CATEGORY_SQL.
    clause = " OR ".join(
        f"LOWER(COALESCE({_OFFENSE_CATEGORY_SQL}, '')) LIKE %s" for _ in tokens
    )
    return (f"({clause})", tokens)


def _count_incidents(
    connection: Any,
    period: Mapping[str, Any],
    offense_category_id: Optional[str],
) -> int:
    date_sql, date_params = _period_params(period)
    offense_sql, offense_params = _offense_params(offense_category_id)
    sql = (
        "SELECT COUNT(*) AS cnt FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} AND {date_sql} AND {offense_sql}"
    )
    rows = _fetch_all(connection, sql, date_params + offense_params)
    if not rows:
        return 0
    return int(rows[0].get("cnt") or 0)


def _count_unmapped_incidents(
    connection: Any,
    period: Mapping[str, Any],
) -> int:
    """Count rows where both offense_code_group and offense_description are empty."""
    date_sql, date_params = _period_params(period)
    sql = (
        "SELECT COUNT(*) AS cnt FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} AND {date_sql} "
        f"AND {_OFFENSE_CATEGORY_SQL} IS NULL"
    )
    rows = _fetch_all(connection, sql, date_params)
    if not rows:
        return 0
    return int(rows[0].get("cnt") or 0)


def _group_incidents(
    connection: Any,
    period: Mapping[str, Any],
    limit: int,
) -> List[Dict[str, Any]]:
    """Return raw source-category aggregates using the authoritative COALESCE field."""
    date_sql, date_params = _period_params(period)
    bounded_limit = max(1, min(int(limit), 250))
    sql = (
        f"SELECT {_OFFENSE_CATEGORY_SQL} AS offense_category, "
        "COUNT(*) AS cnt "
        "FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} AND {date_sql} "
        "GROUP BY 1 ORDER BY cnt DESC LIMIT %s"
    )
    rows = _fetch_all(connection, sql, date_params + [bounded_limit])
    return [
        {
            "offense_category": row.get("offense_category"),
            "offense_group": row.get("offense_category"),  # backward-compatible alias
            "count": int(row.get("cnt") or 0),
        }
        for row in rows
    ]


def clean_offense_label(raw: Any) -> str:
    """Normalize spelling/case/punctuation for display without inventing categories."""
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    if not text:
        return ""
    text = text.replace(" / ", "/").replace(" - ", " - ")
    # Title-case while preserving common BPD abbreviations.
    pieces = []
    for token in text.split(" "):
        upper = token.upper()
        if upper in {"M/V", "MV", "B&E", "PD", "OUI", "DUI", "UCR"}:
            pieces.append(upper)
        elif "/" in token:
            pieces.append("/".join(
                p.upper() if p.upper() in {"M", "V"} else p.capitalize()
                for p in token.split("/")
            ))
        else:
            pieces.append(token.capitalize() if token.isupper() or token.islower() else token)
    return " ".join(pieces).strip(" -/")


def is_non_crime_offense(label: str) -> bool:
    lowered = (label or "").casefold()
    # Leaving-scene / hit-and-run reports are safety-relevant criminal categories.
    if "leaving scene" in lowered or "hit and run" in lowered:
        return False
    return any(pat in lowered for pat in _NON_CRIME_OFFENSE_PATTERNS)


def normalize_offense_category(raw: Any) -> Dict[str, Any]:
    """
    Map a BPD source label to a resident-readable category.

    Priority:
    1. Empty / placeholder → internal unmapped marker (not shown as a concern)
    2. First matching offense-family pattern
    3. Cleaned source label when readable
    """
    cleaned = clean_offense_label(raw)
    if not cleaned or cleaned.casefold() in {"unknown", "null", "none", "n/a", "na"}:
        return {
            "category": None,
            "raw_label": str(raw or "").strip() or None,
            "mapped": False,
            "internal_key": _INTERNAL_UNKNOWN,
        }
    lowered = cleaned.casefold()
    for family, patterns in _OFFENSE_FAMILY_PATTERNS:
        if any(pat in lowered for pat in patterns):
            return {
                "category": family,
                "raw_label": cleaned,
                "mapped": True,
                "internal_key": family.casefold(),
            }
    return {
        "category": cleaned,
        "raw_label": cleaned,
        "mapped": True,
        "internal_key": cleaned.casefold(),
    }


def _build_category_overview(
    raw_groups: Sequence[Mapping[str, Any]],
    *,
    total_count: int,
    limit: int,
    unmapped_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Aggregate normalized categories and apply Unknown/coverage rules."""
    family_counts: Counter[str] = Counter()
    family_display: Dict[str, str] = {}
    raw_samples: List[Dict[str, Any]] = []
    categorized = 0
    empty_in_groups = 0
    for item in raw_groups:
        count = int(item.get("count") or 0)
        if count <= 0:
            continue
        raw = item.get("offense_category")
        if raw is None:
            raw = item.get("offense_group")
        raw_samples.append({"offense_category": raw, "count": count})
        norm = normalize_offense_category(raw)
        if not norm["mapped"] or not norm["category"]:
            empty_in_groups += count
            continue
        categorized += count
        key = str(norm["internal_key"])
        family_counts[key] += count
        family_display[key] = str(norm["category"])

    # Prefer safety-relevant (non "police activity") categories for leading concerns.
    safety_keys = [
        key for key in family_counts
        if not is_non_crime_offense(family_display[key])
    ]
    ordered_keys = sorted(
        safety_keys or list(family_counts.keys()),
        key=lambda k: (-family_counts[k], family_display[k]),
    )
    leading: List[Dict[str, Any]] = []
    for key in ordered_keys:
        label = family_display[key]
        if label.casefold() == "unknown":
            continue
        leading.append({
            "offense_group": label,
            "offense_category": label,
            "count": int(family_counts[key]),
        })
        if len(leading) >= max(1, min(int(limit), MAX_GROUPS)):
            break

    if unmapped_count is None:
        unmapped = empty_in_groups
    else:
        unmapped = max(0, int(unmapped_count))
    # Labels present in the source but outside a truncated GROUP window are still
    # categorized; do not treat the unread remainder as unmapped.
    if total_count > 0 and categorized + unmapped > total_count:
        unmapped = max(0, total_count - categorized)

    coverage = (
        float(max(0, total_count - unmapped)) / float(total_count)
        if total_count
        else 0.0
    )
    reliable = bool(total_count > 0 and coverage >= _MIN_CATEGORY_COVERAGE and leading)
    if total_count > 0 and categorized == 0:
        reliable = False
        leading = []

    return {
        "leading_categories": leading if reliable else [],
        "categorized_incident_count": (
            max(0, total_count - unmapped) if total_count else categorized
        ),
        "unmapped_incident_count": unmapped,
        "category_coverage": round(coverage, 4),
        "category_coverage_reliable": reliable,
        "raw_offense_samples": raw_samples[:15],
        "min_category_coverage": _MIN_CATEGORY_COVERAGE,
    }


def _fetch_all(
    connection: Any,
    sql: str,
    params: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    # mysql-connector accepts dictionary=True; pymysql uses cursorclass at connect.
    try:
        cursor = connection.cursor(dictionary=True)
    except TypeError:
        cursor = connection.cursor()
    try:
        cursor.execute(sql, tuple(params or ()))
        rows = cursor.fetchall() or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, Mapping):
                out.append(dict(row))
            elif hasattr(cursor, "description") and cursor.description:
                keys = [col[0] for col in cursor.description]
                out.append({keys[i]: row[i] for i in range(len(keys))})
            else:
                raise TypeError("crime query returned a non-mapping row")
        return out
    finally:
        cursor.close()


def _compose_answer(
    plan: CrimePlan,
    facts: Mapping[str, Any],
    freshness: Optional[Mapping[str, Any]],
) -> str:
    period = str(facts.get("period_label") or _period_label(plan.period))
    count = int(facts.get("incident_report_count") or 0)
    parts: List[str] = []

    if plan.operation == "GROUP":
        groups = facts.get("top_offense_groups")
        reliable = bool(facts.get("category_coverage_reliable"))
        if reliable and isinstance(groups, list) and groups:
            labels = [
                str(item.get("offense_group") or item.get("offense_category") or "").strip()
                for item in groups[:5]
                if isinstance(item, Mapping)
                and str(item.get("offense_group") or item.get("offense_category") or "").strip()
                and str(item.get("offense_group") or "").casefold() != "unknown"
            ]
            # Drop any residual Unknown presentations.
            labels = [label for label in labels if label.casefold() != "unknown"]
            if labels:
                if len(labels) == 1:
                    lead = labels[0]
                elif len(labels) == 2:
                    lead = f"{labels[0]} and {labels[1]}"
                else:
                    lead = ", ".join(labels[:-1]) + f", and {labels[-1]}"
                parts.append(
                    f"Using BPD incident reports for {period}, the most frequently reported "
                    f"safety-relevant categories in Dorchester were {lead}."
                )
                detail = "; ".join(
                    f"{item.get('offense_group')} ({int(item.get('count') or 0):,})"
                    for item in groups[:5]
                    if isinstance(item, Mapping)
                    and str(item.get("offense_group") or "").casefold() != "unknown"
                )
                if detail:
                    parts.append(f"Leading counts include {detail}.")
            parts.append(
                f"The dataset contains {count:,} Dorchester incident reports for this period."
            )
        elif count == 0:
            parts.append(
                f"I did not find Dorchester BPD incident reports for {period}."
            )
        else:
            parts.append(
                f"For Dorchester in {period}, Boston open data shows {count:,} BPD incident "
                f"reports, but usable offense-category labels are too incomplete to identify "
                f"reliable leading safety categories."
            )
            parts.append(
                "You can ask for a period comparison, an overall count, or inspect the "
                "official BPD open-data source directly."
            )
    else:
        parts.append(
            f"For Dorchester in {period}, Boston open data shows {count:,} BPD incident reports."
        )

    if "comparison_incident_report_count" in facts:
        prior = int(facts.get("comparison_incident_report_count") or 0)
        prior_label = facts.get("comparison_period_label") or "the prior equal period"
        parts.append(
            f"In {prior_label}, the same source shows {prior:,} incident reports."
        )

    parts.append(
        "These figures are published incident reports, not convictions, and they are "
        "not the same as Boston 311 service requests or community concerns."
    )
    if plan.operation == "GROUP":
        parts.append(
            "BPD reports also include non-criminal police responses such as medical assists "
            "or investigations that are not themselves crime categories."
        )
        unmapped = int(facts.get("unmapped_incident_count") or 0)
        total = int(facts.get("incident_report_count") or 0)
        if unmapped and total and 0 < unmapped < total and facts.get("category_coverage_reliable"):
            pct = round(100.0 * unmapped / total)
            if pct >= 5:
                parts.append(
                    f"About {pct}% of reports in this period could not be placed in a "
                    f"named offense category."
                )
    if freshness and freshness.get("data_through_date"):
        parts.append(f"Source coverage currently runs through {freshness['data_through_date']}.")
    return " ".join(parts)


__all__ = [
    "CrimePlan",
    "CrimeResult",
    "build_compiled_crime_plan",
    "clean_offense_label",
    "execute_crime_plan",
    "is_non_crime_offense",
    "normalize_offense_category",
]
