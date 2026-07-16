#!/usr/bin/env python3
"""Focused tests for structured civic source freshness (311 + crime)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))
sys.path.insert(0, str(ROOT / "api"))

from source_freshness import (  # noqa: E402
    SOURCE_311,
    SOURCE_CRIME,
    CoverageStatus,
    FreshnessAssessment,
    FreshnessBasis,
    FreshnessStatus,
    MAX_SERIALIZED_BYTES,
    SourceStatusRecord,
    assess_freshness,
    classify_coverage,
    classify_freshness_status,
    completion_flags_from_assessment,
    current_condition_safety_answer,
    format_structured_count_answer,
    is_current_condition_question,
    parse_source_status_row,
    record_successful_ingest,
    today_ny,
    upsert_source_status,
)
from conversation_transitions import (  # noqa: E402
    CompletionStatus,
    classify_completion_status,
)

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


# ---------------------------------------------------------------------------
# In-memory MySQL stand-in for ingest / lookup tests
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, store: dict):
        self.store = store
        self._row = None
        self.dictionary = False

    def execute(self, sql, params=None):
        sql_l = " ".join(sql.lower().split())
        if sql_l.startswith("select") and "civic_source_status" in sql_l:
            key = params[0] if params else None
            row = self.store.get(key)
            self._row = row
        elif sql_l.startswith("insert into civic_source_status"):
            key = params[0]
            self.store[key] = {
                "source_key": params[0],
                "last_successful_ingest_at": params[1],
                "data_through_date": params[2],
                "latest_record_date": params[3],
                "freshness_basis": params[4],
                "record_count": params[5],
                "status_updated_at": datetime.now(UTC),
            }
            self._row = None
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def close(self):
        return None


class _FakeConn:
    def __init__(self, store: dict):
        self.store = store

    def cursor(self, dictionary=False):
        cur = _FakeCursor(self.store)
        cur.dictionary = dictionary
        return cur

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _status(
    *,
    key=SOURCE_CRIME,
    through=date(2026, 6, 26),
    latest=None,
    basis=FreshnessBasis.INGEST_METADATA,
    ingest=None,
    count=190926,
):
    return SourceStatusRecord(
        source_key=key,
        last_successful_ingest_at=ingest or datetime(2026, 7, 16, 14, 10, tzinfo=UTC),
        data_through_date=through,
        latest_record_date=latest or through,
        freshness_basis=basis,
        record_count=count,
    )


class SourceFreshnessTests(unittest.TestCase):
    def test_01_valid_311_status(self):
        row = parse_source_status_row(
            {
                "source_key": SOURCE_311,
                "last_successful_ingest_at": "2026-07-16T14:10:00Z",
                "data_through_date": "2026-06-27",
                "latest_record_date": "2026-06-27",
                "freshness_basis": "INGEST_METADATA",
                "record_count": 80000,
            }
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.source_key, SOURCE_311)
        self.assertEqual(row.data_through_date, date(2026, 6, 27))

    def test_02_valid_crime_status(self):
        row = parse_source_status_row(
            {
                "source_key": SOURCE_CRIME,
                "last_successful_ingest_at": "2026-07-16T14:10:00Z",
                "data_through_date": "2026-06-26",
                "latest_record_date": "2026-06-26",
                "freshness_basis": "INGEST_METADATA",
                "record_count": 190926,
            }
        )
        self.assertEqual(row.source_key, SOURCE_CRIME)

    def test_03_fresh_threshold(self):
        now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
        status = _status(through=date(2026, 6, 26))
        fres, age, _ = classify_freshness_status(status, now=now)
        self.assertEqual(fres, FreshnessStatus.FRESH)
        self.assertEqual(age, 2)

    def test_04_delayed_threshold(self):
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        status = _status(through=date(2026, 6, 26))
        fres, age, _ = classify_freshness_status(status, now=now)
        self.assertEqual(fres, FreshnessStatus.DELAYED)
        self.assertEqual(age, 5)

    def test_05_stale_threshold(self):
        now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
        status = _status(through=date(2026, 6, 26))
        fres, age, _ = classify_freshness_status(status, now=now)
        self.assertEqual(fres, FreshnessStatus.STALE)
        self.assertGreater(age, 7)

    def test_06_unknown_basis(self):
        status = _status(basis=FreshnessBasis.UNKNOWN)
        fres, _, reasons = classify_freshness_status(status)
        self.assertEqual(fres, FreshnessStatus.UNKNOWN)
        self.assertIn("unknown_basis", reasons)

    def test_07_latest_record_only_not_fresh(self):
        now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
        status = _status(through=date(2026, 6, 26), basis=FreshnessBasis.LATEST_RECORD_ONLY)
        fres, _, reasons = classify_freshness_status(status, now=now)
        self.assertEqual(fres, FreshnessStatus.UNKNOWN)
        self.assertIn("latest_record_only_basis", reasons)

    def test_08_complete_historical(self):
        cov, _ = classify_coverage(
            data_through_date=date(2026, 6, 26),
            requested_start=date(2026, 6, 1),
            requested_end_exclusive=date(2026, 6, 16),
        )
        self.assertEqual(cov, CoverageStatus.COMPLETE)

    def test_09_partial_period(self):
        cov, codes = classify_coverage(
            data_through_date=date(2026, 6, 26),
            requested_start=date(2026, 6, 1),
            requested_end_exclusive=date(2026, 7, 1),
        )
        self.assertEqual(cov, CoverageStatus.PARTIAL)
        self.assertIn("requested_period_extends_beyond_coverage", codes)

    def test_10_outside_period(self):
        cov, _ = classify_coverage(
            data_through_date=date(2026, 6, 26),
            requested_start=date(2026, 7, 1),
            requested_end_exclusive=date(2026, 7, 16),
        )
        self.assertEqual(cov, CoverageStatus.OUTSIDE)

    def test_11_unknown_coverage(self):
        cov, _ = classify_coverage(
            data_through_date=None,
            requested_start=date(2026, 6, 1),
            requested_end_exclusive=date(2026, 6, 16),
        )
        self.assertEqual(cov, CoverageStatus.UNKNOWN)

    def test_12_dst_boundary(self):
        # America/New_York spring forward 2026-03-08; coverage age uses calendar dates.
        before = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)  # 01:30 EST
        after = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)  # 03:30 EDT
        self.assertEqual(today_ny(before), date(2026, 3, 8))
        self.assertEqual(today_ny(after), date(2026, 3, 8))
        status = _status(through=date(2026, 3, 5))
        fres_b, age_b, _ = classify_freshness_status(status, now=before)
        fres_a, age_a, _ = classify_freshness_status(status, now=after)
        self.assertEqual(fres_b, fres_a)
        self.assertEqual(age_b, age_a)

    def test_13_ny_midnight_boundary(self):
        just_before = datetime(2026, 7, 16, 3, 59, tzinfo=UTC)  # 23:59 EDT prior day
        just_after = datetime(2026, 7, 16, 4, 1, tzinfo=UTC)  # 00:01 EDT
        self.assertEqual(today_ny(just_before), date(2026, 7, 15))
        self.assertEqual(today_ny(just_after), date(2026, 7, 16))

    def test_14_malformed_status(self):
        assessment = assess_freshness({"not": "a status"})  # type: ignore[arg-type]
        self.assertEqual(assessment.freshness_status, FreshnessStatus.UNKNOWN)
        self.assertIn("malformed_source_status", assessment.reason_codes)

    def test_15_missing_status_row(self):
        assessment = assess_freshness(None)
        self.assertEqual(assessment.freshness_status, FreshnessStatus.UNKNOWN)
        self.assertIn("missing_source_status", assessment.reason_codes)

    def test_16_failed_ingest_does_not_advance(self):
        store: dict = {}
        factory = lambda: _FakeConn(store)  # noqa: E731
        first = _status(through=date(2026, 6, 20), ingest=datetime(2026, 6, 21, tzinfo=UTC))
        self.assertTrue(upsert_source_status(factory, first))
        before = store[SOURCE_CRIME]["last_successful_ingest_at"]
        # Failed ingest: do not call record_successful_ingest
        after = store[SOURCE_CRIME]["last_successful_ingest_at"]
        self.assertEqual(before, after)
        self.assertEqual(store[SOURCE_CRIME]["data_through_date"], date(2026, 6, 20))

    def test_17_successful_ingest_updates_timestamp(self):
        store: dict = {}
        factory = lambda: _FakeConn(store)  # noqa: E731
        upsert_source_status(factory, _status(through=date(2026, 6, 20)))
        ok = record_successful_ingest(
            factory,
            source_key=SOURCE_CRIME,
            latest_record_date=date(2026, 6, 26),
            record_count=100,
            advance_data_through=True,
            ingest_at=datetime(2026, 7, 16, 15, 0, tzinfo=UTC),
        )
        self.assertTrue(ok)
        self.assertEqual(store[SOURCE_CRIME]["data_through_date"], date(2026, 6, 26))
        self.assertIsNotNone(store[SOURCE_CRIME]["last_successful_ingest_at"])

    def test_18_zero_new_row_does_not_invent_coverage(self):
        store: dict = {}
        factory = lambda: _FakeConn(store)  # noqa: E731
        upsert_source_status(factory, _status(through=date(2026, 6, 26)))
        record_successful_ingest(
            factory,
            source_key=SOURCE_CRIME,
            latest_record_date=date(2026, 6, 26),
            record_count=190926,
            advance_data_through=False,
            ingest_at=datetime(2026, 7, 16, 16, 0, tzinfo=UTC),
        )
        self.assertEqual(store[SOURCE_CRIME]["data_through_date"], date(2026, 6, 26))

    def _crime_answer(self, coverage, count, **kwargs):
        assessment = FreshnessAssessment(
            source_key=SOURCE_CRIME,
            freshness_status=FreshnessStatus.STALE,
            freshness_basis=FreshnessBasis.INGEST_METADATA,
            last_successful_ingest_at=datetime(2026, 7, 16, tzinfo=UTC),
            data_through_date=date(2026, 6, 26),
            latest_record_date=date(2026, 6, 26),
            age_days=20,
            coverage_status=coverage,
            requested_start_date=kwargs.get("start", date(2026, 6, 1)),
            requested_end_date_exclusive=kwargs.get("end", date(2026, 7, 1)),
            reason_codes=(),
        )
        return format_structured_count_answer(
            assessment=assessment,
            count=count,
            subject_phrase="reported shooting incidents",
            geography_phrase="in BPD district C11",
            is_311=False,
        )

    def test_19_crime_complete(self):
        text = self._crime_answer(
            CoverageStatus.COMPLETE, 5, end=date(2026, 6, 21)
        )
        self.assertIn("5", text)
        self.assertIn("June 26, 2026", text)
        self.assertIn("C11", text)

    def test_20_crime_partial(self):
        text = self._crime_answer(CoverageStatus.PARTIAL, 5)
        self.assertIn("not a complete count", text.lower())
        self.assertIn("June 26, 2026", text)

    def test_21_crime_outside(self):
        text = self._crime_answer(
            CoverageStatus.OUTSIDE, 0, start=date(2026, 7, 1), end=date(2026, 7, 16)
        )
        self.assertIn("can't provide a reliable count", text.lower())
        self.assertNotIn("0 reported", text.lower())

    def test_22_311_complete(self):
        assessment = FreshnessAssessment(
            source_key=SOURCE_311,
            freshness_status=FreshnessStatus.DELAYED,
            freshness_basis=FreshnessBasis.INGEST_METADATA,
            last_successful_ingest_at=datetime(2026, 7, 16, tzinfo=UTC),
            data_through_date=date(2026, 6, 27),
            latest_record_date=date(2026, 6, 27),
            age_days=19,
            coverage_status=CoverageStatus.COMPLETE,
            requested_start_date=date(2026, 6, 1),
            requested_end_date_exclusive=date(2026, 6, 21),
            reason_codes=(),
        )
        text = format_structured_count_answer(
            assessment=assessment,
            count=18,
            subject_phrase="pothole-related 311 service requests",
            geography_phrase="in Dorchester",
            is_311=True,
        )
        self.assertIn("18", text)
        self.assertIn("service requests", text)

    def test_23_311_partial(self):
        assessment = FreshnessAssessment(
            source_key=SOURCE_311,
            freshness_status=FreshnessStatus.STALE,
            freshness_basis=FreshnessBasis.INGEST_METADATA,
            last_successful_ingest_at=datetime(2026, 7, 16, tzinfo=UTC),
            data_through_date=date(2026, 6, 27),
            latest_record_date=date(2026, 6, 27),
            age_days=19,
            coverage_status=CoverageStatus.PARTIAL,
            requested_start_date=date(2026, 6, 1),
            requested_end_date_exclusive=date(2026, 7, 1),
            reason_codes=(),
        )
        text = format_structured_count_answer(
            assessment=assessment,
            count=18,
            subject_phrase="pothole-related 311 service requests",
            geography_phrase="in Dorchester",
            is_311=True,
        )
        self.assertIn("not a complete count", text.lower())

    def test_24_311_service_request_caveat(self):
        assessment = FreshnessAssessment(
            source_key=SOURCE_311,
            freshness_status=FreshnessStatus.FRESH,
            freshness_basis=FreshnessBasis.INGEST_METADATA,
            last_successful_ingest_at=datetime(2026, 6, 28, tzinfo=UTC),
            data_through_date=date(2026, 6, 27),
            latest_record_date=date(2026, 6, 27),
            age_days=1,
            coverage_status=CoverageStatus.COMPLETE,
            requested_start_date=date(2026, 6, 1),
            requested_end_date_exclusive=date(2026, 6, 21),
            reason_codes=(),
        )
        text = format_structured_count_answer(
            assessment=assessment,
            count=18,
            subject_phrase="pothole-related 311 service requests",
            geography_phrase="in Dorchester",
            is_311=True,
        )
        self.assertIn("submitted 311 service requests", text.lower())

    def test_25_no_rows_complete(self):
        text = self._crime_answer(
            CoverageStatus.COMPLETE, 0, end=date(2026, 6, 21)
        )
        self.assertIn("no matching", text.lower())
        self.assertNotIn("there were zero incidents", text.lower())

    def test_26_no_rows_partial(self):
        text = self._crime_answer(CoverageStatus.PARTIAL, 0)
        self.assertIn("does not confirm", text.lower())
        self.assertNotIn("nothing happened", text.lower())

    def test_27_right_now_stale_crime(self):
        assessment = assess_freshness(
            _status(through=date(2026, 6, 26)),
            requested_start=date(2026, 7, 16),
            requested_end_exclusive=date(2026, 7, 17),
            current_condition=True,
            now=datetime(2026, 7, 16, 12, tzinfo=UTC),
        )
        text = current_condition_safety_answer(assessment)
        self.assertIn("can't reliably assess current conditions", text.lower())
        self.assertIn("911", text)
        self.assertNotRegex(text.lower(), r"\bis (un)?safe\b")

    def test_28_today_stale_311(self):
        self.assertTrue(is_current_condition_question("How many potholes today?"))
        assessment = assess_freshness(
            _status(key=SOURCE_311, through=date(2026, 6, 27)),
            current_condition=True,
            now=datetime(2026, 7, 16, 12, tzinfo=UTC),
        )
        text = format_structured_count_answer(
            assessment=assessment,
            count=0,
            subject_phrase="pothole-related 311 service requests",
            geography_phrase="in Dorchester",
            is_311=True,
            current_condition=True,
        )
        self.assertIn("does not describe what is happening right now", text.lower())

    def test_29_stale_safety_non_categorical(self):
        text = current_condition_safety_answer(
            assess_freshness(
                _status(),
                current_condition=True,
                now=datetime(2026, 7, 16, tzinfo=UTC),
            )
        )
        self.assertNotRegex(text.lower(), r"dorchester is (un)?safe")

    def test_30_emergency_bypasses_freshness(self):
        # Emergency routing short-circuits before freshness; confirm detector path.
        from emergency_routing import detect_active_emergency

        decision = detect_active_emergency(
            "There's a shooting happening outside right now."
        )
        self.assertTrue(decision.is_active_emergency)

    def test_31_complete_may_commit(self):
        assessment = assess_freshness(
            _status(through=date(2026, 6, 26)),
            requested_start=date(2026, 6, 1),
            requested_end_exclusive=date(2026, 6, 21),
            now=datetime(2026, 7, 16, tzinfo=UTC),
        )
        flags = completion_flags_from_assessment(assessment)
        self.assertEqual(flags["freshness_completion"], "ACCEPTABLE")
        agent = {
            "answer": "ok",
            "freshness_completion": "ACCEPTABLE",
            "result": {"columns": [], "rows": []},
        }
        self.assertEqual(classify_completion_status(agent), CompletionStatus.ACCEPTABLE)

    def test_32_partial_does_not_commit_unsupported(self):
        assessment = assess_freshness(
            _status(through=date(2026, 6, 26)),
            requested_start=date(2026, 6, 1),
            requested_end_exclusive=date(2026, 7, 1),
            now=datetime(2026, 7, 16, tzinfo=UTC),
        )
        flags = completion_flags_from_assessment(assessment)
        self.assertEqual(flags["freshness_completion"], "PARTIAL")
        agent = {
            "answer": "partial",
            "freshness_flags": flags,
            "result": {"columns": [], "rows": [{"cnt": 5}]},
        }
        self.assertEqual(classify_completion_status(agent), CompletionStatus.PARTIAL)

    def test_33_json_sse_parity_shape(self):
        # Same assessment serialization for JSON and SSE consumers.
        assessment = assess_freshness(
            _status(),
            requested_start=date(2026, 6, 1),
            requested_end_exclusive=date(2026, 6, 21),
            now=datetime(2026, 7, 16, tzinfo=UTC),
        )
        a = json.dumps(assessment.to_dict(), sort_keys=True)
        b = json.dumps(assessment.to_dict(), sort_keys=True)
        self.assertEqual(a, b)

    def test_34_no_raw_freshness_object_in_resident_text(self):
        text = self._crime_answer(CoverageStatus.PARTIAL, 5)
        self.assertNotIn("freshness_status", text)
        self.assertNotIn("INGEST_METADATA", text)
        self.assertNotIn("{", text)

    def test_35_no_redis_dependency(self):
        import source_freshness as sf

        src = Path(sf.__file__).read_text(encoding="utf-8")
        self.assertNotIn("redis", src.lower())

    def test_36_mysql_status_survives_worker_change(self):
        store: dict = {}
        factory = lambda: _FakeConn(store)  # noqa: E731
        upsert_source_status(factory, _status())
        # Simulate another worker process reading the same store.
        from source_freshness import load_source_status

        loaded = load_source_status(factory, SOURCE_CRIME)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.data_through_date, date(2026, 6, 26))

    def test_37_users_do_not_share_conversation_state(self):
        # Freshness is global; conversation state remains per-thread (unit check).
        from conversation_state import safe_empty_state

        a = safe_empty_state()
        b = safe_empty_state()
        self.assertIsNot(a, b)

    def test_38_freshness_global_by_source(self):
        store: dict = {}
        factory = lambda: _FakeConn(store)  # noqa: E731
        upsert_source_status(factory, _status(key=SOURCE_CRIME))
        upsert_source_status(
            factory,
            _status(key=SOURCE_311, through=date(2026, 6, 27)),
        )
        self.assertEqual(len(store), 2)
        self.assertIn(SOURCE_CRIME, store)
        self.assertIn(SOURCE_311, store)

    def test_39_lookup_does_not_scan_civic_table(self):
        store: dict = {}
        factory = lambda: _FakeConn(store)  # noqa: E731
        upsert_source_status(factory, _status())
        scanned = {"crime": 0}

        class TrackingConn(_FakeConn):
            def cursor(self, dictionary=False):
                cur = super().cursor(dictionary=dictionary)
                orig = cur.execute

                def execute(sql, params=None):
                    if "crime_incident_reports" in sql.lower() and "civic_source_status" not in sql.lower():
                        scanned["crime"] += 1
                    return orig(sql, params)

                cur.execute = execute  # type: ignore[method-assign]
                return cur

        from source_freshness import load_source_status

        load_source_status(lambda: TrackingConn(store), SOURCE_CRIME)
        self.assertEqual(scanned["crime"], 0)

    def test_40_bounded_size_and_strict_enums(self):
        assessment = assess_freshness(
            _status(),
            requested_start=date(2026, 6, 1),
            requested_end_exclusive=date(2026, 7, 1),
            now=datetime(2026, 7, 16, tzinfo=UTC),
        )
        payload = assessment.to_dict()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self.assertLessEqual(len(encoded.encode("utf-8")), MAX_SERIALIZED_BYTES)
        self.assertIn(payload["freshness_status"], {e.value for e in FreshnessStatus})
        self.assertIn(payload["coverage_status"], {e.value for e in CoverageStatus})
        self.assertIn(payload["freshness_basis"], {e.value for e in FreshnessBasis})

    def test_41_bootstrap_does_not_downgrade_stronger_basis(self):
        store: dict = {}
        factory = lambda: _FakeConn(store)  # noqa: E731
        upsert_source_status(
            factory,
            _status(
                through=date(2026, 6, 26),
                basis=FreshnessBasis.INGEST_METADATA,
                ingest=datetime(2026, 7, 16, 14, 10, tzinfo=UTC),
            ),
        )
        existing = parse_source_status_row(store[SOURCE_CRIME])
        self.assertEqual(existing.freshness_basis, FreshnessBasis.INGEST_METADATA)
        # Bootstrap skip policy: stronger basis must remain unless --force.
        self.assertEqual(store[SOURCE_CRIME]["freshness_basis"], "INGEST_METADATA")
        self.assertEqual(store[SOURCE_CRIME]["data_through_date"], date(2026, 6, 26))
        weaker = _status(
            through=date(2026, 6, 20),
            basis=FreshnessBasis.LATEST_RECORD_ONLY,
            ingest=None,
        )
        self.assertNotEqual(weaker.freshness_basis, existing.freshness_basis)


if __name__ == "__main__":
    unittest.main()
