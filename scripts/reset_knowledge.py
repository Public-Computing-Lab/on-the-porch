#!/usr/bin/env python3
"""Wipe ingest-backed knowledge (MySQL + vector store) and optionally re-ingest.

Does NOT delete users, conversations, auth sessions, or login history.

DotNews remains disabled if cron_ingest.sh / ingest scripts still have it
commented out (default in this repo).

Usage:
    python scripts/reset_knowledge.py --dry-run
    python scripts/reset_knowledge.py --yes
    python scripts/reset_knowledge.py --yes --reingest
    python scripts/reset_knowledge.py --yes --reingest --monthly
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional

import mysql.connector
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_INGESTION = ROOT / "on_the_porch" / "data_ingestion"
_RAG_DIR = ROOT / "on_the_porch" / "rag stuff"
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

import retrieval  # noqa: E402

# Civic / RAG tables only — never users, threads, messages, auth.
DEFAULT_MYSQL_TABLES = (
    "weekly_events",
    "crime_incident_reports",
    "service_requests_311",
    "shootings",
    "shots_fired_data",
    "homicide_data",
    "events_materialized",
    "mv_311_weekly",
    "mv_crime_monthly",
    "rag_ingestion_log",
    "admin_knowledge",
    "api_cache_entries",
    "ingest_job_runs",
)

SYNC_STATE_FILES = (
    DATA_INGESTION / ".sync_state.json",
    DATA_INGESTION / ".email_sync_state.json",
    DATA_INGESTION / ".sync_state_dotnews.json",
)


def _mysql_config() -> dict:
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DB", "rethink_ai_boston"),
    }


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table,))
    return cursor.fetchone() is not None


def _count_rows(cursor, table: str) -> int:
    if not _table_exists(cursor, table):
        return 0
    cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
    row = cursor.fetchone()
    return int(row[0] if row else 0)


def clear_mysql_tables(
    tables: Iterable[str],
    *,
    dry_run: bool,
) -> List[tuple[str, int]]:
    conn = mysql.connector.connect(**_mysql_config())
    cursor = conn.cursor()
    cleared: List[tuple[str, int]] = []
    try:
        for table in tables:
            count = _count_rows(cursor, table)
            if count == 0 and not _table_exists(cursor, table):
                print(f"  skip {table} (missing)")
                continue
            print(f"  {'would truncate' if dry_run else 'truncate'} {table} ({count:,} rows)")
            if not dry_run:
                cursor.execute(f"TRUNCATE TABLE `{table}`")
            cleared.append((table, count))
        if not dry_run:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return cleared


def _vectordb_paths() -> List[Path]:
    paths = [retrieval.resolve_vectordb_dir()]
    for extra in (retrieval.VECTORDB_GEMINI_DIR, retrieval.VECTORDB_BGE_DIR):
        resolved = Path(extra).resolve()
        if resolved not in paths:
            paths.append(resolved)
    return paths


def clear_chroma_store(path: Path, *, dry_run: bool) -> int:
    if not path.exists():
        print(f"  skip chroma {path} (not found)")
        return 0
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(path))
        collection = client.get_collection("langchain")
        count = int(collection.count())
    except Exception as exc:
        print(f"  ⚠ could not read chroma at {path}: {exc}")
        count = -1

    label = "would remove" if dry_run else "removing"
    print(f"  {label} chroma store {path} ({max(count, 0)} docs)")
    if not dry_run:
        shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        import chromadb

        chromadb.PersistentClient(path=str(path)).get_or_create_collection("langchain")
    return max(count, 0)


def clear_qdrant_store(*, dry_run: bool) -> int:
    from qdrant_store import (  # noqa: WPS433
        QDRANT_COLLECTION,
        QDRANT_STAGING_COLLECTION,
        get_qdrant_store,
        reset_qdrant_singleton,
    )

    reset_qdrant_singleton()
    removed = 0
    for name in (QDRANT_COLLECTION, QDRANT_STAGING_COLLECTION):
        try:
            store = get_qdrant_store(collection_name=name)
            count = store.count()
            print(
                f"  {'would clear' if dry_run else 'clearing'} "
                f"qdrant collection {name} ({count} points)"
            )
            if not dry_run:
                store.client.delete_collection(name)
                get_qdrant_store(collection_name=name)
            removed += count
        except Exception as exc:
            print(f"  ⚠ qdrant {name}: {exc}")
    return removed


def clear_vectordb(*, dry_run: bool) -> None:
    provider = os.getenv("VECTOR_DB_PROVIDER", "chroma").strip().lower()
    print(f"\nVector store ({provider}):")
    if provider == "qdrant":
        clear_qdrant_store(dry_run=dry_run)
        return
    for path in _vectordb_paths():
        clear_chroma_store(path, dry_run=dry_run)


def clear_sync_state(*, dry_run: bool) -> None:
    print("\nSync state files:")
    for path in SYNC_STATE_FILES:
        if path.exists():
            print(f"  {'would delete' if dry_run else 'delete'} {path}")
            if not dry_run:
                path.unlink()
        else:
            print(f"  skip {path} (not found)")

    temp_dotnews = DATA_INGESTION / "temp_downloads" / "dotnews"
    if temp_dotnews.exists():
        pdfs = list(temp_dotnews.glob("*.pdf"))
        if pdfs:
            print(
                f"  {'would remove' if dry_run else 'remove'} "
                f"{len(pdfs)} dotnews PDF(s) in {temp_dotnews}"
            )
            if not dry_run:
                for pdf in pdfs:
                    pdf.unlink()


def _python_executable() -> str:
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def run_reingest(*, monthly: bool) -> int:
    print("\nRe-ingest (dotnews steps remain disabled in cron scripts):")
    daily = ROOT / "cron_ingest.sh"
    if not daily.is_file():
        print(f"  ✗ missing {daily}")
        return 1
    subprocess.run([str(daily)], cwd=ROOT, check=True)
    if monthly:
        monthly_script = ROOT / "cron_ingest_monthly.sh"
        if monthly_script.is_file():
            subprocess.run([str(monthly_script)], cwd=ROOT, check=True)
        else:
            print(f"  ⚠ monthly script not found: {monthly_script}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear ingest knowledge (MySQL + vectors) and optionally re-ingest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without making changes",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required to perform destructive reset (omit with --dry-run only)",
    )
    parser.add_argument(
        "--keep-admin-knowledge",
        action="store_true",
        help="Keep admin_knowledge MySQL rows and skip truncating that table",
    )
    parser.add_argument(
        "--keep-api-cache",
        action="store_true",
        help="Keep api_cache_entries (semantic/session cache)",
    )
    parser.add_argument(
        "--skip-sync-state",
        action="store_true",
        help="Do not delete ingestion sync checkpoint files",
    )
    parser.add_argument(
        "--reingest",
        action="store_true",
        help="Run cron_ingest.sh after reset",
    )
    parser.add_argument(
        "--monthly",
        action="store_true",
        help="Also run cron_ingest_monthly.sh (with --reingest)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.yes:
        print("Refusing to run without --yes (use --dry-run to preview).", file=sys.stderr)
        return 2

    tables = list(DEFAULT_MYSQL_TABLES)
    if args.keep_admin_knowledge:
        tables = [t for t in tables if t != "admin_knowledge"]
    if args.keep_api_cache:
        tables = [t for t in tables if t != "api_cache_entries"]

    mode = "DRY RUN" if args.dry_run else "RESET"
    print("=" * 60)
    print(f"{mode}: ingest knowledge (users/conversations untouched)")
    print(f"MySQL: {_mysql_config()['host']} / {_mysql_config()['database']}")
    print("=" * 60)

    print("\nMySQL tables:")
    clear_mysql_tables(tables, dry_run=args.dry_run)

    clear_vectordb(dry_run=args.dry_run)

    if not args.skip_sync_state:
        clear_sync_state(dry_run=args.dry_run)

    print("\n" + "=" * 60)
    if args.dry_run:
        print("Dry run complete — no data was changed.")
        print("Re-run with: python scripts/reset_knowledge.py --yes --reingest")
    else:
        print("Knowledge reset complete.")
        print("Restart gunicorn so API workers reload an empty vector index.")
        if args.reingest:
            return run_reingest(monthly=args.monthly)
        print("Next: ./cron_ingest.sh  (or add --reingest)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
