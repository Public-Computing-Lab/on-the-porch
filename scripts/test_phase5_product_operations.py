#!/usr/bin/env python3
"""Focused Phase 5 architecture and operator-surface gates."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
PORCH = ROOT / "on_the_porch"
sys.path[:0] = [str(ROOT), str(API), str(PORCH)]


def test_seed_inventory_complete():
    from source_registry import load_seed_inventory

    items = load_seed_inventory()
    families = {item.family for item in items}
    required = {
        "crime_public_safety",
        "boston_311",
        "events",
        "policy",
        "zoning",
        "meetings",
        "news",
        "community",
        "entities",
    }
    assert required.issubset(families), families
    assert all(item.source_id for item in items)


def test_migration_012_exists():
    path = API / "migrations" / "012_source_registry.sql"
    text = path.read_text(encoding="utf-8")
    for table in (
        "source_registry",
        "source_ingestion_runs",
        "civic_documents",
        "civic_document_chunks",
        "operator_audit_log",
    ):
        assert table in text


def test_admin_routes_present():
    src = (API / "api_v2.py").read_text(encoding="utf-8")
    for route in (
        "/admin/sources",
        "/admin/ingestion/runs",
        "/admin/ingestion/bootstrap",
        "/admin/documents",
    ):
        assert route in src


def test_ops_cli_and_pipeline_importable():
    from ingestion_pipeline import bootstrap_phase5, ingest_local_directory
    from document_catalog import catalog_stats

    assert callable(bootstrap_phase5)
    assert callable(ingest_local_directory)
    assert callable(catalog_stats)
    assert (ROOT / "scripts" / "otp_ops.py").exists()


def test_corpus_seed_files_exist():
    assert list((ROOT / "data" / "phase5_corpus" / "policy").glob("*"))
    assert list((ROOT / "data" / "phase5_corpus" / "zoning").glob("*"))
    assert list((ROOT / "data" / "phase5_corpus" / "meetings").glob("*"))
    assert list((ROOT / "data" / "phase5_corpus" / "news").glob("*"))
    assert list((ROOT / "data" / "phase5_corpus" / "community").glob("*"))


def test_frontend_source_chip_a11y():
    app_js = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "source-chip" in app_js
    assert "aria-label" in app_js
    assert "message-limitations" in app_js
    css = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert "source-chip:focus-visible" in css
    admin = (ROOT / "public" / "admin.html").read_text(encoding="utf-8")
    assert 'data-tab="sources"' in admin


def test_no_second_framework_markers():
    # Prefer deletion/consolidation: ensure one registry seed file.
    assert (PORCH / "source_registry_seed.json").exists()
    assert not (PORCH / "source_registry_seed_v2.json").exists()


def main() -> int:
    tests = [
        test_seed_inventory_complete,
        test_migration_012_exists,
        test_admin_routes_present,
        test_ops_cli_and_pipeline_importable,
        test_corpus_seed_files_exist,
        test_frontend_source_chip_a11y,
        test_no_second_framework_markers,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
