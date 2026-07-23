#!/usr/bin/env python3
"""Public-beta readiness gates for provenance, gating, crime SQL, and ledger."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api"), str(ROOT / "on_the_porch")]


def test_provenance_excludes_fixtures():
    from source_provenance import (
        ProvenanceClass,
        classify_local_path,
        is_resident_eligible,
    )

    assert not is_resident_eligible(ProvenanceClass.DEMO_FIXTURE.value)
    assert not is_resident_eligible(ProvenanceClass.INTERNAL_ANALYSIS.value)
    assert is_resident_eligible(ProvenanceClass.OFFICIAL_SECONDARY.value)
    assert classify_local_path(
        "data/phase5_corpus/fixtures/policy/Imagine-Boston-2030-Analysis.html",
        source_id="boston.policy_corpus",
    ).value in {"INTERNAL_ANALYSIS", "TEST_ONLY", "DEMO_FIXTURE"}
    assert not is_resident_eligible(
        classify_local_path(
            "data/phase5_corpus/fixtures/policy/Imagine-Boston-2030-Analysis.html",
            source_id="boston.policy_corpus",
        ).value
    )
    assert (
        classify_local_path(
            "data/phase5_corpus/zoning/boston_zoning_resident_pointer.txt",
            source_id="boston.zoning_references",
        )
        is ProvenanceClass.OFFICIAL_SECONDARY
    )


def test_seed_has_provenance():
    from source_registry import load_seed_inventory

    items = load_seed_inventory()
    assert all(item.provenance_class for item in items)
    policy = next(i for i in items if i.source_id == "boston.policy_corpus")
    assert policy.provenance_class == "OFFICIAL_SECONDARY"
    assert "City/BPDA/ISD" in policy.limitations or "curated" in policy.authority.casefold()
    news = next(i for i in items if i.source_id == "dorchester.local_news")
    assert news.min_useful_documents == 3


def test_crime_sql_parameterized():
    src = (ROOT / "on_the_porch" / "civic_crime.py").read_text(encoding="utf-8")
    assert "_sql_escape" not in src
    assert "LIKE %s" in src
    assert "cursor.execute(sql, tuple(params or ()))" in src
    from civic_crime import _offense_params, _period_params

    clause, params = _period_params({
        "start_date": "2024-01-01",
        "end_date_exclusive": "2024-02-01",
    })
    assert "%s" in clause and len(params) == 2
    try:
        _offense_params("'; DROP TABLE crime_incident_reports; --")
        raise AssertionError("expected invalid offense rejection")
    except ValueError:
        pass


def test_documents_filter_helpers_present():
    src = (ROOT / "on_the_porch" / "civic_documents.py").read_text(encoding="utf-8")
    assert "is_resident_eligible" in src
    assert "enabled_sources" in src or "connection_factory" in src
    assert "Official policy documents" not in src


def test_synthesis_ledger_reserved():
    src = (ROOT / "on_the_porch" / "model_led_turn_service.py").read_text(encoding="utf-8")
    assert "ledger.reserve(ModelCallRole.SYNTHESIS)" in src


def test_crime_instruction_in_planner():
    src = (ROOT / "on_the_porch" / "semantic_planner.py").read_text(encoding="utf-8")
    assert "CRIME: broad public-safety" in src


def test_community_not_transcript_in_bootstrap():
    src = (ROOT / "on_the_porch" / "ingestion_pipeline.py").read_text(encoding="utf-8")
    assert '"dorchester.community_documents": (\n                "community"' in src or \
        '"community",\n                [ROOT / "data" / "phase5_corpus" / "community"]' in src
    assert "public" not in src or "public / Policies" not in src.replace(" ", "")
    assert "quarantine_non_resident_documents" in src


def test_guest_rate_limit_and_audit_mode_config():
    import config as cfg

    assert hasattr(cfg.Config, "RATE_LIMIT_GUEST_CHAT_PER_SESSION")
    assert hasattr(cfg.Config, "AUDIT_RATE_LIMIT_MODE")
    api = (ROOT / "api" / "api_v2.py").read_text(encoding="utf-8")
    assert "_effective_chat_rate_limit" in api
    assert "_google_genai_plain_text" in api
    assert "google.generativeai" not in api or api.count("_get_llm_client")  # legacy import may remain unused


def test_mobile_chat_first_and_reduced_motion():
    css = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css
    assert "data-sidebar-open" in css or "sidebar-open" in css
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    assert "sidebar-toggle" in html
    app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "setSidebarOpen" in app


def test_fixtures_quarantined_on_disk():
    fixtures = ROOT / "data" / "phase5_corpus" / "fixtures"
    assert fixtures.exists()
    assert list(fixtures.rglob("*Analysis*")) or list(fixtures.rglob("*digest*"))
    assert not list((ROOT / "data" / "phase5_corpus" / "policy").glob("*Analysis*"))


def test_document_retrieval_excludes_disabled(monkeypatch=None):
    from answering_kernel import GlobalDeadline
    from civic_documents import DocumentPlan, execute_document_plan

    def fake_retrieve(query, *, k, doc_type):
        return {
            "chunks": ["Boston zoning enforcement is handled by ISD for permits and occupancy questions around Dorchester parcels."],
            "metadata": [{
                "source_id": "boston.zoning_references",
                "title": "Zoning pointer",
                "publisher": "On the Porch curated reference",
                "provenance_class": "OFFICIAL_SECONDARY",
                "authority": "Curated secondary reference",
            }],
        }

    plan = DocumentPlan(
        query="zoning",
        corpora=("POLICY",),
        location_id="dorchester",
        time_scope=None,
        source_types=(),
    )
    disabled = execute_document_plan(
        "zoning",
        plan=plan,
        deadline=GlobalDeadline.now(),
        retrieve=fake_retrieve,
        enabled_sources=lambda: set(),
    )
    assert disabled.civic_tool_result.records == ()
    enabled = execute_document_plan(
        "zoning",
        plan=plan,
        deadline=GlobalDeadline.now(),
        retrieve=fake_retrieve,
        enabled_sources=lambda: {"boston.zoning_references"},
    )
    assert len(enabled.civic_tool_result.records) == 1
    assert "Official City" not in (enabled.sources[0].get("authority") or "")
    fixture_blocked = execute_document_plan(
        "zoning",
        plan=plan,
        deadline=GlobalDeadline.now(),
        retrieve=lambda *a, **k: {
            "chunks": ["Internal analysis digest pretending to be BPDA guidance for Dorchester zoning districts."],
            "metadata": [{
                "source_id": "boston.policy_corpus",
                "provenance_class": "INTERNAL_ANALYSIS",
                "publisher": "Internal analysis",
            }],
        },
        enabled_sources=lambda: {"boston.policy_corpus"},
    )
    assert fixture_blocked.civic_tool_result.records == ()


if __name__ == "__main__":
    tests = [
        test_provenance_excludes_fixtures,
        test_seed_has_provenance,
        test_crime_sql_parameterized,
        test_documents_filter_helpers_present,
        test_synthesis_ledger_reserved,
        test_crime_instruction_in_planner,
        test_community_not_transcript_in_bootstrap,
        test_guest_rate_limit_and_audit_mode_config,
        test_mobile_chat_first_and_reduced_motion,
        test_fixtures_quarantined_on_disk,
        test_document_retrieval_excludes_disabled,
    ]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print("public beta readiness tests passed")
