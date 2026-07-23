#!/usr/bin/env python3
"""Operator CLI for On the Porch Phase 5 source/ingestion workflows.

Examples:
  python scripts/otp_ops.py bootstrap
  python scripts/otp_ops.py sources
  python scripts/otp_ops.py ingest-local boston.policy_corpus
  python scripts/otp_ops.py runs
  python scripts/otp_ops.py health
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
PORCH = ROOT / "on_the_porch"
sys.path[:0] = [str(ROOT), str(API), str(PORCH)]

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)


def _connect():
    import mysql.connector

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", ""),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", ""),
    )


def _ensure_migrations() -> None:
    from db_migrations import run_migrations

    run_migrations(_connect, API / "migrations")


def _conn():
    _ensure_migrations()
    return _connect()


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from ingestion_pipeline import bootstrap_phase5

    conn = _conn()
    try:
        result = bootstrap_phase5(
            conn,
            include_local_corpus=not args.skip_local,
            write_vectors=bool(args.write_vectors),
        )
        print(json.dumps({
            "seeded_rows": result["seeded_rows"],
            "catalog": result["catalog"],
            "parity": result["parity"],
            "run_count": len(result["runs"]),
            "runs": result["runs"],
        }, indent=2, default=str))
        return 0 if result["parity"].get("parity_ok", True) else 2
    finally:
        conn.close()


def cmd_sources(_: argparse.Namespace) -> int:
    from source_registry import ensure_registry_seeded, list_sources, registry_health_summary

    conn = _conn()
    try:
        ensure_registry_seeded(conn)
        print(json.dumps({
            "summary": registry_health_summary(conn),
            "sources": list_sources(conn),
        }, indent=2, default=str))
        return 0
    finally:
        conn.close()


def cmd_ingest_local(args: argparse.Namespace) -> int:
    from ingestion_pipeline import DEFAULT_CORPUS_DIRS, ingest_local_directory

    mapping = {
        "boston.policy_corpus": ("policy", [ROOT / "data" / "phase5_corpus" / "policy", ROOT / "public" / "Policies"]),
        "boston.zoning_references": ("policy", [ROOT / "data" / "phase5_corpus" / "zoning"]),
        "dorchester.meeting_transcripts": ("transcript", [ROOT / "data" / "phase5_corpus" / "meetings"]),
        "dorchester.local_news": ("news", [ROOT / "data" / "phase5_corpus" / "news"]),
        "dorchester.community_documents": ("transcript", [ROOT / "data" / "phase5_corpus" / "community"]),
    }
    if args.source_id not in mapping:
        print(json.dumps({"error": "unknown_source", "allowed": sorted(mapping)}))
        return 2
    doc_type, directories = mapping[args.source_id]
    conn = _conn()
    try:
        result = ingest_local_directory(
            conn,
            source_id=args.source_id,
            directories=directories,
            doc_type=doc_type,
            write_vectors=bool(args.write_vectors),
        )
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.status == "success" else 1
    finally:
        conn.close()


def cmd_runs(args: argparse.Namespace) -> int:
    from ingestion_pipeline import list_ingestion_runs

    conn = _conn()
    try:
        print(json.dumps(list_ingestion_runs(conn, limit=args.limit), indent=2, default=str))
        return 0
    finally:
        conn.close()


def cmd_disable(args: argparse.Namespace) -> int:
    from source_registry import record_operator_action, set_source_enabled

    conn = _conn()
    try:
        ok = set_source_enabled(conn, args.source_id, enabled=not args.disable)
        record_operator_action(
            conn,
            action="disable_source" if args.disable else "enable_source",
            target_type="source",
            target_id=args.source_id,
            details={"enabled": not args.disable},
        )
        print(json.dumps({"ok": ok, "source_id": args.source_id, "enabled": not args.disable}))
        return 0 if ok else 1
    finally:
        conn.close()


def cmd_health(_: argparse.Namespace) -> int:
    from document_catalog import catalog_stats, verify_index_parity
    from source_registry import ensure_registry_seeded, registry_health_summary

    conn = _conn()
    try:
        ensure_registry_seeded(conn)
        payload = {
            "registry": registry_health_summary(conn),
            "catalog": catalog_stats(conn),
            "parity": verify_index_parity(conn),
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="On the Porch operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_boot = sub.add_parser("bootstrap", help="Seed registry and populate catalogs")
    p_boot.add_argument("--skip-local", action="store_true")
    p_boot.add_argument("--write-vectors", action="store_true")
    p_boot.set_defaults(func=cmd_bootstrap)

    p_sources = sub.add_parser("sources", help="List registered sources")
    p_sources.set_defaults(func=cmd_sources)

    p_ingest = sub.add_parser("ingest-local", help="Ingest local corpus for one source")
    p_ingest.add_argument("source_id")
    p_ingest.add_argument("--write-vectors", action="store_true")
    p_ingest.set_defaults(func=cmd_ingest_local)

    p_runs = sub.add_parser("runs", help="List recent ingestion runs")
    p_runs.add_argument("--limit", type=int, default=30)
    p_runs.set_defaults(func=cmd_runs)

    p_dis = sub.add_parser("set-enabled", help="Enable or disable a source")
    p_dis.add_argument("source_id")
    p_dis.add_argument("--disable", action="store_true")
    p_dis.set_defaults(func=cmd_disable)

    p_health = sub.add_parser("health", help="Registry/catalog health summary")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
