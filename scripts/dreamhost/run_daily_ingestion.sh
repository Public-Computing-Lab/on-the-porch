#!/usr/bin/env bash
# Daily ingestion cron wrapper for DreamHost (legacy path).
# Prefer running cron_ingest.sh from the repo root.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/ml-misi-community-sentiment}"
LOG_DIR="$PROJECT_DIR/logs"
SCRIPT_LOG="$LOG_DIR/daily_ingestion.log"

mkdir -p "$LOG_DIR"

{
    echo "=========================================="
    echo "Daily Ingestion Started: $(date)"
    echo "=========================================="
} >> "$SCRIPT_LOG"

if ! "$PROJECT_DIR/cron_ingest.sh" >> "$SCRIPT_LOG" 2>&1; then
    EXIT_CODE=$?
    echo "Daily Ingestion failed with exit code $EXIT_CODE at $(date)" >> "$SCRIPT_LOG"
    exit "$EXIT_CODE"
fi

{
    echo "=========================================="
    echo "Daily Ingestion Finished: $(date)"
    echo "=========================================="
    echo ""
} >> "$SCRIPT_LOG"
