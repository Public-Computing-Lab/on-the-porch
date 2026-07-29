from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "api" / "migrations" / "010_conversation_turns.sql"


def test_turn_migration_is_next_sequential_migration() -> None:
    names = sorted(path.name for path in (ROOT / "api" / "migrations").glob("*.sql"))
    assert "009_api_cache_entries.sql" in names
    assert "010_conversation_turns.sql" in names
    assert names.index("010_conversation_turns.sql") == names.index("009_api_cache_entries.sql") + 1


def test_turn_migration_has_required_coordination_fields_and_indexes() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    for expected in (
        "create table if not exists conversation_turns",
        "client_turn_id",
        "request_fingerprint",
        "transport_origin",
        "response_payload_json",
        "normalized_error_category",
        "lease_owner",
        "lease_expires_at",
        "observability_trace_id",
        "unique key uq_conversation_turn_thread_client",
        "key idx_conversation_turn_status_lease",
        "add column thread_state_version",
        "add column active_turn_id",
    ):
        assert expected in sql


def test_migration_does_not_modify_protected_content_tables() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    for table in (
        "weekly_events",
        "service_requests_311",
        "crime_incident_reports",
        "shootings",
    ):
        assert f"alter table {table}" not in sql
