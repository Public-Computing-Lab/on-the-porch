#!/usr/bin/env python3
"""Focused regressions for public-safety category overview field selection."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api"), str(ROOT / "on_the_porch")]

from answering_kernel import GlobalDeadline  # noqa: E402
from civic_crime import (  # noqa: E402
    CrimePlan,
    _MIN_CATEGORY_COVERAGE,
    _OFFENSE_CATEGORY_SQL,
    _build_category_overview,
    _compose_answer,
    _offense_params,
    _period_params,
    clean_offense_label,
    execute_crime_plan,
    is_non_crime_offense,
    normalize_offense_category,
)


class _FakeCursor:
    def __init__(self, owner: "_FakeConnection"):
        self._owner = owner
        self._rows: List[Dict[str, Any]] = []

    def execute(self, sql: str, params=None) -> None:
        self._owner.sql_log.append(sql)
        self._rows = [dict(r) for r in self._owner.rows_for(sql)]

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self) -> None:
        return None


class _FakeConnection:
    """SQL-routed stub so freshness lookups do not consume crime result scripts."""

    def __init__(
        self,
        *,
        count_rows: Sequence[Mapping[str, Any]],
        group_rows: Sequence[Mapping[str, Any]],
        status_rows: Optional[Sequence[Mapping[str, Any]]] = None,
        unmapped_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    ):
        self.count_rows = [dict(r) for r in count_rows]
        self.group_rows = [dict(r) for r in group_rows]
        self.status_rows = [dict(r) for r in (status_rows or [])]
        self.unmapped_rows = [dict(r) for r in (unmapped_rows or [{"cnt": 0}])]
        self.closed = False
        self.sql_log: List[str] = []

    def rows_for(self, sql: str) -> List[Dict[str, Any]]:
        lowered = sql.casefold()
        if "civic_source_status" in lowered:
            return list(self.status_rows)
        if "group by" in lowered:
            return list(self.group_rows)
        if "is null" in lowered and "count(*)" in lowered:
            return list(self.unmapped_rows)
        if "count(*)" in lowered:
            return list(self.count_rows)
        return []

    def cursor(self, dictionary: bool = True):
        del dictionary
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def test_offense_category_sql_priority_uses_description_fallback():
    src = (ROOT / "on_the_porch" / "civic_crime.py").read_text(encoding="utf-8")
    assert "offense_code_group" in _OFFENSE_CATEGORY_SQL
    assert "offense_description" in _OFFENSE_CATEGORY_SQL
    assert "COALESCE" in _OFFENSE_CATEGORY_SQL
    assert "SELECT COALESCE(NULLIF(TRIM(`offense_code_group`), ''), 'Unknown')" not in src


def test_normalize_maps_representative_bpd_labels():
    assert normalize_offense_category("ASSAULT - SIMPLE")["category"] == "Assault"
    assert normalize_offense_category("LARCENY SHOPLIFTING")["category"] == "Larceny"
    assert normalize_offense_category("VANDALISM")["category"] == "Vandalism"
    assert normalize_offense_category("ROBBERY")["category"] == "Robbery"
    assert (
        normalize_offense_category("M/V - LEAVING SCENE - PROPERTY DAMAGE")["category"]
        == "Motor vehicle leaving scene"
    )
    # MV accident property damage must not collapse into Vandalism.
    mv = normalize_offense_category("M/V ACCIDENT - PROPERTY DAMAGE")
    assert mv["category"] != "Vandalism"
    assert is_non_crime_offense("M/V ACCIDENT - PROPERTY DAMAGE")
    assert not is_non_crime_offense("M/V - LEAVING SCENE - PROPERTY DAMAGE")
    # Unmapped-but-readable falls back to cleaned source label, not Unknown.
    custom = normalize_offense_category("ANIMAL ABUSE")
    assert custom["mapped"] is True
    assert "animal" in custom["category"].casefold()
    assert custom["category"].casefold() != "unknown"
    empty = normalize_offense_category(None)
    assert empty["mapped"] is False
    assert empty["category"] is None


def test_unknown_excluded_when_meaningful_categories_exist():
    overview = _build_category_overview(
        [
            {"offense_category": None, "count": 20},
            {"offense_category": "LARCENY SHOPLIFTING", "count": 31},
            {"offense_category": "ASSAULT - SIMPLE", "count": 22},
            {"offense_category": "VANDALISM", "count": 25},
            {"offense_category": "INVESTIGATE PERSON", "count": 85},
        ],
        total_count=183,
        limit=5,
    )
    labels = [item["offense_group"] for item in overview["leading_categories"]]
    assert "Unknown" not in labels
    assert any("Larceny" == x or "larceny" in x.casefold() for x in labels)
    assert any("Assault" == x for x in labels)
    # Non-crime police activity should not lead the safety overview when crime cats exist.
    assert "Investigate Person" not in labels
    assert overview["category_coverage_reliable"] is True
    assert overview["unmapped_incident_count"] == 20
    assert overview["category_coverage"] >= _MIN_CATEGORY_COVERAGE


def test_all_unmapped_never_says_unknown_as_leading():
    overview = _build_category_overview(
        [{"offense_category": None, "count": 633}],
        total_count=633,
        limit=5,
    )
    assert overview["leading_categories"] == []
    assert overview["category_coverage_reliable"] is False
    assert overview["unmapped_incident_count"] == 633
    answer = _compose_answer(
        CrimePlan(
            operation="GROUP",
            location_id="dorchester",
            period={"label": "June 2026", "start_date": "2026-06-01", "end_date_exclusive": "2026-07-01"},
        ),
        {
            "incident_report_count": 633,
            "period_label": "June 2026",
            "top_offense_groups": [],
            "category_coverage_reliable": False,
            "unmapped_incident_count": 633,
            "category_coverage": 0.0,
        },
        {},
    )
    low = answer.casefold()
    assert "unknown" not in low or "incomplete" in low
    assert "leading reported categories include unknown" not in low
    assert "633" in answer
    assert "incomplete" in low or "too incomplete" in low


def test_partial_unknown_disclosed_with_meaningful_leaders():
    overview = _build_category_overview(
        [
            {"offense_category": "", "count": 10},
            {"offense_category": "ASSAULT - AGGRAVATED", "count": 40},
            {"offense_category": "ROBBERY", "count": 30},
        ],
        total_count=80,
        limit=5,
    )
    assert overview["category_coverage_reliable"] is True
    labels = [i["offense_group"] for i in overview["leading_categories"]]
    assert "Assault" in labels and "Robbery" in labels
    assert "Unknown" not in labels
    answer = _compose_answer(
        CrimePlan(
            operation="GROUP",
            location_id="dorchester",
            period={"label": "June 2026"},
        ),
        {
            "incident_report_count": 80,
            "period_label": "June 2026",
            "top_offense_groups": overview["leading_categories"],
            "category_coverage_reliable": True,
            "unmapped_incident_count": overview["unmapped_incident_count"],
            "category_coverage": overview["category_coverage"],
        },
        {"data_through_date": "2026-06-30"},
    )
    low = answer.casefold()
    assert "assault" in low and "robbery" in low
    assert "80" in answer
    assert "june 2026" in low
    assert "not convictions" in low
    assert "311" in low
    assert "leading reported categories include unknown" not in low


def test_sql_parameterization_and_invalid_category():
    clause, params = _period_params({
        "start_date": "2026-06-01",
        "end_date_exclusive": "2026-07-01",
    })
    assert "%s" in clause and params == ["2026-06-01", "2026-07-01"]
    oclause, oparams = _offense_params("larceny")
    assert "%s" in oclause and "%larceny%" in oparams
    assert "offense_description" in oclause
    plural_clause, plural_params = _offense_params("assaults")
    assert "%assault%" in plural_params
    assert "%assaults%" in plural_params
    try:
        _offense_params("'; DROP TABLE x; --")
        raise AssertionError("expected rejection")
    except ValueError:
        pass
    try:
        _offense_params("larceny'; select")
        raise AssertionError("expected rejection")
    except ValueError:
        pass


def test_group_query_uses_coalesce_alias_and_total_count():
    shared = {"conn": None}

    def factory():
        shared["conn"] = _FakeConnection(
            count_rows=[{"cnt": 100}],
            unmapped_rows=[{"cnt": 5}],
            group_rows=[
                {"offense_category": "LARCENY SHOPLIFTING", "cnt": 31},
                {"offense_category": "ASSAULT - SIMPLE", "cnt": 22},
                {"offense_category": None, "cnt": 5},
            ],
        )
        return shared["conn"]

    result = execute_crime_plan(
        "What are the main safety concerns in Dorchester?",
        plan=CrimePlan(
            operation="GROUP",
            location_id="dorchester",
            period={
                "start_date": "2026-06-01",
                "end_date_exclusive": "2026-07-01",
                "label": "June 2026",
            },
            group_by="OFFENSE",
            limit=5,
        ),
        connection_factory=factory,
        deadline=GlobalDeadline.from_started_at(time.perf_counter()),
    )
    conn = shared["conn"]
    assert conn is not None
    assert any("COUNT(*)" in sql for sql in conn.sql_log)
    group_sql = next(sql for sql in conn.sql_log if "GROUP BY" in sql)
    assert "offense_description" in group_sql
    assert "AS offense_category" in group_sql
    assert "'Unknown'" not in group_sql
    assert result.evidence.facts["incident_report_count"] == 100
    labels = [g["offense_group"] for g in result.evidence.facts["top_offense_groups"]]
    assert "Unknown" not in labels
    assert any("Larceny" in x or "Assault" in x for x in labels)
    low = result.answer.casefold()
    assert "unknown (100)" not in low
    assert "unknown (633)" not in low
    assert "june 2026" in low
    assert "100" in result.answer


def test_clean_label_and_non_crime_classifier():
    assert "Assault" in clean_offense_label("ASSAULT - SIMPLE")
    assert is_non_crime_offense("SICK ASSIST")
    assert is_non_crime_offense("INVESTIGATE PERSON")
    assert not is_non_crime_offense("LARCENY SHOPLIFTING")


def test_no_exact_question_routing_in_module():
    src = (ROOT / "on_the_porch" / "civic_crime.py").read_text(encoding="utf-8")
    assert "What are the main safety concerns in Dorchester?" not in src
    planner = (ROOT / "on_the_porch" / "semantic_planner.py").read_text(encoding="utf-8")
    assert "What are the main safety concerns in Dorchester?" not in planner


def main() -> int:
    tests = [
        test_offense_category_sql_priority_uses_description_fallback,
        test_normalize_maps_representative_bpd_labels,
        test_unknown_excluded_when_meaningful_categories_exist,
        test_all_unmapped_never_says_unknown_as_leading,
        test_partial_unknown_disclosed_with_meaningful_leaders,
        test_sql_parameterization_and_invalid_category,
        test_group_query_uses_coalesce_alias_and_total_count,
        test_clean_label_and_non_crime_classifier,
        test_no_exact_question_routing_in_module,
    ]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
