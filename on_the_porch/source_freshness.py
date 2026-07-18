"""Structured civic source freshness for 311 and crime (MySQL-backed).

Distinguishes ingestion time from coverage dates. Latest-record dates alone
are never treated as complete proof of coverage.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc

SOURCE_CRIME = "crime_incident_reports"
SOURCE_311 = "service_requests_311"
KNOWN_SOURCES = frozenset({SOURCE_CRIME, SOURCE_311})

MAX_SERIALIZED_BYTES = 2048

REASON_CODES = frozenset(
    {
        "requested_period_fully_covered",
        "requested_period_extends_beyond_coverage",
        "requested_period_outside_coverage",
        "missing_data_through_date",
        "malformed_source_status",
        "missing_source_status",
        "latest_record_only_basis",
        "unknown_basis",
        "current_condition_request",
        "stale_source",
        "delayed_source",
        "fresh_source",
    }
)


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    DELAYED = "DELAYED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class CoverageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    OUTSIDE = "OUTSIDE"
    UNKNOWN = "UNKNOWN"


class FreshnessBasis(str, Enum):
    SOURCE_AS_OF = "SOURCE_AS_OF"
    INGEST_METADATA = "INGEST_METADATA"
    LATEST_RECORD_ONLY = "LATEST_RECORD_ONLY"
    UNKNOWN = "UNKNOWN"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def freshness_thresholds(source_key: str) -> Tuple[int, int]:
    """Return (fresh_max_days, delayed_max_days) inclusive calendar-day ages."""
    if source_key == SOURCE_311:
        return (
            _env_int("SOURCE_FRESHNESS_311_FRESH_DAYS", 3),
            _env_int("SOURCE_FRESHNESS_311_DELAYED_DAYS", 7),
        )
    if source_key == SOURCE_CRIME:
        return (
            _env_int("SOURCE_FRESHNESS_CRIME_FRESH_DAYS", 3),
            _env_int("SOURCE_FRESHNESS_CRIME_DELAYED_DAYS", 7),
        )
    return (3, 7)


def today_ny(now: Optional[datetime] = None) -> date:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(NEW_YORK).date()


def parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(NEW_YORK).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        if "T" in text or " " in text:
            dt = datetime.fromisoformat(text[:26].replace(" ", "T"))
            if dt.tzinfo is None:
                return dt.date()
            return dt.astimezone(NEW_YORK).date()
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text[:26].replace(" ", "T"))
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class SourceStatusRecord:
    source_key: str
    last_successful_ingest_at: Optional[datetime]
    data_through_date: Optional[date]
    latest_record_date: Optional[date]
    freshness_basis: FreshnessBasis
    record_count: Optional[int]
    status_updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_key": self.source_key,
            "last_successful_ingest_at": (
                self.last_successful_ingest_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if self.last_successful_ingest_at
                else None
            ),
            "data_through_date": (
                self.data_through_date.isoformat() if self.data_through_date else None
            ),
            "latest_record_date": (
                self.latest_record_date.isoformat() if self.latest_record_date else None
            ),
            "freshness_basis": self.freshness_basis.value,
            "record_count": self.record_count,
            "status_updated_at": (
                self.status_updated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if self.status_updated_at
                else None
            ),
        }


@dataclass(frozen=True)
class FreshnessAssessment:
    source_key: str
    freshness_status: FreshnessStatus
    freshness_basis: FreshnessBasis
    last_successful_ingest_at: Optional[datetime]
    data_through_date: Optional[date]
    latest_record_date: Optional[date]
    age_days: Optional[int]
    coverage_status: CoverageStatus
    requested_start_date: Optional[date]
    requested_end_date_exclusive: Optional[date]
    reason_codes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "source_key": self.source_key,
            "freshness_status": self.freshness_status.value,
            "freshness_basis": self.freshness_basis.value,
            "last_successful_ingest_at": (
                self.last_successful_ingest_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if self.last_successful_ingest_at
                else None
            ),
            "data_through_date": (
                self.data_through_date.isoformat() if self.data_through_date else None
            ),
            "latest_record_date": (
                self.latest_record_date.isoformat() if self.latest_record_date else None
            ),
            "age_days": self.age_days,
            "coverage_status": self.coverage_status.value,
            "requested_start_date": (
                self.requested_start_date.isoformat() if self.requested_start_date else None
            ),
            "requested_end_date_exclusive": (
                self.requested_end_date_exclusive.isoformat()
                if self.requested_end_date_exclusive
                else None
            ),
            "reason_codes": list(self.reason_codes),
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_SERIALIZED_BYTES:
            payload["reason_codes"] = payload["reason_codes"][:4]
        return payload


def parse_source_status_row(row: Mapping[str, Any]) -> Optional[SourceStatusRecord]:
    try:
        source_key = str(row.get("source_key") or "").strip()
        if source_key not in KNOWN_SOURCES:
            return None
        basis_raw = str(row.get("freshness_basis") or FreshnessBasis.UNKNOWN.value).strip()
        try:
            basis = FreshnessBasis(basis_raw)
        except ValueError:
            basis = FreshnessBasis.UNKNOWN
        count = row.get("record_count")
        record_count = int(count) if count is not None else None
        return SourceStatusRecord(
            source_key=source_key,
            last_successful_ingest_at=parse_utc_datetime(row.get("last_successful_ingest_at")),
            data_through_date=parse_date(row.get("data_through_date")),
            latest_record_date=parse_date(row.get("latest_record_date")),
            freshness_basis=basis,
            record_count=record_count,
            status_updated_at=parse_utc_datetime(row.get("status_updated_at")),
        )
    except Exception:
        return None


def classify_freshness_status(
    status: Optional[SourceStatusRecord],
    *,
    now: Optional[datetime] = None,
) -> Tuple[FreshnessStatus, Optional[int], Tuple[str, ...]]:
    if status is None:
        return FreshnessStatus.UNKNOWN, None, ("missing_source_status",)
    if status.freshness_basis is FreshnessBasis.UNKNOWN:
        return FreshnessStatus.UNKNOWN, None, ("unknown_basis",)
    if status.freshness_basis is FreshnessBasis.LATEST_RECORD_ONLY:
        # Recent MAX(date) alone is not confident freshness.
        return FreshnessStatus.UNKNOWN, None, ("latest_record_only_basis",)
    coverage = status.data_through_date
    if coverage is None:
        return FreshnessStatus.UNKNOWN, None, ("missing_data_through_date",)
    age = (today_ny(now) - coverage).days
    fresh_max, delayed_max = freshness_thresholds(status.source_key)
    if age < 0:
        age = 0
    if age <= fresh_max:
        return FreshnessStatus.FRESH, age, ("fresh_source",)
    if age <= delayed_max:
        return FreshnessStatus.DELAYED, age, ("delayed_source",)
    return FreshnessStatus.STALE, age, ("stale_source",)


def classify_coverage(
    *,
    data_through_date: Optional[date],
    requested_start: Optional[date],
    requested_end_exclusive: Optional[date],
) -> Tuple[CoverageStatus, Tuple[str, ...]]:
    if data_through_date is None or requested_start is None or requested_end_exclusive is None:
        return CoverageStatus.UNKNOWN, ("missing_data_through_date",)
    if requested_end_exclusive <= requested_start:
        return CoverageStatus.UNKNOWN, ("missing_data_through_date",)
    # Half-open [start, end): covered through date D means last inclusive day is D.
    covered_end_exclusive = data_through_date + timedelta(days=1)
    if requested_start >= covered_end_exclusive:
        return CoverageStatus.OUTSIDE, ("requested_period_outside_coverage",)
    if requested_end_exclusive <= covered_end_exclusive:
        return CoverageStatus.COMPLETE, ("requested_period_fully_covered",)
    return CoverageStatus.PARTIAL, ("requested_period_extends_beyond_coverage",)


def requested_range_from_period(
    *,
    calendar_ym: Optional[str] = None,
    days: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date_exclusive: Optional[date] = None,
    now: Optional[datetime] = None,
) -> Tuple[Optional[date], Optional[date]]:
    if start_date is not None and end_date_exclusive is not None:
        return start_date, end_date_exclusive
    today = today_ny(now)
    if calendar_ym:
        try:
            year_s, month_s = calendar_ym.split("-")
            year, month = int(year_s), int(month_s)
            start = date(year, month, 1)
            if month == 12:
                end = date(year + 1, 1, 1)
            else:
                end = date(year, month + 1, 1)
            return start, end
        except ValueError:
            return None, None
    if days is not None and days > 0:
        end = today + timedelta(days=1)
        start = end - timedelta(days=days)
        return start, end
    return None, None


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_explicit_date_range(
    question: str,
    *,
    now: Optional[datetime] = None,
) -> Tuple[Optional[date], Optional[date]]:
    """Parse 'from June 1 through June 20' / 'June 1 to June 20, 2026'."""
    q = (question or "").lower()
    today = today_ny(now)
    pattern = re.compile(
        r"from\s+([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?"
        r"\s+(?:through|to|until|-)\s+"
        r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?",
        re.I,
    )
    match = pattern.search(q)
    if not match:
        return None, None
    m1, d1, y1, m2, d2, y2 = match.groups()
    month1 = _MONTHS.get(m1.lower())
    month2 = _MONTHS.get(m2.lower())
    if not month1 or not month2:
        return None, None
    year2 = int(y2) if y2 else today.year
    year1 = int(y1) if y1 else year2
    try:
        start = date(year1, month1, int(d1))
        end_inclusive = date(year2, month2, int(d2))
    except ValueError:
        return None, None
    return start, end_inclusive + timedelta(days=1)


def is_current_condition_question(question: str) -> bool:
    q = (question or "").lower()
    markers = (
        "right now",
        "currently",
        "today",
        "this week",
        "recent",
        "lately",
        "happening now",
        "safe right now",
        "safe now",
    )
    return any(marker in q for marker in markers)


def assess_freshness(
    status: Optional[SourceStatusRecord],
    *,
    requested_start: Optional[date] = None,
    requested_end_exclusive: Optional[date] = None,
    current_condition: bool = False,
    now: Optional[datetime] = None,
) -> FreshnessAssessment:
    try:
        if status is None:
            return FreshnessAssessment(
                source_key="unknown",
                freshness_status=FreshnessStatus.UNKNOWN,
                freshness_basis=FreshnessBasis.UNKNOWN,
                last_successful_ingest_at=None,
                data_through_date=None,
                latest_record_date=None,
                age_days=None,
                coverage_status=CoverageStatus.UNKNOWN,
                requested_start_date=requested_start,
                requested_end_date_exclusive=requested_end_exclusive,
                reason_codes=("missing_source_status",),
            )
        freshness, age, fresh_reasons = classify_freshness_status(status, now=now)
        coverage, cov_reasons = classify_coverage(
            data_through_date=status.data_through_date,
            requested_start=requested_start,
            requested_end_exclusive=requested_end_exclusive,
        )
        reasons: List[str] = []
        for code in list(fresh_reasons) + list(cov_reasons):
            if code in REASON_CODES and code not in reasons:
                reasons.append(code)
        if current_condition:
            reasons.append("current_condition_request")
            if freshness in {FreshnessStatus.STALE, FreshnessStatus.UNKNOWN, FreshnessStatus.DELAYED}:
                if "stale_source" not in reasons and freshness is FreshnessStatus.STALE:
                    reasons.append("stale_source")
        return FreshnessAssessment(
            source_key=status.source_key,
            freshness_status=freshness,
            freshness_basis=status.freshness_basis,
            last_successful_ingest_at=status.last_successful_ingest_at,
            data_through_date=status.data_through_date,
            latest_record_date=status.latest_record_date,
            age_days=age,
            coverage_status=coverage,
            requested_start_date=requested_start,
            requested_end_date_exclusive=requested_end_exclusive,
            reason_codes=tuple(reasons[:12]),
        )
    except Exception:
        return FreshnessAssessment(
            source_key=getattr(status, "source_key", "unknown") if status else "unknown",
            freshness_status=FreshnessStatus.UNKNOWN,
            freshness_basis=FreshnessBasis.UNKNOWN,
            last_successful_ingest_at=None,
            data_through_date=None,
            latest_record_date=None,
            age_days=None,
            coverage_status=CoverageStatus.UNKNOWN,
            requested_start_date=requested_start,
            requested_end_date_exclusive=requested_end_exclusive,
            reason_codes=("malformed_source_status",),
        )


def format_data_through_phrase(data_through: Optional[date]) -> str:
    if data_through is None:
        return "an unknown date"
    return data_through.strftime("%B %-d, %Y") if os.name != "nt" else data_through.strftime("%B %d, %Y").replace(" 0", " ")


def _fmt_date(value: date) -> str:
    # Portable day without leading zero issues across platforms.
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def format_structured_count_answer(
    *,
    assessment: FreshnessAssessment,
    count: int,
    subject_phrase: str,
    geography_phrase: str,
    is_311: bool = False,
    current_condition: bool = False,
) -> str:
    """Resident-facing wording for structured 311/crime counts."""
    through = _fmt_date(assessment.data_through_date) if assessment.data_through_date else None
    start = assessment.requested_start_date
    end_excl = assessment.requested_end_date_exclusive
    end_incl = (end_excl - timedelta(days=1)) if end_excl else None

    if current_condition and assessment.freshness_status in {
        FreshnessStatus.STALE,
        FreshnessStatus.UNKNOWN,
        FreshnessStatus.DELAYED,
    }:
        if through:
            if is_311:
                return (
                    "I can't reliably assess current conditions from the available official dataset. "
                    f"The local 311 data contains records through {through}, "
                    "so it does not describe what is happening right now."
                )
            return (
                f"I can't reliably assess current conditions from the available official dataset. "
                f"The local {'311' if is_311 else 'crime'} data contains records through {through}, "
                f"so it does not describe what is happening right now. "
                f"For an immediate emergency, call 911."
            )
        if is_311:
            return "I can't reliably assess current conditions from the available official dataset."
        return (
            "I can't reliably assess current conditions from the available official dataset. "
            "For an immediate emergency, call 911."
        )

    if assessment.coverage_status is CoverageStatus.OUTSIDE:
        if through:
            return (
                f"I can't provide a reliable count for that period from the current local dataset. "
                f"The available {'311 service-request' if is_311 else 'crime'} data contains records "
                f"only through {through}."
            )
        return (
            "I can't provide a reliable count for that period because the local dataset coverage "
            "could not be verified."
        )

    if assessment.coverage_status is CoverageStatus.UNKNOWN:
        if count == 0:
            base = (
                f"I found no matching records for {subject_phrase} {geography_phrase}, but I could not "
                f"verify how current or complete the local dataset is. Treat this as provisional."
            )
        else:
            base = (
                f"I found **{count:,}** {subject_phrase} {geography_phrase}, but I could not verify "
                f"how current or complete the local dataset is. Treat this count as provisional."
            )
        if is_311:
            base += (
                " These are submitted 311 service requests, not necessarily distinct or verified "
                "physical conditions."
            )
        return base

    if assessment.coverage_status is CoverageStatus.PARTIAL:
        covered_end = assessment.data_through_date
        if count == 0:
            text = (
                f"I found no matching records in the covered portion of the local dataset"
                + (f", which contains data through {through}" if through else "")
                + ". That does not confirm that no matching events occurred after that date."
            )
        else:
            range_bits = []
            if start:
                range_bits.append(_fmt_date(start))
            if covered_end:
                range_bits.append(_fmt_date(covered_end))
            covered = " through ".join(range_bits) if range_bits else "the covered period"
            text = (
                f"The local dataset contains **{count:,}** {subject_phrase} from {covered}"
                f"{' ' + geography_phrase if geography_phrase else ''}. "
            )
            if end_incl and covered_end and end_incl > covered_end:
                text += (
                    f"It does not yet cover {_fmt_date(covered_end + timedelta(days=1))} through "
                    f"{_fmt_date(end_incl)}, so this is not a complete count for the full requested period."
                )
            else:
                text += "This is not a complete count for the full requested period."
        if is_311:
            text += (
                " These are submitted service requests, not necessarily distinct or verified physical conditions."
            )
        return text

    # COMPLETE (including LATEST_RECORD_ONLY / UNKNOWN freshness for covered periods)
    if (
        assessment.freshness_status is FreshnessStatus.UNKNOWN
        and assessment.freshness_basis is FreshnessBasis.UNKNOWN
    ):
        if count == 0:
            base = (
                f"I found no matching records for {subject_phrase} {geography_phrase}, but I could not "
                f"verify how current or complete the local dataset is. Treat this as provisional."
            )
        else:
            base = (
                f"I found **{count:,}** {subject_phrase} {geography_phrase}, but I could not verify "
                f"how current or complete the local dataset is. Treat this count as provisional."
            )
        if is_311:
            base += (
                " These are submitted 311 service requests, not necessarily distinct or verified "
                "physical conditions."
            )
        return base

    if count == 0:
        text = (
            f"I found no matching {subject_phrase} {geography_phrase} during the requested period"
            + (f". The local dataset contains records through {through}" if through else "")
            + "."
        )
    else:
        text = (
            f"The available official data records **{count:,}** {subject_phrase} "
            f"{geography_phrase} during the requested period"
            + (f". The local dataset contains records through {through}" if through else "")
            + "."
        )
    if is_311:
        text += (
            " These are submitted 311 service requests, not necessarily distinct or verified "
            "physical conditions."
        )
    return text


def current_condition_safety_answer(assessment: FreshnessAssessment) -> str:
    through = _fmt_date(assessment.data_through_date) if assessment.data_through_date else None
    if through:
        return (
            "I can't reliably assess current conditions from the available official dataset. "
            f"The local crime data contains records through {through}, so it does not describe "
            "what is happening right now. For an immediate emergency, call 911."
        )
    return (
        "I can't reliably assess current conditions from the available official dataset. "
        "For an immediate emergency, call 911."
    )


# ---------------------------------------------------------------------------
# MySQL persistence
# ---------------------------------------------------------------------------


def load_source_status(
    connection_factory: Callable[[], Any],
    source_key: str,
) -> Optional[SourceStatusRecord]:
    if source_key not in KNOWN_SOURCES:
        return None
    conn = None
    try:
        conn = connection_factory()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT source_key, last_successful_ingest_at, data_through_date,
                       latest_record_date, freshness_basis, record_count, status_updated_at
                FROM civic_source_status
                WHERE source_key = %s
                LIMIT 1
                """,
                (source_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return parse_source_status_row(row)
        finally:
            cursor.close()
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def upsert_source_status(
    connection_factory: Callable[[], Any],
    record: SourceStatusRecord,
) -> bool:
    if record.source_key not in KNOWN_SOURCES:
        return False
    conn = None
    try:
        conn = connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO civic_source_status (
                    source_key, last_successful_ingest_at, data_through_date,
                    latest_record_date, freshness_basis, record_count, status_updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                ON DUPLICATE KEY UPDATE
                    last_successful_ingest_at = VALUES(last_successful_ingest_at),
                    data_through_date = VALUES(data_through_date),
                    latest_record_date = VALUES(latest_record_date),
                    freshness_basis = VALUES(freshness_basis),
                    record_count = VALUES(record_count),
                    status_updated_at = UTC_TIMESTAMP()
                """,
                (
                    record.source_key,
                    record.last_successful_ingest_at.replace(tzinfo=None)
                    if record.last_successful_ingest_at
                    else None,
                    record.data_through_date,
                    record.latest_record_date,
                    record.freshness_basis.value,
                    record.record_count,
                ),
            )
            conn.commit()
            return True
        finally:
            cursor.close()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def record_successful_ingest(
    connection_factory: Callable[[], Any],
    *,
    source_key: str,
    latest_record_date: Optional[date],
    record_count: Optional[int],
    advance_data_through: bool = True,
    ingest_at: Optional[datetime] = None,
) -> bool:
    """Update MySQL source status after a successful ingest.

    Zero-new-row success should pass advance_data_through=False so coverage
    dates are not invented forward of the existing data_through_date.
    Failed ingestions must not call this helper.
    """
    if source_key not in KNOWN_SOURCES:
        return False
    existing = load_source_status(connection_factory, source_key)
    now = ingest_at or datetime.now(UTC)
    data_through = latest_record_date
    if not advance_data_through and existing and existing.data_through_date is not None:
        data_through = existing.data_through_date
    elif existing and existing.data_through_date and data_through:
        # Never invent a later coverage date than the latest observed record.
        data_through = min(data_through, latest_record_date) if latest_record_date else existing.data_through_date
    if latest_record_date and data_through and latest_record_date < data_through:
        data_through = latest_record_date
    record = SourceStatusRecord(
        source_key=source_key,
        last_successful_ingest_at=now,
        data_through_date=data_through,
        latest_record_date=latest_record_date
        if latest_record_date is not None
        else (existing.latest_record_date if existing else None),
        freshness_basis=FreshnessBasis.INGEST_METADATA,
        record_count=record_count
        if record_count is not None
        else (existing.record_count if existing else None),
    )
    return upsert_source_status(connection_factory, record)


def slim_freshness_metadata(assessment: FreshnessAssessment) -> Dict[str, Any]:
    """Bounded internal metadata — never dump raw status JSON to residents."""
    return {
        "source_key": assessment.source_key,
        "data_through_date": (
            assessment.data_through_date.isoformat() if assessment.data_through_date else None
        ),
        "freshness_status": assessment.freshness_status.value,
        "coverage_status": assessment.coverage_status.value,
    }


def completion_flags_from_assessment(
    assessment: FreshnessAssessment,
    *,
    current_condition: bool = False,
) -> Dict[str, Any]:
    """Map freshness/coverage onto completion hints for transition gates."""
    if current_condition and assessment.freshness_status in {
        FreshnessStatus.STALE,
        FreshnessStatus.UNKNOWN,
        FreshnessStatus.DELAYED,
    }:
        return {
            "freshness_completion": "DEGRADED",
            "freshness_blocks_current_condition": True,
            "execution_degraded": True,
        }
    if assessment.coverage_status in {
        CoverageStatus.PARTIAL,
        CoverageStatus.OUTSIDE,
        CoverageStatus.UNKNOWN,
    }:
        return {
            "freshness_completion": "PARTIAL",
            "freshness_blocks_current_condition": False,
            "execution_degraded": False,
        }
    if assessment.freshness_status is FreshnessStatus.UNKNOWN:
        return {
            "freshness_completion": "PARTIAL",
            "freshness_blocks_current_condition": False,
            "execution_degraded": False,
        }
    return {
        "freshness_completion": "ACCEPTABLE",
        "freshness_blocks_current_condition": False,
        "execution_degraded": False,
    }


def mysql_connection_factory() -> Callable[[], Any]:
    """Lazy MySQL connector using env vars (no credentials logged)."""
    import mysql.connector  # noqa: WPS433

    def _factory():
        return mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DB", "sentiment_demo"),
        )

    return _factory


def source_key_for_intent(intent: str) -> Optional[str]:
    if intent.startswith("311") or intent in {"311_weekly_count", "311_count_with_types"}:
        return SOURCE_311
    if intent.startswith("crime") or intent in {"crime_monthly_count", "crime_shooting_count"}:
        return SOURCE_CRIME
    return None


def assess_for_question(
    connection_factory: Callable[[], Any],
    *,
    source_key: str,
    question: str,
    calendar_ym: Optional[str] = None,
    days: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date_exclusive: Optional[date] = None,
    now: Optional[datetime] = None,
) -> FreshnessAssessment:
    status = load_source_status(connection_factory, source_key)
    start, end = parse_explicit_date_range(question, now=now)
    if start is None and start_date is not None and end_date_exclusive is not None:
        start, end = start_date, end_date_exclusive
    if start is None and (calendar_ym or days):
        start, end = requested_range_from_period(
            calendar_ym=calendar_ym, days=days, now=now
        )
    if start is None:
        try:
            from query_plan import resolve_period  # noqa: WPS433

            window = resolve_period(question, days or 7)
            start = window.start_date
            end = window.end_date_exclusive
            if start is None and window.calendar_ym:
                start, end = requested_range_from_period(
                    calendar_ym=window.calendar_ym, days=window.days, now=now
                )
            elif start is None and window.days:
                start, end = requested_range_from_period(days=window.days, now=now)
        except Exception:
            pass
    return assess_freshness(
        status,
        requested_start=start,
        requested_end_exclusive=end,
        current_condition=is_current_condition_question(question),
        now=now,
    )
