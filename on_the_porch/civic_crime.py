"""Deterministic Boston crime / public-safety evidence for model-led turns."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
            groups = []
            facts = {
                "incident_report_count": current_count,
                "comparison_incident_report_count": prior_count,
                "period_label": _period_label(plan.period),
                "comparison_period_label": _period_label(prior),
            }
        elif plan.operation == "GROUP":
            groups = _group_incidents(connection, plan.period, plan.limit)
            current_count = sum(int(item.get("count") or 0) for item in groups)
            facts = {
                "incident_report_count": current_count,
                "top_offense_groups": groups,
                "period_label": _period_label(plan.period),
            }
        else:
            current_count = _count_incidents(connection, plan.period, plan.offense_category_id)
            groups = []
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
    evidence_status = EvidenceStatus.COMPLETE
    if str(freshness_map.get("status") or "").upper() in {"STALE", "DELAYED"}:
        evidence_status = EvidenceStatus.STALE_BUT_USABLE
        limitations.append("Crime source coverage may be incomplete for the newest days.")
    bundle = EvidenceBundle(
        question_scope={"domain": "CRIME", "operation": plan.operation},
        issue_family=plan.offense_category_id,
        geography={"location_id": plan.location_id, "name": "Dorchester"},
        requested_period=dict(plan.period),
        executed_period=dict(plan.period),
        freshness=dict(freshness_map),
        coverage={"status": "COVERED"},
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
            else ToolResultStatus.COMPLETE
        ),
        requested_scope=plan.to_dict(),
        executed_scope=plan.to_dict(),
        typed_facts=dict(facts),
        records=tuple(groups),
        evidence_atoms=atoms,
        source_records=(source,),
        freshness=dict(freshness_map),
        coverage={"status": "COVERED"},
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
    token = f"%{raw.replace('_', ' ')}%"
    return (
        "(LOWER(COALESCE(`offense_code_group`, '')) LIKE %s "
        "OR LOWER(COALESCE(`offense_description`, '')) LIKE %s)",
        [token, token],
    )


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


def _group_incidents(
    connection: Any,
    period: Mapping[str, Any],
    limit: int,
) -> List[Dict[str, Any]]:
    date_sql, date_params = _period_params(period)
    bounded_limit = max(1, min(int(limit), MAX_GROUPS))
    sql = (
        "SELECT COALESCE(NULLIF(TRIM(`offense_code_group`), ''), 'Unknown') AS offense_group, "
        "COUNT(*) AS cnt "
        "FROM `crime_incident_reports` "
        f"WHERE {_DORCHESTER_CRIME} AND {date_sql} "
        "GROUP BY 1 ORDER BY cnt DESC LIMIT %s"
    )
    rows = _fetch_all(connection, sql, date_params + [bounded_limit])
    return [
        {
            "offense_group": str(row.get("offense_group") or "Unknown"),
            "count": int(row.get("cnt") or 0),
        }
        for row in rows
    ]


def _fetch_all(
    connection: Any,
    sql: str,
    params: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, tuple(params or ()))
        rows = cursor.fetchall() or []
        return [dict(row) for row in rows]
    finally:
        cursor.close()


def _compose_answer(
    plan: CrimePlan,
    facts: Mapping[str, Any],
    freshness: Optional[Mapping[str, Any]],
) -> str:
    period = str(facts.get("period_label") or _period_label(plan.period))
    count = int(facts.get("incident_report_count") or 0)
    parts = [
        f"For Dorchester in {period}, Boston open data shows {count:,} BPD incident reports."
    ]
    groups = facts.get("top_offense_groups")
    if isinstance(groups, list) and groups:
        labels = [
            f"{item.get('offense_group')} ({int(item.get('count') or 0):,})"
            for item in groups[:5]
            if isinstance(item, Mapping)
        ]
        if labels:
            parts.append("Leading reported categories include " + "; ".join(labels) + ".")
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
    if freshness and freshness.get("data_through_date"):
        parts.append(f"Source coverage currently runs through {freshness['data_through_date']}.")
    return " ".join(parts)


__all__ = [
    "CrimePlan",
    "CrimeResult",
    "build_compiled_crime_plan",
    "execute_crime_plan",
]
